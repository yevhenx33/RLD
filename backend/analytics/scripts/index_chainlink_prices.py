#!/usr/bin/env python3
"""
Index Chainlink AnswerUpdated events from ETH/USD and BTC/USD aggregators
into ClickHouse for precise historical USD pricing.

Chainlink architecture:
  Proxy (stable address) → Aggregator (rotates, emits AnswerUpdated)

AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt)
  - topic0: event signature
  - topic1: price (int256, 8 decimals)
  - topic2: roundId
  - data:   updatedAt (uint256)
"""

import os
import requests
import time
import subprocess
import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

RPC = os.getenv("MAINNET_RPC_URL", "http://localhost:8545")
ANSWER_UPDATED_TOPIC = "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f"

# Chainlink proxy addresses (stable, never change)
FEED_PROXIES = {
    "ETH/USD": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
    "BTC/USD": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
}

# Fluid started at this block
START_BLOCK = 19_258_464


def eth_call(to, data, block="latest"):
    r = requests.post(RPC, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": to, "data": data}, block]
    }).json()
    return r.get("result", "0x")


def get_latest_block():
    r = requests.post(RPC, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []
    }).json()
    return int(r["result"], 16)


def get_block_timestamp(block_num):
    r = requests.post(RPC, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber",
        "params": [hex(block_num), False]
    }).json()
    block = r.get("result", {})
    return int(block.get("timestamp", "0x0"), 16)


def get_current_aggregator(proxy_addr):
    """Read aggregator() from the proxy."""
    result = eth_call(proxy_addr, "0x245a7bfc")
    return "0x" + result[-40:]


def get_phase_aggregators(proxy_addr):
    """
    Enumerate all historical aggregators by calling phaseAggregators(uint16).
    Returns list of (phase_id, aggregator_address).
    """
    aggregators = []
    for phase_id in range(1, 50):  # max 50 phases
        # phaseAggregators(uint16) selector: 0xc1597304
        data = f"0xc1597304{phase_id:064x}"
        result = eth_call(proxy_addr, data)
        addr = "0x" + result[-40:]
        if addr == "0x0000000000000000000000000000000000000000":
            break
        aggregators.append((phase_id, addr))
    return aggregators


def fetch_events_from_aggregator(aggregator_addr, feed_name, start_block, end_block):
    """Fetch all AnswerUpdated events from a single aggregator in chunks."""
    all_events = []
    chunk_size = 100_000

    for b in range(start_block, end_block + 1, chunk_size):
        to_block = min(b + chunk_size - 1, end_block)
        r = requests.post(RPC, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
            "params": [{
                "address": aggregator_addr,
                "topics": [ANSWER_UPDATED_TOPIC],
                "fromBlock": hex(b),
                "toBlock": hex(to_block)
            }]
        }).json()

        if "error" in r:
            # If block range too wide, try smaller chunks
            for b2 in range(b, to_block + 1, 10_000):
                to2 = min(b2 + 9_999, to_block)
                r2 = requests.post(RPC, json={
                    "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                    "params": [{
                        "address": aggregator_addr,
                        "topics": [ANSWER_UPDATED_TOPIC],
                        "fromBlock": hex(b2),
                        "toBlock": hex(to2)
                    }]
                }).json()
                all_events.extend(r2.get("result", []))
        else:
            all_events.extend(r.get("result", []))

    return all_events


def decode_events(events, feed_name):
    """Decode AnswerUpdated events into (block, timestamp, price) tuples."""
    rows = []
    for ev in events:
        block_num = int(ev["blockNumber"], 16)

        # topic1 = indexed int256 price (8 decimals for USD feeds)
        price_raw = int(ev["topics"][1], 16)
        # Handle negative (two's complement for int256)
        if price_raw > (1 << 255):
            price_raw -= (1 << 256)
        price = price_raw / 1e8

        # data contains updatedAt (uint256)
        updated_at = int(ev["data"], 16) if ev["data"] != "0x" else 0

        if price > 0:
            rows.append((block_num, updated_at, feed_name, price))

    return rows


def main():
    ch = clickhouse_connect.get_client(host="localhost", port=8123)

    # Create table
    ch.command("DROP TABLE IF EXISTS chainlink_prices")
    ch.command("""
        CREATE TABLE chainlink_prices (
            block_number  UInt64,
            timestamp     DateTime,
            feed          String,
            price         Float64
        ) ENGINE = MergeTree()
        ORDER BY (feed, timestamp)
    """)
    print("✅ ClickHouse table 'chainlink_prices' created.")

    latest_block = get_latest_block()
    print(f"Latest block: {latest_block}")

    total_rows = 0

    for feed_name, proxy_addr in FEED_PROXIES.items():
        print(f"\n{'='*60}")
        print(f"  Indexing {feed_name} (proxy: {proxy_addr})")
        print(f"{'='*60}")

        # Enumerate all historical aggregators
        aggregators = get_phase_aggregators(proxy_addr)
        current_agg = get_current_aggregator(proxy_addr)
        print(f"  Found {len(aggregators)} historical phases")
        print(f"  Current aggregator: {current_agg}")

        all_decoded = []

        for phase_id, agg_addr in aggregators:
            print(f"  Phase {phase_id}: {agg_addr}", end="")
            events = fetch_events_from_aggregator(
                agg_addr, feed_name, START_BLOCK, latest_block
            )
            if events:
                decoded = decode_events(events, feed_name)
                all_decoded.extend(decoded)
                first_p = decoded[0][3] if decoded else 0
                last_p = decoded[-1][3] if decoded else 0
                print(f" → {len(decoded)} events (${first_p:,.2f} → ${last_p:,.2f})")
            else:
                print(f" → 0 events in range")

        # Deduplicate by block (in case overlapping aggregator events)
        seen = set()
        unique = []
        for row in sorted(all_decoded, key=lambda x: x[0]):
            key = (row[0], row[2])  # (block, feed)
            if key not in seen:
                seen.add(key)
                unique.append(row)

        print(f"  Total unique events: {len(unique)}")

        if not unique:
            continue

        # Convert timestamps: updatedAt is unix epoch
        import datetime
        ch_rows = []
        for block_num, updated_at, feed, price in unique:
            if updated_at > 0:
                ts = datetime.datetime.utcfromtimestamp(updated_at)
            else:
                ts = datetime.datetime(2024, 1, 1)  # fallback
            ch_rows.append([block_num, ts, feed, price])

        ch.insert("chainlink_prices", ch_rows,
                   column_names=["block_number", "timestamp", "feed", "price"])
        total_rows += len(ch_rows)
        print(f"  Inserted {len(ch_rows)} rows into ClickHouse")

    # Verification
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")

    for feed in FEED_PROXIES:
        latest = ch.query_df(f"""
            SELECT timestamp, price FROM chainlink_prices 
            WHERE feed = '{feed}' ORDER BY timestamp DESC LIMIT 1
        """)
        earliest = ch.query_df(f"""
            SELECT timestamp, price FROM chainlink_prices 
            WHERE feed = '{feed}' ORDER BY timestamp ASC LIMIT 1
        """)
        cnt = ch.command(f"SELECT count() FROM chainlink_prices WHERE feed = '{feed}'")

        if len(latest) > 0 and len(earliest) > 0:
            print(f"  {feed}: {cnt} prices, "
                  f"${earliest.iloc[0]['price']:,.2f} ({earliest.iloc[0]['timestamp']}) → "
                  f"${latest.iloc[0]['price']:,.2f} ({latest.iloc[0]['timestamp']})")

    print(f"\n✅ Done. {total_rows} total price points indexed.")


if __name__ == "__main__":
    main()
