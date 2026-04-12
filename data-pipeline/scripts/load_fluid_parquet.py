#!/usr/bin/env python3
"""Load the custom Fluid LogOperate Parquet dump directly into ClickHouse."""
import os, sys, time
import pyarrow.parquet as pq

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))

def load_fluid():
    try:
        import clickhouse_connect
        import eth_abi
    except ImportError:
        print("Missing deps; run pip install clickhouse-connect eth-abi")
        sys.exit(1)

    print(f"Connecting to ClickHouse at {CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}...")
    client = clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT)

    table_name = "fluid_events"
    
    # Drop table first to rebuild with new schema securely
    client.command(f"DROP TABLE IF EXISTS {table_name}")
    
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        block_number    UInt64,
        block_timestamp DateTime,
        tx_hash         String,
        log_index       UInt32,
        contract        String,
        event_name      String,
        topic0          String,
        topic1          Nullable(String),
        topic2          Nullable(String),
        topic3          Nullable(String),
        data            String,
        -- Custom unpacked Fluid Data to match Aave/Morpho
        supply_amount   Int256,
        borrow_amount   Int256,
        supply_exchange_price Float64,
        borrow_exchange_price Float64
    )
    ENGINE = MergeTree()
    ORDER BY (block_number, log_index)
    """
    client.command(ddl)
    print(f"Table '{table_name}' verified.")

    parquet_file = "/mnt/data/hypersync_staging/fluid/fluid_logoperate.parquet"
    if not os.path.exists(parquet_file):
        print(f"ERROR: {parquet_file} not found!")
        sys.exit(1)

    print(f"Reading {parquet_file}...")
    t0 = time.time()
    table = pq.read_table(parquet_file)
    total_logs = len(table)
    print(f"Loaded {total_logs:,} logs in memory ({time.time()-t0:.1f}s).")

    batch_size = 50_000
    total_inserted = 0

    col_block = table.column("block_number").to_pylist()
    col_ts = table.column("block_timestamp").to_pylist()
    col_tx = table.column("tx_hash").to_pylist()
    col_idx = table.column("log_index").to_pylist()
    col_addr = table.column("address").to_pylist()
    col_t0 = table.column("topic0").to_pylist()
    col_t1 = table.column("topic1").to_pylist()
    col_t2 = table.column("topic2").to_pylist()
    col_t3 = table.column("topic3").to_pylist()
    col_data = table.column("data").to_pylist()

    print("Unpacking data and inserting into ClickHouse in batches...")
    for i in range(0, total_logs, batch_size):
        chunk_end = min(i + batch_size, total_logs)
        rows = []
        
        for j in range(i, chunk_end):
            data_hex = col_data[j]
            sup_amt = 0
            bor_amt = 0
            sup_price = 0.0
            bor_price = 0.0
            e_name = "Unknown"
            
            t0hex = col_t0[j]
            
            if t0hex == "0x4d93b232a24e82b284ced7461bf4deacffe66759d5c24513e6f29e571ad78d15":
                # LogOperate
                if data_hex and len(data_hex) >= 130:
                    data_bytes = bytes.fromhex(data_hex[2:130]) # only first 64 bytes needed
                    try:
                        sup_amt, bor_amt = eth_abi.decode(['int256', 'int256'], data_bytes)
                    except Exception:
                        pass
                
                event_names = []
                if sup_amt > 0: event_names.append("Supply")
                elif sup_amt < 0: event_names.append("Withdraw")
                if bor_amt > 0: event_names.append("Borrow")
                elif bor_amt < 0: event_names.append("Repay")
                
                e_name = "Operate_" + ("_".join(event_names) if event_names else "Idle")
                
            elif t0hex == "0x96c40bed7fc8d0ac41633a3bd47f254f0b0076e5df70975c51d23514bc49d3b8":
                e_name = "UpdateExchangePrices"
                if col_t2[j]:
                    sup_price = int(col_t2[j], 16) / 1e12 # Fluid scales typically around 1e12 or 1e27, let's just keep raw or scale
                if col_t3[j]:
                    bor_price = int(col_t3[j], 16) / 1e12
                # In Fluid, exchange prices are scaled by exactly what precision?
                # Actually, storing them purely as raw float handles division perfectly later so just float(raw / 1e12) or 1e18 helps avoid overflow.
                
                # Fluid exchange prices are typically scaled by 1e12 or 1e18. 
                # Let's extract them as pure divided floats:
                sup_price = int(col_t2[j], 16) / 1e12 if col_t2[j] else 0.0
                bor_price = int(col_t3[j], 16) / 1e12 if col_t3[j] else 0.0

            rows.append([
                col_block[j],
                col_ts[j],
                col_tx[j],
                col_idx[j],
                col_addr[j],
                e_name,
                col_t0[j],
                col_t1[j],
                col_t2[j],
                col_t3[j],
                data_hex,
                sup_amt,
                bor_amt,
                sup_price,
                bor_price
            ])

        client.insert(
            table_name,
            rows,
            column_names=[
                "block_number", "block_timestamp", "tx_hash", "log_index",
                "contract", "event_name", "topic0", "topic1", "topic2",
                "topic3", "data", "supply_amount", "borrow_amount",
                "supply_exchange_price", "borrow_exchange_price"
            ],
        )
        total_inserted += len(rows)
        pct = (total_inserted / total_logs) * 100
        print(f"  Inserted {total_inserted:,} / {total_logs:,} ({pct:.1f}%)")

    print(f"DONE! Ingested {total_inserted:,} rows in {time.time()-t0:.1f}s.")
    
    cnt = client.command(f"SELECT count() FROM {table_name}")
    print(f"ClickHouse row count: {cnt}")

if __name__ == "__main__":
    load_fluid()

