"""
IndexerEngine — Shared HyperSync polling loop and event router.

Executes a single HyperSync query per cycle that fetches events for
ALL registered sources. Routes events to the correct source by
contract address, then calls decode() and merge() on each.
"""

import os
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


def require_envio_token(explicit_token: str = "") -> str:
    token = (explicit_token or os.getenv("ENVIO_API_TOKEN", "")).strip()
    if not token:
        raise RuntimeError(
            "ENVIO_API_TOKEN is required for HyperSync access. "
            "Set it in the environment before starting the indexer."
        )
    return token


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
        self.envio_token = require_envio_token(envio_token)
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
        """Execute the fetching, parsing, and aggregation pipeline using a bounded stream."""
        cursors = [s.get_cursor(ch) for s in self.sources]
        from_block = min(cursors) + 1
        head_block = await hs_client.get_height() - CONFIRMATION_BLOCKS

        if head_block < from_block:
            log.info(f"No new blocks (head={head_block}, cursor={min(cursors)})")
            return

        gap = head_block - from_block + 1
        log.info(f"Processing blocks {from_block} → {head_block} ({gap} blocks)")

        # ── STAGE 0: Bounded Batch Strategy ─────────────────────
        BATCH_SIZE = 100_000 # Max blocks per memory ingestion cycle
        log_selections = [s.log_selection() for s in self.sources]

        current_start = from_block
        while current_start <= head_block:
            current_end = min(current_start + BATCH_SIZE - 1, head_block)

            # ── STAGE 1: THE FETCHER (Produce to Memory-Pool) ───
            mempool_logs = []
            mempool_blocks = []
            pages = 0
            cursor = current_start
            
            while cursor <= current_end:
                query = hypersync.Query(
                    from_block=cursor,
                    to_block=current_end,
                    logs=log_selections,
                    field_selection=hypersync.FieldSelection(
                        log=LOG_FIELDS,
                        block=BLOCK_FIELDS,
                    ),
                )
                res = await hs_client.get(query)
                mempool_logs.extend(res.data.logs)
                mempool_blocks.extend(res.data.blocks)
                pages += 1

                nb = res.next_block
                if nb <= cursor:
                    break
                cursor = nb

            # Skip downstream if empty
            if not mempool_logs:
                current_start = current_end + 1
                continue

            block_ts_map = build_block_ts_map(mempool_blocks)
            log.info(f"  [Fetcher] Downloaded {len(mempool_logs)} logs over {pages} pages for blocks {current_start}→{current_end}")

            # ── STAGE 2 & 3: PARSER & AGGREGATOR (Consume & Commit) ───
            for source in self.sources:
                # 2.1 Route to relevant source
                source_logs = [l for l in mempool_logs if source.route(l)]
                if not source_logs and not source.raw_table:
                    continue

                # 2.2 Persist Raw Event Ledger (Mempool -> DB)
                if source.raw_table and source_logs:
                    n_raw = source.insert_raw(ch, source_logs, block_ts_map)
                    log.info(f"    [{source.name}-DB] {n_raw} raw events safely committed")

                # 2.3 Strict Decoder (Invariant Parsing)
                decoded = []
                for entry in source_logs:
                    d = source.decode(entry, block_ts_map)
                    if d is not None:
                        # Poka-Yoke Invariant Checking could occur here dynamically
                        decoded.append(d)

                # 3.1 Aggregation & Merge
                if decoded:
                    n_merged = source.merge(ch, decoded)
                    log.info(f"    [{source.name}-Processor] {len(decoded)} decoded → {n_merged} merged to Timeseries")
                elif source_logs:
                    log.info(f"    [{source.name}-Processor] {len(source_logs)} events, 0 resolved to state delta")

            # ── EXPLICIT MEMORY RECLAMATION ───────────────────────
            del mempool_logs
            del mempool_blocks
            del block_ts_map
            
            # Step batch forward
            current_start = current_end + 1

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
