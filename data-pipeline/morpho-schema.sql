-- Morpho Blue hourly state snapshots
CREATE TABLE IF NOT EXISTS morpho_hourly_state (
    timestamp           TIMESTAMPTZ NOT NULL,
    block_number        BIGINT NOT NULL,
    market_id           VARCHAR(66) NOT NULL,
    loan_token          VARCHAR(42) NOT NULL,
    collateral_token    VARCHAR(42) NOT NULL,
    loan_symbol         VARCHAR(20) NOT NULL,
    collateral_symbol   VARCHAR(20) NOT NULL,
    lltv                DOUBLE PRECISION NOT NULL,

    -- Balances (USD normalized, with accrued interest)
    supplied_usd        DOUBLE PRECISION NOT NULL,
    borrowed_usd        DOUBLE PRECISION NOT NULL,

    -- Rates (annualized decimals, e.g. 0.035 = 3.5%)
    supply_rate         DOUBLE PRECISION NOT NULL,
    borrow_rate         DOUBLE PRECISION NOT NULL,
    utilization_rate    DOUBLE PRECISION NOT NULL,

    -- Oracle
    price_usd           DOUBLE PRECISION NOT NULL,

    PRIMARY KEY (timestamp, market_id)
);

SELECT create_hypertable('morpho_hourly_state', 'timestamp', if_not_exists => TRUE);

-- Materialized view for latest state per market
CREATE MATERIALIZED VIEW IF NOT EXISTS latest_morpho_state AS
SELECT DISTINCT ON (market_id) *
FROM morpho_hourly_state
ORDER BY market_id, timestamp DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_latest_morpho_market ON latest_morpho_state (market_id);

CREATE OR REPLACE FUNCTION refresh_latest_morpho_state()
RETURNS void AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY latest_morpho_state;
END;
$$ LANGUAGE plpgsql;
