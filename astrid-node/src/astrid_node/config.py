"""Astrid Node configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _env(name: str, default: str, legacy: str | None = None) -> str:
    if name in os.environ:
        return os.environ[name]
    if legacy and legacy in os.environ:
        return os.environ[legacy]
    return default


@dataclass(frozen=True)
class NodeConfig:
    clickhouse_host: str = _env("ASTRID_NODE_CLICKHOUSE_HOST", "127.0.0.1", "RLD_NODE_CLICKHOUSE_HOST")
    clickhouse_port: int = int(_env("ASTRID_NODE_CLICKHOUSE_PORT", "8124", "RLD_NODE_CLICKHOUSE_PORT"))
    clickhouse_user: str = _env("ASTRID_NODE_CLICKHOUSE_USER", "astrid", "RLD_NODE_CLICKHOUSE_USER")
    clickhouse_password: str = _env("ASTRID_NODE_CLICKHOUSE_PASSWORD", "astrid", "RLD_NODE_CLICKHOUSE_PASSWORD")
    nats_url: str = _env("ASTRID_NODE_NATS_URL", "nats://127.0.0.1:4223", "RLD_NODE_NATS_URL")
    manifest_url: str = _env("ASTRID_NODE_MANIFEST_URL", "", "RLD_NODE_MANIFEST_URL")
    cache_dir: Path = Path(_env("ASTRID_NODE_CACHE_DIR", ".astrid/cache", "RLD_NODE_CACHE_DIR"))


def load_config() -> NodeConfig:
    return NodeConfig()
