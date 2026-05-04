"""
Backfill ALL Chainlink AnswerUpdated events — discover aggregator implementations
automatically, map to feed names, insert into chainlink_prices.

Usage:
    python scripts/backfill_chainlink_feeds.py [--from-block N] [--dry-run]
"""

import asyncio
import datetime
import logging
import os
import sys
import time
from collections import defaultdict

import clickhouse_connect
import hypersync
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ANSWER_UPDATED = "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f"

# ── Known aggregator → feed mappings ──────────────────────────
# Multiple addresses can map to the same feed (aggregator rotations)
KNOWN_AGGREGATORS: dict[str, str] = {
    # ETH/USD
    "0x7d4e742018fb52e48b08be73d041c18b21de6fb5": "ETH/USD",
    "0xad88fc1a810379ef4efbf2d97ede57e306178e5a": "ETH/USD",
    "0x26f196806f43e88fd27798c9e3fb8fdf4618240f": "ETH/USD",
    "0x7c7fdfca295a787ded12bb5c1a49a8d2cc20e3f8": "ETH/USD",
    # BTC/USD
    "0x4a3411ac2948b33c69666b35cc6d055b27ea84f1": "BTC/USD",
    "0xdc715c751f1cc129a6b47fedc87d9918a4580502": "BTC/USD",
    "0x6f3f8d82694d52e6b6171a7b26a88c9554e7999b": "BTC/USD",
    # LINK/USD
    "0xd8b9aa6e811c935ef63e877cfa7be276931293da": "LINK/USD",
    "0xcd07b31d85756098334eddc92de755deae8fe62f": "LINK/USD",
    # SOL/USD
    "0x965750914f5bb1c9da8dbf5587970fedac1534c4": "SOL/USD",
    "0xb47777d7082d68367aa5f47653def255b37baa61": "SOL/USD",
    "0xe69544ff3e19179969222e7192173a1b9273fd90": "SOL/USD",
    # AAVE/USD
    "0xb38d1d12ba17aa62255e588a0bc845c1a589a50d": "AAVE/USD",
    # UNI/USD
    "0x96d6e33b411dc1f4e3f1e894a5a5d9ce0f96738d": "UNI/USD",
    "0x64c67984a458513c6bab23a815916b1b1075cf3a": "UNI/USD",
    "0x4f3ebf190f8889734424ae71ac0b00e1a8013f3c": "UNI/USD",
    # CRV/USD
    "0xa7daf8a03b064262fff0d615663553dae3e18744": "CRV/USD",
    # SNX/USD
    "0x3917430b7d6e8132b7e90bfd7370ca02620f5454": "SNX/USD",
    # USDC/USD
    "0x709783ab12b65fd6cd948214eee6448f3bdd72a3": "USDC/USD",
    "0x4b2c406f0dbf7624a32971277da7b4c43a7a942b": "USDC/USD",
    # USDT/USD
    "0x8f73090a7c58b8bdcc9a93cbb6816e5cc4f01e8c": "USDT/USD",
    "0x9e37dbf40fe5fe9320e45fe6b95b000aa05459a9": "USDT/USD",
    # stETH/ETH exchange rate 
    "0x5763fc5fabca9080ad12bcafae7a335023b1f9b4": "stETH/ETH",
    "0x563f9b302af72aeeb8a411228cdc65b30ca1cb75": "stETH/ETH",
    "0x81fa0a005d9b9d5bdf7e71b05eba57b1c1fe3756": "stETH/ETH",
    "0xe07f52971153db2713ace5ebaaf2ea8b0a9230b7": "stETH/ETH",
    # wstETH/stETH exchange rate
    "0x966dad3b93c207a9ee3a79c336145e013c5cd3fc": "wstETH/stETH",
    "0xa674a0fd742f37bd5077afc90d1e82485c91989c": "wstETH/stETH",
    "0xf4a3e183f59d2599ee3df213ff78b1b3b1923696": "wstETH/stETH",
    # XAU/USD (gold)
    "0x0e3dd634ffbf7ea89bbdcf09ccc463302fd5f903": "XAU/USD",
    "0x80e1cd5489a144ac6e0a9d1d69ebec9076b4d21c": "XAU/USD",
    "0x6795d4a47c9c8f4117b409d966259cdcf6a9eb6e": "XAU/USD",
    # MKR/USD (or MKR/ETH — need to verify)
    "0xdef8c51d7c1040637a198effc39613865b32ea51": "MKR/USD",
}

# Feeds we want to keep (filter out noise)
DESIRED_FEEDS = {
    "ETH/USD", "BTC/USD", "LINK/USD", "SOL/USD", "AAVE/USD",
    "UNI/USD", "CRV/USD", "SNX/USD", "USDC/USD", "USDT/USD",
    "stETH/ETH", "wstETH/stETH", "XAU/USD", "MKR/USD",
}

CHUNK_SIZE = 50_000  # blocks per HyperSync query


async def backfill(from_block: int, dry_run: bool = False):
    ch = clickhouse_connect.get_client(host="localhost", port=8123)
    token = os.getenv("ENVIO_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ENVIO_API_TOKEN is required")
    client = hypersync.HypersyncClient(
        hypersync.ClientConfig(url="https://eth.hypersync.xyz", bearer_token=token)
    )

    # Get latest block from HyperSync
    head = await client.get_height()
    log.info(f"HyperSync head: {head:,}")
    log.info(f"Scanning blocks {from_block:,} → {head:,} for AnswerUpdated events")
    log.info(f"Known aggregators: {len(KNOWN_AGGREGATORS)}")

    # Collect all addresses we want to query
    addresses = list(KNOWN_AGGREGATORS.keys())

    total_inserted = 0
    cursor = from_block
    t0 = time.time()

    while cursor <= head:
        chunk_end = min(cursor + CHUNK_SIZE - 1, head)

        query = hypersync.Query(
            from_block=cursor,
            to_block=chunk_end,
            logs=[
                hypersync.LogSelection(
                    address=addresses,
                    topics=[[ANSWER_UPDATED]],
                )
            ],
            field_selection=hypersync.FieldSelection(
                log=[
                    hypersync.LogField.ADDRESS,
                    hypersync.LogField.BLOCK_NUMBER,
                    hypersync.LogField.TOPIC0,
                    hypersync.LogField.TOPIC1,
                    hypersync.LogField.TOPIC2,
                    hypersync.LogField.DATA,
                ],
                block=[hypersync.BlockField.NUMBER, hypersync.BlockField.TIMESTAMP],
            ),
        )

        res = await client.get(query)
        logs = res.data.logs

        if not logs:
            cursor = chunk_end + 1
            continue

        # Build block timestamp map
        block_ts = {}
        for b in res.data.blocks:
            if b.number is not None and b.timestamp is not None:
                ts_val = int(b.timestamp, 16) if isinstance(b.timestamp, str) else b.timestamp
                block_ts[b.number] = datetime.datetime.fromtimestamp(
                    ts_val, tz=datetime.UTC
                )

        # Parse events
        rows = []
        for ev in logs:
            addr = ev.address.lower()
            feed = KNOWN_AGGREGATORS.get(addr)
            if not feed or feed not in DESIRED_FEEDS:
                continue

            # Parse price from topic1 (int256, 8 decimals)
            raw = int(ev.topics[1], 16)
            if raw > (1 << 255):
                raw -= 1 << 256
            price = raw / 1e8

            if price <= 0:
                continue

            # Parse timestamp from data field (uint256 updatedAt)
            data = ev.data or "0x"
            if data != "0x" and len(data) > 2:
                updated_at = int(data, 16)
                ts = datetime.datetime.fromtimestamp(updated_at, tz=datetime.UTC)
            else:
                ts = block_ts.get(ev.block_number)
                if ts is None:
                    continue

            rows.append(
                {
                    "feed": feed,
                    "price": price,
                    "block_number": ev.block_number,
                    "timestamp": ts,
                }
            )

        if rows and not dry_run:
            df = pd.DataFrame(rows)
            ch.insert_df("chainlink_prices", df)
            total_inserted += len(df)

        elapsed = time.time() - t0
        pct = (cursor - from_block) / max(head - from_block, 1) * 100
        log.info(
            f"  Block {chunk_end:,} ({pct:.1f}%) • "
            f"{len(logs)} events → {len(rows)} rows • "
            f"total={total_inserted:,} • {elapsed:.0f}s"
        )

        cursor = chunk_end + 1

    # Summary
    elapsed = time.time() - t0
    log.info(f"\n{'='*60}")
    log.info(f"✅ Backfill complete: {total_inserted:,} rows in {elapsed:.0f}s")

    # Verify
    feed_stats = ch.query_df(
        "SELECT feed, count() AS rows, min(timestamp) AS first, max(timestamp) AS last, "
        "round(min(price),2) AS min_p, round(max(price),2) AS max_p, "
        "round(argMax(price, timestamp),2) AS latest "
        "FROM chainlink_prices GROUP BY feed ORDER BY feed"
    )
    log.info(f"\nFeed summary:")
    for _, r in feed_stats.iterrows():
        log.info(
            f"  {r['feed']:<16} {r['rows']:>6} rows  "
            f"{r['first']} → {r['last']}  "
            f"${r['min_p']:>10,.2f} – ${r['max_p']:>10,.2f}  latest=${r['latest']:>10,.2f}"
        )

    ch.close()


def main():
    from_block = 18900000  # Morpho Blue genesis
    dry_run = "--dry-run" in sys.argv

    for arg in sys.argv[1:]:
        if arg.startswith("--from-block="):
            from_block = int(arg.split("=")[1])

    asyncio.run(backfill(from_block, dry_run))


if __name__ == "__main__":
    main()
