#!/usr/bin/env python3
"""
Ghost Balance Limit Order — PoC Simulation
============================================
Pure Python model of the discrete limit order mechanism from limit_orders_spec.md.
Verifies correctness via 7 assertion-gated scenarios + generates outcome charts.

Run: python3 backend/tools/ghost_limit_sim.py
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import random, os

# ── Constants ───────────────────────────────────────────────────────
TWAP_SMOOTHING = 0.3  # EMA alpha for TWAP simulation (lower = more inertia)
CHART_DIR = os.path.join(os.path.dirname(__file__), "ghost_limit_charts")


# ── Data Structures ─────────────────────────────────────────────────
@dataclass
class LimitOrder:
    owner: str
    tick: float          # trigger price
    amount: float        # original deposit
    remaining: float     # unfilled deposit
    is_sell: bool        # True = sell Token0 at tick, False = buy Token0 at tick
    created_at: int = 0

@dataclass
class TickBucket:
    total_deposit: float = 0.0
    accumulator: float = 0.0       # output tokens received from fills
    orders: Dict[str, LimitOrder] = field(default_factory=dict)

@dataclass
class EventLog:
    time: int = 0
    event: str = ""
    detail: str = ""


# ── Simulation Engine ──────────────────────────────────────────────
class GhostLimitHook:
    """Simulates the Ghost Balance Limit Order protocol from the spec."""

    def __init__(self):
        self.sell_buckets: Dict[float, TickBucket] = {}  # sell Token0 orders
        self.buy_buckets: Dict[float, TickBucket] = {}   # buy Token0 orders
        self.twap: float = 0.0
        self.spot: float = 0.0
        self.time: int = 0
        # Tracking
        self.user_balances: Dict[str, Dict[str, float]] = {}  # user -> {Token0, Token1}
        self.hook_token0: float = 0.0  # Token0 held in hook
        self.hook_token1: float = 0.0  # Token1 held in hook
        self.events: List[EventLog] = []
        # Chart data
        self.history: List[dict] = []

    def _log(self, event: str, detail: str = ""):
        self.events.append(EventLog(self.time, event, detail))

    def _snapshot(self, label: str = ""):
        total_sell_ghost = sum(b.total_deposit for b in self.sell_buckets.values())
        total_buy_ghost = sum(b.total_deposit for b in self.buy_buckets.values())
        total_sell_acc = sum(b.accumulator for b in self.sell_buckets.values())
        total_buy_acc = sum(b.accumulator for b in self.buy_buckets.values())
        self.history.append({
            "time": self.time, "twap": self.twap, "spot": self.spot,
            "sell_ghost": total_sell_ghost, "buy_ghost": total_buy_ghost,
            "sell_acc": total_sell_acc, "buy_acc": total_buy_acc,
            "hook_t0": self.hook_token0, "hook_t1": self.hook_token1,
            "label": label,
        })

    def _get_balance(self, user: str, token: str) -> float:
        return self.user_balances.setdefault(user, {"Token0": 0, "Token1": 0}).get(token, 0)

    def _credit(self, user: str, token: str, amount: float):
        self.user_balances.setdefault(user, {"Token0": 0, "Token1": 0})
        self.user_balances[user][token] = self.user_balances[user].get(token, 0) + amount

    def _debit(self, user: str, token: str, amount: float):
        self._credit(user, token, -amount)

    def set_prices(self, spot: float, twap: Optional[float] = None):
        """Set spot price; TWAP updates with EMA smoothing."""
        self.spot = spot
        if twap is not None:
            self.twap = twap
        elif self.twap == 0:
            self.twap = spot
        else:
            self.twap = self.twap * (1 - TWAP_SMOOTHING) + spot * TWAP_SMOOTHING
        self._snapshot("price_update")

    def advance_time(self, dt: int):
        self.time += dt

    # ── §3 Step 1: Place Limit Order ──────────────────────────────
    def place_limit_order(self, user: str, is_sell: bool, amount: float, trigger_tick: float) -> str:
        """Deposit tokens into ghost balance at target tick."""
        order_id = f"{user}_{trigger_tick}_{is_sell}_{self.time}"
        order = LimitOrder(user, trigger_tick, amount, amount, is_sell, self.time)

        if is_sell:
            bucket = self.sell_buckets.setdefault(trigger_tick, TickBucket())
            self._debit(user, "Token0", amount)
            self.hook_token0 += amount
        else:
            bucket = self.buy_buckets.setdefault(trigger_tick, TickBucket())
            self._debit(user, "Token1", amount * trigger_tick)  # deposit Token1 at limit price
            self.hook_token1 += amount * trigger_tick

        bucket.total_deposit += amount
        bucket.orders[order_id] = order
        self._log("PLACE", f"{user} {'SELL' if is_sell else 'BUY'} {amount:.2f} Token0 @ ${trigger_tick:.0f}")
        self._snapshot(f"place_{user}")
        return order_id

    # ── §3 Step 3: Swap (beforeSwap JIT intercept) ────────────────
    def swap(self, taker: str, buy_token0: bool, amount: float) -> float:
        """Taker swap — JIT fills from eligible ghost buckets at TWAP."""
        filled_total = 0.0
        remaining = amount

        if buy_token0:
            # Taker wants Token0, pays Token1. Fill from sell orders where TWAP >= tick
            eligible = sorted(
                [(tick, b) for tick, b in self.sell_buckets.items() if self.twap >= tick and b.total_deposit > 0],
                key=lambda x: x[0]  # best price first
            )
            for tick, bucket in eligible:
                if remaining <= 0:
                    break
                fill = min(remaining, bucket.total_deposit)
                payment = fill * self.twap  # taker pays at TWAP
                # Update bucket
                bucket.total_deposit -= fill
                bucket.accumulator += payment  # Token1 received
                # Pro-rata reduce individual orders
                self._reduce_orders_pro_rata(bucket, fill)
                # Token flows
                self.hook_token0 -= fill       # Token0 leaves hook to taker
                self.hook_token1 += payment    # Token1 enters hook from taker
                self._credit(taker, "Token0", fill)
                self._debit(taker, "Token1", payment)
                filled_total += fill
                remaining -= fill
                self._log("JIT_FILL", f"Sell tick ${tick:.0f}: {fill:.4f} Token0 @ TWAP ${self.twap:.0f}")
        else:
            # Taker wants Token1, pays Token0. Fill from buy orders where TWAP <= tick
            eligible = sorted(
                [(tick, b) for tick, b in self.buy_buckets.items() if self.twap <= tick and b.total_deposit > 0],
                key=lambda x: -x[0]  # best price first (highest buy)
            )
            for tick, bucket in eligible:
                if remaining <= 0:
                    break
                fill = min(remaining, bucket.total_deposit)
                payment = fill * self.twap
                bucket.total_deposit -= fill
                bucket.accumulator += fill  # Token0 received for buy orders
                self._reduce_orders_pro_rata(bucket, fill)
                self.hook_token1 -= payment
                self.hook_token0 += fill
                self._credit(taker, "Token1", payment)
                self._debit(taker, "Token0", fill)
                filled_total += fill
                remaining -= fill
                self._log("JIT_FILL", f"Buy tick ${tick:.0f}: {fill:.4f} Token0 @ TWAP ${self.twap:.0f}")

        if filled_total > 0:
            self._snapshot(f"swap_{taker}")
        return filled_total

    def _reduce_orders_pro_rata(self, bucket: TickBucket, total_fill: float):
        """Reduce individual order.remaining proportionally."""
        if bucket.total_deposit + total_fill == 0:
            return
        for order in bucket.orders.values():
            if order.remaining > 0:
                share = order.remaining / (bucket.total_deposit + total_fill)
                order.remaining -= total_fill * share

    # ── §4: Internal Netting ──────────────────────────────────────
    def try_internal_net(self):
        """Cross opposing limit orders at TWAP when spread is crossed."""
        matched_any = False
        for sell_tick, sell_bucket in list(self.sell_buckets.items()):
            if sell_bucket.total_deposit <= 0 or self.twap < sell_tick:
                continue
            for buy_tick, buy_bucket in list(self.buy_buckets.items()):
                if buy_bucket.total_deposit <= 0 or self.twap > buy_tick:
                    continue
                if sell_tick > buy_tick:
                    continue  # no cross
                # Spread is crossed: match at TWAP
                match_amount = min(sell_bucket.total_deposit, buy_bucket.total_deposit)
                if match_amount <= 0:
                    continue
                payment = match_amount * self.twap
                # Sell side gets Token1
                sell_bucket.total_deposit -= match_amount
                sell_bucket.accumulator += payment
                self._reduce_orders_pro_rata(sell_bucket, match_amount)
                # Buy side gets Token0
                buy_bucket.total_deposit -= match_amount
                buy_bucket.accumulator += match_amount
                self._reduce_orders_pro_rata(buy_bucket, match_amount)
                # Token flows: Token0 from sell ghost → buy accumulator (internal, no net hook change)
                # Token1 from buy ghost → sell accumulator
                # Net: hook_token0 stays (sell's token0 goes to buy's accumulator)
                #       hook_token1 stays (buy's token1 goes to sell's accumulator)
                self._log("NET", f"Sell@${sell_tick:.0f} x Buy@${buy_tick:.0f}: {match_amount:.4f} Token0 @ TWAP ${self.twap:.0f}")
                matched_any = True
        if matched_any:
            self._snapshot("internal_net")

    # ── §3 Step 5: Claim ──────────────────────────────────────────
    def claim(self, user: str, tick: float, is_sell: bool) -> Tuple[float, float]:
        """Claim filled proceeds + optionally cancel unfilled remainder."""
        buckets = self.sell_buckets if is_sell else self.buy_buckets
        bucket = buckets.get(tick)
        if not bucket:
            return 0.0, 0.0

        user_orders = {k: v for k, v in bucket.orders.items() if v.owner == user}
        if not user_orders:
            return 0.0, 0.0

        total_proceeds = 0.0
        total_refund = 0.0

        for oid, order in user_orders.items():
            filled = order.amount - order.remaining
            # Proportional share of accumulator
            if order.amount > 0 and bucket.accumulator > 0:
                # share = filled / total_filled_across_all_orders
                total_original = sum(o.amount for o in bucket.orders.values())
                if total_original > 0:
                    share_of_acc = (order.amount / total_original) * bucket.accumulator
                    proceeds = share_of_acc
                else:
                    proceeds = 0
            else:
                proceeds = 0

            total_proceeds += proceeds
            total_refund += order.remaining

        # Transfer proceeds
        if is_sell:
            self._credit(user, "Token1", total_proceeds)
            self.hook_token1 -= total_proceeds
            # Refund unfilled Token0
            if total_refund > 0:
                self._credit(user, "Token0", total_refund)
                self.hook_token0 -= total_refund
        else:
            self._credit(user, "Token0", total_proceeds)
            self.hook_token0 -= total_proceeds
            if total_refund > 0:
                refund_token1 = total_refund * tick
                self._credit(user, "Token1", refund_token1)
                self.hook_token1 -= refund_token1

        # Remove user's share from accumulator and bucket
        for oid in user_orders:
            total_original = sum(o.amount for o in bucket.orders.values())
            if total_original > 0:
                bucket.accumulator -= (bucket.orders[oid].amount / total_original) * bucket.accumulator
            bucket.total_deposit -= bucket.orders[oid].remaining
            del bucket.orders[oid]

        self._log("CLAIM", f"{user} @ ${tick:.0f}: proceeds={total_proceeds:.2f}, refund={total_refund:.4f}")
        self._snapshot(f"claim_{user}")
        return total_proceeds, total_refund

    def cancel(self, user: str, tick: float, is_sell: bool) -> Tuple[float, float]:
        """Cancel = claim proceeds + withdraw unfilled deposit."""
        return self.claim(user, tick, is_sell)


# ── Chart Generation ────────────────────────────────────────────────
def generate_charts(hook: GhostLimitHook, scenario_name: str):
    """Generate matplotlib charts for a scenario's history."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
    except ImportError:
        print("  ⚠️  matplotlib not installed, skipping charts")
        return None

    os.makedirs(CHART_DIR, exist_ok=True)
    h = hook.history
    if len(h) < 2:
        return None

    times = [d["time"] for d in h]
    t0 = times[0]
    minutes = [(t - t0) / 60 for t in times]

    fig = plt.figure(figsize=(16, 14))
    fig.patch.set_facecolor('#0d1117')
    gs = gridspec.GridSpec(3, 2, hspace=0.35, wspace=0.3)

    colors = {
        'twap': '#58a6ff', 'spot': '#f0883e', 'sell_ghost': '#7ee787',
        'buy_ghost': '#d2a8ff', 'acc_sell': '#ff7b72', 'acc_buy': '#ffa657',
        'hook_t0': '#79c0ff', 'hook_t1': '#d2a8ff',
    }

    def style_ax(ax, title, ylabel):
        ax.set_facecolor('#161b22')
        ax.set_title(title, color='white', fontsize=13, fontweight='bold', pad=10)
        ax.set_ylabel(ylabel, color='#8b949e', fontsize=10)
        ax.set_xlabel('Time (minutes)', color='#8b949e', fontsize=10)
        ax.tick_params(colors='#8b949e')
        for spine in ax.spines.values():
            spine.set_color('#30363d')
        ax.grid(True, alpha=0.15, color='#484f58')

    # 1. TWAP vs Spot Price
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(minutes, [d["twap"] for d in h], color=colors['twap'], linewidth=2, label='TWAP (5min)')
    ax1.plot(minutes, [d["spot"] for d in h], color=colors['spot'], linewidth=1.5, linestyle='--', alpha=0.7, label='Spot')
    # Mark trigger ticks
    for tick in set(t for b in hook.sell_buckets for t in [b]):
        ax1.axhline(y=tick, color='#ff7b72', alpha=0.3, linestyle=':', label=f'Sell Trigger ${tick:.0f}')
    for tick in set(t for b in hook.buy_buckets for t in [b]):
        ax1.axhline(y=tick, color='#7ee787', alpha=0.3, linestyle=':', label=f'Buy Trigger ${tick:.0f}')
    style_ax(ax1, 'Price: TWAP vs Spot', 'Price ($)')
    ax1.legend(fontsize=8, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # 2. Ghost Balances (unfilled deposits)
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.fill_between(minutes, [d["sell_ghost"] for d in h], alpha=0.4, color=colors['sell_ghost'], label='Sell Ghost (Token0)')
    ax2.fill_between(minutes, [d["buy_ghost"] for d in h], alpha=0.4, color=colors['buy_ghost'], label='Buy Ghost (Token0)')
    style_ax(ax2, 'Ghost Balances (Unfilled Deposits)', 'Token0 Amount')
    ax2.legend(fontsize=8, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # 3. Accumulators (filled proceeds)
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.plot(minutes, [d["sell_acc"] for d in h], color=colors['acc_sell'], linewidth=2, label='Sell Accumulator (Token1)')
    ax3.plot(minutes, [d["buy_acc"] for d in h], color=colors['acc_buy'], linewidth=2, label='Buy Accumulator (Token0)')
    style_ax(ax3, 'Accumulators (Filled Proceeds)', 'Amount')
    ax3.legend(fontsize=8, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # 4. Hook Token Custody
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.plot(minutes, [d["hook_t0"] for d in h], color=colors['hook_t0'], linewidth=2, label='Hook Token0')
    ax4.plot(minutes, [d["hook_t1"] for d in h], color=colors['hook_t1'], linewidth=2, label='Hook Token1')
    style_ax(ax4, 'Hook Token Custody', 'Amount')
    ax4.legend(fontsize=8, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # 5. Event Timeline
    ax5 = fig.add_subplot(gs[2, :])
    event_types = {'PLACE': '#7ee787', 'JIT_FILL': '#58a6ff', 'NET': '#d2a8ff', 'CLAIM': '#ffa657'}
    for i, ev in enumerate(hook.events):
        c = event_types.get(ev.event, '#8b949e')
        t_min = (ev.time - t0) / 60
        ax5.barh(ev.event, 0.3, left=t_min, color=c, alpha=0.8, height=0.6)
        ax5.text(t_min + 0.15, ev.event, ev.detail[:50], fontsize=6, color='#c9d1d9', va='center')
    style_ax(ax5, 'Event Timeline', '')
    ax5.set_xlabel('Time (minutes)', color='#8b949e', fontsize=10)

    fig.suptitle(f'Ghost Balance Limit Orders — {scenario_name}',
                 color='white', fontsize=16, fontweight='bold', y=0.98)

    path = os.path.join(CHART_DIR, f"{scenario_name.lower().replace(' ', '_')}.png")
    fig.savefig(path, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    plt.close(fig)
    print(f"  📈 Chart saved: {path}")
    return path


# ── Test Scenarios ──────────────────────────────────────────────────

def scenario_1_happy_path():
    """Place → TWAP crosses trigger → swap fills → claim."""
    print("\n" + "="*60)
    print("  SCENARIO 1: Happy Path")
    print("="*60)
    hook = GhostLimitHook()
    # Give users tokens
    hook._credit("Alice", "Token0", 10.0)
    hook._credit("Taker", "Token1", 50000.0)

    # Market at $2800
    hook.set_prices(spot=2800, twap=2800)

    # Alice sells 10 ETH at $3000
    hook.place_limit_order("Alice", is_sell=True, amount=10.0, trigger_tick=3000)
    assert hook.hook_token0 == 10.0, "Ghost deposit failed"

    # TWAP still below trigger — swap should NOT fill
    hook.advance_time(60)
    hook.set_prices(spot=2900, twap=2850)
    filled = hook.swap("Taker", buy_token0=True, amount=5.0)
    assert filled == 0, "Should NOT fill when TWAP < trigger"

    # TWAP reaches $3010 — sustained price discovery
    hook.advance_time(300)
    hook.set_prices(spot=3020, twap=3010)
    filled = hook.swap("Taker", buy_token0=True, amount=5.0)
    assert abs(filled - 5.0) < 0.001, f"Should fill 5 Token0, got {filled}"

    # Taker paid 5 * 3010 = 15050 Token1
    expected_payment = 5.0 * 3010
    assert abs(hook._get_balance("Taker", "Token1") - (50000 - expected_payment)) < 0.01

    # Alice claims
    proceeds, refund = hook.claim("Alice", 3000, is_sell=True)
    assert abs(proceeds - expected_payment) < 0.01, f"Proceeds wrong: {proceeds}"
    assert abs(refund - 5.0) < 0.01, f"Refund wrong: {refund}"

    print(f"  Alice received: ${proceeds:.2f} Token1 + {refund:.4f} Token0 refund")
    print("  ✅ SCENARIO 1 PASSED")
    return generate_charts(hook, "Scenario 1 Happy Path")


def scenario_2_bounce_back():
    """Price spikes, fills, crashes — filled portion safe."""
    print("\n" + "="*60)
    print("  SCENARIO 2: Bounce-Back Immunity")
    print("="*60)
    hook = GhostLimitHook()
    hook._credit("Alice", "Token0", 10.0)
    hook._credit("Taker", "Token1", 100000.0)

    hook.set_prices(spot=2800, twap=2800)
    hook.place_limit_order("Alice", is_sell=True, amount=10.0, trigger_tick=3000)

    # TWAP rises to 3010, partial fill
    hook.advance_time(300)
    hook.set_prices(spot=3020, twap=3010)
    hook.swap("Taker", buy_token0=True, amount=5.0)

    filled_proceeds_before_crash = 5.0 * 3010

    # CRASH: spot falls to $2000, TWAP follows down
    for i in range(10):
        hook.advance_time(60)
        new_spot = 3000 - i * 100
        hook.set_prices(spot=new_spot)

    # Verify filled portion is SAFE — accumulator still has the Token1
    bucket = hook.sell_buckets[3000]
    assert bucket.accumulator >= filled_proceeds_before_crash - 0.01, "Bounce-back stole filled funds!"

    # Remaining 5 ETH is dormant (TWAP < 3000 now)
    hook.advance_time(60)
    filled2 = hook.swap("Taker", buy_token0=True, amount=5.0)
    assert filled2 == 0, "Should NOT fill after crash (TWAP < trigger)"

    proceeds, refund = hook.claim("Alice", 3000, is_sell=True)
    assert abs(proceeds - filled_proceeds_before_crash) < 0.01
    assert abs(refund - 5.0) < 0.01
    print(f"  After crash: Alice safely claims ${proceeds:.2f} + {refund:.4f} Token0")
    print("  ✅ SCENARIO 2 PASSED")
    return generate_charts(hook, "Scenario 2 Bounce Back Immunity")


def scenario_3_flash_loan():
    """Single-block spot manipulation doesn't trigger orders."""
    print("\n" + "="*60)
    print("  SCENARIO 3: Flash-Loan Resistance")
    print("="*60)
    hook = GhostLimitHook()
    hook._credit("Alice", "Token0", 10.0)
    hook._credit("Whale", "Token1", 1000000.0)

    hook.set_prices(spot=2800, twap=2800)
    hook.place_limit_order("Alice", is_sell=True, amount=10.0, trigger_tick=3000)

    # Whale flash-manipulates spot to $3200 but TWAP barely moves
    hook.advance_time(1)  # 1 second — single block
    hook.set_prices(spot=3200)  # TWAP ≈ 2800 * 0.7 + 3200 * 0.3 = 2920
    assert hook.twap < 3000, f"TWAP should not reach 3000, got {hook.twap:.0f}"

    filled = hook.swap("Whale", buy_token0=True, amount=10.0)
    assert filled == 0, "Flash manipulation should NOT trigger limit order"

    # Spot returns to normal
    hook.advance_time(1)
    hook.set_prices(spot=2800)

    print(f"  TWAP after flash: ${hook.twap:.0f} (below $3000 trigger)")
    print("  ✅ SCENARIO 3 PASSED")
    return generate_charts(hook, "Scenario 3 Flash Loan Resistance")


def scenario_4_partial_fills():
    """Multiple swappers chip away at one order."""
    print("\n" + "="*60)
    print("  SCENARIO 4: Partial Fills")
    print("="*60)
    hook = GhostLimitHook()
    hook._credit("Alice", "Token0", 10.0)
    for t in ["T1", "T2", "T3"]:
        hook._credit(t, "Token1", 50000.0)

    hook.set_prices(spot=3010, twap=3010)
    hook.place_limit_order("Alice", is_sell=True, amount=10.0, trigger_tick=3000)

    # 3 takers each buy 3 Token0
    cumulative = 0.0
    for i, taker in enumerate(["T1", "T2", "T3"]):
        hook.advance_time(60)
        # TWAP drifts slightly each time
        hook.set_prices(spot=3010 + i * 5, twap=3010 + i * 3)
        filled = hook.swap(taker, buy_token0=True, amount=3.0)
        cumulative += filled
        print(f"  {taker}: filled {filled:.4f} @ TWAP ${hook.twap:.0f}")

    assert abs(cumulative - 9.0) < 0.01, f"Expected 9 filled, got {cumulative}"

    proceeds, refund = hook.claim("Alice", 3000, is_sell=True)
    assert refund > 0.9 and refund < 1.1, f"Expected ~1 Token0 refund, got {refund}"
    assert proceeds > 27000, f"Expected >$27k proceeds, got {proceeds}"
    print(f"  Alice: ${proceeds:.2f} Token1 + {refund:.4f} Token0 refund")
    print("  ✅ SCENARIO 4 PASSED")
    return generate_charts(hook, "Scenario 4 Partial Fills")


def scenario_5_internal_netting():
    """Bob sells at $2900, Charlie buys at $3000 — crossed spread."""
    print("\n" + "="*60)
    print("  SCENARIO 5: Internal Netting (Spread Cross)")
    print("="*60)
    hook = GhostLimitHook()
    hook._credit("Bob", "Token0", 5.0)
    hook._credit("Charlie", "Token1", 20000.0)

    hook.set_prices(spot=2950, twap=2950)

    # Bob: sell 5 Token0 at $2900
    hook.place_limit_order("Bob", is_sell=True, amount=5.0, trigger_tick=2900)
    # Charlie: buy 3 Token0 at $3000
    hook.place_limit_order("Charlie", is_sell=False, amount=3.0, trigger_tick=3000)

    # TWAP = $2950: Bob's condition met (>= 2900), Charlie's met (<= 3000)
    hook.try_internal_net()

    # Verify netting occurred
    sell_bucket = hook.sell_buckets[2900]
    assert sell_bucket.total_deposit < 5.0, "Netting should reduce sell ghost"

    matched = 5.0 - sell_bucket.total_deposit
    assert abs(matched - 3.0) < 0.01, f"Should match 3.0, got {matched}"

    # Bob claims — should get 3 * 2950 = 8850 Token1 from netting
    proceeds_bob, refund_bob = hook.claim("Bob", 2900, is_sell=True)
    assert abs(proceeds_bob - 3 * 2950) < 1.0, f"Bob proceeds: {proceeds_bob}"
    assert abs(refund_bob - 2.0) < 0.01

    # Charlie claims — should get 3 Token0
    proceeds_charlie, refund_charlie = hook.claim("Charlie", 3000, is_sell=False)
    assert abs(proceeds_charlie - 3.0) < 0.01, f"Charlie proceeds: {proceeds_charlie}"

    print(f"  Bob: ${proceeds_bob:.2f} Token1 + {refund_bob:.4f} Token0 refund")
    print(f"  Charlie: {proceeds_charlie:.4f} Token0 (zero AMM impact!)")
    print("  ✅ SCENARIO 5 PASSED")
    return generate_charts(hook, "Scenario 5 Internal Netting")


def scenario_6_pro_rata():
    """Alice 10 + Dave 5 at same tick — 2:1 split."""
    print("\n" + "="*60)
    print("  SCENARIO 6: Multi-User Pro-Rata")
    print("="*60)
    hook = GhostLimitHook()
    hook._credit("Alice", "Token0", 10.0)
    hook._credit("Dave", "Token0", 5.0)
    hook._credit("Taker", "Token1", 100000.0)

    hook.set_prices(spot=3010, twap=3010)
    hook.place_limit_order("Alice", is_sell=True, amount=10.0, trigger_tick=3000)
    hook.place_limit_order("Dave", is_sell=True, amount=5.0, trigger_tick=3000)

    # Taker buys 9 Token0 — fills from shared bucket
    hook.swap("Taker", buy_token0=True, amount=9.0)

    # Alice gets 2/3 of proceeds, Dave gets 1/3
    p_alice, r_alice = hook.claim("Alice", 3000, is_sell=True)
    p_dave, r_dave = hook.claim("Dave", 3000, is_sell=True)

    total_proceeds = 9.0 * 3010
    alice_share = total_proceeds * (10.0 / 15.0)
    dave_share = total_proceeds * (5.0 / 15.0)

    assert abs(p_alice - alice_share) < 1.0, f"Alice share wrong: {p_alice} vs {alice_share}"
    assert abs(p_dave - dave_share) < 1.0, f"Dave share wrong: {p_dave} vs {dave_share}"

    print(f"  Alice (10/15): ${p_alice:.2f} Token1 + {r_alice:.4f} refund")
    print(f"  Dave  ( 5/15): ${p_dave:.2f} Token1 + {r_dave:.4f} refund")
    print(f"  Ratio: {p_alice/p_dave:.2f}x (expected 2.00x)")
    print("  ✅ SCENARIO 6 PASSED")
    return generate_charts(hook, "Scenario 6 Pro Rata")


def scenario_7_cancellation():
    """Place → partial fill → cancel → get refund + proceeds."""
    print("\n" + "="*60)
    print("  SCENARIO 7: Cancellation")
    print("="*60)
    hook = GhostLimitHook()
    hook._credit("Alice", "Token0", 10.0)
    hook._credit("Taker", "Token1", 50000.0)

    hook.set_prices(spot=3010, twap=3010)
    hook.place_limit_order("Alice", is_sell=True, amount=10.0, trigger_tick=3000)

    # Partial fill: 3 Token0
    hook.swap("Taker", buy_token0=True, amount=3.0)
    expected_proceeds = 3.0 * 3010

    # Cancel remainder
    proceeds, refund = hook.cancel("Alice", 3000, is_sell=True)
    assert abs(proceeds - expected_proceeds) < 0.01, f"Proceeds: {proceeds}"
    assert abs(refund - 7.0) < 0.01, f"Refund: {refund}"

    # Verify Alice is whole
    alice_t0 = hook._get_balance("Alice", "Token0")
    alice_t1 = hook._get_balance("Alice", "Token1")
    assert abs(alice_t0 - 7.0) < 0.01, f"Alice Token0: {alice_t0}"
    assert abs(alice_t1 - expected_proceeds) < 0.01, f"Alice Token1: {alice_t1}"

    print(f"  Alice: {alice_t0:.4f} Token0 + ${alice_t1:.2f} Token1")
    print("  ✅ SCENARIO 7 PASSED")
    return generate_charts(hook, "Scenario 7 Cancellation")


# ── Monte-Carlo Fuzzer ──────────────────────────────────────────────

def monte_carlo_fuzz(n_rounds: int = 200):
    """Randomized stress test — assert hook solvency after random operations."""
    print("\n" + "="*60)
    print(f"  MONTE-CARLO FUZZ: {n_rounds} rounds")
    print("="*60)
    random.seed(42)
    hook = GhostLimitHook()
    users = [f"U{i}" for i in range(5)]
    takers = [f"T{i}" for i in range(3)]

    for u in users + takers:
        hook._credit(u, "Token0", 1000.0)
        hook._credit(u, "Token1", 3000000.0)

    price = 3000.0
    hook.set_prices(spot=price, twap=price)

    for r in range(n_rounds):
        # Random price walk
        price *= 1 + random.uniform(-0.02, 0.02)
        hook.advance_time(random.randint(1, 120))
        hook.set_prices(spot=price)

        action = random.random()
        if action < 0.4:
            # Place order
            user = random.choice(users)
            is_sell = random.random() < 0.5
            tick = round(price * random.uniform(0.95, 1.05))
            amt = random.uniform(0.1, 5.0)
            try:
                hook.place_limit_order(user, is_sell, amt, tick)
            except Exception:
                pass
        elif action < 0.7:
            # Swap
            taker = random.choice(takers)
            buy_t0 = random.random() < 0.5
            amt = random.uniform(0.1, 3.0)
            hook.swap(taker, buy_t0, amt)
        elif action < 0.85:
            hook.try_internal_net()
        else:
            # Claim random
            user = random.choice(users)
            for tick in list(hook.sell_buckets.keys())[:1]:
                hook.claim(user, tick, is_sell=True)
            for tick in list(hook.buy_buckets.keys())[:1]:
                hook.claim(user, tick, is_sell=False)

    # Solvency check
    assert hook.hook_token0 >= -0.001, f"Token0 insolvency: {hook.hook_token0}"
    assert hook.hook_token1 >= -0.001, f"Token1 insolvency: {hook.hook_token1}"
    print(f"  Hook Token0: {hook.hook_token0:.4f} (≥0 ✅)")
    print(f"  Hook Token1: {hook.hook_token1:.4f} (≥0 ✅)")
    print("  ✅ MONTE-CARLO FUZZ PASSED")


# ── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  GHOST BALANCE LIMIT ORDER — PoC SIMULATION")
    print("  Verifying mechanism correctness from limit_orders_spec.md")
    print("=" * 60)

    charts = []
    charts.append(scenario_1_happy_path())
    charts.append(scenario_2_bounce_back())
    charts.append(scenario_3_flash_loan())
    charts.append(scenario_4_partial_fills())
    charts.append(scenario_5_internal_netting())
    charts.append(scenario_6_pro_rata())
    charts.append(scenario_7_cancellation())
    monte_carlo_fuzz()

    print("\n" + "=" * 60)
    print("  ALL 7 SCENARIOS + FUZZ PASSED ✅")
    print("=" * 60)
    saved = [c for c in charts if c]
    if saved:
        print(f"\n  📊 {len(saved)} charts saved to: {CHART_DIR}/")
        for c in saved:
            print(f"     {c}")
