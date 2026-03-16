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
  v4_quoter           TEXT,
  broker_router       TEXT,
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
  -- Global market state (latest snapshot)
  normalization_factor NUMERIC DEFAULT 1000000000000000000,
  total_debt_raw       NUMERIC DEFAULT 0,
  bad_debt             NUMERIC DEFAULT 0,
  -- Precomputed data blobs
  snapshot            JSONB,            -- materialized global state (rebuilt per block)
  liquidity_bins      JSONB,            -- materialized tick distribution (rebuilt on ModifyLiquidity)
  -- Running counters: total broker-held token balances (updated on ERC20Transfer)
  total_broker_wausdc NUMERIC DEFAULT 0,  -- SUM of all broker wausdc_balance
  total_broker_wrlp   NUMERIC DEFAULT 0,  -- SUM of all broker wrlp_balance
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
  wausdc_balance      NUMERIC,
  wrlp_balance        NUMERIC,
  wausdc_value        NUMERIC,
  wrlp_value          NUMERIC,
  health_factor       TEXT,
  active_lp_token_id     BIGINT DEFAULT 0,
  active_twamm_order_id  TEXT DEFAULT '',
  debt_principal      NUMERIC DEFAULT 0,
  is_liquidated       BOOLEAN DEFAULT false,
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
  -- Per-block swap aggregates
  swap_volume           NUMERIC DEFAULT 0,
  swap_count            INT DEFAULT 0,
  PRIMARY KEY (market_id, block_number)
);

-- ── LIQUIDATIONS ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS liquidations (
  market_id           TEXT NOT NULL REFERENCES markets(market_id),
  block_number        BIGINT NOT NULL,
  block_timestamp     BIGINT NOT NULL,
  user_address        TEXT NOT NULL,
  liquidator_address  TEXT NOT NULL,
  debt_covered        NUMERIC NOT NULL,
  collateral_seized   NUMERIC NOT NULL,
  wrlp_burned         NUMERIC NOT NULL,
  PRIMARY KEY (market_id, block_number, user_address)
);

-- ── RAW EVENT LOG ────────────────────────────────────────────────────────────
-- Append-only. Source of truth for audit trail and events GraphQL query.

CREATE TABLE IF NOT EXISTS events (
  id                  BIGSERIAL PRIMARY KEY,
  market_id           TEXT,
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

-- ── TICK LIQUIDITY NET DELTAS ────────────────────────────────────────────────
-- Per-tick cumulative liquidity delta for the V4 pool.
-- Updated on every ModifyLiquidity: +delta at tickLower, -delta at tickUpper.
-- Used to rebuild the liquidity distribution bins.

CREATE TABLE IF NOT EXISTS tick_liquidity_net (
  pool_id   TEXT NOT NULL,
  tick      INT NOT NULL,
  net_delta NUMERIC NOT NULL DEFAULT 0,
  PRIMARY KEY (pool_id, tick)
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
  is_burned       BOOLEAN NOT NULL,
  is_registered   BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_lp_market_broker ON lp_positions(market_id, broker_address);

-- ── TWAMM ORDERS ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS twamm_orders (
  order_id        TEXT PRIMARY KEY,   -- JTM assigned order ID
  market_id       TEXT NOT NULL REFERENCES markets(market_id),
  owner           TEXT NOT NULL,
  broker_address  TEXT,               -- broker that owns this order
  amount_in       TEXT NOT NULL,      -- raw uint256 string
  expiration      BIGINT NOT NULL,
  start_epoch     BIGINT NOT NULL,
  zero_for_one    BOOLEAN NOT NULL,
  block_number    BIGINT NOT NULL,
  tx_hash         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',  -- active | cancelled | claimed
  is_registered   BOOLEAN NOT NULL DEFAULT false,
  buy_tokens_out  TEXT DEFAULT '0',
  sell_tokens_refund TEXT DEFAULT '0',
  is_cancelled    BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_twamm_market_owner ON twamm_orders(market_id, owner);
CREATE INDEX IF NOT EXISTS idx_twamm_broker ON twamm_orders(broker_address);

-- ── BONDS ────────────────────────────────────────────────────────────────────
-- One row per bond. Broker address is unique (each bond = frozen broker clone).

CREATE TABLE IF NOT EXISTS bonds (
  broker_address  TEXT PRIMARY KEY,
  market_id       TEXT NOT NULL REFERENCES markets(market_id),
  owner           TEXT NOT NULL,
  notional        NUMERIC NOT NULL,
  hedge           NUMERIC NOT NULL,
  duration        BIGINT NOT NULL,
  mint_block      BIGINT NOT NULL,
  mint_tx         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',
  factory_address TEXT,
  close_block     BIGINT,
  close_tx        TEXT
);
CREATE INDEX IF NOT EXISTS idx_bonds_owner ON bonds(owner);
CREATE INDEX IF NOT EXISTS idx_bonds_market ON bonds(market_id);

-- ── RAW EVENT QUEUE (ingestor → processor) ──────────────────────────────────
-- Ingestor writes here. Processor polls status='pending', decodes & updates
-- domain tables, then marks status='done'. Decouples ingestion from processing.

CREATE TABLE IF NOT EXISTS raw_events (
  id              BIGSERIAL PRIMARY KEY,
  block_number    BIGINT NOT NULL,
  block_timestamp BIGINT NOT NULL,
  tx_hash         TEXT NOT NULL,
  log_index       INT NOT NULL,
  contract        TEXT NOT NULL,
  topic0          TEXT NOT NULL,
  topic1          TEXT,
  topic2          TEXT,
  topic3          TEXT,
  data            TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending | done | error
  error_msg       TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_raw_pending ON raw_events(status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_raw_block   ON raw_events(block_number);
