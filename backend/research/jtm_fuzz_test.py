#!/usr/bin/env python3
"""
Paranoid JTM TWAMM Fuzz Simulation
====================================
Comprehensive property-based test for the JTM earningsFactor accounting.

- Hundreds of random agents submitting orders with random sizes/durations/directions
- Arb bot that clears ANY accrued tokens on every step (greedy MEV bot)
- Invariants checked after EVERY state mutation
- Naive baseline: independently tracks expected earnings at the EXACT TWAP price
  used during each second of accrual (no discretization error)
- Runs BUGGY vs SNAPSHOT_FIX side-by-side
- Deterministic seeds for reproducing failures

Run:
    python3 backend/tools/jtm_fuzz_test.py
    python3 backend/tools/jtm_fuzz_test.py --seed 42 --users 200 --steps 1000
"""

import argparse
import math
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── Constants ───────────────────────────────────────────────────────

Q96 = 2**96
RATE_SCALER = 10**18
EXPIRATION_INTERVAL = 3600
DISCOUNT_RATE_BPS_PER_SEC = 1
MAX_DISCOUNT_BPS = 500
UINT256_MAX = 2**256 - 1


# ── Naive Baseline Tracker ──────────────────────────────────────────

@dataclass
class BaselineOrder:
    """Tracks what an order SHOULD earn if sold manually at TWAP."""
    oid: str
    owner: str
    zero_for_one: bool
    deposit: int            # total tokens deposited (sell side)
    sell_rate: int           # tokens per second (unscaled)
    start_time: int
    expiration: int
    # Accumulated expected earnings in buy-token terms
    expected_buy: int = 0    # what naive manual selling would yield
    last_tracked: int = 0

class NaiveBaseline:
    """Independently tracks expected per-order earnings by selling at TWAP each step."""
    def __init__(self):
        self.orders: Dict[str, BaselineOrder] = {}
        self.total_discount_given0: int = 0  # total discount given to arb bots (wRLP)
        self.total_discount_given1: int = 0  # total discount given to arb bots (waUSDC)

    def register_order(self, oid: str, owner: str, zfo: bool, deposit: int,
                        sell_rate_unscaled: int, start_time: int, expiration: int):
        self.orders[oid] = BaselineOrder(
            oid=oid, owner=owner, zero_for_one=zfo, deposit=deposit,
            sell_rate=sell_rate_unscaled, start_time=start_time,
            expiration=expiration, last_tracked=start_time
        )

    def advance_dt(self, dt: int, twap_price_raw: int):
        """Advance all active baseline orders by dt seconds at the given TWAP price.
        Called from inside the engine's accrual loop so price is exactly matched."""
        for bo in self.orders.values():
            if bo.last_tracked >= bo.expiration:
                continue
            tokens_sold = bo.sell_rate * dt
            if bo.zero_for_one:
                earned = (tokens_sold * twap_price_raw) // 10**6
            else:
                earned = (tokens_sold * 10**6) // twap_price_raw
            bo.expected_buy += earned
            bo.last_tracked += dt

    def record_discount(self, zfo: bool, discount_amount: int):
        """Track discount given to arb bots during clearing."""
        if zfo:
            self.total_discount_given1 += discount_amount  # waUSDC discount
        else:
            self.total_discount_given0 += discount_amount  # wRLP discount


# ── Data Structures ─────────────────────────────────────────────────

@dataclass
class Order:
    owner: str
    sell_rate: int
    earnings_factor_last: int
    expiration: int
    zero_for_one: bool
    deposit: int
    synced: bool = False
    synced_buy: int = 0   # actual buy tokens received at sync
    synced_ref: int = 0   # actual sell refund at sync

@dataclass
class StreamPool:
    sell_rate_current: int = 0
    earnings_factor_current: int = 0
    sell_rate_ending: Dict[int, int] = field(default_factory=dict)
    earnings_factor_at_interval: Dict[int, int] = field(default_factory=dict)

@dataclass
class State:
    stream0: StreamPool = field(default_factory=StreamPool)
    stream1: StreamPool = field(default_factory=StreamPool)
    accrued0: int = 0
    accrued1: int = 0
    last_update: int = 0
    last_clear: int = 0
    orders: Dict[str, Order] = field(default_factory=dict)
    balance0: int = 0
    balance1: int = 0
    synced_orders: List[str] = field(default_factory=list)
    total_claimed0: int = 0
    total_claimed1: int = 0


# ── Simulation Engine ───────────────────────────────────────────────

class JTMEngine:
    def __init__(self, fix: bool = False, twap_price: float = 3.38, price_rng=None):
        self.state = State()
        self.state.last_update = 1737770400
        self.state.last_clear = self.state.last_update
        self.fix = fix
        self.twap_price_raw = int(twap_price * 1e6)
        self.order_counter = 0
        self.step = 0
        self.baseline = NaiveBaseline()
        # GBM price walk: small per-epoch-step drift
        # vol = 0.0001 per second ≈ 0.36% per hour ≈ 8.6% per day (realistic crypto)
        self.price_vol = 0.0001
        self.price_rng = price_rng or random.Random(12345)
        self.price_drift = 0.0  # mean revert drift

    @property
    def now(self):
        return self.state.last_update

    def _interval(self, t: int) -> int:
        return (t // EXPIRATION_INTERVAL) * EXPIRATION_INTERVAL

    def _record_earnings(self, stream: StreamPool, earnings: int):
        if stream.sell_rate_current > 0 and earnings > 0:
            delta = (earnings * Q96 * RATE_SCALER) // stream.sell_rate_current
            stream.earnings_factor_current += delta

    def _internal_net(self):
        a0, a1 = self.state.accrued0, self.state.accrued1
        if a0 == 0 or a1 == 0:
            return
        value0_in_1 = (a0 * self.twap_price_raw) // 10**6
        if value0_in_1 <= a1:
            m0, m1 = a0, value0_in_1
        else:
            m1 = a1
            m0 = (a1 * 10**6) // self.twap_price_raw
        if m0 > 0 and m1 > 0:
            self.state.accrued0 -= m0
            self.state.accrued1 -= m1
            self._record_earnings(self.state.stream0, m1)
            self._record_earnings(self.state.stream1, m0)

    def _cross_epoch(self, stream: StreamPool, epoch: int):
        expiring = stream.sell_rate_ending.get(epoch, 0)
        if expiring > 0:
            stream.earnings_factor_at_interval[epoch] = stream.earnings_factor_current
            stream.sell_rate_current -= expiring

    def _accrue_and_net(self, t: int):
        if t <= self.state.last_update:
            return
        current = self.state.last_update
        while current < t:
            next_epoch = self._interval(current) + EXPIRATION_INTERVAL
            step_end = min(next_epoch, t)
            dt = step_end - current
            if dt > 0:
                # Walk price smoothly over this dt (in chunks up to 60s)
                accrual_steps = max(1, dt // 60)
                sub_dt = dt // accrual_steps
                remainder = dt - sub_dt * accrual_steps
                for si in range(accrual_steps):
                    sdt = sub_dt + (1 if si == 0 and remainder > 0 else 0)
                    if sdt <= 0:
                        continue
                    # GBM price step
                    shock = self.price_rng.gauss(0, self.price_vol * math.sqrt(sdt))
                    self.twap_price_raw = max(100000, int(self.twap_price_raw * math.exp(shock)))
                    # Accrue ghost balances
                    if self.state.stream0.sell_rate_current > 0:
                        self.state.accrued0 += (self.state.stream0.sell_rate_current * sdt) // RATE_SCALER
                    if self.state.stream1.sell_rate_current > 0:
                        self.state.accrued1 += (self.state.stream1.sell_rate_current * sdt) // RATE_SCALER
                    # Track baseline at the EXACT same price used for accrual
                    self.baseline.advance_dt(sdt, self.twap_price_raw)
            if step_end == next_epoch:
                if self.fix:
                    self._internal_net()
                    self._cross_epoch(self.state.stream0, next_epoch)
                    self._cross_epoch(self.state.stream1, next_epoch)
                else:
                    self._cross_epoch(self.state.stream0, next_epoch)
                    self._cross_epoch(self.state.stream1, next_epoch)
            current = step_end
        self._internal_net()
        self.state.last_update = t

    def submit_order(self, owner: str, zfo: bool, amount: int, duration: int, t: int) -> Optional[str]:
        self._accrue_and_net(t)
        dur_aligned = max(EXPIRATION_INTERVAL, (duration // EXPIRATION_INTERVAL) * EXPIRATION_INTERVAL)
        sell_rate = amount // dur_aligned
        if sell_rate == 0:
            return None
        scaled = sell_rate * RATE_SCALER
        ci = self._interval(t)
        exp = ci + dur_aligned
        self.order_counter += 1
        oid = f"O{self.order_counter}_{owner}"
        stream = self.state.stream0 if zfo else self.state.stream1
        stream.sell_rate_current += scaled
        stream.sell_rate_ending[exp] = stream.sell_rate_ending.get(exp, 0) + scaled
        actual = sell_rate * dur_aligned
        self.state.orders[oid] = Order(
            owner=owner, sell_rate=scaled,
            earnings_factor_last=stream.earnings_factor_current,
            expiration=exp, zero_for_one=zfo, deposit=actual
        )
        if zfo:
            self.state.balance0 += actual
        else:
            self.state.balance1 += actual
        # Register with baseline tracker
        self.baseline.register_order(oid, owner, zfo, actual, sell_rate,
                                      t, exp)
        return oid

    def get_order_state(self, oid: str) -> Tuple[int, int]:
        order = self.state.orders[oid]
        stream = self.state.stream0 if order.zero_for_one else self.state.stream1
        ef = stream.earnings_factor_current
        if self.fix and self.state.last_update >= order.expiration:
            snap = stream.earnings_factor_at_interval.get(order.expiration, 0)
            if snap > 0 and snap < ef:
                ef = snap
        ef_delta = ef - order.earnings_factor_last
        buy = (order.sell_rate * ef_delta) // (Q96 * RATE_SCALER) if ef_delta > 0 else 0
        ref = 0
        if self.state.last_update < order.expiration:
            remaining = order.expiration - self.state.last_update
            ref = (order.sell_rate * remaining) // RATE_SCALER
        return buy, ref

    def sync_order(self, oid: str, t: int) -> Tuple[int, int]:
        self._accrue_and_net(t)
        order = self.state.orders[oid]
        if order.synced:
            return 0, 0
        buy, ref = self.get_order_state(oid)
        order.synced = True
        order.synced_buy = buy
        order.synced_ref = ref
        order.earnings_factor_last = (self.state.stream0 if order.zero_for_one else self.state.stream1).earnings_factor_current
        if order.zero_for_one:
            self.state.total_claimed1 += buy
            self.state.total_claimed0 += ref
            self.state.balance1 -= buy
            self.state.balance0 -= ref
        else:
            self.state.total_claimed0 += buy
            self.state.total_claimed1 += ref
            self.state.balance0 -= buy
            self.state.balance1 -= ref
        self.state.synced_orders.append(oid)
        return buy, ref

    def clear(self, zfo: bool, t: int) -> int:
        self._accrue_and_net(t)
        avail = self.state.accrued0 if zfo else self.state.accrued1
        stream = self.state.stream0 if zfo else self.state.stream1
        if avail == 0 or stream.sell_rate_current == 0:
            return 0
        discount = min((t - self.state.last_clear) * DISCOUNT_RATE_BPS_PER_SEC, MAX_DISCOUNT_BPS)
        if zfo:
            full_payment = (avail * self.twap_price_raw * 10000) // (10**6 * 10000)
            payment = (avail * self.twap_price_raw * (10000 - discount)) // (10**6 * 10000)
            self.state.accrued0 = 0
            self._record_earnings(stream, payment)
            self.state.balance1 += payment
            self.baseline.record_discount(zfo, full_payment - payment)
        else:
            full_payment = (avail * 10**6 * 10000) // (self.twap_price_raw * 10000)
            payment = (avail * 10**6 * (10000 - discount)) // (self.twap_price_raw * 10000)
            self.state.accrued1 = 0
            self._record_earnings(stream, payment)
            self.state.balance0 += payment
            self.baseline.record_discount(zfo, full_payment - payment)
        self.state.last_clear = t
        return avail


# ── Invariant Checker ───────────────────────────────────────────────

class InvariantError(Exception):
    pass

def check_invariants(eng: JTMEngine, step: int, action: str):
    s = eng.state

    # Compute total demands
    total_buy0, total_buy1 = 0, 0
    total_ref0, total_ref1 = 0, 0

    for oid, order in s.orders.items():
        if order.synced:
            continue
        buy, ref = eng.get_order_state(oid)
        if order.zero_for_one:
            total_buy1 += buy   # wRLP sellers earn waUSDC
            total_ref0 += ref   # wRLP refunds
        else:
            total_buy0 += buy   # waUSDC sellers earn wRLP
            total_ref1 += ref   # waUSDC refunds

    demand0 = total_buy0 + total_ref0 + s.accrued0
    demand1 = total_buy1 + total_ref1 + s.accrued1
    ctx = f"step={step} action={action}"

    # INV 1 & 2: Token solvency
    if s.balance0 < demand0:
        deficit = (demand0 - s.balance0) / 1e6
        raise InvariantError(f"[INV-1] wRLP INSOLVENT: demand={demand0/1e6:.2f} supply={s.balance0/1e6:.2f} deficit={deficit:.2f} ({ctx})")
    if s.balance1 < demand1:
        deficit = (demand1 - s.balance1) / 1e6
        raise InvariantError(f"[INV-2] waUSDC INSOLVENT: demand={demand1/1e6:.2f} supply={s.balance1/1e6:.2f} deficit={deficit:.2f} ({ctx})")

    # INV 3: No negative accrued
    if s.accrued0 < 0:
        raise InvariantError(f"[INV-3] accrued0 < 0: {s.accrued0} ({ctx})")
    if s.accrued1 < 0:
        raise InvariantError(f"[INV-4] accrued1 < 0: {s.accrued1} ({ctx})")

    # INV 5: earningsFactorCurrent never negative
    if s.stream0.earnings_factor_current < 0:
        raise InvariantError(f"[INV-5] stream0 EF < 0 ({ctx})")
    if s.stream1.earnings_factor_current < 0:
        raise InvariantError(f"[INV-6] stream1 EF < 0 ({ctx})")

    # INV 7: No overflow (fits uint256)
    for name, val in [("balance0", s.balance0), ("balance1", s.balance1),
                       ("EF0", s.stream0.earnings_factor_current),
                       ("EF1", s.stream1.earnings_factor_current)]:
        if val > UINT256_MAX:
            raise InvariantError(f"[INV-7] {name} overflow: {val} ({ctx})")

    # INV 8: Synced expired orders should NOT earn more
    for oid in s.synced_orders[-10:]:  # check last 10 synced
        order = s.orders[oid]
        if order.synced and s.last_update >= order.expiration:
            buy, _ = eng.get_order_state(oid)
            if buy > 0:
                raise InvariantError(f"[INV-8] Synced expired order {oid} still earning {buy/1e6:.4f} ({ctx})")

    return demand0, demand1


# ── Arb Bot ─────────────────────────────────────────────────────────

def run_arb_bot(eng: JTMEngine, t: int) -> int:
    """Greedy arb bot: clears ANY accrued tokens immediately."""
    total = 0
    # Try both directions
    for zfo in [True, False]:
        cleared = eng.clear(zfo, t)
        total += cleared
    return total


# ── Simulation Runner ───────────────────────────────────────────────

@dataclass
class SimStats:
    orders_submitted: int = 0
    orders_synced: int = 0
    clears: int = 0
    total_cleared0: int = 0
    total_cleared1: int = 0
    max_deficit0: float = 0.0
    max_deficit1: float = 0.0
    invariant_violations: int = 0
    first_violation: str = ""
    steps_completed: int = 0


def run_fuzz(seed: int, num_users: int, num_steps: int, fix: bool, quiet: bool = False) -> SimStats:
    rng = random.Random(seed)
    label = "SNAPSHOT_FIX" if fix else "BUGGY"
    stats = SimStats()

    # Varying TWAP price — use a separate RNG so TWAP path is identical for BUGGY/FIXED
    twap_price = 3.38
    price_rng = random.Random(seed * 7919 + 1)  # deterministic but different from action rng
    eng = JTMEngine(fix=fix, twap_price=twap_price, price_rng=price_rng)

    # Generate agents with random starting capital
    agents = []
    for i in range(num_users):
        capital = 10**6 * int(10 ** rng.uniform(0, 6))  # $1 to $1M in token units
        agents.append({"name": f"U{i:03d}", "capital": capital, "orders": []})

    active_orders: List[str] = []
    expired_unsynced: List[str] = []
    t = eng.now

    if not quiet:
        print(f"\n{'='*60}")
        print(f"  FUZZ: {label}  seed={seed} users={num_users} steps={num_steps}")
        print(f"{'='*60}")

    for step in range(num_steps):
        # Slowly drift TWAP price — done inside _accrue_and_net now via GBM
        # No manual price override needed

        # Pick action
        roll = rng.random()

        action_desc = ""

        if roll < 0.40 and len(active_orders) < num_users * 3:
            # SUBMIT ORDER
            agent = rng.choice(agents)
            zfo = rng.random() < 0.5
            amount = int(10**6 * 10 ** rng.uniform(0, 5))  # $1 to $100K
            dur_intervals = rng.randint(1, 10)
            duration = dur_intervals * EXPIRATION_INTERVAL
            t += rng.randint(1, 120)  # 1s to 2min gap between submissions

            oid = eng.submit_order(agent["name"], zfo, amount, duration, t)
            if oid:
                active_orders.append(oid)
                agent["orders"].append(oid)
                stats.orders_submitted += 1
                action_desc = f"submit({agent['name']}, {'→' if zfo else '←'}, ${amount/1e6:.0f}, {dur_intervals}h)"

        elif roll < 0.60 and expired_unsynced:
            # SYNC EXPIRED ORDER
            oid = rng.choice(expired_unsynced)
            t += rng.randint(1, 30)
            try:
                buy, ref = eng.sync_order(oid, t)
                expired_unsynced.remove(oid)
                stats.orders_synced += 1
                action_desc = f"sync({oid})"
            except Exception as e:
                action_desc = f"sync_fail({oid}: {e})"

        elif roll < 0.80:
            # ADVANCE TIME
            advance = int(10 ** rng.uniform(0, 3.8))  # 1s to ~1h
            t += advance
            eng._accrue_and_net(t)
            action_desc = f"advance({advance}s)"

        else:
            # JUST ADVANCE SMALL
            t += rng.randint(1, 10)
            eng._accrue_and_net(t)
            action_desc = f"tick({t})"

        # ── ARB BOT: always try to clear ──
        cleared = run_arb_bot(eng, t)
        if cleared > 0:
            stats.clears += 1

        # Baseline is now tracked inside _accrue_and_net — no separate call needed

        # Update expired list
        new_active = []
        for oid in active_orders:
            order = eng.state.orders[oid]
            if not order.synced and t >= order.expiration:
                expired_unsynced.append(oid)
            elif not order.synced and t < order.expiration:
                new_active.append(oid)
        active_orders = new_active

        # ── INVARIANT CHECK ──
        try:
            d0, d1 = check_invariants(eng, step, action_desc)
            surplus0 = (eng.state.balance0 - d0) / 1e6
            surplus1 = (eng.state.balance1 - d1) / 1e6
        except InvariantError as e:
            stats.invariant_violations += 1
            if not stats.first_violation:
                stats.first_violation = str(e)
            if not quiet:
                print(f"  ❌ {e}")
            # Continue to find more violations
            d0 = d1 = 0
            surplus0 = surplus1 = 0

        stats.steps_completed = step + 1

        # Progress
        if not quiet and step % (num_steps // 10) == 0:
            n_orders = len([o for o in eng.state.orders.values() if not o.synced])
            print(f"  step {step:>5}/{num_steps}: orders={n_orders:>4} "
                  f"accrued=({eng.state.accrued0/1e6:.1f}, {eng.state.accrued1/1e6:.1f}) "
                  f"surplus=({surplus0:>+.1f}, {surplus1:>+.1f}) "
                  f"violations={stats.invariant_violations}")

    # Final sync of all remaining orders
    if not quiet:
        print(f"\n  Syncing {len(expired_unsynced)} remaining expired orders...")
    for oid in list(expired_unsynced):
        try:
            eng.sync_order(oid, t)
            stats.orders_synced += 1
        except Exception:
            pass

    # Active orders still running — sync at their expiration
    for oid in list(active_orders):
        order = eng.state.orders[oid]
        if not order.synced:
            exp_t = order.expiration + 1
            if exp_t > t:
                eng._accrue_and_net(exp_t)
                run_arb_bot(eng, exp_t)
                t = exp_t
            try:
                eng.sync_order(oid, t)
                stats.orders_synced += 1
            except Exception:
                pass

    # Final invariant check
    try:
        d0, d1 = check_invariants(eng, num_steps, "FINAL")
        stats.max_deficit0 = max(0, (d0 - eng.state.balance0)) / 1e6
        stats.max_deficit1 = max(0, (d1 - eng.state.balance1)) / 1e6
    except InvariantError as e:
        stats.invariant_violations += 1
        if not stats.first_violation:
            stats.first_violation = str(e)
        if not quiet:
            print(f"  ❌ FINAL: {e}")

    if not quiet:
        surplus0 = (eng.state.balance0 - d0) / 1e6 if 'd0' in dir() else 0
        surplus1 = (eng.state.balance1 - d1) / 1e6 if 'd1' in dir() else 0
        print(f"\n  ── {label} Results ──")
        print(f"  Orders: {stats.orders_submitted} submitted, {stats.orders_synced} synced")
        print(f"  Clears: {stats.clears}")
        print(f"  Violations: {stats.invariant_violations}")
        if stats.first_violation:
            print(f"  First:  {stats.first_violation[:120]}")
        print(f"  Final surplus: wRLP={surplus0:+.2f}  waUSDC={surplus1:+.2f}")

        # ── Naive Baseline Comparison ──
        # Baseline is already tracked at each accrual step inside the engine

        deviations = []
        total_expected_value = 0
        total_actual_value = 0

        for oid, order in eng.state.orders.items():
            bo = eng.baseline.orders.get(oid)
            if not bo:
                continue
            # Use synced earnings if available, otherwise live state
            if order.synced:
                actual_buy = order.synced_buy
            else:
                actual_buy, _ = eng.get_order_state(oid)
            expected_buy = bo.expected_buy
            if expected_buy > 0:
                dev_pct = ((actual_buy - expected_buy) / expected_buy) * 100
            elif actual_buy == 0:
                dev_pct = 0.0
            else:
                dev_pct = 100.0
            deviations.append((oid, bo.owner, bo.zero_for_one, bo.deposit,
                               expected_buy, actual_buy, dev_pct))

            # Compute total value in USD terms for comparison
            if bo.zero_for_one:
                # Sold wRLP, earned waUSDC — earnings are already in USD
                total_expected_value += expected_buy
                total_actual_value += actual_buy
            else:
                # Sold waUSDC, earned wRLP — convert to USD
                total_expected_value += (expected_buy * eng.twap_price_raw) // 10**6
                total_actual_value += (actual_buy * eng.twap_price_raw) // 10**6

        # Sort by absolute deviation
        deviations.sort(key=lambda x: abs(x[6]), reverse=True)

        print(f"\n  ── Value Accuracy (TWAMM vs Naive TWAP Sell) ──")
        print(f"  {'Order':<20} {'Dir':>4} {'Deposit':>12} {'Expected':>12} {'Actual':>12} {'Dev%':>8}")
        print(f"  {'─'*68}")
        # Show top 10 worst deviations
        for oid, owner, zfo, dep, exp_b, act_b, dev in deviations[:10]:
            d = '→' if zfo else '←'
            bt = 'waUSDC' if zfo else 'wRLP'
            icon = '✅' if abs(dev) < 5 else ('⚠️' if abs(dev) < 20 else '❌')
            print(f"  {oid:<20} {d:>4} {dep/1e6:>10.0f}  {exp_b/1e6:>10.2f}  {act_b/1e6:>10.2f}  {dev:>+7.1f}% {icon}")

        # Summary stats
        if deviations:
            devs = [d[6] for d in deviations if d[4] > 0]
            if devs:
                avg_dev = sum(devs) / len(devs)
                max_dev = max(devs, key=abs)
                within5 = sum(1 for d in devs if abs(d) < 5) / len(devs) * 100
                within10 = sum(1 for d in devs if abs(d) < 10) / len(devs) * 100

                print(f"\n  Avg deviation:    {avg_dev:+.2f}%")
                print(f"  Max deviation:    {max_dev:+.2f}%")
                print(f"  Within ±5%:       {within5:.0f}%")
                print(f"  Within ±10%:      {within10:.0f}%")

        # Total value conservation
        discount0 = eng.baseline.total_discount_given0
        discount1 = eng.baseline.total_discount_given1
        total_discount_usd = discount1 + (discount0 * eng.twap_price_raw) // 10**6
        if total_expected_value > 0:
            conservation = (total_actual_value + total_discount_usd) / total_expected_value * 100
            print(f"\n  Total expected:   ${total_expected_value/1e6:,.0f}")
            print(f"  Total actual:     ${total_actual_value/1e6:,.0f}")
            print(f"  Discount to arbs: ${total_discount_usd/1e6:,.0f}")
            print(f"  Conservation:     {conservation:.2f}% (actual + discounts / expected)")

    return stats


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Paranoid JTM TWAMM Fuzz Test")
    parser.add_argument("--seed", type=int, default=None, help="Random seed (default: random)")
    parser.add_argument("--users", type=int, default=200, help="Number of users (default: 200)")
    parser.add_argument("--steps", type=int, default=1000, help="Number of steps (default: 1000)")
    parser.add_argument("--runs", type=int, default=5, help="Number of seed runs (default: 5)")
    args = parser.parse_args()

    print("=" * 60)
    print("  PARANOID JTM TWAMM FUZZ TEST")
    print("=" * 60)
    print(f"  Users: {args.users}, Steps: {args.steps}, Runs: {args.runs}")

    seeds = []
    if args.seed is not None:
        seeds = [args.seed + i for i in range(args.runs)]
    else:
        seeds = [random.randint(0, 2**32) for _ in range(args.runs)]

    buggy_total_violations = 0
    fixed_total_violations = 0

    for seed in seeds:
        print(f"\n{'─'*60}")
        print(f"  SEED: {seed}")
        print(f"{'─'*60}")

        stats_buggy = run_fuzz(seed, args.users, args.steps, fix=False)
        stats_fixed = run_fuzz(seed, args.users, args.steps, fix=True)

        buggy_total_violations += stats_buggy.invariant_violations
        fixed_total_violations += stats_fixed.invariant_violations

        v_b = f"❌ {stats_buggy.invariant_violations}" if stats_buggy.invariant_violations else "✅ 0"
        v_f = f"❌ {stats_fixed.invariant_violations}" if stats_fixed.invariant_violations else "✅ 0"
        print(f"\n  Seed {seed}: BUGGY violations={v_b}  FIXED violations={v_f}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY ({args.runs} runs × {args.steps} steps)")
    print(f"{'='*60}")
    print(f"  BUGGY:        {buggy_total_violations} total violations")
    print(f"  SNAPSHOT_FIX: {fixed_total_violations} total violations")

    if fixed_total_violations == 0 and buggy_total_violations > 0:
        print(f"\n  🎉 FIX VERIFIED — BUGGY breaks, FIXED holds across all seeds!")
    elif fixed_total_violations == 0 and buggy_total_violations == 0:
        print(f"\n  ⚠️ No violations in either — consider more aggressive parameters")
    elif fixed_total_violations > 0:
        print(f"\n  🚨 FIX STILL HAS VIOLATIONS — needs investigation!")

    return 1 if fixed_total_violations > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
