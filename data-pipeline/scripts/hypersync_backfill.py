#!/usr/bin/env python3
"""
Envio HyperSync Historical Backfill — Aave V3 + Morpho Blue + Fluid.

Downloads all lending protocol event logs from Ethereum mainnet
into Parquet files, then ingests into ClickHouse for the RLD
Rates Explorer.

Architecture:
  HyperSync API → Parquet (staging) → ClickHouse (permanent)

Usage:
    cd /home/ubuntu/RLD/data-pipeline
    pip install -r requirements-hypersync.txt
    export ENVIO_API_TOKEN="your-token"
    python scripts/hypersync_backfill.py

Environment:
    ENVIO_API_TOKEN     - Required. Get from https://envio.dev/app/api-tokens
    CLICKHOUSE_HOST     - Default: localhost
    CLICKHOUSE_PORT     - Default: 8123
    STAGING_DIR         - Default: /mnt/data/hypersync_staging
"""

import asyncio
import os
import sys
import time
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indexer.aave_constants import (
    AAVE_V3_POOL,
    AAVE_V3_DEPLOY_BLOCK,
    AAVE_TOPIC_RESERVE_DATA_UPDATED,
    AAVE_TOPIC_SUPPLY,
    AAVE_TOPIC_BORROW,
    AAVE_TOPIC_REPAY,
    AAVE_TOPIC_LIQUIDATION_CALL,
    AAVE_TOPIC_FLASH_LOAN,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("hypersync_backfill")

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────

HYPERSYNC_URL = "https://eth.hypersync.xyz"
STAGING_DIR = Path(os.getenv("STAGING_DIR", "/mnt/data/hypersync_staging"))
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))


@dataclass
class ProtocolConfig:
    """Configuration for a single protocol's event backfill."""
    name: str
    contract: str
    start_block: int
    events: dict[str, str]  # event_name → topic0


# ──────────────────────────────────────────────────────────────
# PROTOCOL DEFINITIONS
# ──────────────────────────────────────────────────────────────

# Aave V3 Pool — Ethereum Mainnet
# Deployed block 16,291,127 (Dec 2022)
AAVE_V3 = ProtocolConfig(
    name="aave_v3",
    contract=AAVE_V3_POOL,
    start_block=AAVE_V3_DEPLOY_BLOCK,
    events={
        "ReserveDataUpdated": AAVE_TOPIC_RESERVE_DATA_UPDATED,
        "Supply": AAVE_TOPIC_SUPPLY,
        "Borrow": AAVE_TOPIC_BORROW,
        "Repay": AAVE_TOPIC_REPAY,
        "LiquidationCall": AAVE_TOPIC_LIQUIDATION_CALL,
        "FlashLoan": AAVE_TOPIC_FLASH_LOAN,
    },
)

# Morpho Blue — Ethereum Mainnet
# Deployed block 18,883,124 (Jan 2024)
MORPHO_BLUE = ProtocolConfig(
    name="morpho_blue",
    contract="0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",
    start_block=18_883_124,
    events={
        "CreateMarket":         "0xac4b2400f169220b0c0afdde7a0b32e775ba727ea1cb30b35f935cdaab8683ac",
        "Supply":               "0xedf8870433c83823eb071d3df1caa8d008f12f6440918c20d75a3602cda30fe0",
        "Borrow":               "0x570954540bed6b1304a87dfe815a5eda4a648f7097a16240dcd85c9b5fd42a43",
        "Repay":                "0x52acb05cebbd3cd39715469f22afbf5a17496295ef3bc9bb5944056c63ccaa09",
        "Liquidate":            "0xa4946ede45d0c6f06a0f5ce92c9ad3b4751e2b26571a85b4b10571077571493f",
        "SupplyCollateral":     "0xa3b9472a1399e17e123f3c2e6586c23e36dadeb77fcfbeaaa7c91af3543f15d3",
        "WithdrawCollateral":   "0xe80ebd7cc9223d7382aab2e0d1d6155c65651f83d53c8b9b06571e7480a87c62",
    },
)

# Fluid (Instadapp) Liquidity Layer — Ethereum Mainnet
# First LogOperate event at block 19,258,464 (March 2024)
# Uses a single unified event for all operations (supply/borrow/withdraw/repay)
# supplyAmount > 0 = deposit, < 0 = withdraw
# borrowAmount > 0 = borrow, < 0 = repay
FLUID = ProtocolConfig(
    name="fluid",
    contract="0x52Aa899454998Be5b000Ad077a46Bbe360F4e497",
    start_block=19_258_464,
    events={
        "LogOperate": "0x4d93b232a24e82b284ced7461bf4deacffe66759d5c24513e6f29e571ad78d15",
        "LogUpdateExchangePrices": "0x96c40bed7fc8d0ac41633a3bd47f254f0b0076e5df70975c51d23514bc49d3b8",
    },
)

PROTOCOLS = [FLUID]


# ──────────────────────────────────────────────────────────────
# HYPERSYNC DOWNLOAD
# ──────────────────────────────────────────────────────────────

async def download_protocol_events(protocol: ProtocolConfig) -> Path:
    """Download all events for a protocol via HyperSync → Parquet."""
    try:
        import hypersync
    except ImportError:
        log.error("hypersync not installed. Run: pip install hypersync")
        sys.exit(1)

    token = os.getenv("ENVIO_API_TOKEN")
    if not token:
        log.error("ENVIO_API_TOKEN not set. Get one at https://envio.dev/app/api-tokens")
        sys.exit(1)

    client = hypersync.HypersyncClient(
        hypersync.ClientConfig(
            url=HYPERSYNC_URL,
            bearer_token=token,
        )
    )

    # Collect all topic0 hashes for this protocol
    topic0_list = list(protocol.events.values())

    output_dir = STAGING_DIR / protocol.name
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(
        f"[{protocol.name}] Downloading events from block {protocol.start_block:,} "
        f"| {len(topic0_list)} event types | contract {protocol.contract[:10]}..."
    )

    query = hypersync.Query(
        from_block=protocol.start_block,
        logs=[
            hypersync.LogSelection(
                address=[protocol.contract],
                topics=[[t] for t in topic0_list] if len(topic0_list) == 1
                else [topic0_list],  # topic0 OR filter
            )
        ],
        field_selection=hypersync.FieldSelection(
            log=[
                hypersync.LogField.BLOCK_NUMBER,
                hypersync.LogField.TRANSACTION_HASH,
                hypersync.LogField.LOG_INDEX,
                hypersync.LogField.ADDRESS,
                hypersync.LogField.TOPIC0,
                hypersync.LogField.TOPIC1,
                hypersync.LogField.TOPIC2,
                hypersync.LogField.TOPIC3,
                hypersync.LogField.DATA,
            ],
            block=[
                hypersync.BlockField.NUMBER,
                hypersync.BlockField.TIMESTAMP,
            ],
        ),
    )

    t0 = time.time()
    await client.collect_parquet(
        path=str(output_dir),
        query=query,
    )
    elapsed = time.time() - t0

    # Count output files
    parquet_files = list(output_dir.glob("*.parquet"))
    total_size_mb = sum(f.stat().st_size for f in parquet_files) / (1024 * 1024)

    log.info(
        f"[{protocol.name}] Download complete in {elapsed:.1f}s "
        f"| {len(parquet_files)} files | {total_size_mb:.1f} MB"
    )

    return output_dir


# ──────────────────────────────────────────────────────────────
# CLICKHOUSE INGESTION
# ──────────────────────────────────────────────────────────────

def create_clickhouse_tables():
    """Create ClickHouse tables for protocol events if they don't exist."""
    try:
        import clickhouse_connect
    except ImportError:
        log.error("clickhouse-connect not installed. Run: pip install clickhouse-connect")
        sys.exit(1)

    client = clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT
    )

    # Generic event log table per protocol
    for protocol in PROTOCOLS:
        table_name = f"{protocol.name}_events"
        ddl = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            block_number    UInt64,
            block_timestamp DateTime,
            tx_hash         String,
            log_index       UInt32,
            contract        String,
            event_name      String,
            topic0          String,
            topic1          Nullable(String),
            topic2          Nullable(String),
            topic3          Nullable(String),
            data            String
        )
        ENGINE = MergeTree()
        ORDER BY (block_number, log_index)
        """
        client.command(ddl)
        log.info(f"[ClickHouse] Table '{table_name}' ready")

    client.close()


def ingest_parquet_to_clickhouse(protocol: ProtocolConfig, parquet_dir: Path):
    """Ingest downloaded Parquet files into ClickHouse."""
    try:
        import clickhouse_connect
        import pyarrow.parquet as pq
    except ImportError as e:
        log.error(f"Missing dependency: {e}")
        sys.exit(1)

    client = clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT
    )

    table_name = f"{protocol.name}_events"

    # Build reverse lookup: topic0 → event_name
    topic0_to_name = {v.lower(): k for k, v in protocol.events.items()}

    # Find log parquet files (HyperSync outputs logs.parquet, blocks.parquet, etc.)
    log_files = sorted(parquet_dir.glob("**/logs*.parquet"))
    block_files = sorted(parquet_dir.glob("**/blocks*.parquet"))

    if not log_files:
        log.warning(f"[{protocol.name}] No log parquet files found in {parquet_dir}")
        return

    log.info(f"[{protocol.name}] Ingesting {len(log_files)} log files into {table_name}")

    # Load block timestamps for joining
    block_timestamps = {}
    for bf in block_files:
        bt = pq.read_table(bf)
        for i in range(len(bt)):
            block_num = bt.column("number")[i].as_py()
            ts = bt.column("timestamp")[i].as_py()
            if block_num is not None and ts is not None:
                block_timestamps[block_num] = ts

    total_rows = 0
    for lf in log_files:
        table = pq.read_table(lf)
        rows = []
        for i in range(len(table)):
            block_num = table.column("block_number")[i].as_py()
            topic0_raw = table.column("topic0")[i].as_py()
            topic0_val = topic0_raw.lower() if topic0_raw else ""
            event_name = topic0_to_name.get(topic0_val, "Unknown")

            # Get block timestamp (default to 0 if not found)
            ts = block_timestamps.get(block_num, 0)

            rows.append([
                block_num,
                ts,
                table.column("transaction_hash")[i].as_py() or "",
                table.column("log_index")[i].as_py() or 0,
                table.column("address")[i].as_py() or "",
                event_name,
                topic0_val,
                table.column("topic1")[i].as_py(),
                table.column("topic2")[i].as_py(),
                table.column("topic3")[i].as_py(),
                table.column("data")[i].as_py() or "",
            ])

        if rows:
            client.insert(
                table_name,
                rows,
                column_names=[
                    "block_number", "block_timestamp", "tx_hash", "log_index",
                    "contract", "event_name", "topic0", "topic1", "topic2",
                    "topic3", "data",
                ],
            )
            total_rows += len(rows)

    log.info(f"[{protocol.name}] Ingested {total_rows:,} events into {table_name}")

    # Verify
    count = client.command(f"SELECT count() FROM {table_name}")
    log.info(f"[{protocol.name}] Verification: {count:,} total rows in {table_name}")

    client.close()


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

async def main():
    log.info("=" * 60)
    log.info("RLD HyperSync Historical Backfill")
    log.info("=" * 60)

    # Step 1: Create ClickHouse tables
    log.info("Step 1: Creating ClickHouse tables...")
    create_clickhouse_tables()

    # Step 2: Download events for each protocol
    log.info("Step 2: Downloading events via HyperSync...")
    for protocol in PROTOCOLS:
        parquet_dir = await download_protocol_events(protocol)

        # Step 3: Ingest into ClickHouse
        log.info(f"Step 3: Ingesting {protocol.name} into ClickHouse...")
        ingest_parquet_to_clickhouse(protocol, parquet_dir)

    log.info("=" * 60)
    log.info("Backfill complete!")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
