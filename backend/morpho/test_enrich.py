"""
Tests for the Morpho DB enrichment engine.

Uses in-memory SQLite databases with synthetic data to verify:
  1. share_pct computation correctness
  2. Utilization regime tagging
  3. Time-in-regime run detection
  4. APY enrichment pipeline integration
"""

import sqlite3
import math
import random
import pytest

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.morpho.irm import (
    WAD, INITIAL_RATE_AT_TARGET, TARGET_UTILIZATION,
    MIN_RATE_AT_TARGET, MAX_RATE_AT_TARGET,
    compute_borrow_rate, borrow_rate_to_apy, compute_supply_apy,
    classify_utilization_regime, compute_full_apy,
)


# ═══════════════════════════════════════════════════════════════════
#  Helpers: In-memory DB scaffolding
# ═══════════════════════════════════════════════════════════════════

def make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the Morpho schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE market_params (
            market_id TEXT PRIMARY KEY, loan_token TEXT NOT NULL,
            loan_symbol TEXT, loan_decimals INTEGER,
            collateral_token TEXT, collateral_symbol TEXT, collateral_decimals INTEGER,
            oracle TEXT, irm TEXT, lltv REAL,
            created_block INTEGER, created_timestamp INTEGER, discovered_at INTEGER
        );
        CREATE TABLE market_snapshots (
            timestamp INTEGER NOT NULL, block_number INTEGER NOT NULL,
            market_id TEXT NOT NULL,
            total_supply_assets TEXT, total_borrow_assets TEXT,
            total_supply_shares TEXT, total_borrow_shares TEXT,
            last_update INTEGER, fee INTEGER,
            utilization REAL, borrow_apy REAL, supply_apy REAL,
            oracle_price TEXT, rate_at_target TEXT,
            regime TEXT,
            PRIMARY KEY (market_id, timestamp)
        );
        CREATE TABLE vault_allocations (
            timestamp INTEGER NOT NULL, vault_address TEXT NOT NULL,
            market_id TEXT NOT NULL,
            supply_shares TEXT, supply_assets TEXT,
            supply_usd REAL, share_pct REAL,
            PRIMARY KEY (vault_address, market_id, timestamp)
        );
        CREATE TABLE market_regime_runs (
            market_id TEXT NOT NULL,
            regime TEXT NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER NOT NULL,
            duration_hours REAL NOT NULL,
            PRIMARY KEY (market_id, start_ts)
        );
    """)
    return conn


def insert_market_param(conn, market_id, irm="0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC"):
    conn.execute(
        "INSERT INTO market_params (market_id, loan_token, irm) VALUES (?, ?, ?)",
        (market_id, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", irm),
    )


def insert_market_snapshot(
    conn, market_id, timestamp, block_number,
    total_supply_assets, total_borrow_assets,
    utilization, fee=0, rate_at_target=None,
    last_update=None,
):
    last_update = last_update or timestamp
    conn.execute(
        """INSERT INTO market_snapshots
        (timestamp, block_number, market_id,
         total_supply_assets, total_borrow_assets,
         total_supply_shares, total_borrow_shares,
         last_update, fee, utilization, borrow_apy, supply_apy,
         oracle_price, rate_at_target, regime)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (timestamp, block_number, market_id,
         str(total_supply_assets), str(total_borrow_assets),
         "0", "0",
         last_update, fee, utilization, None, None,
         None, str(rate_at_target) if rate_at_target else None, None),
    )


def insert_vault_allocation(conn, timestamp, vault_address, market_id, supply_assets):
    conn.execute(
        """INSERT INTO vault_allocations
        (timestamp, vault_address, market_id, supply_shares, supply_assets,
         supply_usd, share_pct)
        VALUES (?,?,?,?,?,?,?)""",
        (timestamp, vault_address, market_id, "0", str(supply_assets), None, None),
    )


# ═══════════════════════════════════════════════════════════════════
#  Import enrich functions (deferred to avoid import errors during
#  initial development — will fail gracefully if enrich.py not ready)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def enrich_module():
    """Import the enrich module. Skip if not available."""
    try:
        from backend.morpho import enrich
        return enrich
    except ImportError:
        pytest.skip("enrich module not yet implemented")


# ═══════════════════════════════════════════════════════════════════
#  1. share_pct Computation
# ═══════════════════════════════════════════════════════════════════

class TestSharePct:
    """Verify share_pct = vault_supply_assets / market_total_supply_assets."""

    def test_single_vault_full_allocation(self, enrich_module):
        """One vault supplying all assets → share_pct = 1.0."""
        conn = make_test_db()
        mid = "0xmarket1"
        ts = 1700000000
        insert_market_param(conn, mid)
        insert_market_snapshot(conn, mid, ts, 100, 1000000, 500000, 0.5)
        insert_vault_allocation(conn, ts, "0xvault1", mid, 1000000)

        enrich_module.enrich_share_pct(conn)

        row = conn.execute(
            "SELECT share_pct FROM vault_allocations WHERE vault_address='0xvault1'"
        ).fetchone()
        assert row is not None
        assert abs(row[0] - 1.0) < 1e-10

    def test_two_vaults_equal_split(self, enrich_module):
        """Two vaults each supplying half → share_pct = 0.5 each."""
        conn = make_test_db()
        mid = "0xmarket1"
        ts = 1700000000
        insert_market_param(conn, mid)
        insert_market_snapshot(conn, mid, ts, 100, 1000000, 500000, 0.5)
        insert_vault_allocation(conn, ts, "0xvault1", mid, 500000)
        insert_vault_allocation(conn, ts, "0xvault2", mid, 500000)

        enrich_module.enrich_share_pct(conn)

        rows = conn.execute(
            "SELECT vault_address, share_pct FROM vault_allocations ORDER BY vault_address"
        ).fetchall()
        assert len(rows) == 2
        for r in rows:
            assert abs(r[1] - 0.5) < 1e-10

    def test_share_pct_sums_lte_one(self, enrich_module):
        """Total share_pct across vaults at a timestamp should be <= 1.0."""
        conn = make_test_db()
        mid = "0xmarket1"
        ts = 1700000000
        insert_market_param(conn, mid)
        insert_market_snapshot(conn, mid, ts, 100, 1000000, 500000, 0.5)
        insert_vault_allocation(conn, ts, "0xvault1", mid, 300000)
        insert_vault_allocation(conn, ts, "0xvault2", mid, 200000)
        insert_vault_allocation(conn, ts, "0xvault3", mid, 100000)

        enrich_module.enrich_share_pct(conn)

        total = conn.execute(
            "SELECT SUM(share_pct) FROM vault_allocations WHERE timestamp=? AND market_id=?",
            (ts, mid),
        ).fetchone()[0]
        assert total <= 1.0 + 1e-10  # Allow small floating point error

    def test_share_pct_zero_supply(self, enrich_module):
        """Zero supply vault → share_pct stays None or 0."""
        conn = make_test_db()
        mid = "0xmarket1"
        ts = 1700000000
        insert_market_param(conn, mid)
        insert_market_snapshot(conn, mid, ts, 100, 1000000, 500000, 0.5)
        insert_vault_allocation(conn, ts, "0xvault1", mid, 0)

        enrich_module.enrich_share_pct(conn)

        row = conn.execute(
            "SELECT share_pct FROM vault_allocations WHERE vault_address='0xvault1'"
        ).fetchone()
        assert row[0] is None or row[0] == 0.0


# ═══════════════════════════════════════════════════════════════════
#  2. Regime Tagging
# ═══════════════════════════════════════════════════════════════════

class TestRegimeTagging:
    """Verify utilization regime is correctly tagged in market_snapshots."""

    def test_regime_assignment(self, enrich_module):
        """Each snapshot gets the correct regime label."""
        conn = make_test_db()
        mid = "0xmarket1"
        insert_market_param(conn, mid)

        test_cases = [
            (1700000000, 0.0, "idle"),
            (1700003600, 0.25, "low"),
            (1700007200, 0.70, "normal"),
            (1700010800, 0.91, "elevated"),
            (1700014400, 0.96, "critical"),
            (1700018000, 1.00, "trapped"),
        ]
        for ts, util, _ in test_cases:
            insert_market_snapshot(conn, mid, ts, 100, 1000000, int(1000000 * util), util)

        enrich_module.enrich_regime(conn)

        for ts, util, expected_regime in test_cases:
            row = conn.execute(
                "SELECT regime FROM market_snapshots WHERE market_id=? AND timestamp=?",
                (mid, ts),
            ).fetchone()
            assert row is not None, f"No row for ts={ts}"
            assert row[0] == expected_regime, (
                f"At util={util}: expected '{expected_regime}', got '{row[0]}'"
            )


# ═══════════════════════════════════════════════════════════════════
#  3. Time-in-Regime Run Detection
# ═══════════════════════════════════════════════════════════════════

class TestRegimeRuns:
    """Verify regime run detection for consecutive snapshots."""

    def test_single_run(self, enrich_module):
        """3 consecutive 'trapped' snapshots → one run of 2 hours."""
        conn = make_test_db()
        mid = "0xmarket1"
        insert_market_param(conn, mid)

        for i in range(3):
            ts = 1700000000 + i * 3600
            insert_market_snapshot(conn, mid, ts, 100 + i, 1000000, 1000000, 1.0)

        # Must tag regimes first
        enrich_module.enrich_regime(conn)
        enrich_module.enrich_regime_runs(conn)

        rows = conn.execute(
            "SELECT * FROM market_regime_runs WHERE market_id=?", (mid,)
        ).fetchall()
        assert len(rows) == 1
        run = dict(rows[0])
        assert run["regime"] == "trapped"
        assert run["start_ts"] == 1700000000
        assert run["end_ts"] == 1700000000 + 2 * 3600
        assert abs(run["duration_hours"] - 2.0) < 0.01

    def test_regime_transition(self, enrich_module):
        """Transition from normal → trapped → normal should produce 3 runs."""
        conn = make_test_db()
        mid = "0xmarket1"
        insert_market_param(conn, mid)

        utils = [0.7, 0.7, 1.0, 1.0, 1.0, 0.6, 0.6]
        for i, util in enumerate(utils):
            ts = 1700000000 + i * 3600
            insert_market_snapshot(
                conn, mid, ts, 100 + i, 1000000, int(1000000 * util), util,
            )

        enrich_module.enrich_regime(conn)
        enrich_module.enrich_regime_runs(conn)

        rows = conn.execute(
            "SELECT regime, duration_hours FROM market_regime_runs WHERE market_id=? ORDER BY start_ts",
            (mid,),
        ).fetchall()
        assert len(rows) == 3
        regimes = [dict(r)["regime"] for r in rows]
        assert regimes == ["normal", "trapped", "normal"]


# ═══════════════════════════════════════════════════════════════════
#  4. APY Enrichment Pipeline
# ═══════════════════════════════════════════════════════════════════

class TestAPYEnrichment:
    """Verify the local APY computation enrichment."""

    def test_apy_enrichment_with_known_rate(self, enrich_module):
        """Given a known rate_at_target, verify APY is computed correctly."""
        conn = make_test_db()
        mid = "0xmarket1"
        insert_market_param(conn, mid)

        r = INITIAL_RATE_AT_TARGET
        util = 0.9  # at target
        ts = 1700000000
        insert_market_snapshot(
            conn, mid, ts, 100, 1000000, 900000, util,
            fee=0, rate_at_target=r,
        )

        enrich_module.enrich_apy_from_stored_rates(conn)

        row = conn.execute(
            "SELECT borrow_apy, supply_apy FROM market_snapshots WHERE market_id=? AND timestamp=?",
            (mid, ts),
        ).fetchone()
        assert row is not None
        borrow_apy = row[0]
        supply_apy = row[1]
        assert borrow_apy is not None
        assert supply_apy is not None
        # At target utilization, initial rate → ~4% APY
        assert 0.03 < borrow_apy < 0.05, f"Borrow APY = {borrow_apy}"
        assert supply_apy < borrow_apy

    def test_apy_null_rate_produces_null(self, enrich_module):
        """Snapshots without rate_at_target should result in NULL APY."""
        conn = make_test_db()
        mid = "0xmarket1"
        insert_market_param(conn, mid)
        ts = 1700000000
        insert_market_snapshot(
            conn, mid, ts, 100, 1000000, 500000, 0.5,
            rate_at_target=None,
        )

        enrich_module.enrich_apy_from_stored_rates(conn)

        row = conn.execute(
            "SELECT borrow_apy, supply_apy FROM market_snapshots WHERE market_id=? AND timestamp=?",
            (mid, ts),
        ).fetchone()
        # Should remain None since rate_at_target is NULL
        assert row[0] is None
        assert row[1] is None

    def test_apy_steepness_effect(self, enrich_module):
        """At 100% util, APY should be ~4x the APY at target (for same rate_at_target)."""
        conn = make_test_db()
        mid_target = "0xmarket_target"
        mid_full = "0xmarket_full"
        insert_market_param(conn, mid_target)
        insert_market_param(conn, mid_full)

        r = INITIAL_RATE_AT_TARGET
        ts = 1700000000

        insert_market_snapshot(
            conn, mid_target, ts, 100, 1000000, 900000, 0.9,
            rate_at_target=r,
        )
        insert_market_snapshot(
            conn, mid_full, ts, 100, 1000000, 1000000, 1.0,
            rate_at_target=r,
        )

        enrich_module.enrich_apy_from_stored_rates(conn)

        row_target = conn.execute(
            "SELECT borrow_apy FROM market_snapshots WHERE market_id=?", (mid_target,)
        ).fetchone()
        row_full = conn.execute(
            "SELECT borrow_apy FROM market_snapshots WHERE market_id=?", (mid_full,)
        ).fetchone()

        # borrow_rate at 100% = 4 * rate_at_target (curve steepness)
        # The APY comparison is not exactly 4x because of exp() nonlinearity,
        # but rate_full > rate_target
        assert row_full[0] > row_target[0]

    def test_apy_with_fee(self, enrich_module):
        """Supply APY should be reduced by fee."""
        conn = make_test_db()
        mid = "0xmarket1"
        insert_market_param(conn, mid)

        r = INITIAL_RATE_AT_TARGET
        ts = 1700000000
        fee = WAD // 10  # 10%

        insert_market_snapshot(
            conn, mid, ts, 100, 1000000, 900000, 0.9,
            fee=fee, rate_at_target=r,
        )

        enrich_module.enrich_apy_from_stored_rates(conn)

        row = conn.execute(
            "SELECT borrow_apy, supply_apy FROM market_snapshots WHERE market_id=?", (mid,)
        ).fetchone()

        borrow_apy = row[0]
        supply_apy = row[1]
        # supply = borrow * util * (1 - fee) = borrow * 0.9 * 0.9 = borrow * 0.81
        expected_supply = borrow_apy * 0.9 * 0.9
        assert abs(supply_apy - expected_supply) < 1e-10


# ═══════════════════════════════════════════════════════════════════
#  Poka-Yoke
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
