import clickhouse_connect
import psycopg2
import sqlite3
import pandas as pd
import numpy as np
import time

# 1. Init ClickHouse
ch_client = clickhouse_connect.get_client(host='localhost', port=8123, username='default', password='')

ch_client.command('DROP TABLE IF EXISTS unified_timeseries')
ch_client.command('''
CREATE TABLE unified_timeseries
(
    timestamp DateTime,
    protocol LowCardinality(String),
    symbol LowCardinality(String),
    entity_id String,
    target_id String,
    supply_usd Float64,
    borrow_usd Float64,
    supply_apy Float64,
    borrow_apy Float64,
    utilization Float64,
    price_usd Float64
) ENGINE = MergeTree
ORDER BY (protocol, symbol, timestamp)
SETTINGS index_granularity = 8192;
''')

print("[1/4] Processing AAVE Markets...")
# Postgres connection for AAVE
pg_conn = psycopg2.connect(host="127.0.0.1", port=5433, user="postgres", dbname="rld_data")
aave_query = """
SELECT 
    to_timestamp((extract(epoch from timestamp)::bigint / 3600) * 3600) AS timestamp_str,
    'AAVE_MARKET' AS protocol,
    COALESCE(UPPER(symbol), 'UNKNOWN') AS symbol,
    reserve_address AS entity_id,
    '' AS target_id,
    COALESCE(supplied_usd, 0.0) AS supply_usd,
    COALESCE(borrowed_usd, 0.0) AS borrow_usd,
    COALESCE(supply_rate, 0.0) AS supply_apy,
    COALESCE(borrow_rate, 0.0) AS borrow_apy,
    COALESCE(utilization_rate, 0.0) AS utilization,
    COALESCE(price_usd, 0.0) AS price_usd
FROM aave_hourly_state
"""
for chunk in pd.read_sql(aave_query, pg_conn, chunksize=100000):
    chunk['timestamp'] = pd.to_datetime(chunk['timestamp_str']).dt.tz_localize(None)
    chunk.drop(columns=['timestamp_str'], inplace=True)
    ch_client.insert_df('unified_timeseries', chunk)
pg_conn.close()


print("[2/4] Processing MORPHO Markets...")
sq_conn = sqlite3.connect("/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db")

morpho_market_query = """
SELECT 
    datetime((s.timestamp / 3600) * 3600, 'unixepoch') AS timestamp_str,
    'MORPHO_MARKET' AS protocol,
    COALESCE(UPPER(p.loan_symbol), 'UNKNOWN') AS symbol,
    s.market_id AS entity_id,
    '' AS target_id,
    0.0 AS supply_usd,  -- Simplified for now as we'd need oracle joins
    0.0 AS borrow_usd,
    COALESCE(s.supply_apy, 0.0) AS supply_apy,
    COALESCE(s.borrow_apy, 0.0) AS borrow_apy,
    COALESCE(s.utilization, 0.0) AS utilization,
    COALESCE(s.oracle_price, 0.0) AS price_usd
FROM market_snapshots s
LEFT JOIN market_params p ON s.market_id = p.market_id
"""
for chunk in pd.read_sql(morpho_market_query, sq_conn, chunksize=100000):
    chunk['timestamp'] = pd.to_datetime(chunk['timestamp_str'])
    chunk.drop(columns=['timestamp_str'], inplace=True)
    chunk['price_usd'] = pd.to_numeric(chunk['price_usd'], errors='coerce').fillna(0.0)
    ch_client.insert_df('unified_timeseries', chunk)


print("[3/4] Processing MORPHO Vaults...")
morpho_vault_query = """
SELECT 
    datetime((s.timestamp / 3600) * 3600, 'unixepoch') AS timestamp_str,
    'MORPHO_VAULT' AS protocol,
    COALESCE(UPPER(m.asset_symbol), 'UNKNOWN') AS symbol,
    s.vault_address AS entity_id,
    '' AS target_id,
    COALESCE(s.total_assets_usd, 0.0) AS supply_usd,
    0.0 AS borrow_usd,
    0.0 AS supply_apy,
    0.0 AS borrow_apy,
    0.0 AS utilization,
    COALESCE(s.share_price, 0.0) AS price_usd
FROM vault_snapshots s
LEFT JOIN vault_meta m ON s.vault_address = m.vault_address
"""
for chunk in pd.read_sql(morpho_vault_query, sq_conn, chunksize=100000):
    chunk['timestamp'] = pd.to_datetime(chunk['timestamp_str'])
    chunk.drop(columns=['timestamp_str'], inplace=True)
    ch_client.insert_df('unified_timeseries', chunk)


print("[4/4] Processing MORPHO Allocations...")
morpho_alloc_query = """
SELECT 
    datetime((a.timestamp / 3600) * 3600, 'unixepoch') AS timestamp_str,
    'MORPHO_ALLOCATION' AS protocol,
    COALESCE(UPPER(m.asset_symbol), 'UNKNOWN') AS symbol,
    a.vault_address AS entity_id,
    a.market_id AS target_id,
    COALESCE(a.supply_usd, 0.0) AS supply_usd,
    0.0 AS borrow_usd,
    0.0 AS supply_apy,
    0.0 AS borrow_apy,
    COALESCE(a.share_pct, 0.0) AS utilization,
    0.0 AS price_usd
FROM vault_allocations a
LEFT JOIN vault_meta m ON a.vault_address = m.vault_address
"""
for chunk in pd.read_sql(morpho_alloc_query, sq_conn, chunksize=100000):
    chunk['timestamp'] = pd.to_datetime(chunk['timestamp_str'])
    chunk.drop(columns=['timestamp_str'], inplace=True)
    ch_client.insert_df('unified_timeseries', chunk)

sq_conn.close()

total_rows = ch_client.command("SELECT count() FROM unified_timeseries")
print(f"\\n✅ SUCCESS! Inserted {total_rows:,} rows into ClickHouse merged_protocols_hourly.")

# Poka-Yoke assertions
assert total_rows > 1000000, f"Expected >1M rows, got {total_rows}"
print("Poka-yoke validation passed.")
