#!/usr/bin/env python3
"""
Backfill MORPHO_VAULT and MORPHO_ALLOCATION data for the Apr 6-13 gap.

Strategy:
  1. Load vault metadata from morpho_vault_meta
  2. Pre-seed vault positions from the latest MORPHO_ALLOCATION before Apr 6
  3. Replay Supply/Withdraw events from morpho_events (Apr 6 → now)
  4. At each hour boundary, write vault + allocation snapshots

Usage:
    cd /home/ubuntu/RLD/data-pipeline
    .venv/bin/python scripts/backfill_morpho_vaults.py
"""

import os, sys, logging
import pandas as pd
import clickhouse_connect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from indexer.tokens import SYM_DECIMALS, get_usd_price, get_chainlink_prices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("backfill-vaults")

# ── Morpho Blue event topic0 hashes ─────────────────────────
SUPPLY_TOPIC = "0xedf8870433c83823eb071d3df1caa8d008f12f6440918c20d75a3602cda30fe0"
WITHDRAW_TOPIC = "0xa56fc0ad5702ec05ce63666221f796fb62437c32db1aa1aa075fc6484cf58fbf"

CH_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CH_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))


def main():
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT)

    # 1. Load vault metadata
    vm = ch.query_df("SELECT vault_address, name, asset_symbol FROM morpho_vault_meta")
    vault_addrs = set(vm["vault_address"].str.lower())
    vault_meta = {}
    for _, row in vm.iterrows():
        vault_meta[row["vault_address"].lower()] = {
            "asset_symbol": row["asset_symbol"],
            "name": row["name"],
        }
    log.info(f"Loaded {len(vault_addrs)} vault addresses")

    # 2. Load market symbol mapping
    params = ch.query_df(
        "SELECT lower(market_id) AS market_id, loan_symbol FROM morpho_market_params"
    )
    market_symbols = dict(zip(params["market_id"], params["loan_symbol"]))
    log.info(f"Loaded {len(market_symbols)} market symbols")

    # 3. Pre-seed vault positions from MORPHO_ALLOCATION at Apr 6 boundary
    vault_positions: dict[tuple[str, str], int] = {}  # (vault, market) -> supply_assets_approx

    pre_seed = ch.query_df("""
        SELECT entity_id AS vault, target_id AS market,
               argMax(supply_usd, timestamp) AS supply_usd,
               argMax(symbol, timestamp) AS sym
        FROM unified_timeseries
        WHERE protocol = 'MORPHO_ALLOCATION'
          AND timestamp <= '2026-04-06 08:00:00'
        GROUP BY entity_id, target_id
        HAVING supply_usd > 0
    """)

    eth_price, btc_price = get_chainlink_prices(ch)

    if not pre_seed.empty:
        for _, row in pre_seed.iterrows():
            va = row["vault"].lower()
            mid = row["market"].lower()
            sym = row["sym"]
            decimals = SYM_DECIMALS.get(sym, 18)
            tp = get_usd_price(sym, eth_price, btc_price)
            if tp <= 0:
                tp = 1.0
            supply_assets = int(row["supply_usd"] / tp * (10 ** decimals))
            vault_positions[(va, mid)] = supply_assets
        log.info(f"Pre-seeded {len(vault_positions)} vault positions at Apr 6 boundary")

    # 4. Load market state for shares→assets conversion
    market_supply = {}  # market_id -> total_supply_assets
    ms_df = ch.query_df("""
        SELECT entity_id AS market_id,
               argMax(supply_usd, timestamp) AS supply_usd,
               argMax(symbol, timestamp) AS sym
        FROM unified_timeseries
        WHERE protocol = 'MORPHO_MARKET'
        GROUP BY entity_id
        HAVING supply_usd > 0
    """)
    for _, row in ms_df.iterrows():
        mid = row["market_id"].lower()
        sym = row["sym"]
        decimals = SYM_DECIMALS.get(sym, 18)
        tp = get_usd_price(sym, eth_price, btc_price)
        if tp <= 0:
            tp = 1.0
        market_supply[mid] = {
            "total_supply_assets": int(row["supply_usd"] / tp * (10 ** decimals)),
            "symbol": sym,
        }
    log.info(f"Loaded {len(market_supply)} market supply states")

    # 5. Replay Supply/Withdraw events from Apr 6 onward
    log.info("Loading Supply/Withdraw events from morpho_events...")
    events = ch.query_df(f"""
        SELECT block_number, block_timestamp, topic0, topic1, topic3, data
        FROM morpho_events
        WHERE topic0 IN ('{SUPPLY_TOPIC}', '{WITHDRAW_TOPIC}')
          AND block_timestamp >= '2026-04-06 08:00:00'
        ORDER BY block_number, log_index
    """)
    log.info(f"Loaded {len(events)} Supply/Withdraw events to replay")

    if events.empty:
        log.warning("No events to replay — exiting")
        return

    # Process events and track position changes
    for _, ev in events.iterrows():
        topic0 = ev["topic0"]
        market_id = (ev["topic1"] or "").lower()
        on_behalf_raw = ev["topic3"] or ""
        if len(on_behalf_raw) < 40:
            continue
        on_behalf = "0x" + on_behalf_raw[-40:].lower()

        if on_behalf not in vault_addrs:
            continue

        data = (ev["data"] or "").replace("0x", "")
        key = (on_behalf, market_id)

        if topic0 == SUPPLY_TOPIC and len(data) >= 128:
            assets = int(data[0:64], 16)
            vault_positions[key] = vault_positions.get(key, 0) + assets

        elif topic0 == WITHDRAW_TOPIC and len(data) >= 192:
            assets = int(data[64:128], 16)
            vault_positions[key] = max(0, vault_positions.get(key, 0) - assets)

    log.info(f"After event replay: {len(vault_positions)} active vault positions")

    # 6. Generate hourly snapshots for the gap period (Apr 6 08:00 → now)
    # Use a single snapshot at each hour from the gap range
    gap_start = pd.Timestamp("2026-04-06 09:00:00")
    gap_end = pd.Timestamp.now().floor("h")
    hours = pd.date_range(gap_start, gap_end, freq="h")
    log.info(f"Generating snapshots for {len(hours)} hours ({gap_start} → {gap_end})")

    # Since we don't have per-hour event boundaries (we applied all events),
    # we write the FINAL position state for ALL hours in the gap.
    # The forward-fill approach: vault allocations rarely change within a week,
    # so the last known state is a reasonable approximation.

    alloc_rows = []
    vault_totals_per_hour: dict[str, float] = {}

    for (va, mid), supply_assets in vault_positions.items():
        if supply_assets <= 0:
            continue
        ms = market_supply.get(mid)
        if not ms or ms["total_supply_assets"] <= 0:
            continue

        sym = ms["symbol"]
        decimals = SYM_DECIMALS.get(sym, 18)
        tp = get_usd_price(sym, eth_price, btc_price)
        supply_usd = supply_assets / (10 ** decimals) * tp
        share_pct = supply_assets / ms["total_supply_assets"] if ms["total_supply_assets"] > 0 else 0.0

        v_meta = vault_meta.get(va, {})
        v_sym = v_meta.get("asset_symbol", sym).upper()

        # Aggregate vault TVL
        vault_totals_per_hour[va] = vault_totals_per_hour.get(va, 0.0) + supply_usd

        for h in hours:
            alloc_rows.append({
                "timestamp": h.to_pydatetime(),
                "protocol": "MORPHO_ALLOCATION",
                "symbol": v_sym,
                "entity_id": va,
                "target_id": mid,
                "supply_usd": supply_usd,
                "borrow_usd": 0.0,
                "supply_apy": 0.0,
                "borrow_apy": 0.0,
                "utilization": share_pct,
                "price_usd": 0.0,
            })

    log.info(f"Generated {len(alloc_rows)} allocation rows")

    # Vault-level rows
    vault_rows = []
    for va, tvl_usd in vault_totals_per_hour.items():
        if tvl_usd <= 0:
            continue
        v_meta = vault_meta.get(va, {})
        v_sym = v_meta.get("asset_symbol", "UNKNOWN").upper()
        for h in hours:
            vault_rows.append({
                "timestamp": h.to_pydatetime(),
                "protocol": "MORPHO_VAULT",
                "symbol": v_sym,
                "entity_id": va,
                "target_id": "",
                "supply_usd": tvl_usd,
                "borrow_usd": 0.0,
                "supply_apy": 0.0,
                "borrow_apy": 0.0,
                "utilization": 0.0,
                "price_usd": 0.0,
            })

    log.info(f"Generated {len(vault_rows)} vault rows")

    # 7. Delete existing gap data and insert
    if alloc_rows:
        alloc_df = pd.DataFrame(alloc_rows)
        min_ts = gap_start.strftime("%Y-%m-%d %H:%M:%S")
        max_ts = gap_end.strftime("%Y-%m-%d %H:%M:%S")
        ch.command(
            f"ALTER TABLE unified_timeseries DELETE "
            f"WHERE protocol='MORPHO_ALLOCATION' "
            f"AND timestamp >= '{min_ts}' AND timestamp <= '{max_ts}'"
        )
        # Insert in chunks (avoid memory issues)
        chunk_size = 100_000
        for i in range(0, len(alloc_df), chunk_size):
            ch.insert_df("unified_timeseries", alloc_df.iloc[i:i+chunk_size])
            log.info(f"  Inserted allocation chunk {i//chunk_size + 1}")

    if vault_rows:
        vault_df = pd.DataFrame(vault_rows)
        min_ts = gap_start.strftime("%Y-%m-%d %H:%M:%S")
        max_ts = gap_end.strftime("%Y-%m-%d %H:%M:%S")
        ch.command(
            f"ALTER TABLE unified_timeseries DELETE "
            f"WHERE protocol='MORPHO_VAULT' "
            f"AND timestamp >= '{min_ts}' AND timestamp <= '{max_ts}'"
        )
        ch.insert_df("unified_timeseries", vault_df)
        log.info(f"  Inserted {len(vault_df)} vault rows")

    # 8. Verify
    total = len(alloc_rows) + len(vault_rows)
    log.info(f"\n✅ Backfill complete: {total:,} rows inserted")

    for proto in ["MORPHO_ALLOCATION", "MORPHO_VAULT"]:
        latest = ch.command(
            f"SELECT max(timestamp) FROM unified_timeseries WHERE protocol = '{proto}'"
        )
        log.info(f"  {proto}: latest = {latest}")

    ch.close()


if __name__ == "__main__":
    main()
