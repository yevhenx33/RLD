import os
import clickhouse_connect
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("init_tables")

def init_clickhouse():
    ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
    ch_port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    client = clickhouse_connect.get_client(host=ch_host, port=ch_port)
    
    table_names = ["chainlink_events", "lido_events", "static_pegs_events"]
    for table in table_names:
        log.info(f"Ensuring {table} schema...")
        client.command(f'''
        CREATE TABLE IF NOT EXISTS {table}
        (
            block_number UInt64,
            block_timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            contract String,
            event_name String,
            topic0 String,
            topic1 Nullable(String),
            topic2 Nullable(String),
            topic3 Nullable(String),
            data String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (block_number, log_index, contract, topic0)
        ''')
    log.info("Schema Initialization Complete.")

if __name__ == "__main__":
    init_clickhouse()
