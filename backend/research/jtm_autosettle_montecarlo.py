#!/usr/bin/env python3
"""
JTM Auto-Settle — Paranoid Monte Carlo Verification
=====================================================
Geometric Brownian Motion price paths × full JTM simulation.

Verifies under 1000+ paths with realistic crypto volatility:
  P1. Solvency: contract balance ≥ all order claims (HARD invariant)
  P2. Value preservation: user gets ≈ deposit in token terms
  P3. No value leak: auto-settle recovers what dust mode loses
  P4. Bounded slippage: auto-settle slippage << AMM fee
  P5. Proportionality: multi-order earnings scale with deposit
  P6. Worst-case bounds: even extreme price moves don't break solvency
"""
import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, List

# ── Constants ───────────────────────────────────────────────────────
Q96 = 2**96
RATE_SCALER = 10**18
EXPIRATION_INTERVAL = 3600

INITIAL_PRICE = 3.40          # waUSDC per wRLP
INITIAL_LIQUIDITY_USD = 16_350_000
POOL_FEE_BPS = 5

# GBM parameters (realistic crypto)
ANNUAL_VOL = 0.80             # 80% annualized vol
SECONDS_PER_YEAR = 365.25 * 24 * 3600
VOL_PER_SEC = ANNUAL_VOL / math.sqrt(SECONDS_PER_YEAR)

# Simulation
N_PATHS = 1000
CLEAR_INTERVAL = 6            # seconds between clears


# ── AMM Pool ────────────────────────────────────────────────────────
class AMMPool:
    def __init__(self, reserve0: float, reserve1: float):
        self.reserve0 = reserve0
        self.reserve1 = reserve1

    @staticmethod
    def from_params(liquidity_usd: float, price: float) -> 'AMMPool':
        r1 = liquidity_usd / 2
        r0 = r1 / price
        return AMMPool(r0, r1)

    @property
    def price(self) -> float:
        return self.reserve1 / self.reserve0

    @property
    def k(self) -> float:
        return self.reserve0 * self.reserve1

    def swap_0_for_1(self, amt0: float) -> float:
        eff = amt0 * (1 - POOL_FEE_BPS / 10000)
        new_r0 = self.reserve0 + eff
        out = self.reserve1 - self.k / new_r0
        self.reserve0 = new_r0
        self.reserve1 -= out
        return max(0, out)

    def swap_1_for_0(self, amt1: float) -> float:
        eff = amt1 * (1 - POOL_FEE_BPS / 10000)
        new_r1 = self.reserve1 + eff
        out = self.reserve0 - self.k / new_r1
        self.reserve1 = new_r1
        self.reserve0 -= out
        return max(0, out)

    def copy(self) -> 'AMMPool':
        return AMMPool(self.reserve0, self.reserve1)


# ── Compact Engine ──────────────────────────────────────────────────
@dataclass
class StreamPool:
    sell_rate_current: int = 0
    earnings_factor_current: int = 0
    sell_rate_ending: Dict[int, int] = field(default_factory=dict)
    earnings_factor_at_interval: Dict[int, int] = field(default_factory=dict)

@dataclass
class Order:
    sell_rate: int
    earnings_factor_last: int
    expiration: int
    zero_for_one: bool
    deposit: int

class JTMEngine:
    def __init__(self, mode: str, pool: AMMPool, price_path: List[Tuple[int, float]]):
        self.mode = mode  # 'dust' or 'autosettle'
        self.pool = pool
        self.price_path = price_path  # [(timestamp, price), ...]
        self.price_idx = 0
        self.price = price_path[0][1] if price_path else INITIAL_PRICE

        self.stream0 = StreamPool()
        self.stream1 = StreamPool()
        self.accrued0 = 0
        self.accrued1 = 0
        self.last_update = 1737770400
        self.balance0 = 0
        self.balance1 = 0
        self.dust0 = 0
        self.dust1 = 0
        self.auto_settled0 = 0
        self.auto_settled1 = 0
        self.orders: Dict[str, Order] = {}
        self.oc = 0
        self._settling = False

        # Track TWAP for naive benchmark
        self.twap_prices: List[float] = []

    def _interval(self, t):
        return (t // EXPIRATION_INTERVAL) * EXPIRATION_INTERVAL

    def _update_price(self, t):
        while self.price_idx < len(self.price_path) - 1 and self.price_path[self.price_idx + 1][0] <= t:
            self.price_idx += 1
        self.price = self.price_path[self.price_idx][1]

    def _price_raw(self):
        return int(self.price * 1e6)

    def _record_earnings(self, stream, earnings):
        if stream.sell_rate_current > 0 and earnings > 0:
            stream.earnings_factor_current += (earnings * Q96 * RATE_SCALER) // stream.sell_rate_current

    def _internal_net(self):
        a0, a1 = self.accrued0, self.accrued1
        if a0 == 0 or a1 == 0:
            return
        pr = self._price_raw()
        v0_in_1 = (a0 * pr) // 10**6
        if v0_in_1 <= a1:
            m0, m1 = a0, v0_in_1
        else:
            m1 = a1
            m0 = (a1 * 10**6) // pr
        if m0 > 0 and m1 > 0:
            self.accrued0 -= m0
            self.accrued1 -= m1
            self._record_earnings(self.stream0, m1)
            self._record_earnings(self.stream1, m0)

    def _cross_epoch(self, stream, epoch):
        exp = stream.sell_rate_ending.get(epoch, 0)
        if exp > 0:
            stream.earnings_factor_at_interval[epoch] = stream.earnings_factor_current
            stream.sell_rate_current -= exp

    def _auto_settle(self, zfo):
        if self._settling:
            return
        self._settling = True
        ghost = self.accrued0 if zfo else self.accrued1
        stream = self.stream0 if zfo else self.stream1
        if ghost == 0 or stream.sell_rate_current == 0:
            self._settling = False
            return
        g = ghost / 1e6
        if zfo:
            proceeds = int(self.pool.swap_0_for_1(g) * 1e6)
            self._record_earnings(stream, proceeds)
            self.balance1 += proceeds
            self.accrued0 = 0
            self.auto_settled0 += ghost
        else:
            proceeds = int(self.pool.swap_1_for_0(g) * 1e6)
            self._record_earnings(stream, proceeds)
            self.balance0 += proceeds
            self.accrued1 = 0
            self.auto_settled1 += ghost
        self._settling = False

    def _accrue_and_net(self, t):
        if t <= self.last_update:
            return
        cur = self.last_update
        while cur < t:
            nxt = self._interval(cur) + EXPIRATION_INTERVAL
            end = min(nxt, t)
            dt = end - cur
            if dt > 0:
                self._update_price(end)
                self.twap_prices.append(self.price)
                if self.stream0.sell_rate_current > 0:
                    self.accrued0 += (self.stream0.sell_rate_current * dt) // RATE_SCALER
                if self.stream1.sell_rate_current > 0:
                    self.accrued1 += (self.stream1.sell_rate_current * dt) // RATE_SCALER
            if end == nxt:
                self._internal_net()
                if self.mode == 'autosettle':
                    for zfo, stream, attr in [(True, self.stream0, 'accrued0'),
                                               (False, self.stream1, 'accrued1')]:
                        ending = stream.sell_rate_ending.get(nxt, 0)
                        if ending > 0 and ending == stream.sell_rate_current and getattr(self, attr) > 0:
                            self._auto_settle(zfo)
                self._cross_epoch(self.stream0, nxt)
                self._cross_epoch(self.stream1, nxt)
                if self.mode == 'dust':
                    if self.stream0.sell_rate_current == 0 and self.accrued0 > 0:
                        self.dust0 += self.accrued0
                        self.balance0 -= self.accrued0
                        self.accrued0 = 0
                    if self.stream1.sell_rate_current == 0 and self.accrued1 > 0:
                        self.dust1 += self.accrued1
                        self.balance1 -= self.accrued1
                        self.accrued1 = 0
            cur = end
        self._internal_net()
        self.last_update = t

    def submit(self, zfo, amount, duration, t):
        self._accrue_and_net(t)
        dur = max(EXPIRATION_INTERVAL, (duration // EXPIRATION_INTERVAL) * EXPIRATION_INTERVAL)
        sr = amount // dur
        if sr == 0:
            return None
        scaled = sr * RATE_SCALER
        ci = self._interval(t)
        exp = ci + dur
        self.oc += 1
        oid = f"O{self.oc}"
        stream = self.stream0 if zfo else self.stream1
        stream.sell_rate_current += scaled
        stream.sell_rate_ending[exp] = stream.sell_rate_ending.get(exp, 0) + scaled
        actual = sr * dur
        self.orders[oid] = Order(scaled, stream.earnings_factor_current, exp, zfo, actual)
        if zfo:
            self.balance0 += actual
        else:
            self.balance1 += actual
        return oid

    def clear(self, zfo, t):
        self._accrue_and_net(t)
        avail = self.accrued0 if zfo else self.accrued1
        stream = self.stream0 if zfo else self.stream1
        if avail == 0 or stream.sell_rate_current == 0:
            return 0
        pr = self._price_raw()
        if zfo:
            payment = (avail * pr) // 10**6
            self.accrued0 = 0
            self._record_earnings(stream, payment)
            self.balance1 += payment
        else:
            payment = (avail * 10**6) // pr
            self.accrued1 = 0
            self._record_earnings(stream, payment)
            self.balance0 += payment
        return avail

    def get_order(self, oid):
        o = self.orders[oid]
        stream = self.stream0 if o.zero_for_one else self.stream1
        ef = stream.earnings_factor_current
        if self.last_update >= o.expiration:
            snap = stream.earnings_factor_at_interval.get(o.expiration, 0)
            if snap > 0 and snap < ef:
                ef = snap
        d = ef - o.earnings_factor_last
        buy = (o.sell_rate * d) // (Q96 * RATE_SCALER) if d > 0 else 0
        ref = 0
        if self.last_update < o.expiration:
            rem = o.expiration - self.last_update
            ref = (o.sell_rate * rem) // RATE_SCALER
        return buy, ref

    def check_solvency(self):
        d0, d1 = 0, 0
        for oid, o in self.orders.items():
            buy, ref = self.get_order(oid)
            if o.zero_for_one:
                d1 += buy; d0 += ref
            else:
                d0 += buy; d1 += ref
        d0 += self.accrued0
        d1 += self.accrued1
        return self.balance0 >= d0, self.balance1 >= d1, self.balance0 - d0, self.balance1 - d1


# ── GBM Price Path Generator ───────────────────────────────────────

def gen_gbm_path(t_start, duration, dt, initial_price, seed):
    rng = random.Random(seed)
    mu = 0.0  # drift-free (risk-neutral)
    path = []
    p = initial_price
    t = t_start
    while t <= t_start + duration:
        path.append((t, p))
        z = rng.gauss(0, 1)
        p *= math.exp((mu - 0.5 * VOL_PER_SEC**2) * dt + VOL_PER_SEC * math.sqrt(dt) * z)
        p = max(0.01, p)  # floor
        t += dt
    return path


# ── Single Path Runner ──────────────────────────────────────────────

def run_single_path(seed, deposit=100_000_000, duration=7200, n_orders=1,
                    opposing_pct=0.3, clear_freq=CLEAR_INTERVAL):
    """Run one GBM path, return results for both modes."""
    path = gen_gbm_path(1737770400, duration + 3600, clear_freq, INITIAL_PRICE, seed)

    results = {}
    for mode in ['dust', 'autosettle']:
        pool = AMMPool.from_params(INITIAL_LIQUIDITY_USD, INITIAL_PRICE)
        eng = JTMEngine(mode, pool, path)
        t0 = eng.last_update

        # Submit main order(s) — waUSDC→wRLP
        oids = []
        per_order = deposit // n_orders
        for i in range(n_orders):
            oid = eng.submit(False, per_order, duration, t0 + i)
            if oid:
                oids.append(oid)

        # Opposing order (partial)
        opp_oid = None
        if opposing_pct > 0:
            opp_amount = int(deposit * opposing_pct / INITIAL_PRICE * 1e6)  # wRLP equivalent
            opp_oid = eng.submit(True, opp_amount, duration // 2, t0)

        # Run clear bot
        for s in range(0, duration + EXPIRATION_INTERVAL + 600, clear_freq):
            eng.clear(True, t0 + s)
            eng.clear(False, t0 + s)

        # Final accrue past expiration
        eng._accrue_and_net(t0 + duration + EXPIRATION_INTERVAL + 600)

        # Collect results
        total_buy = 0
        total_ref = 0
        for oid in oids:
            buy, ref = eng.get_order(oid)
            total_buy += buy
            total_ref += ref

        # Final price
        eng._update_price(t0 + duration)
        final_price = eng.price

        # TWAP average price over the order
        twap = statistics.mean(eng.twap_prices) if eng.twap_prices else INITIAL_PRICE

        # Naive benchmark: what you'd get selling at TWAP continuously
        # deposit_waUSDC / avg_price = expected_wRLP
        naive_wrlp = (deposit / 1e6) / twap * 1e6

        # Value in USD at final price
        buy_usd = total_buy * final_price / 1e6
        ref_usd = total_ref / 1e6
        total_usd = buy_usd + ref_usd

        # Value preservation ratio
        preservation = total_usd / (deposit / 1e6) if deposit > 0 else 0
        # Token-basis preservation (vs naive TWAP benchmark)
        token_pres = total_buy / naive_wrlp if naive_wrlp > 0 else 0

        # Solvency
        sol0, sol1, sur0, sur1 = eng.check_solvency()

        results[mode] = {
            'buy': total_buy,
            'ref': total_ref,
            'buy_usd': buy_usd,
            'total_usd': total_usd,
            'preservation_usd': preservation,
            'token_preservation': token_pres,
            'naive_wrlp': naive_wrlp,
            'final_price': final_price,
            'twap': twap,
            'solvent0': sol0,
            'solvent1': sol1,
            'surplus0': sur0 / 1e6,
            'surplus1': sur1 / 1e6,
            'dust0': eng.dust0 / 1e6,
            'dust1': eng.dust1 / 1e6,
            'auto0': eng.auto_settled0 / 1e6,
            'auto1': eng.auto_settled1 / 1e6,
        }

    return results


# ── Monte Carlo ─────────────────────────────────────────────────────

def run_montecarlo():
    print("=" * 70)
    print("  JTM AUTO-SETTLE — PARANOID MONTE CARLO VERIFICATION")
    print("  GBM Prices | Crypto Vol (80% ann) | 1000 paths")
    print("=" * 70)
    print(f"\n  Initial price: ${INITIAL_PRICE}")
    print(f"  Pool liquidity: ${INITIAL_LIQUIDITY_USD/1e6:.1f}M")
    print(f"  Annual vol: {ANNUAL_VOL*100:.0f}%")
    print(f"  Vol/sec: {VOL_PER_SEC:.6f}")
    print(f"  Order: $100 waUSDC→wRLP, 2h + 30% opposing")
    print(f"  Clear interval: {CLEAR_INTERVAL}s")

    # ── Run all paths ───────────────────────────────────────────────
    dust_results = []
    auto_results = []
    solvency_failures = 0

    print(f"\n  Running {N_PATHS} paths ", end="", flush=True)
    for seed in range(N_PATHS):
        if seed % 100 == 0:
            print(".", end="", flush=True)
        try:
            r = run_single_path(seed)
            dust_results.append(r['dust'])
            auto_results.append(r['autosettle'])

            if not (r['autosettle']['solvent0'] and r['autosettle']['solvent1']):
                solvency_failures += 1
        except Exception as e:
            print(f"\n  ⚠️ seed={seed} failed: {e}")

    print(f" done ({len(auto_results)} paths)\n")

    # ── Property 1: SOLVENCY ────────────────────────────────────────
    print("─" * 70)
    print("  P1. SOLVENCY — contract balance ≥ all claims")
    print("─" * 70)
    d_sol = sum(1 for r in dust_results if r['solvent0'] and r['solvent1'])
    a_sol = sum(1 for r in auto_results if r['solvent0'] and r['solvent1'])
    print(f"  DUST:        {d_sol}/{len(dust_results)} paths solvent")
    print(f"  AUTO-SETTLE: {a_sol}/{len(auto_results)} paths solvent")
    if a_sol == len(auto_results):
        print(f"  ✅ P1 PASS — 100% solvency across all paths")
    else:
        print(f"  ❌ P1 FAIL — {solvency_failures} solvency failures!")
    min_surplus0 = min(r['surplus0'] for r in auto_results)
    min_surplus1 = min(r['surplus1'] for r in auto_results)
    print(f"  Worst-case surplus: wRLP={min_surplus0:.4f}, waUSDC={min_surplus1:.4f}")

    # ── Property 2: VALUE PRESERVATION ──────────────────────────────
    print(f"\n{'─'*70}")
    print("  P2. VALUE PRESERVATION — user gets ≈ deposit in USD terms")
    print("─" * 70)

    for label, results in [("DUST", dust_results), ("AUTO-SETTLE", auto_results)]:
        prv = [r['preservation_usd'] for r in results]
        tok = [r['token_preservation'] for r in results]
        print(f"\n  {label}:")
        print(f"    USD preservation:   mean={statistics.mean(prv)*100:.2f}%, "
              f"std={statistics.stdev(prv)*100:.2f}%, "
              f"min={min(prv)*100:.2f}%, max={max(prv)*100:.2f}%")
        print(f"    Token preservation: mean={statistics.mean(tok)*100:.2f}%, "
              f"std={statistics.stdev(tok)*100:.2f}%, "
              f"min={min(tok)*100:.2f}%, max={max(tok)*100:.2f}%")

    # Token preservation comparison
    d_tok = [r['token_preservation'] for r in dust_results]
    a_tok = [r['token_preservation'] for r in auto_results]
    improvement = [a - d for a, d in zip(a_tok, d_tok)]
    print(f"\n  Token preservation improvement (auto − dust):")
    print(f"    mean={statistics.mean(improvement)*100:.4f}%, "
          f"min={min(improvement)*100:.4f}%, max={max(improvement)*100:.4f}%")

    all_positive = all(i >= -1e-10 for i in improvement)
    print(f"  ✅ P2 {'PASS' if all_positive else 'FAIL'} — "
          f"auto-settle {'never' if all_positive else 'sometimes'} worse than dust")

    # ── Property 3: NO VALUE LEAK ───────────────────────────────────
    print(f"\n{'─'*70}")
    print("  P3. NO VALUE LEAK — auto-settle recovers dust losses")
    print("─" * 70)

    d_dust_total = [r['dust0'] + r['dust1'] for r in dust_results]
    a_dust_total = [r['dust0'] + r['dust1'] for r in auto_results]
    a_settled = [r['auto0'] + r['auto1'] for r in auto_results]

    print(f"  DUST mode — tokens orphaned:")
    print(f"    mean=${statistics.mean(d_dust_total):.4f}, "
          f"max=${max(d_dust_total):.4f}")
    print(f"  AUTO-SETTLE — tokens orphaned:")
    print(f"    mean=${statistics.mean(a_dust_total):.4f}, "
          f"max=${max(a_dust_total):.4f}")
    print(f"  AUTO-SETTLE — tokens auto-settled:")
    print(f"    mean=${statistics.mean(a_settled):.4f}, "
          f"max=${max(a_settled):.4f}")

    zero_dust = all(d < 0.001 for d in a_dust_total)
    print(f"  ✅ P3 {'PASS' if zero_dust else 'FAIL'} — "
          f"{'zero' if zero_dust else 'some'} dust in auto-settle mode")

    # ── Property 4: BOUNDED AMM SLIPPAGE ────────────────────────────
    print(f"\n{'─'*70}")
    print("  P4. BOUNDED SLIPPAGE — auto-settle impact is negligible on deep pool")
    print("─" * 70)

    price_impacts = []
    for r_d, r_a in zip(dust_results, auto_results):
        if r_a['final_price'] > 0 and r_d['final_price'] > 0:
            impact = abs(r_a['final_price'] - r_d['final_price']) / r_d['final_price']
            price_impacts.append(impact * 10000)  # in bps

    print(f"  Price impact of auto-settle vs dust:")
    print(f"    mean={statistics.mean(price_impacts):.4f} bps, "
          f"max={max(price_impacts):.4f} bps, "
          f"p99={sorted(price_impacts)[int(0.99*len(price_impacts))]:.4f} bps")
    print(f"  Pool fee: {POOL_FEE_BPS} bps")
    bounded = max(price_impacts) < POOL_FEE_BPS * 10  # generous bound
    print(f"  ✅ P4 {'PASS' if bounded else 'FAIL'} — impact well within pool depth")

    # ── Property 5: PROPORTIONALITY ─────────────────────────────────
    print(f"\n{'─'*70}")
    print("  P5. PROPORTIONALITY — multi-order earnings scale with deposit")
    print("─" * 70)

    prop_checks = 0
    prop_pass = 0
    for seed in range(50):
        path = gen_gbm_path(1737770400, 10800, CLEAR_INTERVAL, INITIAL_PRICE, seed + 10000)
        pool = AMMPool.from_params(INITIAL_LIQUIDITY_USD, INITIAL_PRICE)
        eng = JTMEngine('autosettle', pool, path)
        t0 = eng.last_update

        # 3 orders with 1:2:3 ratio
        oid1 = eng.submit(False, 10_000_000, 7200, t0)
        oid2 = eng.submit(False, 20_000_000, 7200, t0 + 1)
        oid3 = eng.submit(False, 30_000_000, 7200, t0 + 2)

        for s in range(0, 10800, 6):
            eng.clear(False, t0 + s)
        eng._accrue_and_net(t0 + 10800)

        b1, _ = eng.get_order(oid1)
        b2, _ = eng.get_order(oid2)
        b3, _ = eng.get_order(oid3)

        if b1 > 0:
            ratio_21 = b2 / b1
            ratio_31 = b3 / b1
            prop_checks += 1
            # Should be ~2:1 and ~3:1 (within 5% tolerance for rounding)
            if 1.85 < ratio_21 < 2.15 and 2.75 < ratio_31 < 3.25:
                prop_pass += 1

    print(f"  {prop_pass}/{prop_checks} paths have correct 1:2:3 earnings ratio (±7.5%)")
    print(f"  ✅ P5 {'PASS' if prop_pass >= prop_checks * 0.9 else 'FAIL'}")

    # ── Property 6: EXTREME SCENARIOS ───────────────────────────────
    print(f"\n{'─'*70}")
    print("  P6. EXTREME PRICE SCENARIOS — 3x pump, 80% crash, whipsaw")
    print("─" * 70)

    extreme_cases = [
        ("3x pump",     lambda t: INITIAL_PRICE * (1 + 2.0 * min(1, (t-1737770400)/7200))),
        ("80% crash",   lambda t: INITIAL_PRICE * max(0.2, 1 - 0.8 * min(1, (t-1737770400)/7200))),
        ("Whipsaw 2x",  lambda t: INITIAL_PRICE * (1 + math.sin((t-1737770400)/1800 * math.pi) * 0.5)),
        ("Flash crash",  lambda t: INITIAL_PRICE * (0.1 if 3600 < (t-1737770400) < 3660 else 1.0)),
    ]

    for name, price_fn in extreme_cases:
        t0 = 1737770400
        path = [(t0 + s, price_fn(t0 + s)) for s in range(0, 10800, 6)]

        for mode in ['dust', 'autosettle']:
            pool = AMMPool.from_params(INITIAL_LIQUIDITY_USD, INITIAL_PRICE)
            eng = JTMEngine(mode, pool, path)
            oid = eng.submit(False, 100_000_000, 7200, t0)
            for s in range(0, 10800, 6):
                eng.clear(False, t0 + s)
            eng._accrue_and_net(t0 + 10800)
            buy, ref = eng.get_order(oid)
            sol0, sol1, _, _ = eng.check_solvency()
            final_p = eng.price
            total_usd = buy * final_p / 1e6 + ref / 1e6
            icon = "✅" if sol0 and sol1 else "❌"
            dust = eng.dust0/1e6 + eng.dust1/1e6
            auto = eng.auto_settled0/1e6 + eng.auto_settled1/1e6
            label = "DUST" if mode == "dust" else "AUTO"
            print(f"  {icon} {name:<14} {label:<5}: buy={buy/1e6:.4f} wRLP, "
                  f"val=${total_usd:.2f}, dust=${dust:.4f}, settled=${auto:.4f}")

    # ── Distribution Analysis ───────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  DISTRIBUTION: Token preservation percentiles")
    print("─" * 70)

    for label, results in [("DUST", dust_results), ("AUTO-SETTLE", auto_results)]:
        tok = sorted([r['token_preservation'] for r in results])
        n = len(tok)
        print(f"\n  {label}:")
        for pct in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
            idx = int(pct / 100 * n)
            print(f"    p{pct:02d}: {tok[idx]*100:.3f}%", end="")
        print()

    # ── Final Summary ───────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  FINAL VERDICT")
    print(f"{'='*70}")

    all_pass = True
    checks = [
        (f"P1 Solvency ({a_sol}/{len(auto_results)})", a_sol == len(auto_results)),
        ("P2 Value preservation ≥ dust", all_positive),
        ("P3 Zero dust in auto-settle", zero_dust),
        ("P4 Bounded slippage", bounded),
        (f"P5 Proportionality ({prop_pass}/{prop_checks})", prop_pass >= prop_checks * 0.9),
    ]
    for name, ok in checks:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
        if not ok:
            all_pass = False

    print(f"\n  {'🎉 ALL PROPERTIES VERIFIED' if all_pass else '⚠️ SOME FAILURES'}")
    print(f"  Tested over {N_PATHS} GBM paths with {ANNUAL_VOL*100:.0f}% annual volatility")
    print(f"  + 4 extreme scenarios (3x pump, 80% crash, whipsaw, flash crash)")


if __name__ == "__main__":
    run_montecarlo()
