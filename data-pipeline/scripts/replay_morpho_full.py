#!/usr/bin/env python3
"""
Full replay of ALL Morpho Blue events from genesis to rebuild
unified_timeseries for MORPHO_MARKET, MORPHO_ALLOCATION, MORPHO_VAULT.

Reads 3.5M+ raw events from morpho_events in ClickHouse,
applies cumulative state tracking, emits hourly snapshots.

Usage:
    cd /home/ubuntu/RLD/data-pipeline
    .venv/bin/python scripts/replay_morpho_full.py
"""

import os, sys, math, logging, time
from datetime import datetime, timedelta
from collections import defaultdict

import json
import pandas as pd
import clickhouse_connect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from indexer.tokens import SYM_DECIMALS, get_usd_price, get_chainlink_prices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("replay")

# ── Constants ────────────────────────────────────────────────
WAD = 10**18
SECONDS_PER_YEAR = 365.25 * 86400

TOPIC_ACCRUE    = "0x9d9bd501d0657d7dfe415f779a620a62b78bc508ddc0891fbbd8b7ac0f8fce87"
TOPIC_SUPPLY    = "0xedf8870433c83823eb071d3df1caa8d008f12f6440918c20d75a3602cda30fe0"
TOPIC_WITHDRAW  = "0xa56fc0ad5702ec05ce63666221f796fb62437c32db1aa1aa075fc6484cf58fbf"
TOPIC_BORROW    = "0x570954540bed6b1304a87dfe815a5eda4a648f7097a16240dcd85c9b5fd42a43"
TOPIC_REPAY     = "0x52acb05cebbd3cd39715469f22afbf5a17496295ef3bc9bb5944056c63ccaa09"
TOPIC_LIQUIDATE = "0xa4946ede45d0c6f06a0f5ce92c9ad3b4751452d2fe0e25010783bcab57a67e41"
TOPIC_SETFEE    = "0xd5e969f01efe921d3f766bdebad25f0a05e3f237311f56482bf132d0326309c0"

TOPICS_IN = (
    f"'{TOPIC_ACCRUE}','{TOPIC_SUPPLY}','{TOPIC_WITHDRAW}',"
    f"'{TOPIC_BORROW}','{TOPIC_REPAY}','{TOPIC_LIQUIDATE}',"
    f"'{TOPIC_SETFEE}'"
)

CH_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CH_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))

# Process 1 day of blocks at a time (~7200 blocks)
BLOCK_CHUNK = 50_000


class MarketState:
    __slots__ = ("total_supply", "total_borrow", "fee_wad",
                 "last_supply_apy", "last_borrow_apy",
                 "last_util", "symbol", "decimals")

    def __init__(self):
        self.total_supply = 0
        self.total_borrow = 0
        self.fee_wad = 0
        self.last_supply_apy = 0.0
        self.last_borrow_apy = 0.0
        self.last_util = 0.0
        self.symbol = ""
        self.decimals = 18


def main():
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT)
    t0 = time.time()

    # ── 1. Load market metadata ──────────────────────────────
    params = ch.query_df(
        "SELECT lower(market_id) AS market_id, loan_symbol, collateral_symbol "
        "FROM morpho_market_params"
    )
    market_symbols: dict[str, str] = {}
    market_collateral: dict[str, str] = {}
    for _, row in params.iterrows():
        mid = row["market_id"].lower()
        market_symbols[mid] = row["loan_symbol"]
        market_collateral[mid] = row["collateral_symbol"]
    log.info(f"Loaded {len(market_symbols)} market params")

    # ── 2. Load vault metadata ───────────────────────────────
    vm = ch.query_df("SELECT vault_address, asset_symbol FROM morpho_vault_meta")
    vault_addrs = set()
    vault_meta: dict[str, str] = {}
    for _, row in vm.iterrows():
        va = row["vault_address"].lower()
        vault_addrs.add(va)
        vault_meta[va] = row["asset_symbol"]
    log.info(f"Loaded {len(vault_addrs)} vault addresses")

    # ── 3. Get block range ───────────────────────────────────
    block_range = ch.query_df(f"""
        SELECT min(block_number) AS min_b, max(block_number) AS max_b,
               count() AS total
        FROM morpho_events
        WHERE topic0 IN ({TOPICS_IN})
    """)
    min_block = int(block_range["min_b"].iloc[0])
    max_block = int(block_range["max_b"].iloc[0])
    total_events = int(block_range["total"].iloc[0])
    log.info(f"Block range: {min_block:,} → {max_block:,} ({total_events:,} events)")

    # ── 4. Load historical prices ─────────────────────────────
    markets: dict[str, MarketState] = defaultdict(MarketState)
    vault_positions: dict[tuple[str, str], int] = {}

    # Build hourly price lookup from ALL chainlink feeds
    price_df = ch.query_df("""
        SELECT toStartOfHour(timestamp) AS hour,
               feed,
               avg(price) AS price
        FROM chainlink_prices
        GROUP BY hour, feed
        ORDER BY hour
    """)
    # hourly_feeds[feed_name][hour] = price
    hourly_feeds: dict[str, dict] = defaultdict(dict)
    for _, pr in price_df.iterrows():
        hourly_feeds[pr["feed"]][pr["hour"]] = pr["price"]

    feed_names = sorted(hourly_feeds.keys())
    log.info(f"Loaded {len(price_df)} hourly prices across {len(feed_names)} feeds")

    # Fallback to latest for current hour
    latest_eth, latest_btc = get_chainlink_prices(ch)
    log.info(f"Latest: ETH=${latest_eth:.0f}, BTC=${latest_btc:.0f}")

    # Get latest value for each feed as fallback
    latest_feed_prices: dict[str, float] = {}
    for feed_name, hours_dict in hourly_feeds.items():
        if hours_dict:
            latest_hour = max(hours_dict.keys())
            latest_feed_prices[feed_name] = hours_dict[latest_hour]

    # ── Load per-market oracle compositions ──────────────────
    oracle_comps: dict = {}
    try:
        with open("/tmp/oracle_compositions.json") as f:
            oracle_comps = json.load(f)
        log.info(f"Loaded oracle compositions for {len(oracle_comps)} markets")
    except FileNotFoundError:
        log.warning("No oracle_compositions.json — falling back to symbol-based pricing")

    def _get_feed_answer(feed_name: str, decimals: int, hour_prices: dict) -> float | None:
        """Get raw feed answer (as float in native units) for a given feed at a given hour."""
        price = hour_prices.get(feed_name)
        if price is not None:
            return price * (10 ** decimals)  # Convert back to raw answer
        return None

    def _compute_oracle_price(market_id: str, hour_prices: dict) -> float | None:
        """Reconstruct oracle price() for a market from its feed composition.
        Returns the raw oracle price (same as on-chain price() return value)."""
        comp = oracle_comps.get(market_id)
        if not comp:
            return None

        scale = comp["scale"]
        base_product = 1.0
        quote_product = 1.0

        for feed in comp["feeds"]:
            feed_name = feed["feed"]
            dec = feed["decimals"]
            answer = _get_feed_answer(feed_name, dec, hour_prices)
            if answer is None:
                return None
            if "BASE" in feed["slot"]:
                base_product *= answer
            else:
                quote_product *= answer

        if quote_product == 0:
            return None

        return scale * base_product / quote_product

    def _get_loan_usd_price(market_id: str, loan_sym: str,
                            loan_dec: int, hour_prices: dict) -> float:
        """Get the precise USD price of 1 unit of the loan token.

        Strategy: reconstruct the oracle price and derive loan USD price
        from the oracle's feed composition.  The oracle gives
        collateral_USD / loan_USD, so if we know both feeds we can
        extract loan_USD directly from the quote feeds.
        
        For most markets the quote feed IS the loan token's USD feed."""
        comp = oracle_comps.get(market_id)
        if comp:
            # The quote feeds in the oracle represent the loan token pricing.
            # For a market with QUOTE_FEED_1 = 'USDC / USD', the loan price
            # is simply that feed's value.
            # For ETH-denominated loans with no quote feed, the oracle
            # embeds ETH/USD in the base feeds, so loan price = 1.0 ETH.
            quote_feeds = [f for f in comp["feeds"] if "QUOTE" in f["slot"]]
            if quote_feeds:
                # Compute loan_price from quote feeds.
                # Quote feeds can be in different denominations:
                #   USDC / USD -> gives USD price directly
                #   USDT / ETH -> gives ETH price, needs * ETH/USD
                loan_val = 1.0
                all_found = True
                needs_eth_multiply = False
                needs_btc_multiply = False
                for qf in quote_feeds:
                    p = hour_prices.get(qf["feed"])
                    if p is None:
                        all_found = False
                        break
                    loan_val *= p
                    fname = qf["feed"]
                    if "/ ETH" in fname or "/ETH" in fname:
                        needs_eth_multiply = True
                    elif "/ BTC" in fname or "/BTC" in fname:
                        needs_btc_multiply = True
                if all_found:
                    if needs_eth_multiply:
                        loan_val *= hour_prices.get("ETH / USD", latest_eth)
                    if needs_btc_multiply:
                        loan_val *= hour_prices.get("BTC / USD", latest_btc)
                    return loan_val
            else:
                # No quote feeds: the oracle output denomination is
                # determined by the LAST base feed in the chain.
                # e.g. WBTC/BTC × BTC/USD → output in USD → loan=$1
                #      weETH/ETH (single) → output in ETH → loan=ETH/USD
                base_feeds = [f for f in comp["feeds"] if "BASE" in f["slot"]]
                if base_feeds:
                    # Sort by slot number to get the final feed
                    last_feed = sorted(base_feeds, key=lambda x: x["slot"])[-1]
                    fname = last_feed["feed"]
                    # Determine the output currency from the last feed's denominator
                    if "/ ETH" in fname or "/ETH" in fname:
                        return hour_prices.get("ETH / USD", latest_eth)
                    if "/ BTC" in fname or "/BTC" in fname:
                        return hour_prices.get("BTC / USD", latest_btc)
                # Default: oracle output is in USD terms → loan = $1
                return 1.0

        # Fallback: symbol-based pricing
        ep = hour_prices.get("ETH / USD", latest_eth)
        bp = hour_prices.get("BTC / USD", latest_btc)
        return get_usd_price(loan_sym, ep, bp, extra_prices=hour_prices)

    def _get_prices_for_hour(h) -> tuple[float, float, dict[str, float]]:
        """Get ETH/BTC + all feed prices for a given hour, with forward-fill."""
        extra: dict[str, float] = {}
        
        for feed_name, hours_dict in hourly_feeds.items():
            price = hours_dict.get(h)
            if price is None:
                # Forward-fill from closest prior hour
                for delta in range(1, 25):
                    prev = h - timedelta(hours=delta)
                    price = hours_dict.get(prev)
                    if price is not None:
                        break
                if price is None:
                    price = latest_feed_prices.get(feed_name)
            if price is not None:
                extra[feed_name] = price
                
        ep = extra.get("ETH / USD", latest_eth)
        bp = extra.get("BTC / USD", latest_btc)
        
        # Pure mathematically bound static pegs
        extra["USR Price AggregatorV3 interface"] = 1.0
        extra["Dummy feed with 12 decimals"] = 1.0
        
        # Wrapped Synthetic Deterministic Overrides
        extra["ETH+/USD exchange rate adapter"] = ep
        extra["Syrup USDC convertToAsset adapter"] = 1.0
        extra["Ojo Yield Risk Engine SyrupUSDC Exchange Rate"] = 1.0
        
        return ep, bp, extra

    row_buf: list[dict] = []  # streaming buffer
    total_written = 0
    FLUSH_SIZE = 300_000  # flush to CH every N rows
    current_hour = None
    events_processed = 0
    cursor = min_block

    def flush_buffer():
        nonlocal row_buf, total_written
        if not row_buf:
            return
        # Convert raw amounts to USD using per-market oracle composition
        for r in row_buf:
            mid = r["entity_id"]
            sym = r["symbol"]
            dec = SYM_DECIMALS.get(sym, 18)
            ep = r.pop("_eth_price")
            bp = r.pop("_btc_price")
            xp = r.pop("_extra_prices")
            
            # Use oracle-derived loan token price
            tp = _get_loan_usd_price(mid, sym, dec, xp)
            r["supply_usd"] = r.pop("total_supply_raw") / (10 ** dec) * tp
            r["borrow_usd"] = r.pop("total_borrow_raw") / (10 ** dec) * tp
            r["price_usd"] = tp

        flush_df = pd.DataFrame(row_buf)
        flush_df = flush_df[flush_df["borrow_usd"] > 0]
        if len(flush_df) > 0:
            ch.insert_df("unified_timeseries", flush_df)
            total_written += len(flush_df)
            log.info(f"    ⤷ Flushed {len(flush_df):,} rows (total: {total_written:,})")
        row_buf = []

    while cursor <= max_block:
        chunk_end = min(cursor + BLOCK_CHUNK - 1, max_block)

        batch = ch.query_df(f"""
            SELECT block_number, block_timestamp, topic0, topic1,
                   topic3, data
            FROM morpho_events
            WHERE topic0 IN ({TOPICS_IN})
              AND block_number >= {cursor} AND block_number <= {chunk_end}
            ORDER BY block_number, log_index
        """)

        for _, ev in batch.iterrows():
            topic0 = ev["topic0"]
            topic1 = str(ev["topic1"] or "").lower()
            t3 = ev["topic3"]
            topic3 = str(t3) if pd.notna(t3) else ""
            raw = (ev["data"] or "").replace("0x", "")
            ts = ev["block_timestamp"]
            market_id = topic1

            if not market_id:
                continue

            state = markets[market_id]

            # Set symbol if known
            if not state.symbol and market_id in market_symbols:
                state.symbol = market_symbols[market_id]
                state.decimals = SYM_DECIMALS.get(state.symbol, 18)

            # ── Hour boundary: emit snapshots ────────────────
            if hasattr(ts, 'replace'):
                ev_hour = ts.replace(minute=0, second=0, microsecond=0)
            else:
                ev_hour = pd.Timestamp(ts).replace(minute=0, second=0, microsecond=0)

            if current_hour is not None and ev_hour > current_hour:
                ep, bp, xp = _get_prices_for_hour(current_hour)
                _emit_hourly(markets, current_hour, row_buf, ep, bp, xp)
                # Skip forward-fill for every intermediate hour — just move to current
                current_hour = ev_hour
                if len(row_buf) >= FLUSH_SIZE:
                    flush_buffer()
            elif current_hour is None:
                current_hour = ev_hour

            # ── Apply event to state ─────────────────────────
            try:
                if topic0 == TOPIC_ACCRUE and len(raw) >= 192:
                    prev_rate = int(raw[0:64], 16)
                    interest = int(raw[64:128], 16)
                    state.total_supply += interest
                    state.total_borrow += interest
                    # Cap rate to prevent math.exp overflow (max ~1000% APY)
                    annual_rate = prev_rate / WAD * SECONDS_PER_YEAR
                    if annual_rate < 10:  # < ~22,000% APY
                        borrow_apy = math.exp(annual_rate) - 1.0
                    else:
                        borrow_apy = 0.0  # skip malformed rate
                    util = state.total_borrow / state.total_supply if state.total_supply > 0 else 0.0
                    fee_frac = state.fee_wad / WAD if state.fee_wad > 0 else 0.0
                    state.last_borrow_apy = borrow_apy
                    state.last_supply_apy = borrow_apy * util * (1.0 - fee_frac)
                    state.last_util = util

                elif topic0 == TOPIC_SUPPLY and len(raw) >= 128:
                    assets = int(raw[0:64], 16)
                    shares = int(raw[64:128], 16)
                    state.total_supply += assets
                    if vault_addrs and len(topic3) >= 40:
                        va = "0x" + topic3[-40:].lower()
                        if va in vault_addrs:
                            key = (va, market_id)
                            vault_positions[key] = vault_positions.get(key, 0) + shares

                elif topic0 == TOPIC_WITHDRAW and len(raw) >= 192:
                    assets = int(raw[64:128], 16)
                    shares = int(raw[128:192], 16)
                    state.total_supply -= assets
                    if vault_addrs and len(topic3) >= 40:
                        va = "0x" + topic3[-40:].lower()
                        if va in vault_addrs:
                            key = (va, market_id)
                            vault_positions[key] = max(0, vault_positions.get(key, 0) - shares)

                elif topic0 == TOPIC_BORROW and len(raw) >= 192:
                    assets = int(raw[64:128], 16)
                    state.total_borrow += assets

                elif topic0 == TOPIC_REPAY and len(raw) >= 128:
                    assets = int(raw[0:64], 16)
                    state.total_borrow -= assets

                elif topic0 == TOPIC_LIQUIDATE and len(raw) >= 320:
                    repaid = int(raw[0:64], 16)
                    bad_debt = int(raw[192:256], 16)
                    state.total_borrow -= repaid
                    state.total_borrow -= bad_debt

                elif topic0 == TOPIC_SETFEE and len(raw) >= 64:
                    state.fee_wad = int(raw[0:64], 16)

            except (ValueError, OverflowError):
                pass

            events_processed += 1

        cursor = chunk_end + 1
        elapsed = time.time() - t0
        rate = events_processed / elapsed if elapsed > 0 else 0
        pct = events_processed / total_events * 100 if total_events > 0 else 0
        log.info(
            f"  Block {chunk_end:,} • {events_processed:,}/{total_events:,} "
            f"({pct:.1f}%) • {rate:,.0f} ev/s • "
            f"{len(markets)} mkts • buf={len(row_buf):,}"
        )

    # Emit final hour and flush
    if current_hour is not None:
        ep, bp, xp = _get_prices_for_hour(current_hour)
        _emit_hourly(markets, current_hour, row_buf, ep, bp, xp)
    flush_buffer()

    log.info(f"Replay done in {time.time()-t0:.0f}s: {total_written:,} rows written")

    # ── 5. Delete old data before inserting (already flushed above) ─
    # Data was streamed in during replay, we'll deduplicate below

    # ── 6. Vault allocations at latest hour ──────────────────
    latest_ts = current_hour
    if vault_positions and latest_ts:
        alloc_rows = []
        vault_totals: dict[str, float] = {}

        for (va, mid), supply_shares in vault_positions.items():
            if supply_shares <= 0:
                continue
            mstate = markets.get(mid)
            if not mstate or mstate.total_supply <= 0:
                continue

            supply_assets = supply_shares
            sym = mstate.symbol or market_symbols.get(mid, "UNKNOWN")
            decimals = SYM_DECIMALS.get(sym, 18)
            tp = get_usd_price(sym, latest_eth, latest_btc, extra_prices=latest_feed_prices)
            supply_usd = supply_assets / (10 ** decimals) * tp
            if supply_usd < 1.0:
                continue

            share_pct = supply_assets / mstate.total_supply if mstate.total_supply > 0 else 0.0
            vault_totals[va] = vault_totals.get(va, 0.0) + supply_usd
            v_sym = vault_meta.get(va, sym).upper()

            alloc_rows.append({
                "timestamp": latest_ts, "protocol": "MORPHO_ALLOCATION",
                "symbol": v_sym, "entity_id": va, "target_id": mid,
                "supply_usd": supply_usd, "borrow_usd": 0.0,
                "supply_apy": 0.0, "borrow_apy": 0.0,
                "utilization": share_pct, "price_usd": 0.0,
            })

        if alloc_rows:
            alloc_df = pd.DataFrame(alloc_rows)
            ts_str = latest_ts.strftime("%Y-%m-%d %H:%M:%S")
            ch.command(
                f"ALTER TABLE unified_timeseries DELETE "
                f"WHERE protocol='MORPHO_ALLOCATION' AND timestamp = '{ts_str}'"
            )
            ch.insert_df("unified_timeseries", alloc_df)
            log.info(f"Inserted {len(alloc_df)} allocation rows")

        vault_rows = []
        for va, tvl_usd in vault_totals.items():
            if tvl_usd <= 0:
                continue
            v_sym = vault_meta.get(va, "UNKNOWN").upper()
            vault_rows.append({
                "timestamp": latest_ts, "protocol": "MORPHO_VAULT",
                "symbol": v_sym, "entity_id": va, "target_id": "",
                "supply_usd": tvl_usd, "borrow_usd": 0.0,
                "supply_apy": 0.0, "borrow_apy": 0.0,
                "utilization": 0.0, "price_usd": 0.0,
            })
        if vault_rows:
            vault_df = pd.DataFrame(vault_rows)
            ts_str = latest_ts.strftime("%Y-%m-%d %H:%M:%S")
            ch.command(
                f"ALTER TABLE unified_timeseries DELETE "
                f"WHERE protocol='MORPHO_VAULT' AND timestamp = '{ts_str}'"
            )
            ch.insert_df("unified_timeseries", vault_df)
            log.info(f"Inserted {len(vault_df)} vault rows")

    # ── 7. Summary ───────────────────────────────────────────
    elapsed = time.time() - t0
    log.info(f"\n{'='*60}")
    log.info(f"✅ Full replay: {events_processed:,} events in {elapsed:.0f}s")
    log.info(f"   Markets: {len(markets)}, Rows written: {total_written:,}")

    log.info(f"\nTop 15 markets by supply:")
    for mid, st in sorted(markets.items(),
                          key=lambda x: x[1].total_supply, reverse=True)[:15]:
        sym = st.symbol or "?"
        coll = market_collateral.get(mid, "?")
        dec = st.decimals
        tp = get_usd_price(sym, latest_eth, latest_btc, extra_prices=latest_feed_prices)
        supp = st.total_supply / (10 ** dec) * tp
        borr = st.total_borrow / (10 ** dec) * tp
        log.info(
            f"  {coll:>12s}/{sym:<6s} "
            f"supply=${supp/1e6:>8.1f}M  borrow=${borr/1e6:>8.1f}M  "
            f"rate={st.last_borrow_apy*100:.2f}%"
        )

    ch.close()


# Cached Historical Pricing from pendle_backfill Sparse RPC
RAW_ORACLES = None

def _get_prices_for_hour(ch, hour: datetime, extra: dict, last_price: dict,
                 eth_price: float = 2000.0, btc_price: float = 70000.0,
                 extra_prices: dict[str, float] | None = None) -> dict[str, float]:
    
    global RAW_ORACLES
    if RAW_ORACLES is None:
        try:
            res = ch.query("SELECT block_number, market_id, oracle_price FROM morpho_oracle_historical")
            RAW_ORACLES = {}
            for b, m_id, op in res.result_rows:
                if m_id not in RAW_ORACLES:
                    RAW_ORACLES[m_id] = []
                RAW_ORACLES[m_id].append((b, op))
            for k in RAW_ORACLES:
                RAW_ORACLES[k].sort(key=lambda x: x[0])
        except BaseException as e:
            print("Failed to load historical raw oracles", e)
            RAW_ORACLES = {}
            
    # Try fetching forward-filled raw oracle price
    # NOTE: since we only have blocks, we use block_number interpolation later or 
    # just rely on the fallback structure if we override the USD price calculation below.


def _emit_hourly(markets: dict[str, MarketState], hour, rows: list,
                 eth_price: float = 2000.0, btc_price: float = 70000.0,
                 extra_prices: dict[str, float] | None = None):
    """Snapshot all active markets at the given hour with historical prices."""
    for mid, st in markets.items():
        if st.total_borrow <= 0 and st.total_supply <= 0:
            continue
        if st.last_borrow_apy == 0.0 and st.last_supply_apy == 0.0:
            continue
        rows.append({
            "timestamp": hour,
            "protocol": "MORPHO_MARKET",
            "symbol": st.symbol or mid[:20],
            "entity_id": mid,
            "target_id": "",
            "total_supply_raw": st.total_supply,
            "total_borrow_raw": st.total_borrow,
            "supply_apy": st.last_supply_apy,
            "borrow_apy": st.last_borrow_apy,
            "utilization": st.last_util,
            "_eth_price": eth_price,
            "_btc_price": btc_price,
            "_extra_prices": extra_prices or {},
        })


if __name__ == "__main__":
    main()
