#!/usr/bin/env python3
"""
Aave V3 Cross-Validation: Parquet events vs ClickHouse data.
Vectorized — no iterrows.
"""
import pandas as pd
import numpy as np
import clickhouse_connect

RAY = 10**27

# ── 1. Load Parquet ────────────────────────────────────────
print("Loading Parquet...", flush=True)
df = pd.read_parquet("/mnt/data/hypersync_events/aave_v3_events.parquet")
rdu = df[df["event_name"] == "ReserveDataUpdated"].copy()
print("  ReserveDataUpdated: %d events" % len(rdu), flush=True)

# ── 2. Reserve map from ClickHouse ─────────────────────────
ch = clickhouse_connect.get_client(host="localhost", port=8123)
reserves = ch.query_df("""
    SELECT DISTINCT lower(entity_id) as addr, symbol
    FROM unified_timeseries WHERE protocol = 'AAVE_MARKET' AND entity_id != ''
""")
RMAP = dict(zip(reserves["addr"], reserves["symbol"]))
print("  Reserve map: %d entries" % len(RMAP), flush=True)

# ── 3. Vectorized decode ──────────────────────────────────
# Extract reserve address from topic1 (last 40 hex chars)
rdu["reserve"] = rdu["topic1"].str[-40:].str.lower().apply(lambda x: "0x" + x)
rdu["symbol"] = rdu["reserve"].map(RMAP)
rdu = rdu.dropna(subset=["symbol"])

# Decode data field: word0 = liquidityRate, word2 = variableBorrowRate
# data format: "0x" + 5 x 64-char hex words
valid = rdu["data"].str.len() >= 322
rdu = rdu[valid].copy()

rdu["evt_supply_apy"] = rdu["data"].str[2:66].apply(lambda x: int(x, 16)) / RAY * 100
rdu["evt_borrow_apy"] = rdu["data"].str[130:194].apply(lambda x: int(x, 16)) / RAY * 100

print("  Decoded: %d events, %d symbols" % (len(rdu), rdu["symbol"].nunique()), flush=True)
print("  Time: %s -> %s" % (rdu["block_timestamp"].min(), rdu["block_timestamp"].max()), flush=True)

# ── 4. Hourly aggregation ─────────────────────────────────
rdu["hour"] = rdu["block_timestamp"].dt.floor("h").dt.tz_localize(None)
rdu.sort_values("block_number", inplace=True)
hourly = rdu.groupby(["hour", "symbol"]).agg(
    evt_supply_apy=("evt_supply_apy", "last"),
    evt_borrow_apy=("evt_borrow_apy", "last"),
).reset_index()
print("  Hourly: %d rows" % len(hourly), flush=True)

# ── 5. Load ClickHouse reference (only matching symbols) ──
syms = hourly["symbol"].unique().tolist()
sym_list = ",".join("'%s'" % s for s in syms)
print("Loading ClickHouse reference...", flush=True)
existing = ch.query_df(
    "SELECT timestamp, symbol, supply_apy, borrow_apy "
    "FROM unified_timeseries "
    "WHERE protocol = 'AAVE_MARKET' AND symbol IN (%s)" % sym_list
)
existing.rename(columns={"supply_apy": "ch_supply_apy", "borrow_apy": "ch_borrow_apy"}, inplace=True)
print("  ClickHouse: %d rows" % len(existing), flush=True)

# ── 6. Merge ──────────────────────────────────────────────
merged = pd.merge(
    existing, hourly,
    left_on=["timestamp", "symbol"],
    right_on=["hour", "symbol"],
    how="inner",
)
print("  Matched: %d rows\n" % len(merged), flush=True)

if len(merged) == 0:
    print("No matches. Debugging timestamps...", flush=True)
    print("  CH:  %s -> %s (dtype=%s)" % (
        existing["timestamp"].min(), existing["timestamp"].max(), existing["timestamp"].dtype), flush=True)
    print("  Evt: %s -> %s (dtype=%s)" % (
        hourly["hour"].min(), hourly["hour"].max(), hourly["hour"].dtype), flush=True)
    print("  CH sample: ", existing["timestamp"].head(3).tolist(), flush=True)
    print("  Evt sample:", hourly["hour"].head(3).tolist(), flush=True)

    # Try matching just one known point
    ch_usdc = existing[existing["symbol"] == "USDC"].head(3)
    evt_usdc = hourly[hourly["symbol"] == "USDC"].head(3)
    print("  CH USDC:", ch_usdc[["timestamp"]].values.tolist(), flush=True)
    print("  Evt USDC:", evt_usdc[["hour"]].values.tolist(), flush=True)
else:
    # ── Per-symbol report ──────────────────────────────────
    print("=" * 85, flush=True)
    print("%-12s %8s %11s %11s %11s %11s %8s" % (
        "Symbol", "Matched", "Borr MAE", "Borr Max", "Supp MAE", "Supp Max", "Corr"), flush=True)
    print("=" * 85, flush=True)

    for sym in sorted(merged["symbol"].unique()):
        m = merged[merged["symbol"] == sym]
        if len(m) < 5:
            continue
        b_d = (m["evt_borrow_apy"] - m["ch_borrow_apy"]).abs()
        s_d = (m["evt_supply_apy"] - m["ch_supply_apy"]).abs()
        corr = m["evt_borrow_apy"].corr(m["ch_borrow_apy"])
        if np.isnan(corr):
            corr = 0.0
        print("%-12s %8d %10.4f%% %10.4f%% %10.4f%% %10.4f%% %8.4f" % (
            sym, len(m), b_d.mean(), b_d.max(), s_d.mean(), s_d.max(), corr), flush=True)

    all_b = (merged["evt_borrow_apy"] - merged["ch_borrow_apy"]).abs()
    all_s = (merged["evt_supply_apy"] - merged["ch_supply_apy"]).abs()
    corr_all = merged["evt_borrow_apy"].corr(merged["ch_borrow_apy"])

    print("=" * 85, flush=True)
    print("%-12s %8d %10.4f%% %10.4f%% %10.4f%% %10.4f%% %8.4f" % (
        "OVERALL", len(merged), all_b.mean(), all_b.max(), all_s.mean(), all_s.max(), corr_all), flush=True)

    print("", flush=True)
    print("Borrow APY accuracy:", flush=True)
    print("  Within   1bp: %.1f%%" % ((all_b < 0.01).mean() * 100), flush=True)
    print("  Within  10bp: %.1f%%" % ((all_b < 0.10).mean() * 100), flush=True)
    print("  Within 100bp: %.1f%%" % ((all_b < 1.00).mean() * 100), flush=True)
    print("", flush=True)
    print("Supply APY accuracy:", flush=True)
    print("  Within   1bp: %.1f%%" % ((all_s < 0.01).mean() * 100), flush=True)
    print("  Within  10bp: %.1f%%" % ((all_s < 0.10).mean() * 100), flush=True)
    print("  Within 100bp: %.1f%%" % ((all_s < 1.00).mean() * 100), flush=True)
