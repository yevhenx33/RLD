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
import clickhouse_connect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexer.collector import ProtocolCollector
from indexer.processor import ProtocolProcessor
from indexer.sources import FluidSource, ChainlinkSource, AaveV3Source, MorphoSource, LidoRebaseSource, StaticPegsSource, PendleSwapSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rld-worker")

SOURCE_MAP = {
    "AAVE_MARKET": AaveV3Source,
    "MORPHO_MARKET": MorphoSource,
    "PENDLE_MARKET": PendleSwapSource,
    "FLUID_MARKET": FluidSource,
    "CHAINLINK_PRICES": ChainlinkSource,
    "LIDO_REBASE": LidoRebaseSource,
    "STATIC_PEGS": StaticPegsSource,
}

async def run_worker(source_cls, role: str, genesis_override: int = None, poll_interval: int = 60):
    source = source_cls()
    
    if genesis_override is not None:
        source.genesis_block = genesis_override

    # Special state hydration for Morpho
    if source.name == "MORPHO_MARKET":
        ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
        ch_port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
        ch = clickhouse_connect.get_client(host=ch_host, port=ch_port)
        log.info(f"[{source.name}] Hydrating state cache...")
        source.load_state_from_ch(ch)
        ch.close()

    log.info("═" * 60)
    log.info(f"  RLD ISOLATED WORKER")
    log.info(f"  Protocol: {source.name}")
    log.info(f"  Role:     {role.upper()}")
    log.info(f"  Genesis:  {source.genesis_block}")
    log.info("═" * 60)

    if role == "collector":
        worker = ProtocolCollector(source)
        loop_func = worker.run_collector_cycle
    elif role == "processor":
        if not source.raw_table:
            log.warning(f"[{source.name}] Protocol has no raw_table. Processor role invalid.")
            sys.exit(1)
        worker = ProtocolProcessor(source)
        # Processor cycle is synchronous but we can run it in a thread-pool using asyncio.to_thread if we wanted, 
        # but since this worker ONLY does processing, we can simply run it directly.
        loop_func = worker.run_processor_cycle
    else:
        raise ValueError(f"Invalid role: {role}")

    while True:
        try:
            if asyncio.iscoroutinefunction(loop_func):
                await loop_func()
            else:
                # Still running the blocking sync function in a thread to keep the event loop ticking if needed,
                # though since there's no other task, direct execution is also fine.
                await asyncio.to_thread(loop_func)
        except Exception as e:
            log.error(f"[{source.name}-{role}] Fatal Loop Error: {e}", exc_info=True)
            
        await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RLD Isolated Pipeline Worker")
    parser.add_argument("--source", type=str, required=True, choices=list(SOURCE_MAP.keys()), help="Target Protocol")
    parser.add_argument("--role", type=str, required=True, choices=["collector", "processor"], help="Worker Role")
    parser.add_argument("--genesis-block", type=int, default=None, help="Override default genesis block for this source")
    parser.add_argument("--poll-interval", type=int, default=30, help="Seconds between cycles (default 30)")
    
    args = parser.parse_args()

    # --- THE POKA-YOKE VERIFICATION ---
    # Asserting that the environment strictly obeys our deterministic constraints
    assert args.source in SOURCE_MAP, f"Invalid source: {args.source}"
    assert args.role in ["collector", "processor"], f"Invalid role: {args.role}"
    if args.genesis_block is not None:
        assert args.genesis_block >= 0, "Genesis block cannot be negative"
        
    source_class = SOURCE_MAP[args.source]
    if args.role == "processor":
        test_instance = source_class()
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
