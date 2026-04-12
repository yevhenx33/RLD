"""
IndexerEngine — Shared HyperSync polling loop and event router.

Executes a single HyperSync query per cycle that fetches events for
ALL registered sources. Routes events to the correct source by
contract address, then calls decode() and merge() on each.
"""

import os
import sys
import time
import asyncio
import logging
import datetime

import hypersync
import clickhouse_connect

from .base import BaseSource

log = logging.getLogger("indexer")

# ── HyperSync field selections (shared by all sources) ───────
LOG_FIELDS = [
    hypersync.LogField.BLOCK_NUMBER,
    hypersync.LogField.LOG_INDEX,
    hypersync.LogField.TRANSACTION_HASH,
    hypersync.LogField.ADDRESS,
    hypersync.LogField.TOPIC0,
    hypersync.LogField.TOPIC1,
    hypersync.LogField.TOPIC2,
    hypersync.LogField.TOPIC3,
    hypersync.LogField.DATA,
]
BLOCK_FIELDS = [hypersync.BlockField.NUMBER, hypersync.BlockField.TIMESTAMP]

CONFIRMATION_BLOCKS = 3


def build_block_ts_map(blocks) -> dict:
    """Convert HyperSync block list to {block_number: datetime} map."""
    ts_map = {}
    for b in blocks:
        if b.number is not None and b.timestamp is not None:
            ts_val = b.timestamp
            if isinstance(ts_val, str):
                ts_val = int(ts_val, 16) if ts_val.startswith("0x") else int(ts_val)
            ts_map[b.number] = datetime.datetime.fromtimestamp(ts_val, tz=datetime.UTC)
    return ts_map


class IndexerEngine:
    """
    The main indexing engine.

    Usage:
        engine = IndexerEngine(
            sources=[FluidSource(), ChainlinkSource()],
            poll_interval=300,
        )
        engine.run()
    """

    def __init__(
        self,
        sources: list[BaseSource],
        poll_interval: int = 300,
        envio_token: str = "",
        clickhouse_host: str = "localhost",
        clickhouse_port: int = 8123,
    ):
        self.sources = sources
        self.poll_interval = poll_interval
        self.envio_token = envio_token or os.getenv(
            "ENVIO_API_TOKEN", "7a850568-160d-4cd5-bf06-2961bd383cc6"
        )
        self.ch_host = clickhouse_host
        self.ch_port = clickhouse_port

    def _create_hs_client(self):
        return hypersync.HypersyncClient(hypersync.ClientConfig(
            url="https://eth.hypersync.xyz",
            bearer_token=self.envio_token,
        ))

    def _create_ch_client(self):
        return clickhouse_connect.get_client(
            host=self.ch_host, port=self.ch_port
        )

    async def run_cycle(self, hs_client, ch):
        """Execute one poll → decode → merge cycle for all sources."""

        # 1. Determine block range from all sources' cursors
        cursors = [s.get_cursor(ch) for s in self.sources]
        from_block = min(cursors) + 1
        head_block = await hs_client.get_height() - CONFIRMATION_BLOCKS

        if head_block < from_block:
            log.info(f"No new blocks (head={head_block}, cursor={min(cursors)})")
            return

        gap = head_block - from_block + 1
        log.info(f"Processing blocks {from_block} → {head_block} "
                 f"({gap} blocks, ~{gap * 12 / 60:.0f} min)")

        # 2. Build ONE HyperSync query with all sources' log selections
        log_selections = [s.log_selection() for s in self.sources]

        # 3. Paginated fetch — HyperSync returns max ~5000 events per get().
        #    Use next_block to iterate until all events are collected.
        all_logs = []
        all_blocks = []
        cursor = from_block
        t0 = time.time()
        pages = 0

        while cursor < head_block:
            query = hypersync.Query(
                from_block=cursor,
                to_block=head_block,
                logs=log_selections,
                field_selection=hypersync.FieldSelection(
                    log=LOG_FIELDS,
                    block=BLOCK_FIELDS,
                ),
            )
            res = await hs_client.get(query)
            all_logs.extend(res.data.logs)
            all_blocks.extend(res.data.blocks)
            pages += 1

            nb = res.next_block
            if nb <= cursor:
                break
            cursor = nb

        elapsed = time.time() - t0
        log.info(f"  HyperSync: {len(all_logs)} events in {elapsed:.2f}s "
                 f"({pages} pages)")

        # 4. Build block timestamp map
        block_ts_map = build_block_ts_map(all_blocks)

        # 5. Route events to sources and process
        for source in self.sources:
            source_logs = [l for l in all_logs if source.route(l)]
            if not source_logs and not source.raw_table:
                continue

            # Insert raw events (if source has a raw table)
            if source.raw_table and source_logs:
                n_raw = source.insert_raw(ch, source_logs, block_ts_map)
                log.info(f"  {source.name}: {n_raw} raw events stored")

            # Decode
            decoded = []
            for entry in source_logs:
                d = source.decode(entry, block_ts_map)
                if d is not None:
                    decoded.append(d)

            # Merge
            if decoded:
                n_merged = source.merge(ch, decoded)
                log.info(f"  {source.name}: {len(decoded)} decoded → {n_merged} merged")
            elif source_logs:
                log.info(f"  {source.name}: {len(source_logs)} events, 0 decoded")

    async def _async_run(self):
        """Async main loop."""
        log.info("═" * 50)
        log.info("  RLD Indexer Engine (HyperSync)")
        log.info(f"  Sources: {[s.name for s in self.sources]}")
        log.info(f"  ClickHouse: {self.ch_host}:{self.ch_port}")
        log.info(f"  Poll interval: {self.poll_interval}s")
        log.info("═" * 50)

        hs_client = self._create_hs_client()
        ch = self._create_ch_client()

        # Log initial state
        for s in self.sources:
            cursor = s.get_cursor(ch)
            log.info(f"  {s.name}: cursor at block {cursor}")

        while True:
            try:
                await self.run_cycle(hs_client, ch)
            except KeyboardInterrupt:
                log.info("Shutting down.")
                break
            except Exception as e:
                log.error(f"Cycle failed: {e}", exc_info=True)
                try:
                    hs_client = self._create_hs_client()
                    ch = self._create_ch_client()
                except Exception:
                    pass

            log.info(f"Sleeping {self.poll_interval}s...")
            await asyncio.sleep(self.poll_interval)

    def run(self):
        """Start the indexer (blocking)."""
        asyncio.run(self._async_run())
