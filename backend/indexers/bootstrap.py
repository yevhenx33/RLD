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

    # session_start_block defaults to 0 if not present (indexer will catch up)
    cfg.setdefault("session_start_block", 0)
    cfg.setdefault("chain_id", 1)
    cfg.setdefault("v4_state_view", cfg.get("v4_state_view", ""))
    cfg.setdefault("v4_quoter", cfg.get("v4_quoter", ""))
    cfg.setdefault("v4_position_descriptor", cfg.get("v4_position_descriptor", ""))
    cfg.setdefault("universal_router", cfg.get("universal_router", ""))
    cfg.setdefault("permit2", cfg.get("permit2", ""))

    log.info(
        "Loaded deployment.json from %s: rld_core=%s v4_pool=%s session_start=%d",
        path, cfg["rld_core"], cfg["v4_pool_manager"], cfg["session_start_block"]
    )
    return cfg


async def apply_schema(conn: asyncpg.Connection) -> None:
    """Run schema.sql — all CREATE TABLE IF NOT EXISTS, safe to re-run."""
    ddl = SCHEMA_PATH.read_text()
    await conn.execute(ddl)
    log.info("Schema applied from %s", SCHEMA_PATH)


async def bootstrap(pool: asyncpg.Pool) -> dict:
    """
    1. Load deployment.json (tolerant of old/new formats)
    2. Apply schema (idempotent)
    3. Return normalised global config dict for the indexer loop
    """
    cfg = load_deployment_json()
    async with pool.acquire() as conn:
        await apply_schema(conn)
    return cfg
