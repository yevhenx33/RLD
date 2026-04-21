import os
import asyncio
import logging
import datetime
import hypersync
import clickhouse_connect
from indexer.base import BaseSource

log = logging.getLogger("collector")

# ── HyperSync field selections ───────
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
BATCH_SIZE = 100_000


def require_envio_token() -> str:
    token = os.getenv("ENVIO_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "ENVIO_API_TOKEN is required for HyperSync collection. "
            "Set it in the environment before starting the collector."
        )
    return token


def build_block_ts_map(blocks) -> dict:
    ts_map = {}
    for b in blocks:
        if b.number is not None and b.timestamp is not None:
            ts_val = b.timestamp
            if isinstance(ts_val, str):
                ts_val = int(ts_val, 16) if ts_val.startswith("0x") else int(ts_val)
            ts_map[b.number] = datetime.datetime.fromtimestamp(ts_val, tz=datetime.UTC)
    return ts_map

class ProtocolCollector:
    """
    Vertical Event Collector logic. 
    Strictly isolated fetcher that ONLY pulls from HyperSync to the ClickHouse mempool.
    """
    def __init__(self, source: BaseSource, clickhouse_host="localhost", clickhouse_port=8123):
        self.source = source
        self.envio_token = require_envio_token()
        self.ch_host = clickhouse_host
        self.ch_port = clickhouse_port

    def _create_hs_client(self):
        return hypersync.HypersyncClient(hypersync.ClientConfig(
            url="https://eth.hypersync.xyz",
            bearer_token=self.envio_token,
        ))

    def _create_ch_client(self):
        return clickhouse_connect.get_client(host=self.ch_host, port=self.ch_port)

    async def run_collector_cycle(self):
        """Fetch raw events for THIS protocol and flush to ClickHouse."""
        if not self.source.raw_table:
            log.info(f"[{self.source.name}-Collector] No raw_table configured. Skipping collection.")
            return

        hs_client = self._create_hs_client()
        ch = self._create_ch_client()

        # Step 1: Query local DB strictly for this protocol's schema
        cursor = self.source.get_cursor(ch)
        from_block = (cursor + 1) if cursor > 0 else self.source.genesis_block
        head_block = await hs_client.get_height() - CONFIRMATION_BLOCKS

        if head_block < from_block:
            log.info(f"[{self.source.name}-Collector] No new blocks. Cursor at {from_block}")
            return

        log.info(f"[{self.source.name}-Collector] Syncing {from_block} -> {head_block}")
        
        log_selection = self.source.log_selection()
        current_start = from_block

        while current_start <= head_block:
            current_end = min(current_start + BATCH_SIZE - 1, head_block)
            
            mempool_logs = []
            mempool_blocks = []
            pages = 0
            cursor = current_start

            while cursor <= current_end:
                query = hypersync.Query(
                    from_block=cursor,
                    to_block=current_end,
                    logs=[log_selection],
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

            if not mempool_logs:
                current_start = current_end + 1
                continue

            # Route & Write strictly to this protocol's raw_table
            block_ts_map = build_block_ts_map(mempool_blocks)
            source_logs = [l for l in mempool_logs if self.source.route(l)]
            
            if source_logs:
                # We assert invariant: Poka-Yoke isolation.
                n_raw = self.source.insert_raw(ch, source_logs, block_ts_map)
                log.info(f"[{self.source.name}-Collector] DUMPED {n_raw} raw events to {self.source.raw_table}")
            else:
                log.info(f"[{self.source.name}-Collector] 0 matched events in blocks {current_start}->{current_end}")

            # Strict memory clearing
            mempool_logs.clear()
            mempool_blocks.clear()
            block_ts_map.clear()
            
            current_start = current_end + 1
