import os
import asyncio
import logging
import clickhouse_connect
from indexer.base import BaseSource

log = logging.getLogger("offchain-collector")

class OffchainCollector:
    """
    Vertical Event Collector logic perfectly isolated for REST/Offchain endpoints.
    Uses time-based checkpointing instead of EVM blocks.
    """
    def __init__(self, source: BaseSource, clickhouse_host="localhost", clickhouse_port=8123):
        self.source = source
        self.ch_host = clickhouse_host
        self.ch_port = clickhouse_port

    def _create_ch_client(self):
        return clickhouse_connect.get_client(host=self.ch_host, port=self.ch_port)

    async def run_collector_cycle(self):
        """Fetch raw offchain snapshots and dump to ClickHouse."""
        if not hasattr(self.source, 'raw_table') or not self.source.raw_table:
            log.info(f"[{self.source.name}-Collector] No raw_table configured.")
            return

        ch = self._create_ch_client()
        
        try:
            # Custom hook on the source itself for polling REST/chainlink
            num_inserted = await self.source.poll_and_insert(ch)
            if num_inserted > 0:
                log.info(f"[{self.source.name}-Collector] Dumped {num_inserted} events to {self.source.raw_table}")
        except Exception as e:
            log.error(f"[{self.source.name}-Collector] Sync failed: {e}")
        finally:
            ch.close()
