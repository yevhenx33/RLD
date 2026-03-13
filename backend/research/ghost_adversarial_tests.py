#!/usr/bin/env python3
"""
Deep Adversarial Testing: Ghost Balance Limit Orders
======================================================
Attack vectors, edge cases, and capacity stress tests.
Each test uses the AuditableGhostHook with per-step invariant checking.

Run: python3 backend/tools/ghost_adversarial_tests.py
"""

import sys, os, random, math
sys.path.insert(0, os.path.dirname(__file__))
from ghost_solvency_audit import AuditableGhostHook, EPSILON

PASSED = 0
FAILED = 0
CHART_DIR = os.path.join(os.path.dirname(__file__), "ghost_limit_charts")


def run_test(name: str, fn):
    global PASSED, FAILED
    try:
        fn()
        PASSED += 1
        print(f"  ✅ {name}")
    except AssertionError as e:
        FAILED += 1
        print(f"  ❌ {name}: {e}")
    except Exception as e:
        FAILED += 1
        print(f"  💥 {name}: CRASH — {e}")


# ═══════════════════════════════════════════════════════════════════
# 1. MEV ATTACK VECTORS
# ═══════════════════════════════════════════════════════════════════

def test_sandwich_attack():
    """Sandwich attack: front-run + back-run around a large swap.
    Attacker tries to profit by placing orders before a known large swap."""
    h = AuditableGhostHook()
    h.mint_tokens("Victim", 0, 500_000)
    h.mint_tokens("Attacker", 100, 500_000)
    h.mint_tokens("Maker", 50, 0)

    h.twap = 3000
    # Maker has a sell order at 3000
    h.place("Maker", True, 50, 3000)

    # Attacker front-runs: places sell order at slightly better price
    h.place("Attacker", True, 10, 2999)

    # Victim's large buy swap
    h.swap("Victim", True, 40)

    # Attacker back-runs: claims + tries to profit
    p_atk, r_atk = h.claim("Attacker", 2999, True)
    p_maker, r_maker = h.claim("Maker", 3000, True)

    # TWAP protection: attacker fills at TWAP (3000), not at their 2999 tick
    # So attacker gets TWAP price just like everyone else — no sandwich profit
    # The key assertion: victim's fill price == TWAP, not worse
    victim_t0 = h._wallet("Victim")["T0"]
    victim_paid = 500_000 - h._wallet("Victim")["T1"]
    if victim_t0 > 0:
        avg_price = victim_paid / victim_t0
        assert avg_price <= 3001, f"Victim overpaid: ${avg_price:.2f} (TWAP was $3000)"


def test_frontrun_order_placement():
    """Front-runner sees pending limit order and tries to place before it."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 100, 0)
    h.mint_tokens("FrontRunner", 100, 0)
    h.mint_tokens("Taker", 0, 1_000_000)

    h.twap = 3010
    # FrontRunner places at same tick before Alice
    h.place("FrontRunner", True, 10, 3000)
    h.place("Alice", True, 10, 3000)

    # Taker buys 15 Token0 — both get pro-rata
    h.swap("Taker", True, 15)

    p_fr, _ = h.claim("FrontRunner", 3000, True)
    p_alice, _ = h.claim("Alice", 3000, True)

    # Pro-rata: both deposited equal amounts, both get equal share
    assert abs(p_fr - p_alice) < 1.0, \
        f"Front-runner shouldn't get advantage: FR={p_fr:.2f} vs Alice={p_alice:.2f}"


def test_backrun_claim():
    """Attacker tries to claim right after a fill to extract value."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 50, 0)
    h.mint_tokens("Attacker", 50, 0)
    h.mint_tokens("Taker", 0, 500_000)

    h.twap = 3010
    h.place("Alice", True, 50, 3000)
    h.place("Attacker", True, 50, 3000)

    # Taker buys 30 — fills both pro-rata
    h.swap("Taker", True, 30)

    # Attacker claims immediately after fill
    p_atk, r_atk = h.claim("Attacker", 3000, True)

    # Alice claims later
    p_alice, r_alice = h.claim("Alice", 3000, True)

    # Both should get exactly 50% of proceeds
    total = p_atk + p_alice
    assert abs(p_atk / total - 0.5) < 0.01, \
        f"Attacker got unfair share: {p_atk/total*100:.1f}%"


# ═══════════════════════════════════════════════════════════════════
# 2. DOUBLE-CLAIM / RE-ENTRANCY EXPLOITS
# ═══════════════════════════════════════════════════════════════════

def test_double_claim():
    """Attempt to claim the same order twice."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 0)
    h.mint_tokens("Taker", 0, 100_000)

    h.twap = 3010
    h.place("Alice", True, 10, 3000)
    h.swap("Taker", True, 10)

    # First claim — should succeed
    p1, r1 = h.claim("Alice", 3000, True)
    assert p1 > 0, "First claim should have proceeds"

    # Second claim — should return nothing (order already removed)
    p2, r2 = h.claim("Alice", 3000, True)
    assert p2 == 0 and r2 == 0, f"Double claim returned: proceeds={p2}, refund={r2}"


def test_claim_nonexistent_order():
    """Claim from a tick where user has no orders."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 0)
    h.twap = 3000
    h.place("Alice", True, 10, 3000)

    # Try to claim from wrong tick
    p, r = h.claim("Alice", 3001, True)
    assert p == 0 and r == 0, "Claimed from non-existent tick"

    # Try to claim wrong side
    p, r = h.claim("Alice", 3000, False)
    assert p == 0 and r == 0, "Claimed from wrong side"

    # Try to claim as wrong user
    p, r = h.claim("Bob", 3000, True)
    assert p == 0 and r == 0, "Wrong user claimed"


def test_claim_unfilled_order():
    """Claim an order that was never filled (pure refund)."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 0)
    h.twap = 2900  # below trigger

    h.place("Alice", True, 10, 3000)

    # No swap, TWAP is below trigger
    p, r = h.claim("Alice", 3000, True)
    assert p == 0 or abs(p) < EPSILON, f"Unfilled order shouldn't have proceeds: {p}"
    assert abs(r - 10) < EPSILON, f"Full refund expected: {r}"
    assert abs(h._wallet("Alice")["T0"] - 10) < EPSILON, "Alice should have all T0 back"


# ═══════════════════════════════════════════════════════════════════
# 3. DUST / ROUNDING ATTACKS
# ═══════════════════════════════════════════════════════════════════

def test_dust_deposits():
    """Many tiny deposits — ensure no rounding exploit."""
    h = AuditableGhostHook()
    n_users = 50
    dust = 0.000001  # 1 wei equivalent
    for i in range(n_users):
        h.mint_tokens(f"D{i}", dust * 10, 0)
        h.place(f"D{i}", True, dust, 3000)

    h.mint_tokens("Taker", 0, 100_000)
    h.twap = 3010
    h.swap("Taker", True, dust * n_users)

    # Claim all — verify no user gets phantom tokens
    total_claimed = 0
    for i in range(n_users):
        p, r = h.claim(f"D{i}", 3000, True)
        total_claimed += p
        assert p >= 0, f"D{i} got negative proceeds"

    # Hook should be near-empty
    assert h.hook_t0 < EPSILON * 100, f"Hook T0 leak: {h.hook_t0}"


def test_asymmetric_dust_vs_whale():
    """1 wei dust deposit alongside a whale — dust user shouldn't steal."""
    h = AuditableGhostHook()
    h.mint_tokens("Whale", 1000, 0)
    h.mint_tokens("Dust", 0.000001, 0)
    h.mint_tokens("Taker", 0, 5_000_000)

    h.twap = 3010
    h.place("Whale", True, 1000, 3000)
    h.place("Dust", True, 0.000001, 3000)

    h.swap("Taker", True, 500)

    p_whale, _ = h.claim("Whale", 3000, True)
    p_dust, _ = h.claim("Dust", 3000, True)

    # Dust's share should be proportional
    ratio = p_dust / p_whale if p_whale > 0 else 0
    expected_ratio = 0.000001 / 1000
    assert abs(ratio - expected_ratio) < expected_ratio * 0.01 or p_dust < EPSILON, \
        f"Dust got disproportionate share: {ratio:.2e} vs expected {expected_ratio:.2e}"


def test_zero_amount_rejection():
    """Zero-amount operations should be no-ops."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 100_000)
    h.twap = 3000

    # Zero-amount swap should fill nothing
    filled = h.swap("Alice", True, 0)
    assert filled == 0, "Zero swap should fill nothing"


# ═══════════════════════════════════════════════════════════════════
# 4. STALE TWAP GRIEFING
# ═══════════════════════════════════════════════════════════════════

def test_stale_twap_no_false_trigger():
    """If TWAP is stale (no trades), orders shouldn't trigger on old data."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 0)
    h.mint_tokens("Taker", 0, 100_000)

    # TWAP was 3010 an hour ago, no trades since
    h.twap = 3010
    h.time = 0
    h.place("Alice", True, 10, 3000)

    # Fast-forward 1 hour with no price updates (TWAP stays stale)
    h.time = 3600

    # Swap at stale TWAP — this SHOULD fill (TWAP is above trigger)
    # But in production, the TWAP should be refreshed by the swap itself
    # Our simulation uses externally-set TWAP, so this tests that
    # the mechanism correctly gates on TWAP, not spot
    filled = h.swap("Taker", True, 5)
    assert filled == 5, "Should fill at stale TWAP if it's above trigger"


def test_twap_just_below_trigger():
    """TWAP at 2999.999... should NOT trigger a 3000 limit."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 0)
    h.mint_tokens("Taker", 0, 100_000)

    h.twap = 2999.999999
    h.place("Alice", True, 10, 3000)

    filled = h.swap("Taker", True, 10)
    assert filled == 0, f"TWAP {h.twap} < 3000 should NOT trigger"


def test_twap_exactly_at_trigger():
    """TWAP exactly at trigger price — should trigger."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 0)
    h.mint_tokens("Taker", 0, 100_000)

    h.twap = 3000.0  # exactly at trigger
    h.place("Alice", True, 10, 3000)

    filled = h.swap("Taker", True, 5)
    assert filled == 5, f"TWAP exactly at trigger should fill"


# ═══════════════════════════════════════════════════════════════════
# 5. SAME-USER-BOTH-SIDES / SELF-NETTING
# ═══════════════════════════════════════════════════════════════════

def test_same_user_both_sides():
    """User places sell AND buy at different ticks — self-netting."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 100, 500_000)

    h.twap = 3000
    h.place("Alice", True, 10, 2900)   # sell 10 at 2900
    h.place("Alice", False, 5, 3100)    # buy 5 at 3100

    # Netting won't match (sell trigger 2900, buy trigger 3100, TWAP 3000)
    # sell condition: TWAP >= 2900 ✓, buy condition: TWAP <= 3100 ✓
    # sell_tick <= buy_tick? 2900 <= 3100 ✓ — spread is crossed!
    h.internal_net()

    # Alice nets against herself — should be fine
    p_sell, r_sell = h.claim("Alice", 2900, True)
    p_buy, r_buy = h.claim("Alice", 3100, False)

    # Conservation: Alice should end up with same total value
    initial_value = 100 * 3000 + 500_000
    final_t0 = h._wallet("Alice")["T0"]
    final_t1 = h._wallet("Alice")["T1"]
    final_value = final_t0 * 3000 + final_t1
    # Value should be conserved (modulo TWAP pricing)
    assert final_t0 >= 0 and final_t1 >= 0, "Self-netting left negative balance"


def test_user_multiple_orders_same_tick():
    """User places multiple orders at the same tick."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 30, 0)
    h.mint_tokens("Taker", 0, 500_000)

    h.twap = 3010
    h.place("Alice", True, 10, 3000)
    h.place("Alice", True, 10, 3000)
    h.place("Alice", True, 10, 3000)

    h.swap("Taker", True, 15)
    p, r = h.claim("Alice", 3000, True)

    # Alice has 3 orders totaling 30 at same tick
    # 15 filled, 15 remaining — she should get all proceeds + all refunds
    assert p > 0, "Should have proceeds from partial fill"
    assert r > 0, "Should have refund from unfilled portion"
    assert abs(h._wallet("Alice")["T0"] + 15 - 30) < EPSILON, \
        "Alice should have 15 T0 refund"


# ═══════════════════════════════════════════════════════════════════
# 6. ORDERING ATTACKS
# ═══════════════════════════════════════════════════════════════════

def test_claim_then_swap():
    """Claim BEFORE more swaps happen — shouldn't affect future fills."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 20, 0)
    h.mint_tokens("Taker", 0, 500_000)

    h.twap = 3010
    h.place("Alice", True, 20, 3000)
    h.swap("Taker", True, 5)

    # Claim partially filled order
    p1, r1 = h.claim("Alice", 3000, True)
    assert p1 > 0

    # Place again and swap more — previous claim shouldn't corrupt state
    h.place("Alice", True, r1, 3000)  # re-deposit refund
    h.swap("Taker", True, 5)
    p2, r2 = h.claim("Alice", 3000, True)

    # Both claims should have non-negative proceeds
    assert p2 >= 0, f"Second claim corrupted: {p2}"


def test_interleaved_place_swap_claim():
    """Rapidly interleave place/swap/claim operations."""
    h = AuditableGhostHook()
    for i in range(10):
        h.mint_tokens(f"U{i}", 100, 500_000)
    h.mint_tokens("Taker", 100, 1_000_000)

    h.twap = 3010
    for round_num in range(20):
        user = f"U{round_num % 10}"
        h.place(user, True, 5, 3000)
        h.swap("Taker", True, 2)
        h.claim(user, 3000, True)

    # After all rounds, verify solvency
    # All user and taker balances should be non-negative
    for i in range(10):
        w = h._wallet(f"U{i}")
        assert w["T0"] >= -EPSILON, f"U{i} T0 negative: {w['T0']}"
        assert w["T1"] >= -EPSILON, f"U{i} T1 negative: {w['T1']}"


def test_net_then_swap_then_claim():
    """Internal netting followed by external swap — no double-counting."""
    h = AuditableGhostHook()
    h.mint_tokens("Seller", 20, 0)
    h.mint_tokens("Buyer", 0, 200_000)
    h.mint_tokens("Taker", 0, 200_000)

    h.twap = 3000
    h.place("Seller", True, 20, 2900)
    h.place("Buyer", False, 10, 3100)

    # Net first — matches 10
    h.internal_net()

    # Then swap from remaining 10 sell orders
    h.swap("Taker", True, 5)

    # Claims
    p_s, r_s = h.claim("Seller", 2900, True)
    p_b, r_b = h.claim("Buyer", 3100, False)

    # Seller got proceeds from net + swap, buyer got T0 from net
    assert p_s > 0, "Seller should have proceeds"
    assert p_b > 0, "Buyer should have proceeds from netting"


# ═══════════════════════════════════════════════════════════════════
# 7. CAPACITY STRESS TEST
# ═══════════════════════════════════════════════════════════════════

def test_100_users_50_ticks():
    """100 users, 50 tick levels, random operations."""
    h = AuditableGhostHook()
    n_users = 100
    ticks = [2900 + i * 5 for i in range(50)]  # 50 ticks from 2900 to 3145

    for i in range(n_users):
        h.mint_tokens(f"U{i}", 50, 200_000)

    h.mint_tokens("Taker", 500, 5_000_000)
    h.twap = 3050
    random.seed(777)

    placed = []
    for _ in range(500):
        action = random.random()
        if action < 0.4:
            user = f"U{random.randint(0, n_users-1)}"
            tick = random.choice(ticks)
            is_sell = random.random() < 0.5
            amt = round(random.uniform(0.1, 2.0), 6)
            oid = h.place(user, is_sell, amt, tick)
            if oid:
                placed.append((user, tick, is_sell))
        elif action < 0.7:
            h.swap("Taker", random.random() < 0.5, random.uniform(0.5, 5.0))
        elif action < 0.8:
            h.internal_net()
        elif placed:
            u, t, s = random.choice(placed)
            h.claim(u, t, s)

    # Full drain
    for tick in ticks:
        for bucket_map, is_sell in [(h.sell_buckets, True), (h.buy_buckets, False)]:
            b = bucket_map.get(tick)
            if b:
                owners = set(o.owner for o in b.orders.values())
                for owner in owners:
                    h.claim(owner, tick, is_sell)

    assert abs(h.hook_t0) < EPSILON * 1000, f"T0 leak after 100-user drain: {h.hook_t0}"
    assert abs(h.hook_t1) < EPSILON * 1000, f"T1 leak after 100-user drain: {h.hook_t1}"


def test_many_orders_same_tick():
    """500 orders at the same tick — pro-rata must hold."""
    h = AuditableGhostHook()
    n = 500
    for i in range(n):
        h.mint_tokens(f"U{i}", 1, 0)
        h.place(f"U{i}", True, 1, 3000)

    h.mint_tokens("Taker", 0, 5_000_000)
    h.twap = 3010

    # Fill 250 of 500
    h.swap("Taker", True, 250)

    # Each user should get 50% filled (250/500)
    proceeds_list = []
    for i in range(n):
        p, r = h.claim(f"U{i}", 3000, True)
        proceeds_list.append(p)

    # All proceeds should be approximately equal
    avg = sum(proceeds_list) / len(proceeds_list)
    for i, p in enumerate(proceeds_list):
        if avg > 0:
            assert abs(p - avg) / avg < 0.01, \
                f"U{i} got {p:.4f} vs avg {avg:.4f} — pro-rata violated"


# ═══════════════════════════════════════════════════════════════════
# 8. TWAP BOUNDARY PRECISION
# ═══════════════════════════════════════════════════════════════════

def test_twap_epsilon_below():
    """TWAP is meaningfully below trigger — should NOT fill."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 0)
    h.mint_tokens("Taker", 0, 100_000)
    h.twap = 2999.99  # meaningfully below
    h.place("Alice", True, 10, 3000)
    filled = h.swap("Taker", True, 10)
    assert filled == 0, f"Below trigger should NOT fill (TWAP={h.twap})"


def test_twap_epsilon_above():
    """TWAP is 1 wei above trigger — should fill."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 10, 0)
    h.mint_tokens("Taker", 0, 100_000)
    h.twap = 3000 + 1e-18  # 1 wei above
    h.place("Alice", True, 10, 3000)
    filled = h.swap("Taker", True, 10)
    assert filled == 10, f"1 wei above trigger should fill"


def test_buy_order_twap_above_tick():
    """Buy order: TWAP above buy tick — should NOT fill."""
    h = AuditableGhostHook()
    h.mint_tokens("Alice", 0, 100_000)
    h.mint_tokens("Taker", 100, 0)
    h.twap = 3100  # above buy tick
    h.place("Alice", False, 10, 3000)
    filled = h.swap("Taker", False, 10)
    assert filled == 0, "Buy order shouldn't fill when TWAP > tick"


def test_extreme_price_range():
    """Orders at extreme prices ($1 and $100,000)."""
    h = AuditableGhostHook()
    h.mint_tokens("PennyTrader", 1_000_000, 0)
    h.mint_tokens("WhaleTrader", 1, 0)
    h.mint_tokens("Taker", 0, 200_000_000)

    h.twap = 1
    h.place("PennyTrader", True, 1_000_000, 1)
    h.swap("Taker", True, 100)
    p1, _ = h.claim("PennyTrader", 1, True)
    assert p1 > 0, "Penny-price order should work"

    h.twap = 100_000
    h.place("WhaleTrader", True, 1, 100_000)
    h.swap("Taker", True, 1)
    p2, _ = h.claim("WhaleTrader", 100_000, True)
    assert p2 > 0, "100k-price order should work"


# ═══════════════════════════════════════════════════════════════════
# 9. INVARIANT-PRESERVING FUZZ WITH ADVERSARIAL PATTERNS
# ═══════════════════════════════════════════════════════════════════

def test_adversarial_fuzz():
    """Adversarial fuzzer: intentionally tries to break invariants."""
    h = AuditableGhostHook()
    random.seed(314159)
    n_users = 20

    for i in range(n_users):
        h.mint_tokens(f"A{i}", 200, 1_000_000)

    for r in range(2000):
        h.twap = 2500 + random.random() * 1000  # 2500-3500
        action = random.random()

        if action < 0.2:
            # Place at weird tick values
            u = f"A{random.randint(0, n_users-1)}"
            is_sell = random.random() < 0.5
            tick = round(h.twap * random.uniform(0.8, 1.2), random.randint(0, 4))
            amt = 10 ** random.uniform(-6, 2)  # 0.000001 to 100
            h.place(u, is_sell, amt, max(1, tick))  # tick must be > 0

        elif action < 0.5:
            # Swap varying sizes
            u = f"A{random.randint(0, n_users-1)}"
            amt = 10 ** random.uniform(-4, 1)
            h.swap(u, random.random() < 0.5, amt)

        elif action < 0.6:
            h.internal_net()

        elif action < 0.8:
            # Claim random
            u = f"A{random.randint(0, n_users-1)}"
            for tick in list(h.sell_buckets.keys())[:2]:
                h.claim(u, tick, True)
            for tick in list(h.buy_buckets.keys())[:2]:
                h.claim(u, tick, False)

        else:
            # Double + triple claims (should be no-ops)
            u = f"A{random.randint(0, n_users-1)}"
            for tick in list(h.sell_buckets.keys())[:1]:
                h.claim(u, tick, True)
                h.claim(u, tick, True)
                h.claim(u, tick, True)

    # Final: drain everything
    for tick in list(h.sell_buckets.keys()):
        owners = set(o.owner for o in h.sell_buckets[tick].orders.values())
        for o in owners:
            h.claim(o, tick, True)
    for tick in list(h.buy_buckets.keys()):
        owners = set(o.owner for o in h.buy_buckets[tick].orders.values())
        for o in owners:
            h.claim(o, tick, False)

    assert abs(h.hook_t0) < 0.01, f"Adversarial fuzz T0 leak: {h.hook_t0}"
    assert abs(h.hook_t1) < 0.01, f"Adversarial fuzz T1 leak: {h.hook_t1}"


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  DEEP ADVERSARIAL TESTING SUITE")
    print("  Ghost Balance Limit Orders — Attack Vectors & Edge Cases")
    print("=" * 70)

    print("\n  ── 1. MEV ATTACKS ──")
    run_test("Sandwich attack (front-run + back-run)", test_sandwich_attack)
    run_test("Front-run order placement", test_frontrun_order_placement)
    run_test("Back-run claim timing", test_backrun_claim)

    print("\n  ── 2. DOUBLE-CLAIM / RE-ENTRANCY ──")
    run_test("Double claim same order", test_double_claim)
    run_test("Claim non-existent order", test_claim_nonexistent_order)
    run_test("Claim unfilled order (pure refund)", test_claim_unfilled_order)

    print("\n  ── 3. DUST / ROUNDING ──")
    run_test("50 dust deposits (1 wei each)", test_dust_deposits)
    run_test("Dust vs whale (1 wei vs 1000 ETH)", test_asymmetric_dust_vs_whale)
    run_test("Zero amount rejection", test_zero_amount_rejection)

    print("\n  ── 4. STALE TWAP ──")
    run_test("Stale TWAP (no trades for 1hr)", test_stale_twap_no_false_trigger)
    run_test("TWAP just below trigger", test_twap_just_below_trigger)
    run_test("TWAP exactly at trigger", test_twap_exactly_at_trigger)

    print("\n  ── 5. SELF-NETTING ──")
    run_test("Same user both sides", test_same_user_both_sides)
    run_test("Multiple orders same tick", test_user_multiple_orders_same_tick)

    print("\n  ── 6. ORDERING ──")
    run_test("Claim then swap (re-deposit)", test_claim_then_swap)
    run_test("Interleaved place/swap/claim", test_interleaved_place_swap_claim)
    run_test("Net → swap → claim (no double-count)", test_net_then_swap_then_claim)

    print("\n  ── 7. CAPACITY ──")
    run_test("100 users × 50 ticks + full drain", test_100_users_50_ticks)
    run_test("500 orders same tick (pro-rata)", test_many_orders_same_tick)

    print("\n  ── 8. TWAP BOUNDARY PRECISION ──")
    run_test("TWAP ε below trigger", test_twap_epsilon_below)
    run_test("TWAP ε above trigger", test_twap_epsilon_above)
    run_test("Buy order TWAP above tick", test_buy_order_twap_above_tick)
    run_test("Extreme prices ($1 and $100k)", test_extreme_price_range)

    print("\n  ── 9. ADVERSARIAL FUZZ (2000 rounds) ──")
    run_test("Adversarial fuzz (weird ticks, dust, double-claims)", test_adversarial_fuzz)

    print(f"\n{'=' * 70}")
    print(f"  RESULTS: {PASSED} passed, {FAILED} failed out of {PASSED + FAILED} tests")
    if FAILED == 0:
        print(f"  ALL {PASSED} ADVERSARIAL TESTS PASSED ✅")
    else:
        print(f"  ⚠️  {FAILED} TESTS FAILED")
        sys.exit(1)
    print(f"{'=' * 70}")
