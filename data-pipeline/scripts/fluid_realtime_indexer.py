#!/usr/bin/env python3
"""
Fluid Protocol Real-Time Indexer (HyperSync)

Polls Envio HyperSync every 5 minutes for new LogOperate events and
Chainlink price updates. Decodes BigMath-packed data, inserts into
ClickHouse, and runs an incremental merge into unified_timeseries.

State is tracked via MAX(block_number) — fully stateless and self-healing.
"""

import os
import sys
import time
import asyncio
import logging
import datetime
import numpy as np
import pandas as pd
import hypersync
import clickhouse_connect

# ── Configuration ────────────────────────────────────────────
ENVIO_TOKEN = os.getenv("ENVIO_API_TOKEN", "").strip()
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))  # 5 minutes
CONFIRMATION_BLOCKS = 3  # Reorg safety margin

FLUID_LIQUIDITY = "0x52Aa899454998Be5b000Ad077a46Bbe360F4e497"
LOGOPERATE_TOPIC = "0x4d93b232a24e82b284ced7461bf4deacffe66759d5c24513e6f29e571ad78d15"

# Chainlink aggregators (current phase — update when they rotate)
CHAINLINK_AGGS = {
    "ETH/USD": "0x7d4e742018fb52e48b08be73d041c18b21de6fb5",
    "BTC/USD": "0x4a3411ac2948b33c69666b35cc6d055b27ea84f1",
}
ANSWER_UPDATED_TOPIC = "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f"

# ── Token registry (shared with merge_fluid.py) ─────────────
ADDR_MAP = {
    'dac17f958d2ee523a2206206994597c13d831ec7': ('USDT', 6),
    'a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': ('USDC', 6),
    '4c9edd5852cd905f086c759e8383e09bff1e68b3': ('USDe', 18),
    'c139190f447e929f090edeb554d95abb8b18ac1c': ('USDtb', 18),
    '9d39a5de30e57443bff2a8307a4256c8797a3497': ('sUSDe', 18),
    '2260fac5e5542a773aa44fbcfedf7c193bc2c599': ('WBTC', 8),
    'cbb7c0000ab88b473b1f5afd9ef808440eed33bf': ('CBBTC', 8),
    'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee': ('ETH', 18),
    '40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f': ('GHO', 18),
    '66a1e37c9b0eaddca17d3662d6c05f4decf3e110': ('USR', 18),
    '085780639cc2cacd35e474e71f4d000e2405d8f6': ('fxUSD', 18),
    '8236a87084f8b84306f72007f36f2618a5634494': ('LBTC', 8),
    '4956b52ae2ff65d74ca2d61207523288e4528f96': ('RLP', 18),
    '1202f5c7b4b9e47a1a484e8b270be34dbbc75055': ('wstUSR', 18),
    '7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0': ('WSTETH', 18),
    'beefc011e94f43b8b7b455ebab290c7ab4e216f1': ('csUSDL', 18),
    'a1290d69c65a6fe4df752f95823fae25cb99e5a7': ('rsETH', 18),
    '45804880de22913dafe09f4980848ece6ecbaf78': ('PAXG', 18),
    '68749665ff8d2d112fa859aa293f07a622782f38': ('XAUt', 6),
    '917cee801a67f933f2e6b33fc0cd1ed2d5909d88': ('weETHs', 18),
    'bf5495efe5db9ce00f80364c8b423567e58d2110': ('ezETH', 18),
    '73a15fed60bf67631dc6cd7bc5b6e8da8190acf5': ('USD0', 18),
    '15700b564ca08d9439c58ca5053166e8317aa138': ('deUSD', 18),
    'cd5fe23c85820f7b72d0926fc9b05b43e359b7ee': ('weETH', 18),
    '48f9e38f3070ad8945dfeae3fa70987722e3d89c': ('iUSD', 18),
    '6f40d4a6237c257fff2db00fa0510deeecd303eb': ('FLUID', 18),
    'a3931d71877c0e7a3148cb7eb4463524fec27fbd': ('sUSDS', 18),
    '3d7d6fdf07ee548b939a80edbc9b2256d0cdc003': ('srUSDe', 18),
    '80ac24aa929eaf5013f6436cda2a7ba190f5cc0b': ('syrupUSDC', 6),
    'c58d044404d8b14e953c115e67823784dea53d8f': ('jrUSDe', 18),
    'f1c9acdc66974dfb6decb12aa385b9cd01190e38': ('osETH', 18),
    '356b8d89c1e1239cbbb9de4815c39a1474d5ba7d': ('syrupUSDT', 6),
    '657e8c867d8b37dcc18fa4caead9c45eb088c642': ('eBTC', 8),
    '5086bf358635b81d8c47c66d1c8b9e567db70c72': ('reUSD', 18),
    '18084fba666a33d37592fa2633fd49a74dd93a88': ('tBTC', 18),
    'd5f7838f5c461feff7fe49ea5ebaf7728bb0adfa': ('mETH', 18),
}

STABLES = {
    'USDC', 'USDT', 'USDe', 'USDtb', 'GHO', 'USR', 'fxUSD', 'USD0',
    'deUSD', 'iUSD', 'syrupUSDC', 'syrupUSDT', 'reUSD', 'sUSDS', 'sUSDe',
    'srUSDe', 'jrUSDe', 'csUSDL', 'wstUSR',
}
BTC_ASSETS = {'WBTC', 'CBBTC', 'eBTC', 'tBTC', 'LBTC'}
ETH_ASSETS = {'WSTETH', 'ETH', 'weETHs', 'ezETH', 'weETH', 'rsETH', 'osETH', 'mETH'}

PRICE_MULTIPLIERS = {
    'ETH': 1.000, 'WSTETH': 1.230, 'weETH': 1.050, 'weETHs': 1.050,
    'rsETH': 1.040, 'ezETH': 1.020, 'osETH': 1.010, 'mETH': 1.040,
    'WBTC': 1.000, 'CBBTC': 1.000, 'LBTC': 1.000, 'tBTC': 1.000, 'eBTC': 1.000,
}

MASK_64 = (1 << 64) - 1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fluid-indexer")


# ── HyperSync client ─────────────────────────────────────────
def create_hypersync_client():
    if not ENVIO_TOKEN:
        raise RuntimeError(
            "ENVIO_API_TOKEN is required for HyperSync access. "
            "Set it in the environment before starting fluid_realtime_indexer.py."
        )
    return hypersync.HypersyncClient(hypersync.ClientConfig(
        url="https://eth.hypersync.xyz",
        bearer_token=ENVIO_TOKEN,
    ))


async def get_chain_head(hs_client):
    return await hs_client.get_height() - CONFIRMATION_BLOCKS


LOG_FIELDS = [
    hypersync.LogField.BLOCK_NUMBER,
    hypersync.LogField.LOG_INDEX,
    hypersync.LogField.TRANSACTION_HASH,
    hypersync.LogField.ADDRESS,
    hypersync.LogField.TOPIC0,
    hypersync.LogField.TOPIC1,
    hypersync.LogField.TOPIC2,
    hypersync.LogField.TOPIC3,
    hypersync.LogField.DATA,
]
BLOCK_FIELDS = [hypersync.BlockField.NUMBER, hypersync.BlockField.TIMESTAMP]


# ── BigMath decode ───────────────────────────────────────────
def bigmath(p):
    return (p >> 8) << (p & 0xFF)


def decode_logoperate_hs(entry, block_ts_map):
    """Decode a single HyperSync LogOperate event. Returns dict or None."""
    topics = entry.topics or []
    data = entry.data

    if len(topics) < 3 or not data or len(data) < 386:
        return None

    addr = topics[2][26:].lower()  # token address from topic2
    if addr not in ADDR_MAP:
        return None

    block_num = entry.block_number
    tx_hash = entry.transaction_hash
    log_idx = entry.log_index

    try:
        raw = bytes.fromhex(data[2:])
        w4 = int.from_bytes(raw[128:160], 'big')
        w5 = int.from_bytes(raw[160:192], 'big')
    except Exception:
        return None

    sup_int = bigmath(w4 & MASK_64)
    sup_free = bigmath((w4 >> 64) & MASK_64)
    bor_int = bigmath((w4 >> 128) & MASK_64)
    bor_free = bigmath((w4 >> 192) & MASK_64)

    sup_ep = (w5 >> 91) & MASK_64
    bor_ep = (w5 >> 155) & MASK_64
    if sup_ep == 0: sup_ep = int(1e12)
    if bor_ep == 0: bor_ep = int(1e12)

    util_raw = (w5 >> 30) & 0x3FFF
    rate_raw = w5 & 0xFFFF
    fee_raw = (w5 >> 16) & 0x3FFF

    symbol, decimals = ADDR_MAP[addr]
    scale = 10 ** decimals

    supply_tokens = (sup_int * sup_ep / 1e12 + sup_free) / scale
    borrow_tokens = (bor_int * bor_ep / 1e12 + bor_free) / scale
    utilization = util_raw / 10000.0
    borrow_apy = rate_raw / 10000.0
    fee = fee_raw / 10000.0
    supply_apy = max(0, borrow_apy * utilization * (1.0 - fee))

    ts = block_ts_map.get(block_num, datetime.datetime.now(datetime.UTC))

    return {
        "block_number": block_num,
        "tx_hash": tx_hash,
        "log_index": log_idx,
        "addr": addr,
        "symbol": symbol,
        "decimals": decimals,
        "entity_id": topics[2].lower(),
        "supply_tokens": supply_tokens,
        "borrow_tokens": borrow_tokens,
        "utilization": utilization,
        "borrow_apy": borrow_apy,
        "supply_apy": supply_apy,
        "data": data,
        "timestamp": ts,
    }


# ── Core pipeline steps (HyperSync) ──────────────────────────
async def poll_all_events(hs_client, ch, from_block, to_block):
    """
    Single HyperSync query that fetches BOTH Fluid LogOperate and
    Chainlink AnswerUpdated events, plus block timestamps.
    """
    # Build log selections for all event sources
    chainlink_addresses = list(CHAINLINK_AGGS.values())
    log_selections = [
        # Fluid LogOperate
        hypersync.LogSelection(
            address=[FLUID_LIQUIDITY],
            topics=[[LOGOPERATE_TOPIC]],
        ),
        # Chainlink AnswerUpdated (both ETH and BTC aggregators)
        hypersync.LogSelection(
            address=chainlink_addresses,
            topics=[[ANSWER_UPDATED_TOPIC]],
        ),
    ]

    query = hypersync.Query(
        from_block=from_block,
        to_block=to_block,
        logs=log_selections,
        field_selection=hypersync.FieldSelection(
            log=LOG_FIELDS,
            block=BLOCK_FIELDS,
        ),
    )

    t0 = time.time()
    res = await hs_client.get(query)
    elapsed = time.time() - t0

    # Build block timestamp map
    block_ts_map = {}
    for b in res.data.blocks:
        if b.number is not None and b.timestamp is not None:
            ts_val = b.timestamp
            if isinstance(ts_val, str):
                ts_val = int(ts_val, 16) if ts_val.startswith("0x") else int(ts_val)
            block_ts_map[b.number] = datetime.datetime.fromtimestamp(ts_val, tz=datetime.UTC)

    # Split logs by source
    fluid_logs = []
    chainlink_logs = []
    agg_addr_to_feed = {v.lower(): k for k, v in CHAINLINK_AGGS.items()}

    for entry in res.data.logs:
        addr = entry.address.lower() if entry.address else ""
        if addr == FLUID_LIQUIDITY.lower():
            fluid_logs.append(entry)
        elif addr in agg_addr_to_feed:
            chainlink_logs.append((entry, agg_addr_to_feed[addr]))

    log.info(f"  HyperSync: {len(res.data.logs)} events in {elapsed:.2f}s "
             f"(fluid={len(fluid_logs)}, chainlink={len(chainlink_logs)})")

    # ── Process Fluid events ──
    raw_rows = []
    decoded = []
    for entry in fluid_logs:
        topics = entry.topics or []
        ts = block_ts_map.get(entry.block_number, datetime.datetime.now(datetime.UTC))
        ts_naive = ts.replace(tzinfo=None)

        raw_rows.append([
            entry.block_number, ts_naive, entry.transaction_hash or "",
            entry.log_index or 0,
            (entry.address or "").lower(),
            "Operate",
            topics[0] if len(topics) > 0 else "",
            topics[1] if len(topics) > 1 else None,
            topics[2] if len(topics) > 2 else None,
            topics[3] if len(topics) > 3 else None,
            entry.data or "",
            0, 0,
        ])

        d = decode_logoperate_hs(entry, block_ts_map)
        if d:
            d["timestamp"] = ts_naive
            decoded.append(d)

    if raw_rows:
        ch.insert("fluid_events", raw_rows, column_names=[
            "block_number", "block_timestamp", "tx_hash", "log_index",
            "contract", "event_name", "topic0", "topic1", "topic2",
            "topic3", "data", "supply_amount", "borrow_amount",
        ])
    log.info(f"  Fluid: {len(raw_rows)} events inserted, {len(decoded)} decoded")

    # ── Process Chainlink events ──
    chainlink_rows = []
    for entry, feed_name in chainlink_logs:
        topics = entry.topics or []
        if len(topics) < 2:
            continue
        price_raw = int(topics[1], 16)
        if price_raw > (1 << 255):
            price_raw -= (1 << 256)
        price = price_raw / 1e8

        data = entry.data or "0x"
        updated_at = int(data, 16) if data != "0x" and len(data) > 2 else 0
        ts = datetime.datetime.fromtimestamp(updated_at, tz=datetime.UTC) if updated_at > 0 else datetime.datetime.now(datetime.UTC)

        if price > 0:
            chainlink_rows.append([entry.block_number, ts.replace(tzinfo=None), feed_name, price])

    if chainlink_rows:
        ch.insert("chainlink_prices", chainlink_rows,
                   column_names=["block_number", "timestamp", "feed", "price"])
    log.info(f"  Chainlink: {len(chainlink_rows)} new prices")

    return decoded


def incremental_merge(ch, decoded_events):
    """
    Take decoded events and merge them into unified_timeseries.
    Only processes hours that have new data.
    """
    if not decoded_events:
        return

    df = pd.DataFrame(decoded_events)
    df['ts'] = df['timestamp'].dt.floor('h')

    # Take last event per (hour, symbol)
    df.sort_values('block_number', inplace=True)
    hourly = df.groupby(['ts', 'symbol']).last().reset_index()

    # Get Chainlink prices for these hours
    eth_prices = ch.query_df("""
        SELECT toStartOfHour(timestamp) AS ts, argMax(price, timestamp) AS eth_usd
        FROM chainlink_prices WHERE feed = 'ETH/USD'
        GROUP BY ts ORDER BY ts
    """)
    btc_prices = ch.query_df("""
        SELECT toStartOfHour(timestamp) AS ts, argMax(price, timestamp) AS btc_usd
        FROM chainlink_prices WHERE feed = 'BTC/USD'
        GROUP BY ts ORDER BY ts
    """)

    merged = pd.merge(hourly, eth_prices, on='ts', how='left')
    merged = pd.merge(merged, btc_prices, on='ts', how='left')
    merged['eth_usd'] = merged['eth_usd'].ffill().bfill()
    merged['btc_usd'] = merged['btc_usd'].ffill().bfill()

    def get_price(row):
        sym = row['symbol']
        if sym in STABLES:
            return 1.0
        if sym in PRICE_MULTIPLIERS:
            mult = PRICE_MULTIPLIERS[sym]
            if sym in ETH_ASSETS:
                return row.get('eth_usd', 2000) * mult
            elif sym in BTC_ASSETS:
                return row.get('btc_usd', 70000) * mult
        if sym in ('PAXG', 'XAUt'):
            return 3300.0
        if sym == 'FLUID':
            return 0.50
        return 1.0

    merged['price_usd'] = merged.apply(get_price, axis=1)
    merged['supply_usd'] = merged['supply_tokens'] * merged['price_usd']
    merged['borrow_usd'] = merged['borrow_tokens'] * merged['price_usd']

    # Delete existing rows for these (timestamp, symbol) pairs and re-insert
    hours = merged['ts'].unique()
    for h in hours:
        ts_str = pd.Timestamp(h).strftime('%Y-%m-%d %H:%M:%S')
        ch.command(
            f"ALTER TABLE unified_timeseries DELETE "
            f"WHERE protocol='FLUID_MARKET' AND timestamp='{ts_str}'"
        )

    final = pd.DataFrame({
        'timestamp': merged['ts'],
        'protocol': 'FLUID_MARKET',
        'symbol': merged['symbol'],
        'entity_id': merged['entity_id'],
        'target_id': '',
        'supply_usd': merged['supply_usd'],
        'borrow_usd': merged['borrow_usd'],
        'supply_apy': merged['supply_apy'],
        'borrow_apy': merged['borrow_apy'],
        'utilization': merged['utilization'],
        'price_usd': merged['price_usd'],
    })
    final = final[final['supply_usd'] > 0]

    if len(final) > 0:
        ch.insert_df("unified_timeseries", final)

    log.info(f"  Merge: {len(final)} rows upserted into unified_timeseries "
             f"({len(hours)} hours)")


# ── Main loop ────────────────────────────────────────────────
async def run_cycle(hs_client, ch):
    """Execute one poll-decode-merge cycle."""
    last_block = int(ch.command("SELECT max(block_number) FROM fluid_events") or 0)
    head_block = await get_chain_head(hs_client)

    if head_block <= last_block:
        log.info(f"No new blocks (head={head_block}, last={last_block})")
        return

    gap = head_block - last_block
    log.info(f"Processing blocks {last_block + 1} → {head_block} ({gap} blocks, ~{gap * 12 / 60:.0f} min)")

    # Single HyperSync query for ALL events + block timestamps
    decoded = await poll_all_events(hs_client, ch, last_block + 1, head_block)

    # Incremental merge
    incremental_merge(ch, decoded)


async def async_main():
    log.info("═" * 50)
    log.info("  Fluid Real-Time Indexer (HyperSync)")
    log.info(f"  ClickHouse: {CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}")
    log.info(f"  Poll interval: {POLL_INTERVAL}s")
    log.info("═" * 50)

    hs_client = create_hypersync_client()
    ch = clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT)

    # Verify tables exist
    try:
        ch.command("SELECT 1 FROM fluid_events LIMIT 1")
        ch.command("SELECT 1 FROM chainlink_prices LIMIT 1")
        ch.command("SELECT 1 FROM unified_timeseries LIMIT 1")
    except Exception as e:
        log.error(f"Required tables missing: {e}")
        log.error("Run the historical backfill first.")
        sys.exit(1)

    last_block = ch.command("SELECT max(block_number) FROM fluid_events")
    log.info(f"Resuming from block {last_block}")

    while True:
        try:
            await run_cycle(hs_client, ch)
        except KeyboardInterrupt:
            log.info("Shutting down.")
            break
        except Exception as e:
            log.error(f"Cycle failed: {e}", exc_info=True)
            try:
                hs_client = create_hypersync_client()
                ch = clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT)
            except Exception:
                pass

        log.info(f"Sleeping {POLL_INTERVAL}s...")
        await asyncio.sleep(POLL_INTERVAL)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
