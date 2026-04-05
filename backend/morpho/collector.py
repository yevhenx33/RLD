"""Morpho Blue Indexer — Snapshot Collector."""
import time, math, logging
from morpho.db import get_conn, get_tracked_markets, get_tracked_vaults, set_sync_value
from morpho.rpc import (
    multicall_market_states, multicall_positions, multicall_vault_states,
    multicall_oracle_prices, multicall_irm_rates, eth_block_number, eth_get_block,
)

log = logging.getLogger(__name__)

SECONDS_PER_YEAR = 365.25 * 24 * 3600
WAD = 10**18

def collect_snapshot(block_number=None):
    """Collect a full snapshot at given block (or latest)."""
    markets = get_tracked_markets()
    vaults = get_tracked_vaults()
    if not markets:
        log.warning("No markets tracked — run discovery first")
        return

    market_ids = [m["market_id"] for m in markets]
    vault_addrs = [v["vault_address"] for v in vaults]
    oracles = list(set(m["oracle"] for m in markets if m.get("oracle")))

    # Resolve block
    if block_number is None:
        block_number = eth_block_number()
    block_data = eth_get_block(block_number)
    block_ts = int(block_data["timestamp"], 16)
    # Round to nearest hour
    snap_ts = (block_ts // 3600) * 3600

    log.info(f"Collecting snapshot at block {block_number} (ts={snap_ts})")

    # 1. Market states
    log.info(f"  Fetching {len(market_ids)} market states...")
    mkt_states = multicall_market_states(market_ids, block_number)

    # 2. Oracle prices
    log.info(f"  Fetching {len(oracles)} oracle prices...")
    oracle_prices = multicall_oracle_prices(oracles, block_number)

    # 3. IRM rates
    log.info(f"  Fetching IRM rateAtTarget...")
    irm_rates = multicall_irm_rates(market_ids, block_number)

    # 4. Vault states (totalAssets, totalSupply)
    log.info(f"  Fetching {len(vault_addrs)} vault states...")
    vault_states = multicall_vault_states(vault_addrs, block_number)

    # 5. Vault positions (supply shares per market)
    log.info(f"  Fetching vault positions ({len(market_ids)}×{len(vault_addrs)} pairs)...")
    positions = multicall_positions(market_ids, vault_addrs, block_number)

    # Build oracle lookup: market_id -> oracle_address
    oracle_map = {m["market_id"]: m["oracle"] for m in markets}

    # ─── Store ────────────────────────────────────────────────
    with get_conn() as conn:
        # Market snapshots
        for mid in market_ids:
            ms = mkt_states.get(mid)
            if not ms:
                continue
            supply = ms["totalSupplyAssets"]
            borrow = ms["totalBorrowAssets"]
            util = borrow / supply if supply > 0 else 0

            # Compute APY from IRM rate
            rate = irm_rates.get(mid)
            borrow_apy = None
            supply_apy = None
            if rate is not None and rate > 0:
                # rate is per-second, scale to APY
                # borrowRate varies with utilization via AdaptiveCurve
                # rateAtTarget is the base; actual rate = f(util, rateAtTarget)
                # For now store rateAtTarget-derived APY as approximation
                borrow_apy = (math.exp(rate / WAD * SECONDS_PER_YEAR) - 1) * 100
                fee_pct = ms["fee"] / WAD if ms["fee"] else 0
                supply_apy = borrow_apy * util * (1 - fee_pct) if borrow_apy else None

            oracle_addr = oracle_map.get(mid)
            oracle_p = oracle_prices.get(oracle_addr) if oracle_addr else None

            conn.execute("""
                INSERT OR REPLACE INTO market_snapshots
                (timestamp, block_number, market_id,
                 total_supply_assets, total_borrow_assets,
                 total_supply_shares, total_borrow_shares,
                 last_update, fee, utilization, borrow_apy, supply_apy,
                 oracle_price, rate_at_target)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (snap_ts, block_number, mid,
                  str(supply), str(borrow),
                  str(ms["totalSupplyShares"]), str(ms["totalBorrowShares"]),
                  ms["lastUpdate"], ms["fee"],
                  util, borrow_apy, supply_apy,
                  str(oracle_p) if oracle_p else None,
                  str(rate) if rate else None))

        # Vault snapshots
        for va in vault_addrs:
            vs = vault_states.get(va)
            if not vs:
                continue
            ta = vs["totalAssets"]
            ts_shares = vs["totalSupply"]
            sp = ta / ts_shares if ts_shares > 0 else 0
            conn.execute("""
                INSERT OR REPLACE INTO vault_snapshots
                (timestamp, block_number, vault_address,
                 total_assets, total_supply, share_price, total_assets_usd)
                VALUES (?,?,?,?,?,?,?)
            """, (snap_ts, block_number, va,
                  str(ta), str(ts_shares), sp, None))

        # Vault allocations (only non-zero positions)
        for (mid, va), supply_shares in positions.items():
            ms = mkt_states.get(mid)
            if not ms or ms["totalSupplyShares"] == 0:
                continue
            # Convert shares to assets
            supply_assets = supply_shares * ms["totalSupplyAssets"] // ms["totalSupplyShares"]
            total_supply = ms["totalSupplyAssets"]
            share_pct = supply_assets / total_supply if total_supply > 0 else 0
            conn.execute("""
                INSERT OR REPLACE INTO vault_allocations
                (timestamp, vault_address, market_id,
                 supply_shares, supply_assets, supply_usd, share_pct)
                VALUES (?,?,?,?,?,?,?)
            """, (snap_ts, va, mid,
                  str(supply_shares), str(supply_assets), None, share_pct))

        conn.execute("INSERT OR REPLACE INTO sync_state (key,value) VALUES (?,?)",
                     ("last_snapshot_ts", str(snap_ts)))
        conn.execute("INSERT OR REPLACE INTO sync_state (key,value) VALUES (?,?)",
                     ("last_snapshot_block", str(block_number)))

    n_allocs = len(positions)
    log.info(f"  Stored: {len(mkt_states)} markets, {len(vault_states)} vaults, {n_allocs} allocations")
    return snap_ts
