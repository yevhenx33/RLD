"""
Morpho DB Enrichment Engine.

Post-processes the existing morpho.db to fill computed fields:
  1. borrow_apy / supply_apy  — from stored rate_at_target + utilization via IRM curve
  2. share_pct               — vault supply_assets / market total_supply_assets
  3. regime                  — utilization regime classification
  4. market_regime_runs      — consecutive time-in-regime detection

Usage:
    python3 -m backend.morpho.enrich [--db-path PATH] [--step STEP_NAME]

Steps can be run individually or all at once (default).
"""

from __future__ import annotations

import os
import sys
import logging
import sqlite3
import argparse
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.morpho.irm import (
    WAD,
    INITIAL_RATE_AT_TARGET,
    compute_borrow_rate, borrow_rate_to_apy, compute_supply_apy,
    classify_utilization_regime, compute_full_apy,
    evolve_rate_at_target,
)
from backend.morpho.config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("enrich")


# ═══════════════════════════════════════════════════════════════════
#  Schema Migration
# ═══════════════════════════════════════════════════════════════════

MIGRATION_SQL = """
-- Add regime column if missing
CREATE TABLE IF NOT EXISTS market_regime_runs (
    market_id TEXT NOT NULL,
    regime TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    duration_hours REAL NOT NULL,
    PRIMARY KEY (market_id, start_ts)
);
"""


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply schema migrations for enrichment columns and tables."""
    conn.executescript(MIGRATION_SQL)

    # Add 'regime' column to market_snapshots if not present
    cols = {row[1] for row in conn.execute("PRAGMA table_info(market_snapshots)")}
    if "regime" not in cols:
        log.info("Adding 'regime' column to market_snapshots")
        conn.execute("ALTER TABLE market_snapshots ADD COLUMN regime TEXT")
    conn.commit()


# ═══════════════════════════════════════════════════════════════════
#  1. APY Enrichment (from stored rate_at_target values)
# ═══════════════════════════════════════════════════════════════════

def enrich_apy_from_stored_rates(conn: sqlite3.Connection) -> int:
    """Compute borrow_apy and supply_apy for snapshots that have rate_at_target set.

    Uses the faithful AdaptiveCurveIRM curve function to derive the actual
    borrow rate from (rate_at_target, utilization), then compounds to APY.

    Returns: number of rows updated.
    """
    cursor = conn.execute("""
        SELECT rowid, utilization, fee, rate_at_target
        FROM market_snapshots
        WHERE rate_at_target IS NOT NULL
          AND rate_at_target != '0'
          AND rate_at_target != ''
          AND (borrow_apy IS NULL OR borrow_apy = 0)
    """)

    batch = []
    updated = 0

    for row in cursor:
        rowid = row[0]
        utilization = row[1]
        fee_raw = row[2] or 0
        rate_str = row[3]

        try:
            rate_at_target = int(rate_str)
        except (ValueError, TypeError):
            continue

        if rate_at_target <= 0:
            continue

        # Convert utilization float (0-1) → WAD int
        util_wad = int(utilization * WAD)
        fee_wad = fee_raw  # Already WAD-scaled int from chain

        borrow_apy, supply_apy = compute_full_apy(rate_at_target, util_wad, fee_wad)

        if borrow_apy is not None:
            batch.append((borrow_apy, supply_apy, rowid))
            updated += 1

        if len(batch) >= 5000:
            conn.executemany(
                "UPDATE market_snapshots SET borrow_apy=?, supply_apy=? WHERE rowid=?",
                batch,
            )
            conn.commit()
            batch = []

    if batch:
        conn.executemany(
            "UPDATE market_snapshots SET borrow_apy=?, supply_apy=? WHERE rowid=?",
            batch,
        )
        conn.commit()

    log.info(f"enrich_apy_from_stored_rates: updated {updated:,} rows")
    return updated


# ═══════════════════════════════════════════════════════════════════
#  2. share_pct Enrichment
# ═══════════════════════════════════════════════════════════════════

def enrich_share_pct(conn: sqlite3.Connection) -> int:
    """Compute share_pct = vault supply_assets / market total_supply_assets.

    Joins vault_allocations against market_snapshots on (market_id, timestamp).

    Returns: number of rows updated.
    """
    # Use a single UPDATE with subquery for efficiency
    result = conn.execute("""
        UPDATE vault_allocations
        SET share_pct = (
            SELECT CAST(vault_allocations.supply_assets AS REAL)
                 / CAST(ms.total_supply_assets AS REAL)
            FROM market_snapshots ms
            WHERE ms.market_id = vault_allocations.market_id
              AND ms.timestamp = vault_allocations.timestamp
              AND CAST(ms.total_supply_assets AS REAL) > 0
        )
        WHERE supply_assets IS NOT NULL
          AND supply_assets != '0'
          AND EXISTS (
            SELECT 1 FROM market_snapshots ms
            WHERE ms.market_id = vault_allocations.market_id
              AND ms.timestamp = vault_allocations.timestamp
              AND CAST(ms.total_supply_assets AS REAL) > 0
          )
    """)
    conn.commit()
    updated = result.rowcount
    log.info(f"enrich_share_pct: updated {updated:,} rows")
    return updated


# ═══════════════════════════════════════════════════════════════════
#  3. Utilization Regime Tagging
# ═══════════════════════════════════════════════════════════════════

def enrich_regime(conn: sqlite3.Connection) -> int:
    """Tag each market_snapshot with a utilization regime label.

    Returns: number of rows updated.
    """
    cursor = conn.execute("""
        SELECT rowid, utilization FROM market_snapshots
        WHERE regime IS NULL AND utilization IS NOT NULL
    """)

    batch = []
    updated = 0

    for row in cursor:
        rowid = row[0]
        utilization = row[1]
        regime = classify_utilization_regime(utilization)
        batch.append((regime, rowid))
        updated += 1

        if len(batch) >= 10000:
            conn.executemany(
                "UPDATE market_snapshots SET regime=? WHERE rowid=?",
                batch,
            )
            conn.commit()
            batch = []

    if batch:
        conn.executemany(
            "UPDATE market_snapshots SET regime=? WHERE rowid=?",
            batch,
        )
        conn.commit()

    log.info(f"enrich_regime: tagged {updated:,} rows")
    return updated


# ═══════════════════════════════════════════════════════════════════
#  4. Time-in-Regime Run Detection
# ═══════════════════════════════════════════════════════════════════

def enrich_regime_runs(conn: sqlite3.Connection) -> int:
    """Detect consecutive regime runs per market.

    A "run" is a consecutive sequence of snapshots with the same regime,
    ordered by timestamp. Stores results in market_regime_runs table.

    Returns: number of runs inserted.
    """
    # Clear existing runs (re-compute from scratch)
    conn.execute("DELETE FROM market_regime_runs")

    # Get all distinct markets with regime data
    markets = conn.execute(
        "SELECT DISTINCT market_id FROM market_snapshots WHERE regime IS NOT NULL"
    ).fetchall()

    total_runs = 0

    for (market_id,) in markets:
        rows = conn.execute(
            """SELECT timestamp, regime FROM market_snapshots
               WHERE market_id = ? AND regime IS NOT NULL
               ORDER BY timestamp ASC""",
            (market_id,),
        ).fetchall()

        if not rows:
            continue

        runs = []
        run_start_ts = rows[0][0]
        run_regime = rows[0][1]
        prev_ts = rows[0][0]

        for ts, regime in rows[1:]:
            if regime != run_regime:
                # Close current run
                duration_h = (prev_ts - run_start_ts) / 3600.0
                runs.append((market_id, run_regime, run_start_ts, prev_ts, duration_h))
                # Start new run
                run_start_ts = ts
                run_regime = regime
            prev_ts = ts

        # Close final run
        duration_h = (prev_ts - run_start_ts) / 3600.0
        runs.append((market_id, run_regime, run_start_ts, prev_ts, duration_h))

        if runs:
            conn.executemany(
                """INSERT OR REPLACE INTO market_regime_runs
                   (market_id, regime, start_ts, end_ts, duration_hours)
                   VALUES (?,?,?,?,?)""",
                runs,
            )
            total_runs += len(runs)

    conn.commit()
    log.info(f"enrich_regime_runs: inserted {total_runs:,} runs across {len(markets)} markets")
    return total_runs


# ═══════════════════════════════════════════════════════════════════
#  5. Fetch rate_at_target from Chain + Backward Extrapolation
# ═══════════════════════════════════════════════════════════════════

def fetch_and_backfill_rates(conn: sqlite3.Connection) -> int:
    """Fetch current rate_at_target from chain, backward-extrapolate, compute APY.

    Strategy:
      1. RPC batch: get current rate_at_target for all 226 IRM-enabled markets
      2. For each market, walk snapshots backward chronologically
      3. Reverse the adaptive evolution to estimate historical rate_at_target
      4. Compute borrow_apy and supply_apy using the curve function

    The backward evolution inverts the forward formula:
      forward:  end_rate = evolve(start_rate, util, elapsed_seconds)
      backward: start_rate = reverse_evolve(end_rate, util, elapsed_seconds)

    Returns: number of snapshots updated with APY.
    """
    from backend.morpho.rpc import multicall_irm_rates
    from backend.morpho.irm import (
        ADJUSTMENT_SPEED, MIN_RATE_AT_TARGET, MAX_RATE_AT_TARGET,
        compute_err, w_mul_to_zero, w_exp, bound,
    )

    # Step 1: Get IRM-enabled markets
    irm_markets = conn.execute(
        """SELECT market_id FROM market_params
           WHERE irm IS NOT NULL
             AND irm != '0x0000000000000000000000000000000000000000'"""
    ).fetchall()
    market_ids = [r[0] for r in irm_markets]
    log.info(f"Fetching rate_at_target for {len(market_ids)} IRM-enabled markets...")

    # Step 2: RPC batch fetch current rate_at_target
    current_rates = multicall_irm_rates(market_ids)
    fetched = {mid: rate for mid, rate in current_rates.items() if rate and rate > 0}
    log.info(f"  Fetched {len(fetched)}/{len(market_ids)} non-zero rates from chain")

    if not fetched:
        log.warning("No rates fetched from chain. Check RPC connectivity.")
        return 0

    # Step 3: For each market, backward-extrapolate and compute APY
    total_updated = 0

    for market_id, anchor_rate in fetched.items():
        # Get all snapshots for this market, ordered newest-first
        snapshots = conn.execute(
            """SELECT rowid, timestamp, utilization, fee, last_update
               FROM market_snapshots
               WHERE market_id = ?
               ORDER BY timestamp DESC""",
            (market_id,),
        ).fetchall()

        if not snapshots:
            continue

        batch = []
        current_rate = anchor_rate

        for i, snap in enumerate(snapshots):
            rowid = snap[0]
            snap_ts = snap[1]
            utilization = snap[2]
            fee_wad = snap[3] or 0

            if utilization is None:
                continue

            util_wad = int(utilization * WAD)

            # Compute APY with current rate estimate
            borrow_apy, supply_apy = compute_full_apy(current_rate, util_wad, fee_wad)

            if borrow_apy is not None:
                batch.append((str(current_rate), borrow_apy, supply_apy, rowid))
                total_updated += 1

            # Backward-extrapolate to the PREVIOUS (older) snapshot
            if i + 1 < len(snapshots):
                prev_snap = snapshots[i + 1]
                prev_ts = prev_snap[1]
                elapsed = snap_ts - prev_ts

                if elapsed > 0:
                    # Reverse the forward evolution:
                    # forward:  end = start * exp(speed * elapsed)
                    # backward: start = end * exp(-speed * elapsed)
                    # = evolve_rate_at_target(current_rate, util, -elapsed)
                    err = compute_err(util_wad)
                    speed = w_mul_to_zero(ADJUSTMENT_SPEED, err)
                    reverse_adaptation = -(speed * elapsed)

                    if reverse_adaptation != 0:
                        from backend.morpho.irm import new_rate_at_target as _nrt
                        current_rate = _nrt(current_rate, reverse_adaptation)
                    # else: rate stays the same (was at target utilization)

            # Flush periodically
            if len(batch) >= 5000:
                conn.executemany(
                    """UPDATE market_snapshots
                       SET rate_at_target=?, borrow_apy=?, supply_apy=?
                       WHERE rowid=?""",
                    batch,
                )
                conn.commit()
                batch = []

        # Flush remaining
        if batch:
            conn.executemany(
                """UPDATE market_snapshots
                   SET rate_at_target=?, borrow_apy=?, supply_apy=?
                   WHERE rowid=?""",
                batch,
            )
            conn.commit()

        if total_updated % 50000 == 0 and total_updated > 0:
            log.info(f"  Progress: {total_updated:,} snapshots updated...")

    log.info(f"fetch_and_backfill_rates: updated {total_updated:,} snapshots")
    return total_updated


# ═══════════════════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════════════════

STEPS = {
    "fetch_rates": fetch_and_backfill_rates,
    "apy": enrich_apy_from_stored_rates,
    "share_pct": enrich_share_pct,
    "regime": enrich_regime,
    "regime_runs": enrich_regime_runs,
}


def run_all(conn: sqlite3.Connection) -> dict[str, int]:
    """Run all enrichment steps in order."""
    results = {}
    for name, func in STEPS.items():
        t0 = time.time()
        count = func(conn)
        elapsed = time.time() - t0
        results[name] = count
        log.info(f"  {name}: {count:,} updates in {elapsed:.1f}s")
    return results


def main():
    parser = argparse.ArgumentParser(description="Morpho DB Enrichment Engine")
    parser.add_argument("--db-path", default=DB_PATH, help="Path to morpho.db")
    parser.add_argument(
        "--step",
        choices=list(STEPS.keys()) + ["all"],
        default="all",
        help="Which enrichment step to run",
    )
    args = parser.parse_args()

    log.info(f"Opening database: {args.db_path}")
    conn = sqlite3.connect(args.db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Apply migrations
    migrate_schema(conn)

    if args.step == "all":
        results = run_all(conn)
        log.info(f"Enrichment complete: {results}")
    else:
        func = STEPS[args.step]
        t0 = time.time()
        count = func(conn)
        log.info(f"{args.step}: {count:,} updates in {time.time()-t0:.1f}s")

    conn.close()


if __name__ == "__main__":
    main()
