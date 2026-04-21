-- Minimal ClickHouse mirror schema for simulation indexer dual-write.
-- Scope is intentionally narrow for P0 parity checks:
--   - indexer cursor
--   - raw events
--   - block snapshots
--   - candles

CREATE TABLE IF NOT EXISTS sim_indexer_cursor (
    market_id String,
    last_indexed_block UInt64,
    last_indexed_at DateTime64(3),
    total_events UInt64,
    synced_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(synced_at)
ORDER BY (market_id);

CREATE TABLE IF NOT EXISTS sim_events (
    market_id String,
    block_number UInt64,
    block_timestamp UInt64,
    tx_hash String,
    log_index Int32,
    event_name String,
    contract_address String,
    data String,
    synced_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (block_number, tx_hash, log_index);

CREATE TABLE IF NOT EXISTS sim_block_states (
    market_id String,
    block_number UInt64,
    block_timestamp UInt64,
    normalization_factor String,
    total_debt String,
    index_price String,
    mark_price String,
    liquidity String,
    token0_balance String,
    token1_balance String,
    swap_volume String,
    swap_count Int32,
    synced_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(synced_at)
ORDER BY (market_id, block_number);

CREATE TABLE IF NOT EXISTS sim_candles (
    market_id String,
    resolution String,
    bucket UInt64,
    index_open String,
    index_high String,
    index_low String,
    index_close String,
    mark_open String,
    mark_high String,
    mark_low String,
    mark_close String,
    volume_usd String,
    swap_count Int32,
    synced_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(synced_at)
ORDER BY (market_id, resolution, bucket);
