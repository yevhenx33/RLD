#!/usr/bin/env python3
"""
Fluid Vault-Level Event Replay
===============================
Replays Operate/LogOperate events to reconstruct per-vault daily timeseries.

Uses raw-share accumulation (not delta summation) to correctly account for
interest accrual via exchange price growth.

State equation:
  raw_delta = normal_delta × 1e12 / exchange_price_at_event
  vault_raw_shares[token] += raw_delta
  balance_at_time_t = vault_raw_shares[token] × exchange_price_t / 1e12

Output: fluid_vault_timeseries table with daily TVL, flows, APY per vault.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
from collections import defaultdict

import clickhouse_connect

log = logging.getLogger("fluid_vault_replay")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

PRECISION = 10**12
MASK_64 = (1 << 64) - 1

# ── Token metadata (symbol, decimals) ──
# Imported from the existing tokens registry
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from tokens import TOKENS
except ImportError:
    try:
        from analytics.tokens import TOKENS
    except ImportError:
        TOKENS = {}


def bigmath(packed: int) -> int:
    """Fluid BigMath: 56-bit coefficient + 8-bit exponent."""
    return (packed >> 8) << (packed & 0xFF)


def decode_int256(hex_str: str) -> int:
    """Decode a 64-char hex string as signed int256."""
    val = int(hex_str, 16)
    return val - (1 << 256) if val >= (1 << 255) else val


def get_ch() -> clickhouse_connect.driver.Client:
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "rld_clickhouse"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", os.getenv("CLICKHOUSE_PASS", "")),
    )


def ensure_table(ch):
    """Create the output table if it doesn't exist."""
    ch.command("""
        CREATE TABLE IF NOT EXISTS fluid_vault_timeseries (
            timestamp          DateTime,
            vault_id           String,
            symbol             LowCardinality(String),
            collateral_token   String,
            debt_token         String,
            supply_raw_shares  Float64,
            borrow_raw_shares  Float64,
            supply_tokens      Float64,
            borrow_tokens      Float64,
            supply_usd         Float64,
            borrow_usd         Float64,
            utilization        Float64,
            supply_ex_price    Float64,
            borrow_ex_price    Float64,
            supply_apy         Float64,
            borrow_apy         Float64,
            supply_inflow_usd  Float64,
            supply_outflow_usd Float64,
            borrow_inflow_usd  Float64,
            borrow_outflow_usd Float64,
            net_supply_flow_usd Float64,
            net_borrow_flow_usd Float64,
            event_count        UInt32,
            inserted_at        DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (vault_id, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
    """)
    log.info("Ensured fluid_vault_timeseries table exists")


def load_vault_registry(ch) -> dict[str, dict]:
    """Load known vault addresses from fluid_contract_registry."""
    rows = ch.query("""
        SELECT DISTINCT contract
        FROM fluid_contract_registry
        WHERE product_type = 'VAULT' AND name = 'Fluid Vault'
    """).result_rows
    vaults = {}
    for (addr,) in rows:
        vaults[addr.lower()] = {"address": addr.lower()}
    log.info("Loaded %d vault addresses from registry", len(vaults))
    return vaults


def load_vault_metadata(ch, vault_ids: set[str]) -> dict[str, dict]:
    """Load vault metadata (symbol, collateral, debt tokens) from product_snapshots."""
    if not vault_ids:
        return {}
    placeholders = ", ".join(f"'{v}'" for v in vault_ids)
    rows = ch.query(f"""
        SELECT
            product_id,
            argMax(symbol, timestamp) AS symbol,
            argMax(collateral_token, timestamp) AS col_token,
            argMax(debt_token, timestamp) AS debt_token
        FROM fluid_product_snapshots
        WHERE product_type = 'VAULT' AND product_id IN ({placeholders})
        GROUP BY product_id
    """).result_rows
    meta = {}
    for product_id, symbol, col_token, debt_token in rows:
        meta[product_id.lower()] = {
            "symbol": str(symbol),
            "collateral_token": str(col_token).lower(),
            "debt_token": str(debt_token).lower(),
        }
    log.info("Loaded metadata for %d vaults from product_snapshots", len(meta))
    return meta


def load_chainlink_prices(ch) -> dict[str, dict[str, float]]:
    """Load latest Chainlink prices per token feed for USD conversion.
    Returns {feed: {hourly_ts_str: price}}
    """
    rows = ch.query("""
        SELECT feed, toStartOfHour(timestamp) AS ts, argMax(price, timestamp) AS price
        FROM chainlink_prices
        WHERE feed IN ('ETH / USD', 'BTC / USD', 'USDC / USD', 'USDT / USD', 'DAI / USD')
        GROUP BY feed, ts
        ORDER BY feed, ts
    """).result_rows
    prices = defaultdict(dict)
    for feed, ts, price in rows:
        prices[str(feed)][str(ts)] = float(price)
    log.info("Loaded Chainlink prices for %d feeds", len(prices))
    return dict(prices)


# ── Symbol → price resolution (same logic as existing FluidSource) ──
def resolve_price(symbol: str, feed_prices: dict[str, float]) -> float | None:
    """Resolve a token symbol to USD price using Chainlink feed prices."""
    sym = symbol.upper()
    if sym in ("USDC", "USDT", "DAI", "SUSDS", "GHO", "FDUSD"):
        return 1.0
    if sym in ("USDTB",):
        return 1.0
    if sym in ("ETH", "WETH"):
        return feed_prices.get("ETH / USD")
    if sym == "WSTETH":
        eth = feed_prices.get("ETH / USD")
        return eth * 1.18 if eth else None  # approximate wstETH/ETH ratio
    if sym == "WEETH":
        eth = feed_prices.get("ETH / USD")
        return eth * 1.05 if eth else None
    if sym in ("WBTC", "CBBTC", "TBTC"):
        return feed_prices.get("BTC / USD")
    if sym == "USDE":
        return 1.0
    if sym == "SUSDE":
        return 1.05  # approximate sUSDe premium
    # For wrapped/derivative tokens, try to match base
    if "ETH" in sym:
        return feed_prices.get("ETH / USD")
    if "BTC" in sym:
        return feed_prices.get("BTC / USD")
    return None


def replay_events(ch, vault_addrs: set[str]) -> dict:
    """
    Replay all Operate/LogOperate events for known vaults.

    Returns per-vault, per-day state snapshots.
    """
    placeholders = ", ".join(f"'{v}'" for v in vault_addrs)

    log.info("Querying vault events...")
    rows = ch.query(f"""
        SELECT
            block_number,
            block_timestamp,
            lower(concat('0x', substring(topic1, 27))) AS vault_addr,
            lower(concat('0x', substring(topic2, 27))) AS token_addr,
            substring(data, 3, 64) AS w0,
            substring(data, 67, 64) AS w1,
            substring(data, 323, 64) AS w5_exchange
        FROM fluid_events
        WHERE (event_name = 'Operate' OR event_name = 'LogOperate')
          AND lower(concat('0x', substring(topic1, 27))) IN ({placeholders})
        ORDER BY block_number ASC
    """).result_rows
    log.info("Processing %d vault events", len(rows))

    # State tracking per (vault, token)
    # vault_shares[vault][token] = accumulated raw shares
    vault_shares: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Track exchange prices per token (global, not per vault)
    token_ex_prices: dict[str, tuple[int, int]] = {}  # token -> (supply_ex, borrow_ex)
    # Daily aggregation
    # daily_state[vault][day] -> {metrics}
    daily_state: dict[str, dict[str, dict]] = defaultdict(dict)
    # Track per-token per-day exchange prices for APY derivation
    daily_ex_prices: dict[str, dict[str, tuple[int, int]]] = {}  # token -> {day -> (supply_ex, borrow_ex)}
    # Track flows per vault per day
    daily_flows: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {
        "supply_inflow": 0, "supply_outflow": 0,
        "borrow_inflow": 0, "borrow_outflow": 0,
        "event_count": 0,
    }))

    for block_num, block_ts, vault_addr, token_addr, w0_hex, w1_hex, w5_hex in rows:
        supply_delta = decode_int256(w0_hex)
        borrow_delta = decode_int256(w1_hex)

        # Extract exchange prices from this event
        if w5_hex and len(w5_hex) >= 64:
            ex_packed = int(w5_hex, 16)
            supply_ex = (ex_packed >> 91) & MASK_64
            borrow_ex = (ex_packed >> 155) & MASK_64
            if supply_ex > 0 or borrow_ex > 0:
                token_ex_prices[token_addr] = (
                    supply_ex if supply_ex > 0 else token_ex_prices.get(token_addr, (PRECISION, PRECISION))[0],
                    borrow_ex if borrow_ex > 0 else token_ex_prices.get(token_addr, (PRECISION, PRECISION))[1],
                )
        else:
            supply_ex, borrow_ex = token_ex_prices.get(token_addr, (PRECISION, PRECISION))

        supply_ex, borrow_ex = token_ex_prices.get(token_addr, (PRECISION, PRECISION))

        # Convert normal deltas to raw shares
        if supply_delta != 0 and supply_ex > 0:
            raw_supply_delta = (supply_delta * PRECISION) // supply_ex
            vault_shares[vault_addr][f"{token_addr}_supply"] += raw_supply_delta
        if borrow_delta != 0 and borrow_ex > 0:
            raw_borrow_delta = (borrow_delta * PRECISION) // borrow_ex
            vault_shares[vault_addr][f"{token_addr}_borrow"] += raw_borrow_delta

        # Track daily flows (in normal token units, will be priced later)
        day_key = str(block_ts)[:10]  # YYYY-MM-DD
        flows = daily_flows[vault_addr][day_key]
        flows["event_count"] += 1
        if supply_delta > 0:
            flows["supply_inflow"] += supply_delta
        elif supply_delta < 0:
            flows["supply_outflow"] += abs(supply_delta)
        if borrow_delta > 0:
            flows["borrow_inflow"] += borrow_delta
        elif borrow_delta < 0:
            flows["borrow_outflow"] += abs(borrow_delta)

        # Snapshot end-of-day state (last event of the day wins)
        daily_state[vault_addr][day_key] = {
            "timestamp": block_ts,
            "token_addr": token_addr,
            "supply_ex": supply_ex,
            "borrow_ex": borrow_ex,
            # Deep copy current shares state
            "shares": {k: v for k, v in vault_shares[vault_addr].items()},
        }

        # Track per-token, per-day exchange prices (last event of day wins)
        if token_addr not in daily_ex_prices:
            daily_ex_prices[token_addr] = {}
        daily_ex_prices[token_addr][day_key] = (supply_ex, borrow_ex)

    log.info("Replay complete. %d vaults with data across %d unique days",
             len(daily_state),
             len(set(d for vault_days in daily_state.values() for d in vault_days)))

    return {
        "daily_state": dict(daily_state),
        "daily_flows": dict(daily_flows),
        "token_ex_prices": dict(token_ex_prices),
        "daily_ex_prices": daily_ex_prices,
        "vault_shares": dict(vault_shares),
    }


def build_timeseries(
    replay_data: dict,
    vault_meta: dict[str, dict],
    chainlink_prices: dict[str, dict[str, float]],
) -> list[dict]:
    """Build the final timeseries rows from replay state."""
    daily_state = replay_data["daily_state"]
    daily_flows = replay_data["daily_flows"]
    token_ex_prices = replay_data["token_ex_prices"]
    daily_ex_prices = replay_data.get("daily_ex_prices", {})

    timeseries_rows = []

    for vault_id, days in sorted(daily_state.items()):
        meta = vault_meta.get(vault_id, {})
        symbol = meta.get("symbol", "UNKNOWN")
        col_token = meta.get("collateral_token", "")
        debt_token = meta.get("debt_token", "")

        # Determine token symbols for pricing
        col_meta = _token_meta(col_token)
        debt_meta = _token_meta(debt_token)
        col_symbol = col_meta[0] if col_meta else ""
        col_decimals = col_meta[1] if col_meta else 18
        debt_symbol = debt_meta[0] if debt_meta else ""
        debt_decimals = debt_meta[1] if debt_meta else 18

        for day_key in sorted(days.keys()):
            state = days[day_key]
            shares = state["shares"]
            supply_ex = state["supply_ex"]
            borrow_ex = state["borrow_ex"]

            # Get exchange prices for both tokens ON THIS DAY
            # Fall back to latest global price if no per-day data
            col_ex = (
                daily_ex_prices.get(col_token, {}).get(day_key)
                or token_ex_prices.get(col_token, (PRECISION, PRECISION))
            )
            debt_ex = (
                daily_ex_prices.get(debt_token, {}).get(day_key)
                or token_ex_prices.get(debt_token, (PRECISION, PRECISION))
            )

            # Compute normal balances from raw shares
            supply_raw = shares.get(f"{col_token}_supply", 0)
            borrow_raw = shares.get(f"{debt_token}_borrow", 0)

            # Use the collateral token's supply exchange price for supply,
            # and debt token's borrow exchange price for borrow
            supply_tokens = (supply_raw * col_ex[0]) / PRECISION / (10 ** col_decimals) if col_decimals else 0
            borrow_tokens = (borrow_raw * debt_ex[1]) / PRECISION / (10 ** debt_decimals) if debt_decimals else 0

            # Price lookup (hourly resolution, use start of day)
            day_ts = f"{day_key} 00:00:00"
            feed_prices = {}
            for feed, price_map in chainlink_prices.items():
                # Find closest price <= day_ts
                closest = None
                for ts_str, price in price_map.items():
                    if ts_str <= day_ts:
                        closest = price
                feed_prices[feed] = closest or 0.0

            col_price = resolve_price(col_symbol, feed_prices) if col_symbol else None
            debt_price = resolve_price(debt_symbol, feed_prices) if debt_symbol else None

            supply_usd = supply_tokens * col_price if col_price else 0.0
            borrow_usd = borrow_tokens * debt_price if debt_price else 0.0

            # Flows
            flows = daily_flows.get(vault_id, {}).get(day_key, {})
            col_scale = 10 ** col_decimals
            debt_scale = 10 ** debt_decimals
            supply_inflow_usd = (flows.get("supply_inflow", 0) / col_scale * col_price) if col_price else 0.0
            supply_outflow_usd = (flows.get("supply_outflow", 0) / col_scale * col_price) if col_price else 0.0
            borrow_inflow_usd = (flows.get("borrow_inflow", 0) / debt_scale * debt_price) if debt_price else 0.0
            borrow_outflow_usd = (flows.get("borrow_outflow", 0) / debt_scale * debt_price) if debt_price else 0.0

            # Utilization
            utilization = borrow_usd / supply_usd if supply_usd > 0 else 0.0

            # APY from exchange price (will be computed at query time as price change)
            ts = datetime.datetime.strptime(day_key, "%Y-%m-%d")
            timeseries_rows.append({
                "timestamp": ts,
                "vault_id": vault_id,
                "symbol": symbol,
                "collateral_token": col_token,
                "debt_token": debt_token,
                "supply_raw_shares": float(supply_raw),
                "borrow_raw_shares": float(borrow_raw),
                "supply_tokens": float(supply_tokens),
                "borrow_tokens": float(borrow_tokens),
                "supply_usd": float(supply_usd),
                "borrow_usd": float(borrow_usd),
                "utilization": float(utilization),
                "supply_ex_price": float(col_ex[0]),
                "borrow_ex_price": float(debt_ex[1]),
                "supply_apy": 0.0,  # backfilled post-insert from fluid_timeseries
                "borrow_apy": 0.0,  # backfilled post-insert from fluid_timeseries
                "supply_inflow_usd": float(supply_inflow_usd),
                "supply_outflow_usd": float(supply_outflow_usd),
                "borrow_inflow_usd": float(borrow_inflow_usd),
                "borrow_outflow_usd": float(borrow_outflow_usd),
                "net_supply_flow_usd": float(supply_inflow_usd - supply_outflow_usd),
                "net_borrow_flow_usd": float(borrow_inflow_usd - borrow_outflow_usd),
                "event_count": flows.get("event_count", 0),
            })

    log.info("Built %d timeseries rows for %d vaults", len(timeseries_rows), len(daily_state))
    return timeseries_rows


def _token_meta(address: str) -> tuple[str, int] | None:
    """Look up token symbol and decimals."""
    meta = TOKENS.get(address.removeprefix("0x").lower())
    if not meta:
        # Common tokens fallback
        FALLBACK = {
            "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": ("ETH", 18),
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
            "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
            "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": ("wstETH", 18),
            "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
            "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
            "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": ("cbBTC", 8),
            "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": ("USDe", 18),
            "0x9d39a5de30e57443bff2a8307a4256c8797a3497": ("sUSDe", 18),
            "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f": ("GHO", 18),
            "0xcd5fe23c85820f7b72d0926fc9b05b43e359b7ee": ("weETH", 18),
        }
        fb = FALLBACK.get(address.lower())
        if fb:
            return fb
        return None
    return str(meta[0]), int(meta[1])


def write_timeseries(ch, rows: list[dict]) -> int:
    """Write timeseries rows to ClickHouse."""
    if not rows:
        return 0
    columns = [
        "timestamp", "vault_id", "symbol", "collateral_token", "debt_token",
        "supply_raw_shares", "borrow_raw_shares", "supply_tokens", "borrow_tokens",
        "supply_usd", "borrow_usd", "utilization",
        "supply_ex_price", "borrow_ex_price", "supply_apy", "borrow_apy",
        "supply_inflow_usd", "supply_outflow_usd",
        "borrow_inflow_usd", "borrow_outflow_usd",
        "net_supply_flow_usd", "net_borrow_flow_usd", "event_count",
    ]
    data = [[row[col] for col in columns] for row in rows]
    ch.insert("fluid_vault_timeseries", data, column_names=columns)
    log.info("Wrote %d rows to fluid_vault_timeseries", len(data))
    return len(data)


def backfill_apy(ch) -> None:
    """
    Backfill supply_apy and borrow_apy into fluid_vault_timeseries by joining
    against per-token daily APY from fluid_timeseries.

    For each vault row:
      supply_apy = collateral_token's avg daily supply_apy
      borrow_apy = debt_token's avg daily borrow_apy
    """
    log.info("Backfilling APY from per-token timeseries...")

    # Build lookup: (token, day_str) -> (supply_apy, borrow_apy)
    apy_rows = ch.query("""
        SELECT entity_id, toStartOfDay(timestamp) AS day,
               avg(supply_apy) AS avg_supply_apy,
               avg(borrow_apy) AS avg_borrow_apy
        FROM fluid_timeseries
        WHERE protocol = 'FLUID_MARKET'
        GROUP BY entity_id, day
    """).result_rows

    token_daily_apy: dict[tuple[str, str], tuple[float, float]] = {}
    for entity_id, day, s_apy, b_apy in apy_rows:
        token_daily_apy[(str(entity_id).lower(), str(day)[:10])] = (
            float(s_apy or 0), float(b_apy or 0),
        )
    log.info("Loaded %d token-day APY entries", len(token_daily_apy))

    # Read all vault timeseries rows
    columns = [
        "timestamp", "vault_id", "symbol", "collateral_token", "debt_token",
        "supply_raw_shares", "borrow_raw_shares", "supply_tokens", "borrow_tokens",
        "supply_usd", "borrow_usd", "utilization",
        "supply_ex_price", "borrow_ex_price", "supply_apy", "borrow_apy",
        "supply_inflow_usd", "supply_outflow_usd",
        "borrow_inflow_usd", "borrow_outflow_usd",
        "net_supply_flow_usd", "net_borrow_flow_usd", "event_count",
    ]
    vault_rows = ch.query(f"""
        SELECT {', '.join(columns)}
        FROM fluid_vault_timeseries FINAL
        ORDER BY vault_id, timestamp
    """).result_rows

    matched = 0
    updated_data = []
    for row in vault_rows:
        row_list = list(row)
        day_str = str(row_list[0])[:10]
        col_token = str(row_list[3]).lower()
        debt_token = str(row_list[4]).lower()

        col_apy = token_daily_apy.get((col_token, day_str))
        debt_apy = token_daily_apy.get((debt_token, day_str))

        if col_apy:
            row_list[14] = col_apy[0]  # supply_apy
            matched += 1
        if debt_apy:
            row_list[15] = debt_apy[1]  # borrow_apy

        updated_data.append(row_list)

    ch.insert("fluid_vault_timeseries", updated_data, column_names=columns)
    log.info("APY backfill complete: %d/%d rows matched", matched, len(updated_data))


def main():
    ch = get_ch()
    ensure_table(ch)

    # 1. Load vault registry
    vaults = load_vault_registry(ch)
    if not vaults:
        log.error("No vaults found in registry")
        sys.exit(1)

    vault_addrs = set(vaults.keys())

    # 2. Load vault metadata
    vault_meta = load_vault_metadata(ch, vault_addrs)

    # 3. Load Chainlink prices
    chainlink_prices = load_chainlink_prices(ch)

    # 4. Replay events
    replay_data = replay_events(ch, vault_addrs)

    # 5. Build timeseries
    rows = build_timeseries(replay_data, vault_meta, chainlink_prices)

    # 6. Filter to vaults with meaningful TVL
    meaningful = [r for r in rows if abs(r["supply_usd"]) > 100 or abs(r["borrow_usd"]) > 100]
    log.info("Filtered from %d to %d rows with TVL > $100", len(rows), len(meaningful))

    # 7. Write to ClickHouse
    if meaningful:
        written = write_timeseries(ch, meaningful)
        log.info("REPLAY COMPLETE: %d rows written for %d vaults",
                 written, len(set(r["vault_id"] for r in meaningful)))
    else:
        log.warning("No meaningful rows to write")

    # 8. Summary stats
    if meaningful:
        unique_vaults = set(r["vault_id"] for r in meaningful)
        min_date = min(r["timestamp"] for r in meaningful)
        max_date = max(r["timestamp"] for r in meaningful)
        total_supply = sum(r["supply_usd"] for r in meaningful if r["timestamp"] == max_date)
        total_borrow = sum(r["borrow_usd"] for r in meaningful if r["timestamp"] == max_date)
        log.info("Summary: %d vaults, %s to %s, latest day supply=$%.0f borrow=$%.0f",
                 len(unique_vaults), min_date, max_date, total_supply, total_borrow)

    # 9. Backfill APY from per-token timeseries
    if meaningful:
        backfill_apy(ch)


if __name__ == "__main__":
    main()
