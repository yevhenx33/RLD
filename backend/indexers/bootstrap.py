"""
bootstrap.py — Schema migration + global session bootstrap.

Reads DEPLOYMENT_JSON env var (or /config/deployment.json) for the global
session config: V4 infra, RLD core, session_start_block.

design:
  - tolerant of both the old single-key format and any new format
  - if session_start_block is missing → query on-chain current block
  - applies schema.sql once (CREATE TABLE IF NOT EXISTS — idempotent)
"""
import asyncpg
import json
import logging
import os
import pathlib

log = logging.getLogger(__name__)

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def _deployment_json_path() -> pathlib.Path:
    env_path = os.getenv("DEPLOYMENT_JSON")
    if env_path:
        return pathlib.Path(env_path)
    # fallback: look upward from this file to find /config/deployment.json
    candidates = [
        pathlib.Path("/config/deployment.json"),
        pathlib.Path(__file__).parents[3] / "docker" / "deployment.json",
        pathlib.Path(__file__).parents[2] / "deployment.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]  # will raise FileNotFoundError in caller


def load_deployment_json() -> dict:
    """Load global session bootstrap from deployment.json.

    Normalises key differences between old (pool_manager) and new
    (v4_pool_manager) formats. Returns a unified cfg dict.
    """
    path = _deployment_json_path()
    if not path.exists():
        raise FileNotFoundError(f"deployment.json not found at {path}")

    with open(path) as f:
        raw = json.load(f)

    # Normalise key aliases (old format → canonical names used by indexer)
    aliases = {
        "pool_manager": "v4_pool_manager",
        "position_manager": "v4_position_manager",
        "state_view": "v4_state_view",
        "quoter": "v4_quoter",
        "position_descriptor": "v4_position_descriptor",
    }
    cfg = dict(raw)
    for old, new in aliases.items():
        if old in cfg and new not in cfg:
            cfg[new] = cfg[old]

    # Required core fields — fail fast if missing
    required_core = {"rld_core", "v4_pool_manager", "v4_position_manager"}
    missing = required_core - cfg.keys()
    if missing:
        raise ValueError(f"deployment.json missing required keys: {missing}")

    # session_start_block: always driven by fork_block so indexer starts
    # at the chain fork point, not at block 0.
    cfg["session_start_block"] = cfg.get("fork_block") or cfg.get("session_start_block", 0)
    cfg.setdefault("deploy_block", cfg["session_start_block"])
    cfg.setdefault("deploy_timestamp", 0)
    cfg.setdefault("chain_id", 1)
    cfg.setdefault("v4_state_view", cfg.get("v4_state_view", ""))
    cfg.setdefault("v4_quoter", cfg.get("v4_quoter", ""))
    cfg.setdefault("v4_position_descriptor", cfg.get("v4_position_descriptor", ""))
    cfg.setdefault("universal_router", cfg.get("universal_router", ""))
    cfg.setdefault("permit2", cfg.get("permit2", ""))

    log.info(
        "Loaded deployment.json from %s: rld_core=%s v4_pool=%s fork_block=%d session_start=%d",
        path, cfg["rld_core"], cfg["v4_pool_manager"],
        cfg.get("fork_block", 0), cfg["session_start_block"]
    )
    return cfg


async def apply_schema(conn: asyncpg.Connection) -> None:
    """Run schema.sql — all CREATE TABLE IF NOT EXISTS, safe to re-run."""
    ddl = SCHEMA_PATH.read_text()
    await conn.execute(ddl)
    log.info("Schema applied from %s", SCHEMA_PATH)


async def reset(pool: asyncpg.Pool) -> None:
    """Truncate all indexed data and re-seed market — called by POST /admin/reset."""
    async with pool.acquire() as conn:
        await conn.execute("""
            TRUNCATE events, block_states, candles,
                     broker_operators, brokers, lp_positions, twamm_orders, liquidations,
                     indexer_state, markets
            CASCADE
        """)
    log.info("All indexed data truncated")
    await bootstrap_market(pool)


async def bootstrap(pool: asyncpg.Pool) -> dict:
    """
    Startup-only: apply schema.
    Does NOT seed markets or load deployment.json — that happens via
    POST /admin/reset from the deployer after contracts are deployed.
    """
    async with pool.acquire() as conn:
        await apply_schema(conn)
    log.info("Schema applied, waiting for deployer to call POST /admin/reset")
    return {}


async def bootstrap_market(pool: asyncpg.Pool) -> dict:
    """
    Seed the markets row from deployment.json.
    Called ONLY from reset() after deployer has written fresh config.
    """
    cfg = load_deployment_json()
    market_id = cfg.get("market_id", "")
    if not market_id:
        log.warning("No market_id in deployment.json — skipping market seed")
        return cfg

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO markets (
                market_id, deploy_block, deploy_timestamp,
                broker_factory, mock_oracle, twamm_hook, ghost_router, twap_engine, twap_engine_lens,
                wausdc, wausdc_symbol, wrlp, wrlp_symbol,
                pool_id, pool_fee, tick_spacing,
                swap_router, bond_factory, basis_trade_factory, broker_executor, v4_quoter, broker_router,
                v4_position_manager, v4_state_view, pool_manager,
                min_col_ratio, maintenance_margin, liq_close_factor,
                funding_period_sec, debt_cap, created_at
            ) VALUES (
                $1, $2, $3,
                $4, $5, $6, $7, $8, $9,
                $10, 'waUSDC', $11, 'wRLP',
                $12, 500, 5,
                $13, $14, $15, $16, $17, $18,
                $19, $20, $21,
                '1500000000000000000', '1250000000000000000', '500000000000000000',
                2592000, '1000000000000000000000000', NOW()
            )
            ON CONFLICT (market_id) DO UPDATE SET
                wausdc              = COALESCE(NULLIF(EXCLUDED.wausdc, ''),              markets.wausdc),
                wrlp                = COALESCE(NULLIF(EXCLUDED.wrlp, ''),                markets.wrlp),
                pool_id             = COALESCE(NULLIF(EXCLUDED.pool_id, ''),             markets.pool_id),
                broker_factory      = COALESCE(NULLIF(EXCLUDED.broker_factory, ''),      markets.broker_factory),
                mock_oracle         = COALESCE(NULLIF(EXCLUDED.mock_oracle, ''),         markets.mock_oracle),
                twamm_hook          = COALESCE(NULLIF(EXCLUDED.twamm_hook, ''),          markets.twamm_hook),
                ghost_router        = COALESCE(NULLIF(EXCLUDED.ghost_router, ''),        markets.ghost_router),
                twap_engine         = COALESCE(NULLIF(EXCLUDED.twap_engine, ''),         markets.twap_engine),
                twap_engine_lens    = COALESCE(NULLIF(EXCLUDED.twap_engine_lens, ''),    markets.twap_engine_lens),
                swap_router         = COALESCE(NULLIF(EXCLUDED.swap_router, ''),         markets.swap_router),
                bond_factory        = COALESCE(NULLIF(EXCLUDED.bond_factory, ''),        markets.bond_factory),
                basis_trade_factory = COALESCE(NULLIF(EXCLUDED.basis_trade_factory, ''), markets.basis_trade_factory),
                broker_executor     = COALESCE(NULLIF(EXCLUDED.broker_executor, ''),     markets.broker_executor),
                v4_quoter           = COALESCE(NULLIF(EXCLUDED.v4_quoter, ''),           markets.v4_quoter),
                broker_router       = COALESCE(NULLIF(EXCLUDED.broker_router, ''),       markets.broker_router),
                v4_position_manager = COALESCE(NULLIF(EXCLUDED.v4_position_manager, ''), markets.v4_position_manager),
                v4_state_view       = COALESCE(NULLIF(EXCLUDED.v4_state_view, ''),       markets.v4_state_view),
                pool_manager        = COALESCE(NULLIF(EXCLUDED.pool_manager, ''),        markets.pool_manager)
        """,
            market_id,
            cfg.get("deploy_block", 0),
            cfg.get("deploy_timestamp", 0),
            cfg.get("broker_factory", ""),
            cfg.get("mock_oracle", ""),
            cfg.get("twamm_hook", ""),
            cfg.get("ghost_router", ""),
            cfg.get("twap_engine", ""),
            cfg.get("twap_engine_lens", ""),
            cfg.get("wausdc", cfg.get("token0", "")),
            cfg.get("position_token", cfg.get("token1", "")),
            cfg.get("pool_id", ""),
            cfg.get("swap_router", ""),
            cfg.get("bond_factory", ""),
            cfg.get("basis_trade_factory", ""),
            cfg.get("broker_executor", ""),
            cfg.get("v4_quoter", ""),
            cfg.get("broker_router", ""),
            cfg.get("v4_position_manager", ""),
            cfg.get("v4_state_view", ""),
            cfg.get("pool_manager", cfg.get("v4_pool_manager", "")),
        )

        # Seed a baseline block_state index price for genesis-style deployments.
        # In the anvil-snapshot -> reth-genesis flow, oracle state is present at
        # block 0 but no RateUpdated event exists on-chain for indexer replay.
        oracle_index_price_wad = cfg.get("oracle_index_price_wad")
        seeded_index_price = None
        if oracle_index_price_wad not in (None, "", "0", 0):
            try:
                seeded_index_price = int(oracle_index_price_wad) / 1e18
            except (TypeError, ValueError):
                seeded_index_price = None

        if seeded_index_price is not None and seeded_index_price > 0:
            seed_block = int(cfg.get("deploy_block", 0) or 0)
            seed_ts = int(cfg.get("deploy_timestamp", 0) or 0)
            await conn.execute("""
                INSERT INTO block_states (
                    market_id, block_number, block_timestamp,
                    index_price, normalization_factor, total_debt
                )
                VALUES ($1, $2, $3, $4, 1.0, 0)
                ON CONFLICT (market_id, block_number) DO UPDATE SET
                    index_price = COALESCE(block_states.index_price, EXCLUDED.index_price),
                    normalization_factor = COALESCE(block_states.normalization_factor, EXCLUDED.normalization_factor),
                    total_debt = COALESCE(block_states.total_debt, EXCLUDED.total_debt)
            """, market_id, seed_block, seed_ts, seeded_index_price)
            log.info(
                "Seeded initial index_price=%.6f into block_states for market %s at block %d",
                seeded_index_price, market_id[:16], seed_block
            )

        # Also seed indexer_state so the polling loop knows where to start
        await conn.execute("""
            INSERT INTO indexer_state (market_id, last_indexed_block, total_events)
            VALUES ($1, $2, 0)
            ON CONFLICT (market_id) DO NOTHING
        """, market_id, cfg["session_start_block"])

    log.info("Seeded market %s with oracle=%s pool_id=%s",
             market_id[:16], cfg.get("mock_oracle", "")[:16], cfg.get("pool_id", "")[:16])
    return cfg

