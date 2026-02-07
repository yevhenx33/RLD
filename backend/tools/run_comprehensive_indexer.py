#!/usr/bin/env python3
"""
Run Comprehensive Block-Level Indexer.

Usage:
    # Take a snapshot of the current block
    python scripts/run_comprehensive_indexer.py --snapshot
    
    # Index continuously from current block
    python scripts/run_comprehensive_indexer.py --run
    
    # Index from a specific block
    python scripts/run_comprehensive_indexer.py --run --from-block 21698573
    
    # Index a range of blocks
    python scripts/run_comprehensive_indexer.py --range 21698573 21698600
"""
import argparse
import asyncio
import json
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "../../contracts/.env"))

from indexers.comprehensive import ComprehensiveIndexer, create_indexer_from_env
from db.comprehensive import get_latest_summary, get_block_summary


def format_snapshot(snapshot: dict) -> str:
    """Format snapshot for display."""
    lines = []
    lines.append("")
    lines.append("═" * 70)
    lines.append(f"📸 BLOCK {snapshot.get('block_number', 'N/A')} SNAPSHOT")
    lines.append("═" * 70)
    
    # Market State
    ms = snapshot.get('market_state', {})
    lines.append("")
    lines.append("📊 MARKET STATE")
    lines.append("─" * 40)
    
    nf = ms.get('normalization_factor', 0)
    nf_display = nf / 1e18 if nf else 0
    lines.append(f"  Normalization Factor: {nf_display:.10f}")
    
    total_debt = ms.get('total_debt', 0)
    debt_display = total_debt / 1e6 if total_debt else 0  # Assuming 6 decimals
    lines.append(f"  Total Debt:           {debt_display:,.2f} wRLP")
    
    index_price = ms.get('index_price', 0)
    if index_price:
        # Index price is in WAD (1e18)
        index_display = index_price / 1e18
        lines.append(f"  Index Price:          ${index_display:.4f}")
    
    last_update = ms.get('last_update_timestamp', 0)
    lines.append(f"  Last Update:          {last_update}")
    
    # Pool State
    ps = snapshot.get('pool_state', {})
    if ps:
        lines.append("")
        lines.append("💧 V4 POOL STATE")
        lines.append("─" * 40)
        lines.append(f"  Token0:     {ps.get('token0', 'N/A')}")
        lines.append(f"  Token1:     {ps.get('token1', 'N/A')}")
        lines.append(f"  Mark Price: ${ps.get('mark_price', 0):.4f}")
        lines.append(f"  Tick:       {ps.get('tick', 0)}")
        lines.append(f"  Liquidity:  {ps.get('liquidity', 0):,}")
        lines.append(f"  SqrtPriceX96: {ps.get('sqrt_price_x96', 0)}")
    
    # Events
    events = snapshot.get('events', [])
    if events:
        lines.append("")
        lines.append("📝 EVENTS")
        lines.append("─" * 40)
        for e in events:
            lines.append(f"  {e['event_name']}:")
            lines.append(f"    TX: {e['tx_hash'][:20]}...")
            data = e.get('data', {})
            for k, v in data.items():
                lines.append(f"    {k}: {v}")
    
    # Broker Positions
    positions = snapshot.get('broker_positions', [])
    if positions:
        lines.append("")
        lines.append("👤 BROKER POSITIONS")
        lines.append("─" * 40)
        for p in positions:
            broker = p.get('broker', 'Unknown')
            lines.append(f"  Broker: {broker[:10]}...{broker[-6:]}")
            col = p.get('collateral', 0)
            debt = p.get('debt', 0)
            lines.append(f"    Collateral: {col / 1e6 if col else 0:,.2f}")
            lines.append(f"    Debt:       {debt / 1e18 if debt else 0:,.2f}")
            lines.append(f"    Health:     {p.get('health_factor', 0):.4f}")
    
    lines.append("")
    lines.append("═" * 70)
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Comprehensive Block-Level Indexer")
    parser.add_argument("--snapshot", action="store_true", 
                       help="Take a snapshot of the current block")
    parser.add_argument("--run", action="store_true",
                       help="Run continuous indexing")
    parser.add_argument("--from-block", type=int,
                       help="Starting block for continuous indexing")
    parser.add_argument("--range", nargs=2, type=int, metavar=("FROM", "TO"),
                       help="Index a range of blocks")
    parser.add_argument("--latest", action="store_true",
                       help="Show the latest indexed snapshot from DB")
    parser.add_argument("--block", type=int,
                       help="Show snapshot for a specific block from DB")
    parser.add_argument("--json", action="store_true",
                       help="Output as JSON instead of formatted text")
    parser.add_argument("--rpc-url", type=str,
                       help="Override RPC URL")
    parser.add_argument("--market-id", type=str,
                       help="Override market ID")
    parser.add_argument("--brokers", type=str, nargs="*",
                       help="Broker addresses to track")
    
    args = parser.parse_args()
    
    # Create indexer
    try:
        indexer = create_indexer_from_env()
        
        # Add any additional brokers from args
        if args.brokers:
            indexer.tracked_brokers.extend(args.brokers)
            
    except Exception as e:
        print(f"❌ Failed to create indexer: {e}")
        print("Make sure deployments.json exists and RPC is available")
        sys.exit(1)
    
    if args.snapshot:
        # Take a snapshot of current block
        snapshot = indexer.snapshot_block()
        if args.json:
            print(json.dumps(snapshot, indent=2, default=str))
        else:
            print(format_snapshot(snapshot))
    
    elif args.latest:
        # Show latest from DB
        summary = get_latest_summary()
        if args.json:
            print(json.dumps(summary, indent=2, default=str))
        else:
            if 'error' in summary:
                print(f"❌ {summary['error']}")
            else:
                print(format_snapshot(summary))
    
    elif args.block:
        # Show specific block from DB
        summary = get_block_summary(args.block)
        if args.json:
            print(json.dumps(summary, indent=2, default=str))
        else:
            print(format_snapshot(summary))
    
    elif args.range:
        # Index a range of blocks
        from_block, to_block = args.range
        asyncio.run(indexer.index_range(from_block, to_block))
        print(f"\n✅ Indexed blocks {from_block} to {to_block}")
    
    elif args.run:
        # Run continuous indexing
        from_block = args.from_block
        try:
            asyncio.run(indexer.run(from_block=from_block))
        except KeyboardInterrupt:
            indexer.stop()
            print("\n🛑 Indexer stopped")
    
    else:
        # Default: take a snapshot
        snapshot = indexer.snapshot_block()
        if args.json:
            print(json.dumps(snapshot, indent=2, default=str))
        else:
            print(format_snapshot(snapshot))


if __name__ == "__main__":
    main()
