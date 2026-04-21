"""Full Aave V3 cross-validation with explicit error handling."""
import sys, os, time, asyncio, traceback
import pandas as pd
import numpy as np
import hypersync
import clickhouse_connect

ENVIO_TOKEN = os.getenv("ENVIO_API_TOKEN", "").strip()
RAY = 10**27
BATCH_SIZE = 1_000_000

RESERVE_MAP = {
    '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': 'USDC',
    '0xdac17f958d2ee523a2206206994597c13d831ec7': 'USDT',
    '0x6b175474e89094c44da98b954eedeac495271d0f': 'DAI',
    '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2': 'WETH',
    '0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0': 'WSTETH',
    '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599': 'WBTC',
    '0x514910771af9ca656af840dff83e8264ecf986ca': 'LINK',
    '0xae78736cd615f374d3085123a210448e74fc6393': 'RETH',
    '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9': 'AAVE',
    '0xd533a949740bb3306d119cc777fa900ba034cd52': 'CRV',
    '0x5f98805a4e8be255a32880fdec7f6728c6568ba0': 'LUSD',
    '0xbe9895146f7af43049ca1c1ae358b0541ea49704': 'CBETH',
    '0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f': 'GHO',
    '0x853d955acef822db058eb8505911ed77f175b99e': 'FRAX',
    '0xba100000625a3754423978a60c9317c58a424e3d': 'BAL',
    '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984': 'UNI',
    '0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f': 'SNX',
    '0x9d39a5de30e57443bff2a8307a4256c8797a3497': 'SUSDE',
    '0x4c9edd5852cd905f086c759e8383e09bff1e68b3': 'USDE',
    '0xf939e0a03fb07f59a73314e73794be0e57ac1b4e': 'CRVUSD',
    '0x6c3ea9036406852006290770bedfcaba0e23a0e8': 'PYUSD',
    '0xcd5fe23c85820f7b72d0926fc9b05b43e359b7ee': 'WEETH',
    '0xbf5495efe5db9ce00f80364c8b423567e58d2110': 'EZETH',
    '0xa1290d69c65a6fe4df752f95823fae25cb99e5a7': 'RSETH',
    '0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf': 'CBBTC',
}

async def download():
    if not ENVIO_TOKEN:
        raise RuntimeError("ENVIO_API_TOKEN is required")
    print("Creating client...", flush=True)
    try:
        client = hypersync.HypersyncClient(hypersync.ClientConfig(
            url="https://eth.hypersync.xyz",
            bearer_token=ENVIO_TOKEN,
        ))
    except Exception as e:
        print("CLIENT ERROR: %s" % e, flush=True)
        traceback.print_exc()
        return []
    
    try:
        head = await client.get_height()
    except Exception as e:
        print("HEIGHT ERROR: %s" % e, flush=True)
        traceback.print_exc()
        return []
    
    print("Head: %d" % head, flush=True)
    
    all_decoded = []
    block_ts = {}
    from_block = 16_291_127
    
    while from_block < head:
        to_block = min(from_block + BATCH_SIZE, head)
        try:
            query = hypersync.Query(
                from_block=from_block,
                to_block=to_block,
                logs=[hypersync.LogSelection(
                    address=["0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"],
                    topics=[["0x804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a"]],
                )],
                field_selection=hypersync.FieldSelection(
                    log=[hypersync.LogField.BLOCK_NUMBER, hypersync.LogField.TOPIC1, hypersync.LogField.DATA],
                    block=[hypersync.BlockField.NUMBER, hypersync.BlockField.TIMESTAMP],
                ),
            )
            t0 = time.time()
            res = await client.get(query)
            elapsed = time.time() - t0
        except Exception as e:
            print("QUERY ERROR at block %d: %s" % (from_block, e), flush=True)
            traceback.print_exc()
            from_block = to_block + 1
            continue
        
        for b in res.data.blocks:
            if b.number is not None and b.timestamp is not None:
                ts = b.timestamp
                if isinstance(ts, str):
                    ts = int(ts, 16) if ts.startswith("0x") else int(ts)
                block_ts[b.number] = ts
        
        batch = 0
        for e in res.data.logs:
            topics = e.topics or []
            if len(topics) < 2 or not e.data or len(e.data) < 322:
                continue
            addr = "0x" + topics[1][26:].lower()
            sym = RESERVE_MAP.get(addr)
            if not sym:
                continue
            raw = e.data[2:]
            ts_unix = block_ts.get(e.block_number)
            if not ts_unix:
                continue
            all_decoded.append({
                "block_number": e.block_number,
                "symbol": sym,
                "supply_apy": int(raw[0:64], 16) / RAY * 100,
                "borrow_apy": int(raw[128:192], 16) / RAY * 100,
                "timestamp": pd.Timestamp.utcfromtimestamp(ts_unix),
            })
            batch += 1
        
        print("  %10d -> %10d: +%5d events, %5d decoded (%.1fs)" % (
            from_block, to_block, len(res.data.logs), batch, elapsed), flush=True)
        from_block = to_block + 1
    
    print("Total: %d decoded events" % len(all_decoded), flush=True)
    return all_decoded

def compare(decoded):
    df = pd.DataFrame(decoded)
    print("Events: %d, symbols: %d" % (len(df), df["symbol"].nunique()), flush=True)
    print("Range: %s -> %s" % (df["timestamp"].min(), df["timestamp"].max()), flush=True)
    
    # Hourly
    df["hour"] = df["timestamp"].dt.floor("h")
    df.sort_values("block_number", inplace=True)
    hourly = df.groupby(["hour", "symbol"]).last().reset_index()
    print("Hourly: %d rows" % len(hourly), flush=True)
    
    # Load ClickHouse
    ch = clickhouse_connect.get_client(host="localhost", port=8123)
    existing = ch.query_df(
        "SELECT timestamp, symbol, supply_apy, borrow_apy "
        "FROM unified_timeseries WHERE protocol = 'AAVE_MARKET'"
    )
    print("ClickHouse: %d rows" % len(existing), flush=True)
    
    # Merge
    hourly_r = hourly.rename(columns={"hour": "timestamp", "supply_apy": "evt_supply", "borrow_apy": "evt_borrow"})
    existing_r = existing.rename(columns={"supply_apy": "ch_supply", "borrow_apy": "ch_borrow"})
    merged = pd.merge(
        existing_r[["timestamp", "symbol", "ch_supply", "ch_borrow"]],
        hourly_r[["timestamp", "symbol", "evt_supply", "evt_borrow"]],
        on=["timestamp", "symbol"], how="inner",
    )
    print("Matched: %d rows" % len(merged), flush=True)
    
    if len(merged) == 0:
        print("ERROR: No matching rows!", flush=True)
        return
    
    # Report
    print("", flush=True)
    print("=" * 82, flush=True)
    print("%-12s %8s %12s %12s %12s %12s" % ("Symbol", "Matched", "Borr MAE", "Borr Max", "Supp MAE", "Supp Max"), flush=True)
    print("=" * 82, flush=True)
    
    for sym in sorted(merged["symbol"].unique()):
        m = merged[merged["symbol"] == sym]
        if len(m) < 5:
            continue
        b_diff = (m["evt_borrow"] - m["ch_borrow"]).abs()
        s_diff = (m["evt_supply"] - m["ch_supply"]).abs()
        print("%-12s %8d %11.4f%% %11.4f%% %11.4f%% %11.4f%%" % (
            sym, len(m), b_diff.mean(), b_diff.max(), s_diff.mean(), s_diff.max()), flush=True)
    
    all_b = (merged["evt_borrow"] - merged["ch_borrow"]).abs()
    all_s = (merged["evt_supply"] - merged["ch_supply"]).abs()
    print("=" * 82, flush=True)
    print("%-12s %8d %11.4f%% %11.4f%% %11.4f%% %11.4f%%" % (
        "OVERALL", len(merged), all_b.mean(), all_b.max(), all_s.mean(), all_s.max()), flush=True)
    print("", flush=True)
    print("Borrow: %.1f%% within 1bp, %.1f%% within 10bp, %.1f%% within 100bp" % (
        (all_b < 0.01).mean()*100, (all_b < 0.10).mean()*100, (all_b < 1.00).mean()*100), flush=True)
    print("Supply: %.1f%% within 1bp, %.1f%% within 10bp, %.1f%% within 100bp" % (
        (all_s < 0.01).mean()*100, (all_s < 0.10).mean()*100, (all_s < 1.00).mean()*100), flush=True)

async def main():
    try:
        decoded = await download()
        if decoded:
            compare(decoded)
    except Exception as e:
        print("FATAL: %s" % e, flush=True)
        traceback.print_exc()

asyncio.run(main())
print("FINISHED", flush=True)
