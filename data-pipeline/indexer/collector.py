import os
import logging
import datetime
import hypersync
import clickhouse_connect
from indexer.base import BaseSource
from indexer.protocols import CHAINLINK_PRICES, RAW_HEAD_QUERY_BY_PROTOCOL
from indexer.state import get_source_status, update_source_status, ensure_source_status_table

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
    def __init__(self, source: BaseSource, clickhouse_host=None, clickhouse_port=None):
        self.source = source
        self.envio_token = require_envio_token()
        self.ch_host = clickhouse_host or os.getenv("CLICKHOUSE_HOST", "localhost")
        self.ch_port = int(clickhouse_port or os.getenv("CLICKHOUSE_PORT", "8123"))
        self._hs_client = None
        self._ch = None
        self._health_table_ready = False

    def _create_hs_client(self):
        return hypersync.HypersyncClient(hypersync.ClientConfig(
            url="https://eth.hypersync.xyz",
            bearer_token=self.envio_token,
        ))

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

    def _get_hs_client(self):
        if self._hs_client is None:
            self._hs_client = self._create_hs_client()
        return self._hs_client

    def _get_ch_client(self):
        if self._ch is None:
            self._ch = self._create_ch_client()
        return self._ch

    def _reset_clients(self):
        self._hs_client = None
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
        if self._health_table_ready:
            return
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
        self._health_table_ready = True

    def _set_last_collected_block(
        self,
        ch,
        block_num: int,
        *,
        source_head_block: int | None = None,
        last_event_block: int | None = None,
    ):
        protocol = CHAINLINK_PRICES if self.source.name == CHAINLINK_PRICES else self.source.name
        ch.insert(
            "collector_state",
            [[protocol, int(block_num)]],
            column_names=["protocol", "last_collected_block"],
        )
        update_source_status(
            ch,
            protocol,
            "collector",
            last_scanned_block=int(block_num),
            last_event_block=int(last_event_block if last_event_block is not None else block_num),
            source_head_block=int(source_head_block if source_head_block is not None else block_num),
        )

    def _raw_head(self, ch) -> int:
        query = RAW_HEAD_QUERY_BY_PROTOCOL.get(self.source.name)
        if query:
            value = ch.command(query)
            return int(value) if value not in (None, "", "None") else 0
        value = ch.command(f"SELECT max(block_number) FROM {self.source.raw_table}")
        return int(value) if value not in (None, "", "None") else 0

    async def run_collector_cycle(self):
        """Fetch raw events for THIS protocol and flush to ClickHouse."""
        if not self.source.raw_table:
            log.info(f"[{self.source.name}-Collector] No raw_table configured. Skipping collection.")
            return

        hs_client = self._get_hs_client()
        ch = self._get_ch_client()
        self._ensure_collector_health_table(ch)

        # Initialize source-specific tables/caches, then prefer the scanned cursor.
        raw_cursor = self.source.get_cursor(ch)
        status = get_source_status(ch, self.source.name, "collector")
        cursor = status["last_scanned_block"] or raw_cursor
        is_offchain = bool(getattr(self.source, "is_offchain", False))
        from_block = (cursor + 1) if (cursor > 0 and not is_offchain) else self.source.genesis_block
        try:
            head_block = await hs_client.get_height() - CONFIRMATION_BLOCKS
        except Exception:
            self._reset_clients()
            raise

        if head_block < from_block:
            log.info(f"[{self.source.name}-Collector] No new blocks. Cursor at {from_block}")
            self._set_last_collected_block(
                ch,
                max(0, from_block - 1),
                source_head_block=head_block,
                last_event_block=self._raw_head(ch),
            )
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

            try:
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
            except Exception:
                self._reset_clients()
                raise

            if not mempool_logs:
                self._set_last_collected_block(
                    ch,
                    current_end,
                    source_head_block=head_block,
                    last_event_block=self._raw_head(ch),
                )
                current_start = current_end + 1
                continue

            # Route & Write strictly to this protocol's raw_table
            block_ts_map = build_block_ts_map(mempool_blocks)
            source_logs = [
                log_entry for log_entry in mempool_logs if self.source.route(log_entry)
            ]
            
            if source_logs:
                # We assert invariant: Poka-Yoke isolation.
                n_raw = self.source.insert_raw(ch, source_logs, block_ts_map)
                log.info(f"[{self.source.name}-Collector] DUMPED {n_raw} raw events to {self.source.raw_table}")
            else:
                log.info(f"[{self.source.name}-Collector] 0 matched events in blocks {current_start}->{current_end}")
            self._set_last_collected_block(
                ch,
                current_end,
                source_head_block=head_block,
                last_event_block=self._raw_head(ch),
            )

            # Strict memory clearing
            mempool_logs.clear()
            mempool_blocks.clear()
            block_ts_map.clear()
            
            current_start = current_end + 1
