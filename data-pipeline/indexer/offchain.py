import os
import logging
import clickhouse_connect
from indexer.base import BaseSource
from indexer.protocols import RAW_HEAD_QUERY_BY_PROTOCOL, SOFR_RATES
from indexer.state import ensure_source_status_table, update_source_status

log = logging.getLogger("offchain-collector")
CLICKHOUSE_CONNECT_TIMEOUT = int(os.getenv("CLICKHOUSE_CONNECT_TIMEOUT", "5"))
CLICKHOUSE_SEND_RECEIVE_TIMEOUT = int(os.getenv("CLICKHOUSE_SEND_RECEIVE_TIMEOUT", "30"))
CLICKHOUSE_QUERY_RETRIES = int(os.getenv("CLICKHOUSE_QUERY_RETRIES", "1"))
CLICKHOUSE_AUTOGENERATE_SESSION_ID = (
    os.getenv("CLICKHOUSE_AUTOGENERATE_SESSION_ID", "false").strip().lower()
    in {"1", "true", "yes"}
)
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_ASYNC_INSERT = (
    os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower()
    in {"1", "true", "yes"}
)
CLICKHOUSE_WAIT_FOR_ASYNC_INSERT = (
    os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower()
    in {"1", "true", "yes"}
)

class OffchainCollector:
    """
    Vertical Event Collector logic perfectly isolated for REST/Offchain endpoints.
    Uses time-based checkpointing instead of EVM blocks.
    """
    def __init__(self, source: BaseSource, clickhouse_host=None, clickhouse_port=None):
        self.source = source
        self.ch_host = clickhouse_host or os.getenv("CLICKHOUSE_HOST", "localhost")
        self.ch_port = int(clickhouse_port or os.getenv("CLICKHOUSE_PORT", "8123"))
        self._ch = None

    def _create_ch_client(self):
        settings = {}
        if CLICKHOUSE_ASYNC_INSERT:
            settings["async_insert"] = 1
            settings["wait_for_async_insert"] = 1 if CLICKHOUSE_WAIT_FOR_ASYNC_INSERT else 0
        return clickhouse_connect.get_client(
            host=self.ch_host,
            port=self.ch_port,
            username=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            settings=settings,
            connect_timeout=CLICKHOUSE_CONNECT_TIMEOUT,
            send_receive_timeout=CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
            query_retries=CLICKHOUSE_QUERY_RETRIES,
            autogenerate_session_id=CLICKHOUSE_AUTOGENERATE_SESSION_ID,
        )

    def _get_ch_client(self):
        if self._ch is None:
            self._ch = self._create_ch_client()
        return self._ch

    def _reset_client(self):
        if self._ch is not None:
            try:
                self._ch.close_connections()
            except Exception:
                pass
            try:
                self._ch.close()
            except Exception:
                pass
        self._ch = None

    def _ensure_collector_health_table(self, ch):
        ensure_source_status_table(ch)
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS collector_state (
                protocol String,
                last_collected_block UInt64,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            ORDER BY protocol
            """
        )

    def _set_last_collected_block(self, ch):
        protocol = SOFR_RATES if self.source.name == SOFR_RATES else self.source.name
        query = RAW_HEAD_QUERY_BY_PROTOCOL.get(self.source.name)
        value = ch.command(query) if query else 0
        head = int(value) if value not in (None, "", "None") else 0
        ch.insert(
            "collector_state",
            [[protocol, head]],
            column_names=["protocol", "last_collected_block"],
        )
        max_ts = ch.command(f"SELECT max(timestamp) FROM {self.source.raw_table}")
        update_source_status(
            ch,
            protocol,
            "collector",
            last_scanned_block=head,
            last_event_block=head,
            source_head_block=head,
            last_data_timestamp=max_ts,
        )

    async def run_collector_cycle(self):
        """Fetch raw offchain snapshots and dump to ClickHouse."""
        if not hasattr(self.source, 'raw_table') or not self.source.raw_table:
            log.info(f"[{self.source.name}-Collector] No raw_table configured.")
            return

        ch = self._get_ch_client()
        self._ensure_collector_health_table(ch)
        
        try:
            # Custom hook on the source itself for polling REST/chainlink
            num_inserted = await self.source.poll_and_insert(ch)
            self._set_last_collected_block(ch)
            if num_inserted > 0:
                log.info(f"[{self.source.name}-Collector] Dumped {num_inserted} events to {self.source.raw_table}")
        except Exception as e:
            log.error(f"[{self.source.name}-Collector] Sync failed: {e}")
            self._reset_client()
