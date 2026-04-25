"""Config loader for the ClickHouse analytics indexer."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATHS = (
    Path(os.getenv("RLD_INDEXER_CONFIG", "")) if os.getenv("RLD_INDEXER_CONFIG") else None,
    Path("/app/config.toml"),
    Path(__file__).resolve().parents[1] / "config.toml",
)


def load_config(path: str | None = None) -> dict[str, Any]:
    candidates = [Path(path)] if path else [p for p in DEFAULT_CONFIG_PATHS if p is not None]
    for candidate in candidates:
        if candidate.exists():
            with candidate.open("rb") as fh:
                return tomllib.load(fh)
    return {}


def _setdefault(name: str, value: Any) -> None:
    if value is None or name in os.environ:
        return
    if isinstance(value, bool):
        os.environ[name] = "true" if value else "false"
    elif isinstance(value, (list, tuple)):
        os.environ[name] = ",".join(str(item) for item in value)
    else:
        os.environ[name] = str(value)


def apply_env_from_config(path: str | None = None) -> dict[str, Any]:
    cfg = load_config(path)
    api = cfg.get("api", {})
    ch = cfg.get("clickhouse", {})
    roles = ch.get("roles", {})

    _setdefault("API_PORT", api.get("port"))
    _setdefault("ENVIO_CORS_ORIGINS", api.get("cors_origins"))
    _setdefault("INDEXER_READY_PROTOCOLS", api.get("ready_protocols"))
    _setdefault("INDEXER_MAX_READY_LAG_BLOCKS", api.get("max_ready_lag_blocks"))
    _setdefault("INDEXER_MAX_READY_SOFR_BUSINESS_DAYS", api.get("max_ready_sofr_business_days"))

    _setdefault("CLICKHOUSE_HOST", ch.get("host"))
    _setdefault("CLICKHOUSE_PORT", ch.get("port"))
    _setdefault("CLICKHOUSE_USER", ch.get("user"))
    password_env = ch.get("password_env")
    if password_env and password_env in os.environ:
        _setdefault("CLICKHOUSE_PASSWORD", os.environ[password_env])
    _setdefault("CLICKHOUSE_ASYNC_INSERT", ch.get("async_insert"))
    _setdefault("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", ch.get("wait_for_async_insert"))
    _setdefault("CLICKHOUSE_INSERT_BATCH_SIZE", ch.get("insert_batch_size"))
    _setdefault("CLICKHOUSE_MANAGE_USERS", ch.get("manage_users"))

    _setdefault("CLICKHOUSE_API_USER", roles.get("api_user"))
    api_password_env = roles.get("api_password_env")
    if api_password_env and api_password_env in os.environ:
        _setdefault("CLICKHOUSE_API_PASSWORD", os.environ[api_password_env])
    _setdefault("CLICKHOUSE_WORKER_USER", roles.get("worker_user"))
    worker_password_env = roles.get("worker_password_env")
    if worker_password_env and worker_password_env in os.environ:
        _setdefault("CLICKHOUSE_WORKER_PASSWORD", os.environ[worker_password_env])
    _setdefault("CLICKHOUSE_MIGRATION_USER", roles.get("migration_user"))
    migration_password_env = roles.get("migration_password_env")
    if migration_password_env and migration_password_env in os.environ:
        _setdefault("CLICKHOUSE_MIGRATION_PASSWORD", os.environ[migration_password_env])

    return cfg


def source_poll_interval(source: str, default: int = 30, path: str | None = None) -> int:
    cfg = load_config(path)
    source_cfg = (cfg.get("sources", {}) or {}).get(source, {})
    return int(source_cfg.get("poll_interval_sec", default))
