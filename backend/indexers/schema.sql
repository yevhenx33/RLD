-- RLD Protocol Indexer Schema
-- Run at bootstrap. All tables are market_id-scoped.
-- No DEFAULT values — every write must be explicit.

-- ── GLOBAL ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS markets (
  market_id           TEXT PRIMARY KEY,
  market_type         TEXT NOT NULL DEFAULT 'perp',
  deploy_block        BIGINT NOT NULL,
  deploy_timestamp    BIGINT NOT NULL,
  -- RLD contracts
  broker_factory      TEXT NOT NULL,
  mock_oracle         TEXT NOT NULL,
  twamm_hook          TEXT NOT NULL,
  ghost_router        TEXT,
  twap_engine         TEXT,
  twap_engine_lens    TEXT,
  swap_router         TEXT,
  bond_factory        TEXT,
  basis_trade_factory TEXT,
  broker_executor     TEXT,
  v4_quoter           TEXT,
  broker_router       TEXT,
  v4_position_manager TEXT,
  v4_state_view       TEXT,
  pool_manager        TEXT,
  -- Tokens
  collateral_token    TEXT,
  collateral_symbol   TEXT,
  position_token      TEXT,
  position_symbol     TEXT,
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
  funding_model       TEXT,
  settlement_module   TEXT,
  decay_rate_wad      TEXT,
  debt_cap            TEXT NOT NULL,
  product_metadata    JSONB DEFAULT '{}'::jsonb,
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

-- Backward-compatible migration for already initialized databases.
ALTER TABLE markets ADD COLUMN IF NOT EXISTS ghost_router TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS twap_engine TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS twap_engine_lens TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS market_type TEXT NOT NULL DEFAULT 'perp';
ALTER TABLE markets ADD COLUMN IF NOT EXISTS collateral_token TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS collateral_symbol TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS position_token TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS position_symbol TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS funding_model TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS settlement_module TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS decay_rate_wad TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS product_metadata JSONB DEFAULT '{}'::jsonb;
UPDATE markets
SET collateral_token = COALESCE(collateral_token, wausdc),
    collateral_symbol = COALESCE(collateral_symbol, wausdc_symbol),
    position_token = COALESCE(position_token, wrlp),
    position_symbol = COALESCE(position_symbol, wrlp_symbol)
WHERE collateral_token IS NULL OR collateral_symbol IS NULL
   OR position_token IS NULL OR position_symbol IS NULL;

-- ── INDEXER PROGRESS ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS indexer_state (
  market_id           TEXT PRIMARY KEY REFERENCES markets(market_id),
  last_indexed_block  BIGINT NOT NULL,
  last_indexed_at     TIMESTAMPTZ,
  total_events        BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_status (
  source               TEXT NOT NULL,
  kind                 TEXT NOT NULL,
  market_id            TEXT,
  market_type          TEXT,
  last_scanned_block   BIGINT NOT NULL DEFAULT 0,
  last_event_block     BIGINT NOT NULL DEFAULT 0,
  last_processed_block BIGINT NOT NULL DEFAULT 0,
  source_head_block    BIGINT NOT NULL DEFAULT 0,
  last_success_at      TIMESTAMPTZ,
  last_error           TEXT NOT NULL DEFAULT '',
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (source, kind)
);
CREATE INDEX IF NOT EXISTS idx_source_status_market ON source_status(market_id, kind);

-- ── BROKERS ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS brokers (
  address             TEXT PRIMARY KEY,
  market_id           TEXT NOT NULL REFERENCES markets(market_id),
  owner               TEXT NOT NULL,
  -- Raw ERC-20 balances (uint256 strings, updated by ERC20Transfer events)
  wausdc_balance      TEXT DEFAULT '0',
  wrlp_balance        TEXT DEFAULT '0',
  -- Debt principal (uint256 string, updated by PositionModified deltaDebt)
  debt_principal      TEXT DEFAULT '0',
  -- Active collateral tracking (updated by ActivePositionChanged / ActiveTwammOrderChanged)
  active_lp_token_id     TEXT DEFAULT '0',
  active_twamm_order_id  TEXT DEFAULT '',
  -- Lifecycle state
  is_frozen           BOOLEAN DEFAULT false,
  is_liquidated       BOOLEAN DEFAULT false,
  -- Provenance
  created_block       BIGINT NOT NULL,
  created_tx          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_brokers_market ON brokers(market_id);
CREATE INDEX IF NOT EXISTS idx_brokers_owner  ON brokers(owner);

-- ── BROKER OPERATORS ────────────────────────────────────────────────────────
-- Tracks operator addresses per broker. Updated by OperatorUpdated events.
-- INSERT on active=true, DELETE on active=false.

CREATE TABLE IF NOT EXISTS broker_operators (
  broker_address  TEXT NOT NULL,
  operator        TEXT NOT NULL,
  PRIMARY KEY (broker_address, operator)
);

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
-- Reference table for all V4 LP NFTs associated with our pool.
-- Populated by LiquidityAdded/Removed + ERC721 Transfer + ModifyLiquidity.
-- Decoupled from brokers: owner tracks current NFT holder (user or broker).

CREATE TABLE IF NOT EXISTS lp_positions (
  token_id        TEXT PRIMARY KEY,       -- uint256 string (V4 PositionManager NFT ID)
  pool_id         TEXT,                   -- V4 pool ID (from ModifyLiquidity or market config)
  owner           TEXT NOT NULL,          -- current NFT holder (updated on ERC721 Transfer)
  liquidity       TEXT NOT NULL DEFAULT '0', -- raw uint128 string
  tick_lower      INT,                    -- from ModifyLiquidity decode
  tick_upper      INT,                    -- from ModifyLiquidity decode
  mint_block      BIGINT NOT NULL,
  is_active       BOOLEAN NOT NULL DEFAULT false, -- is this the broker's tracked LP?
  is_burned       BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_lp_owner ON lp_positions(owner);
CREATE INDEX IF NOT EXISTS idx_lp_pool  ON lp_positions(pool_id);

-- ── TWAMM ORDERS ─────────────────────────────────────────────────────────────
-- Reference table for all JTM streaming orders.
-- Populated by TwammOrderSubmitted (from PrimeBroker) + SubmitOrder (from JTM hook).

CREATE TABLE IF NOT EXISTS twamm_orders (
  order_id        TEXT PRIMARY KEY,        -- bytes32 JTM assigned order ID
  pool_id         TEXT,                    -- V4 pool ID (from SubmitOrder topic)
  owner           TEXT NOT NULL,           -- order owner (broker address or EOA)
  amount_in       TEXT NOT NULL,           -- raw uint256 string
  expiration      BIGINT NOT NULL,
  start_epoch     BIGINT,                  -- from JTM SubmitOrder data
  sell_rate        TEXT,                    -- raw uint256 from JTM SubmitOrder data
  nonce           BIGINT,                   -- order nonce from OrderKey
  zero_for_one    BOOLEAN NOT NULL,
  block_number    BIGINT NOT NULL,
  tx_hash         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',  -- active | cancelled | claimed
  is_registered   BOOLEAN NOT NULL DEFAULT false,  -- is this the broker's tracked order?
  buy_tokens_out  TEXT DEFAULT '0',        -- filled on cancel/claim
  sell_tokens_refund TEXT DEFAULT '0'       -- filled on cancel
);
CREATE INDEX IF NOT EXISTS idx_twamm_owner ON twamm_orders(owner);
CREATE INDEX IF NOT EXISTS idx_twamm_pool  ON twamm_orders(pool_id);

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
  entry_rate      DOUBLE PRECISION,
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
