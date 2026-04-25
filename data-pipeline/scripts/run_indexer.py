#!/usr/bin/env python3
"""
RLD Deterministic Worker Orchestrator

Initializes a single, isolated ProtocolCollector or ProtocolProcessor 
for a specific Source.

Poka-Yoke: The execution boundary is perfectly isolated.
"""

import sys
import os
import argparse
import asyncio
import logging
from typing import Callable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexer.config import apply_env_from_config, source_poll_interval

apply_env_from_config()

from indexer.collector import ProtocolCollector
from indexer.processor import ProtocolProcessor
from indexer.sources import FluidSource, ChainlinkSource, AaveV3Source, LidoRebaseSource, StaticPegsSource, SofrSource
from indexer.protocols import (
    AAVE_MARKET,
    FLUID_MARKET,
    CHAINLINK_PRICES,
    SOFR_RATES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rld-worker")
INDEXER_VERSION = os.getenv("INDEXER_VERSION", "dev")

SOURCE_MAP = {
    AAVE_MARKET: AaveV3Source,
    FLUID_MARKET: FluidSource,
    CHAINLINK_PRICES: ChainlinkSource,
    "LIDO_REBASE": LidoRebaseSource,
    "STATIC_PEGS": StaticPegsSource,
    SOFR_RATES: SofrSource,
}

def _build_cycle(source, role: str) -> Callable:
    if role == "collector":
        if getattr(source, "is_offchain", False):
            from indexer.offchain import OffchainCollector
            return OffchainCollector(source).run_collector_cycle
        return ProtocolCollector(source).run_collector_cycle
    if role == "processor":
        if not source.raw_table:
            log.warning(f"[{source.name}] Protocol has no raw_table. Processor role invalid.")
            sys.exit(1)
        return ProtocolProcessor(source).run_processor_cycle
    raise ValueError(f"Invalid role: {role}")


async def _run_cycle(loop_func: Callable) -> None:
    if asyncio.iscoroutinefunction(loop_func):
        await loop_func()
    else:
        await asyncio.to_thread(loop_func)


async def run_worker(source_cls, role: str, genesis_override: int = None, poll_interval: int = 60):
    source = source_cls()
    
    if genesis_override is not None:
        source.genesis_block = genesis_override

    log.info("═" * 60)
    log.info(f"  RLD ISOLATED WORKER")
    log.info(f"  Protocol: {source.name}")
    log.info(f"  Role:     {role.upper()}")
    log.info(f"  Genesis:  {source.genesis_block}")
    log.info(f"  Version:  {INDEXER_VERSION}")
    log.info("═" * 60)

    if role == "worker":
        collector_cycle = _build_cycle(source, "collector")
        processor_cycle = None if getattr(source, "is_offchain", False) else _build_cycle(source, "processor")
    else:
        loop_func = _build_cycle(source, role)

    while True:
        try:
            if role == "worker":
                await _run_cycle(collector_cycle)
                if processor_cycle is not None:
                    await _run_cycle(processor_cycle)
            else:
                await _run_cycle(loop_func)
        except Exception as e:
            log.error(f"[{source.name}-{role}] Fatal Loop Error: {e}", exc_info=True)
            
        await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RLD Isolated Pipeline Worker")
    parser.add_argument("--source", type=str, required=True, choices=list(SOURCE_MAP.keys()), help="Target Protocol")
    parser.add_argument("--role", type=str, required=True, choices=["collector", "processor", "worker"], help="Worker Role")
    parser.add_argument("--genesis-block", type=int, default=None, help="Override default genesis block for this source")
    parser.add_argument("--poll-interval", type=int, default=None, help="Seconds between cycles")
    
    args = parser.parse_args()

    # --- THE POKA-YOKE VERIFICATION ---
    # Asserting that the environment strictly obeys our deterministic constraints
    assert args.source in SOURCE_MAP, f"Invalid source: {args.source}"
    assert args.role in ["collector", "processor", "worker"], f"Invalid role: {args.role}"
    if args.genesis_block is not None:
        assert args.genesis_block >= 0, "Genesis block cannot be negative"
        
    source_class = SOURCE_MAP[args.source]
    if args.poll_interval is None:
        args.poll_interval = source_poll_interval(args.source, default=30)
    if args.role in {"processor", "worker"}:
        test_instance = source_class()
        if not getattr(test_instance, "is_offchain", False):
            assert test_instance.raw_table is not None, f"Cannot run processor for {args.source} since raw_table is None. This failure mode was caught organically."

    try:
        asyncio.run(run_worker(
            source_cls=source_class,
            role=args.role,
            genesis_override=args.genesis_block,
            poll_interval=args.poll_interval
        ))
    except KeyboardInterrupt:
        log.info("Shutting down cleanly.")
