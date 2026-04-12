#!/usr/bin/env python3
"""
RLD Indexer — Entry point.

Registers protocol sources and starts the HyperSync polling engine.
To add a new protocol, import its source and add to the sources list.
"""

import os
import sys
import logging

# Ensure the data-pipeline package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from indexer.core import IndexerEngine
from indexer.sources import FluidSource, ChainlinkSource, AaveV3Source, MorphoSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Register all sources ─────────────────────────────────────
sources = [
    FluidSource(),
    ChainlinkSource(),
    AaveV3Source(),
    MorphoSource(),
]

# ── Start engine ─────────────────────────────────────────────
engine = IndexerEngine(
    sources=sources,
    poll_interval=int(os.getenv("POLL_INTERVAL", "300")),
    clickhouse_host=os.getenv("CLICKHOUSE_HOST", "localhost"),
    clickhouse_port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
)

if __name__ == "__main__":
    engine.run()
