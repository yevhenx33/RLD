-- RLD Protocol Indexer Schema
-- Run at bootstrap. All tables are market_id-scoped.
-- No DEFAULT values — every write must be explicit.

-- ── GLOBAL ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS markets (
  market_id           TEXT PRIMARY KEY,
  deploy_block        BIGINT NOT NULL,
  deploy_timestamp    BIGINT NOT NULL,
  -- RLD contracts
  broker_factory      TEXT NOT NULL,
  mock_oracle         TEXT NOT NULL,
  twamm_hook          TEXT NOT NULL,
  swap_router         TEXT,
  bond_factory        TEXT,
  basis_trade_factory TEXT,
  broker_executor     TEXT,
  -- Tokens
  wausdc              TEXT NOT NULL,
  wausdc_symbol       TEXT NOT NULL,
  wrlp                TEXT NOT NULL,
  wrlp_symbol         TEXT NOT NULL,
  -- V4 pool
  pool_id             TEXT NOT NULL,
  pool_fee            INT NOT NULL,
  tick_spacing        INT NOT NULL,
  -- Risk params (raw WAD strings — no precision loss)
  min_col_ratio       TEXT NOT NULL,
  maintenance_margin  TEXT NOT NULL,
  liq_close_factor    TEXT NOT NULL,
  funding_period_sec  BIGINT NOT NULL,
  debt_cap            TEXT NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL
);

-- ── INDEXER PROGRESS ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS indexer_state (
  market_id           TEXT PRIMARY KEY REFERENCES markets(market_id),
  last_indexed_block  BIGINT NOT NULL,
  last_indexed_at     TIMESTAMPTZ,
  total_events        BIGINT NOT NULL
);

-- ── BROKERS ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS brokers (
  address             TEXT PRIMARY KEY,
  market_id           TEXT NOT NULL REFERENCES markets(market_id),
  owner               TEXT NOT NULL,
  -- State (NULL until first state event arrives)
  collateral          NUMERIC,
  debt                NUMERIC,
  collateral_value    NUMERIC,
  debt_value          NUMERIC,
  health_factor       TEXT,
  active_token_id     BIGINT,
  -- Provenance
  created_block       BIGINT NOT NULL,
  created_tx          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_brokers_market ON brokers(market_id);
CREATE INDEX IF NOT EXISTS idx_brokers_owner  ON brokers(owner);

-- ── BLOCK STATE SNAPSHOTS ────────────────────────────────────────────────────
-- One row per block per market. "latest" = MAX(block_number) for the market.

CREATE TABLE IF NOT EXISTS block_states (
  market_id             TEXT NOT NULL REFERENCES markets(market_id),
  block_number          BIGINT NOT NULL,
  block_timestamp       BIGINT NOT NULL,
  -- Market state
  normalization_factor  NUMERIC,
  total_debt            NUMERIC,
  index_price           NUMERIC,
  -- V4 pool state (Slot0)
  sqrt_price_x96        TEXT,
  tick                  INT,
  mark_price            NUMERIC,
  liquidity             TEXT,
  token0_balance        NUMERIC,
  token1_balance        NUMERIC,
  fee_growth_global0    TEXT,
  fee_growth_global1    TEXT,
  PRIMARY KEY (market_id, block_number)
);

-- ── RAW EVENT LOG ────────────────────────────────────────────────────────────
-- Append-only. Source of truth for audit trail and events GraphQL query.

CREATE TABLE IF NOT EXISTS events (
  id                  BIGSERIAL PRIMARY KEY,
  market_id           TEXT REFERENCES markets(market_id),
  block_number        BIGINT NOT NULL,
  block_timestamp     BIGINT NOT NULL,
  tx_hash             TEXT NOT NULL,
  log_index           INT NOT NULL,
  event_name          TEXT NOT NULL,
  contract_address    TEXT NOT NULL,
  data                JSONB NOT NULL,
  UNIQUE (tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_events_market_name ON events(market_id, event_name);
CREATE INDEX IF NOT EXISTS idx_events_block       ON events(block_number);
CREATE INDEX IF NOT EXISTS idx_events_contract    ON events(contract_address);

-- ── CANDLES ──────────────────────────────────────────────────────────────────
-- Single table for all resolutions: 1m, 5m, 15m, 1h, 4h, 1d.
-- Upserted inline on every Swap event. No aggregation daemon.

CREATE TABLE IF NOT EXISTS candles (
  market_id   TEXT NOT NULL REFERENCES markets(market_id),
  resolution  TEXT NOT NULL,
  bucket      BIGINT NOT NULL,  -- floor(block_timestamp / interval_secs) * interval_secs
  index_open  NUMERIC NOT NULL,
  index_high  NUMERIC NOT NULL,
  index_low   NUMERIC NOT NULL,
  index_close NUMERIC NOT NULL,
  mark_open   NUMERIC NOT NULL,
  mark_high   NUMERIC NOT NULL,
  mark_low    NUMERIC NOT NULL,
  mark_close  NUMERIC NOT NULL,
  volume_usd  NUMERIC NOT NULL,
  swap_count  INT NOT NULL,
  PRIMARY KEY (market_id, resolution, bucket)
);

-- ── V4 LP POSITIONS ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lp_positions (
  token_id        BIGINT PRIMARY KEY,
  market_id       TEXT NOT NULL REFERENCES markets(market_id),
  broker_address  TEXT NOT NULL,
  liquidity       TEXT NOT NULL,
  tick_lower      INT NOT NULL,
  tick_upper      INT NOT NULL,
  entry_price     NUMERIC,        -- NULL until pool price known at mint block
  entry_tick      INT,
  mint_block      BIGINT NOT NULL,
  is_active       BOOLEAN NOT NULL,
  is_burned       BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lp_market_broker ON lp_positions(market_id, broker_address);

-- ── TWAMM ORDERS ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS twamm_orders (
  order_id        TEXT PRIMARY KEY,   -- keccak256(owner || expiration || zeroForOne)
  market_id       TEXT NOT NULL REFERENCES markets(market_id),
  owner           TEXT NOT NULL,
  broker_address  TEXT,               -- NULL if submitted directly
  amount_in       TEXT NOT NULL,      -- raw uint256 string
  sell_rate       TEXT,               -- filled from getOrder() enrichment
  expiration      BIGINT NOT NULL,
  start_epoch     BIGINT NOT NULL,
  zero_for_one    BOOLEAN NOT NULL,
  block_number    BIGINT NOT NULL,
  tx_hash         TEXT NOT NULL,
  is_cancelled    BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_twamm_market_owner ON twamm_orders(market_id, owner);
