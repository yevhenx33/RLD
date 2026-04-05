-- Create the core snapshot table
CREATE TABLE IF NOT EXISTS aave_hourly_state (
    timestamp TIMESTAMPTZ NOT NULL,
    block_number BIGINT NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    reserve_address VARCHAR(42) NOT NULL,
    
    -- Balances (USD normalized)
    supplied_usd DOUBLE PRECISION NOT NULL,
    borrowed_usd DOUBLE PRECISION NOT NULL,
    
    -- Rates & Ratios (Decimals)
    supply_rate DOUBLE PRECISION NOT NULL,
    borrow_rate DOUBLE PRECISION NOT NULL,
    utilization_rate DOUBLE PRECISION NOT NULL,
    
    -- Oracle
    price_usd DOUBLE PRECISION NOT NULL,
    
    PRIMARY KEY (timestamp, reserve_address)
);

-- Convert to Timescale Hypertables for lightning-fast OLAP queries
SELECT create_hypertable('aave_hourly_state', 'timestamp', if_not_exists => TRUE);

-- Create a materialized view for instant 'latest' lookups (OLTP equivalent functionality)
CREATE MATERIALIZED VIEW IF NOT EXISTS latest_pool_state AS
SELECT DISTINCT ON (reserve_address) *
FROM aave_hourly_state
ORDER BY reserve_address, timestamp DESC;

CREATE UNIQUE INDEX ON latest_pool_state (reserve_address);

-- Refresh function to be run at the end of the async script
CREATE OR REPLACE FUNCTION refresh_latest_pool_state()
RETURNS void AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY latest_pool_state;
END;
$$ LANGUAGE plpgsql;
