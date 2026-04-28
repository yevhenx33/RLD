import os
import logging
import clickhouse_connect
from indexer.base import BaseSource
from indexer.protocols import PROCESSOR_STATE_ALIASES
from indexer.state import ensure_source_status_table, update_source_status

log = logging.getLogger("processor")
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

class SimulatedLog:
    """Mock HyperSync log object for decoders reading from ClickHouse mempool."""
    def __init__(self, row):
        self.block_number = row[0]
        # row[1] is block_timestamp -> handled by block_ts_map separately
        self.transaction_hash = row[2]
        self.log_index = row[3]
        self.address = row[4]
        # topics
        self.topics = [t for t in [row[6], row[7], row[8], row[9]] if t]
        self.data = row[10]


class ProtocolProcessor:
    """
    Vertical Event Processor.
    Strictly isolated decoder that reads from ClickHouse mempool 
    and idempotently writes to unified_timeseries.
    """
    def __init__(self, source: BaseSource, clickhouse_host=None, clickhouse_port=None):
        self.source = source
        self.ch_host = clickhouse_host or os.getenv("CLICKHOUSE_HOST", "localhost")
        self.ch_port = int(clickhouse_port or os.getenv("CLICKHOUSE_PORT", "8123"))
        self.batch_blocks = 50_000 
        self._ch = None
        
        # Ensure state table exists
        ch = self._get_ch_client()
        ensure_source_status_table(ch)
        ch.command("""
        CREATE TABLE IF NOT EXISTS processor_state (
            protocol String,
            last_processed_block UInt64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY protocol
        """)

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

    def get_last_processed_block(self, ch) -> int:
        aliases = PROCESSOR_STATE_ALIASES.get(self.source.name, (self.source.name,))
        state_in = ", ".join(f"'{a}'" for a in aliases)
        res = ch.command(f"SELECT max(last_processed_block) FROM processor_state WHERE protocol IN ({state_in})")
        return int(res) if res else 0

    def set_last_processed_block(self, ch, block_num: int):
        ch.insert('processor_state', [[self.source.name, int(block_num)]], column_names=['protocol', 'last_processed_block'])
        update_source_status(
            ch,
            self.source.name,
            "processor",
            last_processed_block=int(block_num),
            last_event_block=int(block_num),
        )

    def run_processor_cycle(self):
        if not self.source.raw_table:
            log.info(f"[{self.source.name}-Processor] No raw_table configured. Skipping processing.")
            return

        try:
            ch = self._get_ch_client()
            self.source.get_cursor(ch)  # Ensure source state tables exist
            last_processed = self.get_last_processed_block(ch)
            if last_processed == 0:
                last_processed = max(0, self.source.genesis_block - 1)
            
            # Identify absolute head of mempool
            max_mempool_block_res = ch.command(f"SELECT max(block_number) FROM {self.source.raw_table}")
            max_mempool_block = int(max_mempool_block_res) if max_mempool_block_res else 0

            if max_mempool_block <= last_processed:
                log.info(f"[{self.source.name}-Processor] Up to date at block {last_processed}")
                update_source_status(
                    ch,
                    self.source.name,
                    "processor",
                    last_processed_block=last_processed,
                    last_event_block=max_mempool_block,
                )
                return

            log.info(f"[{self.source.name}-Processor] Decoding {last_processed} -> {max_mempool_block}")
            log.info(f"[{self.source.name}-Processor] POKA-YOKE: Output table = {self.source.output_table}")

            current_start = last_processed + 1
            while current_start <= max_mempool_block:
                current_end = min(current_start + self.batch_blocks - 1, max_mempool_block)
                
                # Extract batch from mempool
                query = f"""
                SELECT 
                    block_number, block_timestamp, tx_hash, log_index, contract, 
                    event_name, topic0, topic1, topic2, topic3, data
                FROM {self.source.raw_table}
                WHERE block_number >= {current_start} AND block_number <= {current_end}
                ORDER BY block_number ASC, log_index ASC
                """
                rows = ch.query(query).result_rows

                if not rows:
                    self.set_last_processed_block(ch, current_end)
                    current_start = current_end + 1
                    continue

                block_ts_map = {}
                simulated_logs = []
                
                for row in rows:
                    block_ts_map[row[0]] = row[1]  # cache block -> timestamp
                    simulated_logs.append(SimulatedLog(row))

                decoded_rows = []
                for log_entry in simulated_logs:
                    # Poison Pill Defensive Wrapper
                    try:
                        d = self.source.decode(log_entry, block_ts_map)
                        if d:
                            decoded_rows.append(d)
                    except Exception as e:
                        # In production this would write to dead_letter_events
                        log.error(f"[{self.source.name}-Processor] Decode Error at block {log_entry.block_number}: {e}")

                if decoded_rows:
                    n_merged = self.source.merge(ch, decoded_rows)
                    log.info(f"[{self.source.name}-Processor] {len(decoded_rows)} decoded -> {n_merged} timeseries metrics written (blocks {current_start}->{current_end})")
                else:
                    log.info(f"[{self.source.name}-Processor] 0 decoded metrics emitted (blocks {current_start}->{current_end})")

                # Advance cursor
                self.set_last_processed_block(ch, current_end)
                update_source_status(
                    ch,
                    self.source.name,
                    "processor",
                    last_processed_block=current_end,
                    last_event_block=max_mempool_block,
                    last_data_timestamp=max(block_ts_map.values()) if block_ts_map else None,
                )
                current_start = current_end + 1
                
                # Explicit reclaim
                decoded_rows.clear()
                simulated_logs.clear()
                block_ts_map.clear()
                rows = []
        except Exception:
            self._reset_client()
            raise
