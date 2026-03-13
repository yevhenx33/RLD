#!/usr/bin/env python3
"""
100K Users Stress Test: Ghost Balance Limit Orders
=====================================================
Proves solvency and correctness at production-scale user counts.

Key design choices for performance:
  - Invariant checks every 5,000 ops (not every op) — still catches bugs
  - Lightweight engine (no step_log, no per-op audit trail)
  - Final full-drain proves claimability for all 100k users
  - Conservation check at drain: sum(all_wallets) == sum(minted)

Run: python3 backend/tools/ghost_100k_stress.py
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple
import random, time, os, sys

CHART_DIR = os.path.join(os.path.dirname(__file__), "ghost_limit_charts")
EPSILON = 1e-6


# ═══════════════════════════════════════════════════════════════════
# Lean Engine (optimized for 100k users)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Order:
    __slots__ = ['owner_id', 'tick', 'original', 'remaining', 'is_sell']
    owner_id: int
    tick: int
    original: float
    remaining: float
    is_sell: bool

@dataclass
class Bucket:
    total_deposit: float = 0.0
    accumulator: float = 0.0
    total_original: float = 0.0
    t1_surplus: float = 0.0
    orders: List = field(default_factory=list)  # List[Order] — faster than dict


class LeanGhostHook:
    """Optimized Ghost Balance engine for 100k-user stress testing."""

    def __init__(self, n_users: int):
        self.n_users = n_users
        self.sell_buckets: Dict[int, Bucket] = {}
        self.buy_buckets: Dict[int, Bucket] = {}
        self.twap: float = 3000.0

        # Wallets as parallel arrays (faster than dict of dicts)
        self.wallet_t0 = [0.0] * n_users
        self.wallet_t1 = [0.0] * n_users
        self.hook_t0: float = 0.0
        self.hook_t1: float = 0.0
        self.total_t0_minted: float = 0.0
        self.total_t1_minted: float = 0.0

        # Stats
        self.op_count = 0
        self.invariant_checks = 0

    def mint(self, uid: int, t0: float, t1: float):
        self.wallet_t0[uid] += t0
        self.wallet_t1[uid] += t1
        self.total_t0_minted += t0
        self.total_t1_minted += t1

    def place(self, uid: int, is_sell: bool, amount: float, tick: int) -> bool:
        if is_sell:
            if self.wallet_t0[uid] < amount - EPSILON:
                return False
            self.wallet_t0[uid] -= amount
            self.hook_t0 += amount
            bucket = self.sell_buckets.setdefault(tick, Bucket())
        else:
            cost = amount * tick
            if self.wallet_t1[uid] < cost - EPSILON:
                return False
            self.wallet_t1[uid] -= cost
            self.hook_t1 += cost
            bucket = self.buy_buckets.setdefault(tick, Bucket())

        order = Order(uid, tick, amount, amount, is_sell)
        bucket.orders.append(order)
        bucket.total_deposit += amount
        bucket.total_original += amount
        self.op_count += 1
        return True

    def swap(self, uid: int, buy_token0: bool, amount: float) -> float:
        filled = 0.0
        remaining = amount

        if buy_token0:
            eligible = sorted(
                [(t, b) for t, b in self.sell_buckets.items()
                 if self.twap >= t and b.total_deposit > EPSILON],
                key=lambda x: x[0]
            )
            for tick, bucket in eligible:
                if remaining <= EPSILON:
                    break
                fill = min(remaining, bucket.total_deposit)
                payment = fill * self.twap

                if self.wallet_t1[uid] < payment - EPSILON:
                    fill = self.wallet_t1[uid] / self.twap
                    payment = fill * self.twap
                    if fill < EPSILON:
                        continue

                ratio = fill / bucket.total_deposit if bucket.total_deposit > EPSILON else 0
                for order in bucket.orders:
                    order.remaining -= order.remaining * ratio

                bucket.total_deposit -= fill
                bucket.accumulator += payment
                self.hook_t0 -= fill
                self.hook_t1 += payment
                self.wallet_t0[uid] += fill
                self.wallet_t1[uid] -= payment
                filled += fill
                remaining -= fill
        else:
            eligible = sorted(
                [(t, b) for t, b in self.buy_buckets.items()
                 if self.twap <= t and b.total_deposit > EPSILON],
                key=lambda x: -x[0]
            )
            for tick, bucket in eligible:
                if remaining <= EPSILON:
                    break
                fill = min(remaining, bucket.total_deposit)
                payment = fill * self.twap

                if self.wallet_t0[uid] < fill - EPSILON:
                    fill = self.wallet_t0[uid]
                    payment = fill * self.twap
                    if fill < EPSILON:
                        continue

                ratio = fill / bucket.total_deposit if bucket.total_deposit > EPSILON else 0
                for order in bucket.orders:
                    order.remaining -= order.remaining * ratio

                bucket.total_deposit -= fill
                bucket.accumulator += fill
                bucket.t1_surplus += fill * (tick - self.twap)
                self.hook_t1 -= payment
                self.hook_t0 += fill
                self.wallet_t1[uid] += payment
                self.wallet_t0[uid] -= fill
                filled += fill
                remaining -= fill

        self.op_count += 1
        return filled

    def internal_net(self):
        for sell_tick, sb in list(self.sell_buckets.items()):
            if sb.total_deposit <= EPSILON or self.twap < sell_tick:
                continue
            for buy_tick, bb in list(self.buy_buckets.items()):
                if bb.total_deposit <= EPSILON or self.twap > buy_tick:
                    continue
                if sell_tick > buy_tick:
                    continue

                match = min(sb.total_deposit, bb.total_deposit)
                if match <= EPSILON:
                    continue
                payment = match * self.twap

                if sb.total_deposit > EPSILON:
                    ratio = match / sb.total_deposit
                    for o in sb.orders:
                        o.remaining -= o.remaining * ratio
                sb.total_deposit -= match
                sb.accumulator += payment

                if bb.total_deposit > EPSILON:
                    ratio = match / bb.total_deposit
                    for o in bb.orders:
                        o.remaining -= o.remaining * ratio
                bb.total_deposit -= match
                bb.accumulator += match
                bb.t1_surplus += match * (buy_tick - self.twap)

        self.op_count += 1

    def claim(self, uid: int, tick: int, is_sell: bool) -> Tuple[float, float]:
        buckets = self.sell_buckets if is_sell else self.buy_buckets
        bucket = buckets.get(tick)
        if not bucket:
            return 0.0, 0.0

        total_proceeds = 0.0
        total_refund = 0.0
        remaining_orders = []

        for order in bucket.orders:
            if order.owner_id != uid:
                remaining_orders.append(order)
                continue

            if bucket.total_original > EPSILON and bucket.accumulator > EPSILON:
                share = (order.original / bucket.total_original) * bucket.accumulator
            else:
                share = 0.0

            refund = order.remaining

            if is_sell:
                self.wallet_t1[uid] += share
                self.hook_t1 -= share
                if refund > EPSILON:
                    self.wallet_t0[uid] += refund
                    self.hook_t0 -= refund
            else:
                self.wallet_t0[uid] += share
                self.hook_t0 -= share
                if refund > EPSILON:
                    refund_t1 = refund * tick
                    self.wallet_t1[uid] += refund_t1
                    self.hook_t1 -= refund_t1
                if bucket.t1_surplus > EPSILON and bucket.total_original > EPSILON:
                    surplus_share = (order.original / bucket.total_original) * bucket.t1_surplus
                    self.wallet_t1[uid] += surplus_share
                    self.hook_t1 -= surplus_share
                    bucket.t1_surplus -= surplus_share

            bucket.accumulator -= share
            bucket.total_deposit -= refund
            bucket.total_original -= order.original
            total_proceeds += share
            total_refund += refund

        bucket.orders = remaining_orders
        self.op_count += 1
        return total_proceeds, total_refund

    def check_invariants(self) -> bool:
        """Check conservation and solvency. Returns True if all pass."""
        self.invariant_checks += 1

        # Conservation
        total_t0 = self.hook_t0 + sum(self.wallet_t0)
        total_t1 = self.hook_t1 + sum(self.wallet_t1)
        if abs(total_t0 - self.total_t0_minted) > EPSILON:
            raise AssertionError(f"T0 conservation: {total_t0} vs {self.total_t0_minted}")
        if abs(total_t1 - self.total_t1_minted) > EPSILON:
            raise AssertionError(f"T1 conservation: {total_t1} vs {self.total_t1_minted}")

        # Solvency
        pending_t0 = sum(b.accumulator for b in self.buy_buckets.values())
        pending_t0 += sum(b.total_deposit for b in self.sell_buckets.values())
        pending_t1 = sum(b.accumulator for b in self.sell_buckets.values())
        pending_t1 += sum(b.total_deposit * t for t, b in self.buy_buckets.items())
        pending_t1 += sum(b.t1_surplus for b in self.buy_buckets.values())

        if self.hook_t0 < pending_t0 - EPSILON:
            raise AssertionError(f"T0 solvency: hook={self.hook_t0}, owes={pending_t0}")
        if self.hook_t1 < pending_t1 - EPSILON:
            raise AssertionError(f"T1 solvency: hook={self.hook_t1}, owes={pending_t1}")

        # No negative wallets (sample 1000 random)
        sample = random.sample(range(self.n_users), min(1000, self.n_users))
        for uid in sample:
            if self.wallet_t0[uid] < -EPSILON:
                raise AssertionError(f"User {uid} T0 negative: {self.wallet_t0[uid]}")
            if self.wallet_t1[uid] < -EPSILON:
                raise AssertionError(f"User {uid} T1 negative: {self.wallet_t1[uid]}")

        return True


# ═══════════════════════════════════════════════════════════════════
# Stress Test Runner
# ═══════════════════════════════════════════════════════════════════

def run_100k_stress(n_users: int = 100_000, n_rounds: int = 50_000, seed: int = 42):
    print(f"\n  Initializing {n_users:,} users...")
    t0 = time.time()
    random.seed(seed)

    hook = LeanGhostHook(n_users)

    # Mint: each user gets 10 T0 and 30,000 T1
    for uid in range(n_users):
        hook.mint(uid, 10.0, 30_000.0)

    t_mint = time.time() - t0
    print(f"  Minted in {t_mint:.1f}s")

    # Separate maker and taker pools (80% makers, 20% takers)
    n_makers = int(n_users * 0.8)
    n_takers = n_users - n_makers

    price = 3000.0
    hook.twap = price
    placed_ticks: Dict[int, List[Tuple[int, bool]]] = {}  # tick -> [(uid, is_sell)]

    stats = {"place": 0, "swap": 0, "net": 0, "claim": 0}
    check_interval = max(1, n_rounds // 10)

    print(f"  Running {n_rounds:,} rounds...")
    t_start = time.time()

    for r in range(n_rounds):
        # Price walk
        price *= 1 + random.gauss(0, 0.003)
        price = max(price, 2500)
        price = min(price, 3500)
        hook.twap = hook.twap * 0.8 + price * 0.2

        action = random.random()

        if action < 0.35:
            uid = random.randint(0, n_makers - 1)
            is_sell = random.random() < 0.5
            tick = round(price * random.uniform(0.97, 1.03))
            amt = round(random.uniform(0.01, 2.0), 6)
            if hook.place(uid, is_sell, amt, tick):
                placed_ticks.setdefault(tick, []).append((uid, is_sell))
                stats["place"] += 1

        elif action < 0.6:
            uid = random.randint(n_makers, n_users - 1)
            buy_t0 = random.random() < 0.5
            amt = round(random.uniform(0.1, 5.0), 6)
            hook.swap(uid, buy_t0, amt)
            stats["swap"] += 1

        elif action < 0.7:
            hook.internal_net()
            stats["net"] += 1

        elif action < 0.9 and placed_ticks:
            tick = random.choice(list(placed_ticks.keys()))
            entries = placed_ticks[tick]
            if entries:
                uid, is_sell = random.choice(entries)
                hook.claim(uid, tick, is_sell)
                stats["claim"] += 1

        # Periodic invariant check
        if (r + 1) % check_interval == 0:
            hook.check_invariants()
            elapsed = time.time() - t_start
            ops_per_sec = (r + 1) / elapsed if elapsed > 0 else 0
            print(f"  Round {r+1:>6,}/{n_rounds:,} | "
                  f"checks: {hook.invariant_checks} | "
                  f"{ops_per_sec:,.0f} ops/s | "
                  f"ticks: {len(hook.sell_buckets) + len(hook.buy_buckets)}")

    t_rounds = time.time() - t_start
    print(f"\n  Rounds complete in {t_rounds:.1f}s ({n_rounds/t_rounds:,.0f} ops/s)")
    print(f"  Operations: {stats}")

    return hook, stats


def full_drain(hook: LeanGhostHook):
    """Drain ALL orders and verify hook is empty."""
    print(f"\n  ── FULL DRAIN ({hook.n_users:,} users) ──")
    t0 = time.time()

    # Drain all sell buckets
    for tick in list(hook.sell_buckets.keys()):
        bucket = hook.sell_buckets[tick]
        # Group by owner for efficiency
        owners = set(o.owner_id for o in bucket.orders)
        for uid in owners:
            hook.claim(uid, tick, True)

    # Drain all buy buckets
    for tick in list(hook.buy_buckets.keys()):
        bucket = hook.buy_buckets[tick]
        owners = set(o.owner_id for o in bucket.orders)
        for uid in owners:
            hook.claim(uid, tick, False)

    t_drain = time.time() - t0
    print(f"  Drain complete in {t_drain:.1f}s")

    # Verify hook is empty
    r_sell = sum(b.total_deposit for b in hook.sell_buckets.values())
    r_buy = sum(b.total_deposit for b in hook.buy_buckets.values())
    a_sell = sum(b.accumulator for b in hook.sell_buckets.values())
    a_buy = sum(b.accumulator for b in hook.buy_buckets.values())

    print(f"  Hook T0:          {hook.hook_t0:>20.10f}")
    print(f"  Hook T1:          {hook.hook_t1:>20.10f}")
    print(f"  Sell deposits:    {r_sell:>20.10f}")
    print(f"  Buy deposits:     {r_buy:>20.10f}")
    print(f"  Sell accumulator: {a_sell:>20.10f}")
    print(f"  Buy accumulator:  {a_buy:>20.10f}")

    # Tolerance scales with number of operations (floating-point accumulation)
    tol = EPSILON * hook.op_count
    assert abs(r_sell) < tol, f"Sell deposits not drained: {r_sell}"
    assert abs(r_buy) < tol, f"Buy deposits not drained: {r_buy}"
    assert abs(a_sell) < tol, f"Sell accum not drained: {a_sell}"
    assert abs(a_buy) < tol, f"Buy accum not drained: {a_buy}"
    assert abs(hook.hook_t0) < tol, f"Hook T0 not empty: {hook.hook_t0}"
    assert abs(hook.hook_t1) < tol, f"Hook T1 not empty: {hook.hook_t1}"

    # Final conservation
    total_t0 = sum(hook.wallet_t0)
    total_t1 = sum(hook.wallet_t1)
    assert abs(total_t0 - hook.total_t0_minted) < tol, \
        f"T0 conservation: {total_t0} vs {hook.total_t0_minted}, diff={total_t0 - hook.total_t0_minted}"
    assert abs(total_t1 - hook.total_t1_minted) < tol, \
        f"T1 conservation: {total_t1} vs {hook.total_t1_minted}, diff={total_t1 - hook.total_t1_minted}"

    print(f"  FULL DRAIN: Hook empty ✅")
    print(f"  CONSERVATION: All tokens accounted for ✅")

    # Check all 100k users have non-negative balances
    neg_t0 = sum(1 for x in hook.wallet_t0 if x < -EPSILON)
    neg_t1 = sum(1 for x in hook.wallet_t1 if x < -EPSILON)
    assert neg_t0 == 0, f"{neg_t0} users have negative T0"
    assert neg_t1 == 0, f"{neg_t1} users have negative T1"
    print(f"  ALL {hook.n_users:,} USER BALANCES NON-NEGATIVE ✅")

    return total_t0, total_t1


def distribution_analysis(hook: LeanGhostHook):
    """Analyze the wealth distribution — did anyone get phantom tokens?"""
    print(f"\n  ── DISTRIBUTION ANALYSIS ──")

    mint_t0 = 10.0
    mint_t1 = 30_000.0

    # Check for users who ended up with more than they could possibly have
    max_possible_t0 = mint_t0 * 10  # generous upper bound
    max_possible_t1 = mint_t1 * 10

    suspicious_t0 = sum(1 for x in hook.wallet_t0 if x > max_possible_t0)
    suspicious_t1 = sum(1 for x in hook.wallet_t1 if x > max_possible_t1)

    # Stats
    avg_t0 = sum(hook.wallet_t0) / hook.n_users
    avg_t1 = sum(hook.wallet_t1) / hook.n_users
    min_t0 = min(hook.wallet_t0)
    max_t0 = max(hook.wallet_t0)
    min_t1 = min(hook.wallet_t1)
    max_t1 = max(hook.wallet_t1)

    print(f"  Token0: avg={avg_t0:.4f}  min={min_t0:.4f}  max={max_t0:.4f}")
    print(f"  Token1: avg={avg_t1:.2f}  min={min_t1:.2f}  max={max_t1:.2f}")
    print(f"  Suspicious T0 balances: {suspicious_t0}")
    print(f"  Suspicious T1 balances: {suspicious_t1}")

    # The average should equal the mint amount (conservation)
    assert abs(avg_t0 - mint_t0) < 0.1, f"Average T0 drifted: {avg_t0} vs {mint_t0}"
    assert abs(avg_t1 - mint_t1) < 100, f"Average T1 drifted: {avg_t1} vs {mint_t1}"
    print(f"  AVERAGES MATCH MINTS ✅ (conservation holds in aggregate)")


def generate_chart(hook: LeanGhostHook, stats: dict, elapsed: float):
    """Generate audit summary chart."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  matplotlib not installed, skipping chart")
        return None

    os.makedirs(CHART_DIR, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.patch.set_facecolor('#0d1117')

    def style_ax(ax, title):
        ax.set_facecolor('#161b22')
        ax.set_title(title, color='white', fontsize=12, fontweight='bold', pad=10)
        ax.tick_params(colors='#8b949e')
        for spine in ax.spines.values():
            spine.set_color('#30363d')

    # 1. Token0 distribution (histogram of 100k balances)
    ax1 = axes[0]
    sample_t0 = random.sample(hook.wallet_t0, min(10000, hook.n_users))
    ax1.hist(sample_t0, bins=80, color='#7ee787', alpha=0.8, edgecolor='#30363d')
    ax1.axvline(10.0, color='#ff7b72', linestyle='--', linewidth=1.5, label='Initial mint')
    style_ax(ax1, f'Token0 Distribution ({hook.n_users:,} users)')
    ax1.set_xlabel('Balance', color='#8b949e')
    ax1.legend(fontsize=8, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # 2. Token1 distribution
    ax2 = axes[1]
    sample_t1 = random.sample(hook.wallet_t1, min(10000, hook.n_users))
    ax2.hist(sample_t1, bins=80, color='#58a6ff', alpha=0.8, edgecolor='#30363d')
    ax2.axvline(30000.0, color='#ff7b72', linestyle='--', linewidth=1.5, label='Initial mint')
    style_ax(ax2, f'Token1 Distribution ({hook.n_users:,} users)')
    ax2.set_xlabel('Balance', color='#8b949e')
    ax2.legend(fontsize=8, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # 3. Audit scorecard
    ax3 = axes[2]
    lines = [
        f"{hook.n_users:,} users",
        f"{hook.op_count:,} operations",
        f"{hook.invariant_checks} invariant checks",
        f"{elapsed:.1f}s total runtime",
        "",
        "ALL PASSED",
    ]
    for i, line in enumerate(lines):
        color = '#7ee787' if i == len(lines) - 1 else '#c9d1d9'
        size = 18 if i == len(lines) - 1 else 11
        weight = 'bold' if i == len(lines) - 1 else 'normal'
        ax3.text(0.5, 0.85 - i * 0.12, line, ha='center', va='center',
                fontsize=size, color=color, fontweight=weight, transform=ax3.transAxes)

    checks = ["✓ Conservation", "✓ Solvency", "✓ Full Drain", "✓ No Negative", "✓ Distribution"]
    for i, c in enumerate(checks):
        ax3.text(0.5, 0.2 - i * 0.07, c, ha='center', va='center',
                fontsize=9, color='#7ee787', transform=ax3.transAxes)
    style_ax(ax3, '100K Stress Audit')
    ax3.set_xticks([])
    ax3.set_yticks([])

    fig.suptitle(f'100K Users Stress Test — Ghost Balance Solvency Proof',
                 color='white', fontsize=14, fontweight='bold', y=1.02)
    fig.tight_layout()

    path = os.path.join(CHART_DIR, "100k_stress_test.png")
    fig.savefig(path, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    plt.close(fig)
    print(f"  Chart saved: {path}")
    return path


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    N_USERS = 100_000
    N_ROUNDS = 50_000

    print("=" * 70)
    print(f"  100K USERS STRESS TEST")
    print(f"  {N_USERS:,} users | {N_ROUNDS:,} rounds")
    print(f"  Conservation + Solvency + Full Drain + Distribution")
    print("=" * 70)

    t_total = time.time()

    hook, stats = run_100k_stress(N_USERS, N_ROUNDS, seed=42)

    print(f"\n  Invariant checks during run: {hook.invariant_checks}")
    print(f"  Total operations: {hook.op_count:,}")

    # Full drain — the ultimate test
    full_drain(hook)

    # Distribution analysis
    distribution_analysis(hook)

    elapsed = time.time() - t_total

    print(f"\n{'=' * 70}")
    print(f"  FINAL VERDICT")
    print(f"  {N_USERS:,} users | {hook.op_count:,} operations | {elapsed:.1f}s")
    print(f"  CONSERVATION: ✅  sum(all_wallets) == sum(minted)")
    print(f"  SOLVENCY:     ✅  Hook held enough at every check")
    print(f"  FULL DRAIN:   ✅  Hook empty after all claims")
    print(f"  NO NEGATIVE:  ✅  All {N_USERS:,} balances ≥ 0")
    print(f"  DISTRIBUTION: ✅  Averages match mints")
    print(f"{'=' * 70}")

    chart = generate_chart(hook, stats, elapsed)
