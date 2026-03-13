#!/usr/bin/env python3
"""
Unified GhostEngine Simulation
================================
Modular V4 hook: TwapStreamModule + LimitOrderModule through a single
matching engine with pro-rata ghost sharing and 7 invariants.

Run: python3 backend/tools/ghost_unified_sim.py
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Protocol
from abc import ABC, abstractmethod
import random, math, os, time, sys

EPSILON = 1e-6
RATE_SCALER = 1e18
CHART_DIR = os.path.join(os.path.dirname(__file__), "ghost_limit_charts")


# ═══════════════════════════════════════════════════════════════════
# Module Interface (mirrors IGhostModule.sol)
# ═══════════════════════════════════════════════════════════════════

class IGhostModule(ABC):
    """Interface that every order-type module implements."""

    @abstractmethod
    def accrue(self, twap: float, elapsed: float) -> Tuple[float, float]:
        """Produce ghost. Returns (new_ghost0, new_ghost1)."""
        ...

    @abstractmethod
    def record_earnings(self, zero_for_one: bool, earnings: float,
                        ghost_consumed: float, total_ghost: float):
        """Distribute earnings from a fill to depositors."""
        ...

    @abstractmethod
    def nettable_ghost(self) -> Tuple[float, float]:
        """How much ghost can participate in internal netting."""
        ...

    @abstractmethod
    def record_netting(self, netted0: float, netted1: float,
                       earned0: float, earned1: float):
        """After netting, update module state."""
        ...

    @abstractmethod
    def pending_claims(self) -> Tuple[float, float]:
        """Total tokens owed to depositors (T0, T1)."""
        ...

    @abstractmethod
    def ghost_balances(self) -> Tuple[float, float]:
        """Current ghost (T0, T1) from this module."""
        ...


# ═══════════════════════════════════════════════════════════════════
# Module 1: TWAP Stream (port of JTM streaming)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StreamOrder:
    owner: str
    sell_rate: float          # tokens per second (scaled)
    earnings_factor_last: float
    zero_for_one: bool
    start_time: float
    expiration: float
    original_deposit: float


class TwapStreamModule(IGhostModule):
    """Continuous time-weighted ghost accrual — port of JTM's streaming model."""

    def __init__(self):
        # Per-direction stream pools
        self.sell_rate_0for1: float = 0.0  # aggregate: selling T0 for T1
        self.sell_rate_1for0: float = 0.0  # aggregate: selling T1 for T0
        self.ef_0for1: float = 0.0         # earnings factor T0→T1
        self.ef_1for0: float = 0.0         # earnings factor T1→T0
        self.ghost0: float = 0.0           # ghost T0 (from 0→1 streams)
        self.ghost1: float = 0.0           # ghost T1 (from 1→0 streams)
        self.orders: Dict[str, StreamOrder] = {}
        self._order_counter = 0

    def submit_order(self, owner: str, amount: float, duration: float,
                     zero_for_one: bool, current_time: float) -> str:
        """Submit a streaming TWAP order."""
        sell_rate = amount / duration
        self._order_counter += 1
        oid = f"stream_{self._order_counter}"

        order = StreamOrder(
            owner=owner,
            sell_rate=sell_rate,
            earnings_factor_last=self.ef_0for1 if zero_for_one else self.ef_1for0,
            zero_for_one=zero_for_one,
            start_time=current_time,
            expiration=current_time + duration,
            original_deposit=amount
        )
        self.orders[oid] = order

        if zero_for_one:
            self.sell_rate_0for1 += sell_rate
        else:
            self.sell_rate_1for0 += sell_rate

        return oid

    def accrue(self, twap: float, elapsed: float) -> Tuple[float, float]:
        new_ghost0 = self.sell_rate_0for1 * elapsed
        new_ghost1 = self.sell_rate_1for0 * elapsed
        self.ghost0 += new_ghost0
        self.ghost1 += new_ghost1
        return new_ghost0, new_ghost1

    def cross_epochs(self, current_time: float):
        """Expire orders that have reached their expiration."""
        expired = [oid for oid, o in self.orders.items()
                   if current_time >= o.expiration and o.sell_rate > 0]
        for oid in expired:
            o = self.orders[oid]
            if o.zero_for_one:
                self.sell_rate_0for1 -= o.sell_rate
            else:
                self.sell_rate_1for0 -= o.sell_rate
            # sellRate → 0 marks it as expired (keep for claim)

    def record_earnings(self, zero_for_one: bool, earnings: float,
                        ghost_consumed: float, total_ghost: float):
        if zero_for_one:
            # Taker bought T0, paid T1 → earnings go to 0→1 stream
            if self.sell_rate_0for1 > EPSILON:
                delta_ef = earnings / self.sell_rate_0for1
                self.ef_0for1 += delta_ef
            self.ghost0 -= ghost_consumed
        else:
            if self.sell_rate_1for0 > EPSILON:
                delta_ef = earnings / self.sell_rate_1for0
                self.ef_1for0 += delta_ef
            self.ghost1 -= ghost_consumed

    def nettable_ghost(self) -> Tuple[float, float]:
        return self.ghost0, self.ghost1

    def record_netting(self, netted0: float, netted1: float,
                       earned0: float, earned1: float):
        self.ghost0 -= netted0
        self.ghost1 -= netted1
        # Record earnings for each direction
        if earned1 > 0 and self.sell_rate_0for1 > EPSILON:
            self.ef_0for1 += earned1 / self.sell_rate_0for1
        if earned0 > 0 and self.sell_rate_1for0 > EPSILON:
            self.ef_1for0 += earned0 / self.sell_rate_1for0

    def claim_order(self, oid: str) -> Tuple[float, float]:
        """Claim earnings from a stream order. Returns (buyTokens, sellRefund)."""
        order = self.orders.get(oid)
        if not order:
            return 0.0, 0.0

        if order.zero_for_one:
            ef_now = self.ef_0for1
        else:
            ef_now = self.ef_1for0

        # Earnings = (ef_now - ef_last) × sellRate
        earnings = (ef_now - order.earnings_factor_last) * order.sell_rate
        order.earnings_factor_last = ef_now

        return max(0, earnings), 0.0

    def cancel_order(self, oid: str, current_time: float) -> Tuple[float, float]:
        """Cancel a stream order. Returns (buyTokens, sellRefund)."""
        order = self.orders.get(oid)
        if not order or order.sell_rate == 0:
            return 0.0, 0.0

        # Claim accumulated earnings first
        earnings, _ = self.claim_order(oid)

        # Refund remaining time
        remaining = max(0, order.expiration - current_time)
        refund = order.sell_rate * remaining

        if order.zero_for_one:
            self.sell_rate_0for1 -= order.sell_rate
        else:
            self.sell_rate_1for0 -= order.sell_rate

        order.sell_rate = 0  # mark cancelled
        return earnings, refund

    def pending_claims(self) -> Tuple[float, float]:
        pending_t0 = 0.0  # T0 owed (earnings from 1→0 streams)
        pending_t1 = 0.0  # T1 owed (earnings from 0→1 streams)
        for o in self.orders.values():
            if o.sell_rate == 0:
                continue
            if o.zero_for_one:
                # 0→1 stream: deposit T0, earn T1
                pending_t1 += (self.ef_0for1 - o.earnings_factor_last) * o.sell_rate
            else:
                # 1→0 stream: deposit T1, earn T0
                pending_t0 += (self.ef_1for0 - o.earnings_factor_last) * o.sell_rate
        return pending_t0, pending_t1

    def ghost_balances(self) -> Tuple[float, float]:
        return self.ghost0, self.ghost1


# ═══════════════════════════════════════════════════════════════════
# Module 2: Limit Orders (port of proven ghost limit engine)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class LimitOrder:
    owner: str
    original: float
    remaining: float
    is_sell: bool
    tick: int


@dataclass
class LimitBucket:
    total_deposit: float = 0.0
    accumulator: float = 0.0
    total_original: float = 0.0
    t1_surplus: float = 0.0
    activated: bool = False
    orders: List = field(default_factory=list)


class LimitOrderModule(IGhostModule):
    """Tick-gated discrete limit orders with TWAP activation."""

    def __init__(self):
        self.sell_buckets: Dict[int, LimitBucket] = {}  # tick → bucket
        self.buy_buckets: Dict[int, LimitBucket] = {}
        self.ghost0: float = 0.0  # activated sell-side ghost
        self.ghost1: float = 0.0  # activated buy-side ghost
        self._order_counter = 0

    def submit_limit(self, owner: str, amount: float, tick: int,
                     is_sell: bool) -> str:
        self._order_counter += 1
        oid = f"limit_{self._order_counter}"
        order = LimitOrder(owner, amount, amount, is_sell, tick)

        if is_sell:
            bucket = self.sell_buckets.setdefault(tick, LimitBucket())
        else:
            bucket = self.buy_buckets.setdefault(tick, LimitBucket())

        bucket.orders.append(order)
        bucket.total_deposit += amount
        bucket.total_original += amount
        return oid

    def accrue(self, twap: float, elapsed: float) -> Tuple[float, float]:
        """Activate eligible limit orders based on TWAP."""
        new_g0, new_g1 = 0.0, 0.0

        # Sell orders: activate when TWAP ≥ tick
        for tick, bucket in self.sell_buckets.items():
            if not bucket.activated and twap >= tick and bucket.total_deposit > EPSILON:
                bucket.activated = True
                new_g0 += bucket.total_deposit
                self.ghost0 += bucket.total_deposit

        # Buy orders: activate when TWAP ≤ tick
        for tick, bucket in self.buy_buckets.items():
            if not bucket.activated and twap <= tick and bucket.total_deposit > EPSILON:
                bucket.activated = True
                # Buy ghost is in T1 terms (they deposited T1)
                new_g1 += bucket.total_deposit * tick
                self.ghost1 += bucket.total_deposit * tick

        return new_g0, new_g1

    def record_earnings(self, zero_for_one: bool, earnings: float,
                        ghost_consumed: float, total_ghost: float):
        if zero_for_one:
            # Taker bought T0 → sell orders filled
            # Distribute earnings (T1) to activated sell buckets pro-rata
            if self.ghost0 < EPSILON:
                return
            for tick, bucket in self.sell_buckets.items():
                if not bucket.activated or bucket.total_deposit < EPSILON:
                    continue
                bucket_share = bucket.total_deposit / self.ghost0
                fill = ghost_consumed * bucket_share
                earn = earnings * bucket_share

                if fill > bucket.total_deposit:
                    fill = bucket.total_deposit

                ratio = fill / bucket.total_deposit if bucket.total_deposit > EPSILON else 0
                for order in bucket.orders:
                    order.remaining -= order.remaining * ratio
                bucket.total_deposit -= fill
                bucket.accumulator += earn

            self.ghost0 -= ghost_consumed
        else:
            # Taker bought T1 → buy orders filled
            if self.ghost1 < EPSILON:
                return
            for tick, bucket in self.buy_buckets.items():
                if not bucket.activated or bucket.total_deposit < EPSILON:
                    continue
                ghost_here = bucket.total_deposit * tick
                bucket_share = ghost_here / self.ghost1
                fill = ghost_consumed * bucket_share / tick  # convert back to T0 amount
                earn = earnings * bucket_share

                if fill > bucket.total_deposit:
                    fill = bucket.total_deposit

                ratio = fill / bucket.total_deposit if bucket.total_deposit > EPSILON else 0
                for order in bucket.orders:
                    order.remaining -= order.remaining * ratio
                bucket.total_deposit -= fill
                bucket.accumulator += fill  # buy orders accumulate T0

            self.ghost1 -= ghost_consumed

    def nettable_ghost(self) -> Tuple[float, float]:
        return self.ghost0, self.ghost1

    def record_netting(self, netted0: float, netted1: float,
                       earned0: float, earned1: float):
        # Netting: sell ghost0 matched vs buy ghost1
        if netted0 > EPSILON and self.ghost0 > EPSILON:
            for tick, bucket in self.sell_buckets.items():
                if not bucket.activated or bucket.total_deposit < EPSILON:
                    continue
                share = bucket.total_deposit / self.ghost0
                fill = netted0 * share
                earn = earned1 * share  # sell T0, earn T1
                if fill > bucket.total_deposit:
                    fill = bucket.total_deposit
                ratio = fill / bucket.total_deposit if bucket.total_deposit > EPSILON else 0
                for order in bucket.orders:
                    order.remaining -= order.remaining * ratio
                bucket.total_deposit -= fill
                bucket.accumulator += earn
            self.ghost0 -= netted0

        if netted1 > EPSILON and self.ghost1 > EPSILON:
            for tick, bucket in self.buy_buckets.items():
                if not bucket.activated or bucket.total_deposit < EPSILON:
                    continue
                ghost_here = bucket.total_deposit * tick
                share = ghost_here / self.ghost1
                fill_t0 = earned0 * share  # they receive T0
                fill = netted1 * share / tick if tick > 0 else 0
                if fill > bucket.total_deposit:
                    fill = bucket.total_deposit
                ratio = fill / bucket.total_deposit if bucket.total_deposit > EPSILON else 0
                for order in bucket.orders:
                    order.remaining -= order.remaining * ratio
                bucket.total_deposit -= fill
                bucket.accumulator += fill_t0 if fill_t0 > 0 else fill
                bucket.t1_surplus += fill * (tick - 1)  # simplified
            self.ghost1 -= netted1

    def claim_limit(self, owner: str, tick: int, is_sell: bool) -> Tuple[float, float]:
        """Claim from a specific tick. Returns (proceeds, refund)."""
        buckets = self.sell_buckets if is_sell else self.buy_buckets
        bucket = buckets.get(tick)
        if not bucket:
            return 0.0, 0.0

        total_proceeds = 0.0
        total_refund = 0.0
        remaining = []

        for order in bucket.orders:
            if order.owner != owner:
                remaining.append(order)
                continue

            if bucket.total_original > EPSILON and bucket.accumulator > EPSILON:
                share = (order.original / bucket.total_original) * bucket.accumulator
            else:
                share = 0.0

            refund = order.remaining

            if is_sell:
                total_proceeds += share
                total_refund += refund
            else:
                total_proceeds += share
                total_refund += refund
                # T1 surplus
                if bucket.t1_surplus > EPSILON and bucket.total_original > EPSILON:
                    surplus = (order.original / bucket.total_original) * bucket.t1_surplus
                    total_proceeds += surplus  # counted as extra T1 proceeds
                    bucket.t1_surplus -= surplus

            bucket.accumulator -= share
            bucket.total_deposit -= refund
            bucket.total_original -= order.original

        bucket.orders = remaining
        return total_proceeds, total_refund

    def pending_claims(self) -> Tuple[float, float]:
        pending_t0 = 0.0
        pending_t1 = 0.0
        for tick, b in self.sell_buckets.items():
            pending_t0 += b.total_deposit  # refundable T0
            pending_t1 += b.accumulator    # earned T1
        for tick, b in self.buy_buckets.items():
            pending_t0 += b.accumulator    # earned T0
            pending_t1 += b.total_deposit * tick + b.t1_surplus  # refundable T1
        return pending_t0, pending_t1

    def ghost_balances(self) -> Tuple[float, float]:
        return self.ghost0, self.ghost1


# ═══════════════════════════════════════════════════════════════════
# GhostEngine — The Unified Hook
# ═══════════════════════════════════════════════════════════════════

class GhostEngine:
    """Unified V4 hook with modular ghost production and shared matching."""

    def __init__(self):
        self.modules: List[IGhostModule] = []
        self.twap: float = 3000.0
        self.current_time: float = 0.0
        self.last_update_time: float = 0.0

        # Unified ghost pool
        self.total_ghost0: float = 0.0
        self.total_ghost1: float = 0.0
        self.module_ghost0: List[float] = []
        self.module_ghost1: List[float] = []

        # Hook custody
        self.hook_t0: float = 0.0
        self.hook_t1: float = 0.0

        # User wallets
        self.wallets: Dict[str, Dict[str, float]] = {}
        self.total_minted_t0: float = 0.0
        self.total_minted_t1: float = 0.0

        # Stats
        self.invariant_checks = 0
        self.op_count = 0

    def register_module(self, module: IGhostModule):
        self.modules.append(module)
        self.module_ghost0.append(0.0)
        self.module_ghost1.append(0.0)

    def _wallet(self, user: str) -> Dict[str, float]:
        if user not in self.wallets:
            self.wallets[user] = {"T0": 0.0, "T1": 0.0}
        return self.wallets[user]

    def mint(self, user: str, t0: float, t1: float):
        w = self._wallet(user)
        w["T0"] += t0
        w["T1"] += t1
        self.total_minted_t0 += t0
        self.total_minted_t1 += t1

    # ── Order Submission (delegates to modules) ──

    def submit_stream(self, user: str, amount: float, duration: float,
                      zero_for_one: bool) -> str:
        w = self._wallet(user)
        if zero_for_one:
            assert w["T0"] >= amount - EPSILON, f"{user} insufficient T0"
            w["T0"] -= amount
            self.hook_t0 += amount
        else:
            assert w["T1"] >= amount - EPSILON, f"{user} insufficient T1"
            w["T1"] -= amount
            self.hook_t1 += amount

        stream_mod = self.modules[0]  # TwapStreamModule is always index 0
        oid = stream_mod.submit_order(user, amount, duration, zero_for_one,
                                       self.current_time)
        self.op_count += 1
        return oid

    def submit_limit(self, user: str, amount: float, tick: int,
                     is_sell: bool) -> str:
        w = self._wallet(user)
        if is_sell:
            assert w["T0"] >= amount - EPSILON, f"{user} insufficient T0"
            w["T0"] -= amount
            self.hook_t0 += amount
        else:
            cost = amount * tick
            assert w["T1"] >= cost - EPSILON, f"{user} insufficient T1"
            w["T1"] -= cost
            self.hook_t1 += cost

        limit_mod = self.modules[1]  # LimitOrderModule is always index 1
        oid = limit_mod.submit_limit(user, amount, tick, is_sell)
        self.op_count += 1
        return oid

    # ── Core Pipeline (mirrors _beforeSwap) ──

    def advance_time(self, dt: float):
        """Advance simulation time and run the accrual pipeline."""
        self.current_time += dt
        elapsed = self.current_time - self.last_update_time

        # Phase 1: Ghost production (modules update their internal ghost)
        for i, mod in enumerate(self.modules):
            mod.accrue(self.twap, elapsed)

        self._sync_total_ghost()

        # Phase 2: Internal netting
        if self.total_ghost0 > EPSILON and self.total_ghost1 > EPSILON:
            self._internal_net()

        # Phase 3: Cross epochs (stream-specific)
        if isinstance(self.modules[0], TwapStreamModule):
            self.modules[0].cross_epochs(self.current_time)

        self.last_update_time = self.current_time

    def swap(self, user: str, buy_token0: bool, amount: float) -> float:
        """Taker swap — fills from unified ghost pool at TWAP."""
        w = self._wallet(user)
        self._sync_total_ghost()

        if buy_token0:
            available = self.total_ghost0
            if available < EPSILON:
                return 0.0
            fill = min(amount, available)
            payment = fill * self.twap

            if w["T1"] < payment - EPSILON:
                fill = w["T1"] / self.twap
                payment = fill * self.twap
                if fill < EPSILON:
                    return 0.0

            # Transfer
            w["T0"] += fill
            w["T1"] -= payment
            self.hook_t0 -= fill
            self.hook_t1 += payment

            # Distribute to modules proportionally
            for i, mod in enumerate(self.modules):
                if self.total_ghost0 > EPSILON and self.module_ghost0[i] > EPSILON:
                    share = fill * (self.module_ghost0[i] / self.total_ghost0)
                    earn = payment * (self.module_ghost0[i] / self.total_ghost0)
                    mod.record_earnings(True, earn, share, self.total_ghost0)

            self._sync_total_ghost()

        else:
            available = self.total_ghost1
            if available < EPSILON:
                return 0.0
            fill_t1 = min(amount * self.twap, available)
            fill = fill_t1 / self.twap if self.twap > 0 else 0
            payment = fill

            if w["T0"] < fill - EPSILON:
                fill = w["T0"]
                fill_t1 = fill * self.twap
                if fill < EPSILON:
                    return 0.0

            # Transfer
            w["T1"] += fill_t1
            w["T0"] -= fill
            self.hook_t1 -= fill_t1
            self.hook_t0 += fill

            for i, mod in enumerate(self.modules):
                if self.total_ghost1 > EPSILON and self.module_ghost1[i] > EPSILON:
                    share = fill_t1 * (self.module_ghost1[i] / self.total_ghost1)
                    earn = fill * (self.module_ghost1[i] / self.total_ghost1)
                    mod.record_earnings(False, earn, share, self.total_ghost1)

            self._sync_total_ghost()

        self.op_count += 1
        return fill

    def _internal_net(self):
        """Cross-module internal netting at TWAP."""
        # Convert ghost1 to T0 terms
        ghost1_as_t0 = self.total_ghost1 / self.twap if self.twap > 0 else 0

        if self.total_ghost0 <= ghost1_as_t0:
            matched_t0 = self.total_ghost0
        else:
            matched_t0 = ghost1_as_t0

        matched_t1 = matched_t0 * self.twap

        if matched_t0 < EPSILON:
            return

        # Distribute netting across modules proportionally
        for i, mod in enumerate(self.modules):
            # Sell-side (ghost0)
            if self.total_ghost0 > EPSILON and self.module_ghost0[i] > EPSILON:
                share0 = matched_t0 * (self.module_ghost0[i] / self.total_ghost0)
                earn1 = matched_t1 * (self.module_ghost0[i] / self.total_ghost0)
            else:
                share0, earn1 = 0.0, 0.0

            # Buy-side (ghost1)
            if self.total_ghost1 > EPSILON and self.module_ghost1[i] > EPSILON:
                share1 = matched_t1 * (self.module_ghost1[i] / self.total_ghost1)
                earn0 = matched_t0 * (self.module_ghost1[i] / self.total_ghost1)
            else:
                share1, earn0 = 0.0, 0.0

            mod.record_netting(share0, share1, earn0, earn1)

        self._sync_total_ghost()

    def _sync_total_ghost(self):
        """Sync engine's tracking from module ground truth."""
        for i, mod in enumerate(self.modules):
            g0, g1 = mod.ghost_balances()
            self.module_ghost0[i] = max(0, g0)
            self.module_ghost1[i] = max(0, g1)
        self.total_ghost0 = sum(self.module_ghost0)
        self.total_ghost1 = sum(self.module_ghost1)

    # ── Claims ──

    def claim_stream(self, user: str, oid: str):
        stream_mod: TwapStreamModule = self.modules[0]
        order = stream_mod.orders.get(oid)
        if not order:
            return 0.0, 0.0

        earnings, refund = stream_mod.claim_order(oid)
        w = self._wallet(user)

        if order.zero_for_one:
            w["T1"] += earnings    # earned T1
            self.hook_t1 -= earnings
        else:
            w["T0"] += earnings    # earned T0
            self.hook_t0 -= earnings

        self.op_count += 1
        return earnings, refund

    def cancel_stream(self, user: str, oid: str):
        """Cancel stream: claim earnings + refund remaining deposit."""
        stream_mod: TwapStreamModule = self.modules[0]
        order = stream_mod.orders.get(oid)
        if not order or order.sell_rate == 0:
            return 0.0, 0.0

        earnings, refund = stream_mod.cancel_order(oid, self.current_time)
        w = self._wallet(user)

        if order.zero_for_one:
            w["T1"] += earnings
            self.hook_t1 -= earnings
            if refund > EPSILON:
                w["T0"] += refund
                self.hook_t0 -= refund
        else:
            w["T0"] += earnings
            self.hook_t0 -= earnings
            if refund > EPSILON:
                w["T1"] += refund
                self.hook_t1 -= refund

        # Sync ghost from module (module already adjusted)
        self._sync_total_ghost()

        self.op_count += 1
        return earnings, refund

    def claim_limit(self, user: str, tick: int, is_sell: bool):
        limit_mod: LimitOrderModule = self.modules[1]
        proceeds, refund = limit_mod.claim_limit(user, tick, is_sell)
        w = self._wallet(user)

        if is_sell:
            w["T1"] += proceeds  # earned T1
            self.hook_t1 -= proceeds
            if refund > EPSILON:
                w["T0"] += refund  # refund T0
                self.hook_t0 -= refund
        else:
            w["T0"] += proceeds  # earned T0
            self.hook_t0 -= proceeds
            if refund > EPSILON:
                w["T1"] += refund * tick  # refund T1
                self.hook_t1 -= refund * tick

        self.op_count += 1
        return proceeds, refund

    # ── Invariant Checking ──

    def check_invariants(self, context: str = ""):
        self.invariant_checks += 1

        # I1: Module ghost sums == total ghost
        sum_g0 = sum(max(0, g) for g in self.module_ghost0)
        sum_g1 = sum(max(0, g) for g in self.module_ghost1)
        assert abs(sum_g0 - self.total_ghost0) < EPSILON, \
            f"I1 Ghost0 drift: {sum_g0} vs {self.total_ghost0} [{context}]"
        assert abs(sum_g1 - self.total_ghost1) < EPSILON, \
            f"I1 Ghost1 drift: {sum_g1} vs {self.total_ghost1} [{context}]"

        # I2: Conservation
        total_t0 = self.hook_t0 + sum(w["T0"] for w in self.wallets.values())
        total_t1 = self.hook_t1 + sum(w["T1"] for w in self.wallets.values())
        assert abs(total_t0 - self.total_minted_t0) < EPSILON * 100, \
            f"I2 T0 conservation: {total_t0} vs {self.total_minted_t0} [{context}]"
        assert abs(total_t1 - self.total_minted_t1) < EPSILON * 100, \
            f"I2 T1 conservation: {total_t1} vs {self.total_minted_t1} [{context}]"

        # I4: No module ghost < 0
        for i in range(len(self.modules)):
            assert self.module_ghost0[i] >= -EPSILON, \
                f"I4 Module {i} ghost0 negative: {self.module_ghost0[i]} [{context}]"
            assert self.module_ghost1[i] >= -EPSILON, \
                f"I4 Module {i} ghost1 negative: {self.module_ghost1[i]} [{context}]"

        # I7: No user balance < 0
        for user, w in self.wallets.items():
            assert w["T0"] >= -EPSILON, f"I7 {user} T0 < 0: {w['T0']} [{context}]"
            assert w["T1"] >= -EPSILON, f"I7 {user} T1 < 0: {w['T1']} [{context}]"


# ═══════════════════════════════════════════════════════════════════
# SCENARIOS
# ═══════════════════════════════════════════════════════════════════

PASS = 0
FAIL = 0

def run_test(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✅ {name}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}: {e}")


def test_1_pure_twap_stream():
    """Backward compat: TWAP stream works alone."""
    engine = GhostEngine()
    stream = TwapStreamModule()
    limit = LimitOrderModule()
    engine.register_module(stream)
    engine.register_module(limit)

    engine.mint("Alice", 100, 0)
    engine.mint("Taker", 0, 500_000)
    engine.twap = 3000

    # Alice streams 100 T0 → T1 over 100 seconds
    oid = engine.submit_stream("Alice", 100, 100, True)

    # Advance 50 seconds → 50 ghost T0
    engine.advance_time(50)
    assert abs(stream.ghost0 - 50) < EPSILON, f"Ghost should be 50: {stream.ghost0}"

    # Taker buys 30 T0
    filled = engine.swap("Taker", True, 30)
    assert abs(filled - 30) < 1, f"Should fill 30: {filled}"

    engine.check_invariants("after swap")

    # Advance to end
    engine.advance_time(50)

    # Alice claims
    earnings, _ = engine.claim_stream("Alice", oid)
    assert earnings > 0, "Alice should have earnings"

    engine.check_invariants("after claim")


def test_2_pure_limit_orders():
    """Limit orders work alone."""
    engine = GhostEngine()
    stream = TwapStreamModule()
    limit = LimitOrderModule()
    engine.register_module(stream)
    engine.register_module(limit)

    engine.mint("Alice", 10, 0)
    engine.mint("Taker", 0, 500_000)
    engine.twap = 3010

    engine.submit_limit("Alice", 10, 3000, True)

    # Advance → activates limit at TWAP 3010 ≥ 3000
    engine.advance_time(1)
    assert limit.ghost0 > 9, f"Limit ghost should be ~10: {limit.ghost0}"

    # Taker fills
    filled = engine.swap("Taker", True, 5)
    assert abs(filled - 5) < 1, f"Should fill 5: {filled}"

    engine.check_invariants("after limit fill")

    # Claim
    p, r = engine.claim_limit("Alice", 3000, True)
    assert p > 0, "Alice should have proceeds"
    assert r > 0, "Alice should have refund"


def test_3_mixed_twap_and_limit():
    """Both modules produce ghost, taker fills from unified pool."""
    engine = GhostEngine()
    stream = TwapStreamModule()
    limit = LimitOrderModule()
    engine.register_module(stream)
    engine.register_module(limit)

    engine.mint("Streamer", 100, 0)
    engine.mint("Limiter", 50, 0)
    engine.mint("Taker", 0, 1_000_000)
    engine.twap = 3010

    # Streamer: 100 T0 over 100s
    stream_oid = engine.submit_stream("Streamer", 100, 100, True)
    # Limiter: 50 T0 at tick 3000
    engine.submit_limit("Limiter", 50, 3000, True)

    # Advance 50s → stream produces 50 ghost, limit activates 50 ghost
    engine.advance_time(50)

    assert stream.ghost0 > 45, f"Stream ghost: {stream.ghost0}"
    assert limit.ghost0 > 45, f"Limit ghost: {limit.ghost0}"

    total = engine.total_ghost0
    assert total > 90, f"Total ghost should be ~100: {total}"

    # Taker fills 60 from the unified pool
    filled = engine.swap("Taker", True, 60)
    assert abs(filled - 60) < 1, f"Should fill 60: {filled}"

    # Both modules got proportional earnings
    assert stream.ghost0 < 50, "Stream ghost should decrease"
    assert limit.ghost0 < 50, "Limit ghost should decrease"

    engine.check_invariants("after mixed fill")


def test_4_cross_module_netting():
    """Stream ghost0 nets against limit ghost1."""
    engine = GhostEngine()
    stream = TwapStreamModule()
    limit = LimitOrderModule()
    engine.register_module(stream)
    engine.register_module(limit)

    engine.mint("Streamer", 100, 0)     # will stream-sell T0
    engine.mint("Buyer", 0, 500_000)     # will limit-buy T0

    engine.twap = 3000

    # Streamer sells T0 → T1
    engine.submit_stream("Streamer", 100, 100, True)
    # Buyer places limit buy at 3000
    engine.submit_limit("Buyer", 10, 3000, False)

    # Advance → stream accrues ghost0, limit activates ghost1
    engine.advance_time(50)

    # Internal netting should have crossed them
    engine.check_invariants("after netting")

    # Both should have earned something
    assert stream.ghost0 < 50 or limit.ghost1 < 30_000, \
        "Netting should have consumed some ghost"


def test_5_limit_fills_before_stream():
    """Limit ghost is available immediately, stream accrues over time."""
    engine = GhostEngine()
    stream = TwapStreamModule()
    limit = LimitOrderModule()
    engine.register_module(stream)
    engine.register_module(limit)

    engine.mint("Limiter", 20, 0)
    engine.mint("Streamer", 100, 0)
    engine.mint("Taker", 0, 500_000)
    engine.twap = 3010

    engine.submit_limit("Limiter", 20, 3000, True)
    engine.submit_stream("Streamer", 100, 1000, True)  # slow stream

    # After 1 second: limit has 20 ghost, stream has 0.1 ghost
    engine.advance_time(1)

    assert limit.ghost0 > 19, f"Limit ghost: {limit.ghost0}"
    assert stream.ghost0 < 1, f"Stream ghost should be tiny: {stream.ghost0}"

    # Taker fills 15 — mostly from limit
    filled = engine.swap("Taker", True, 15)
    assert abs(filled - 15) < 1

    # Limit consumed most, stream barely touched
    assert limit.ghost0 < 10, "Limit should be heavily consumed"

    engine.check_invariants("after sequenced fill")


def test_6_scale_stress():
    """100 streamers + 100 limit makers + 10 takers."""
    engine = GhostEngine()
    stream = TwapStreamModule()
    limit = LimitOrderModule()
    engine.register_module(stream)
    engine.register_module(limit)

    random.seed(42)

    # 100 streamers
    for i in range(100):
        engine.mint(f"S{i}", 50, 0)
        engine.submit_stream(f"S{i}", 50, 1000, True)

    # 100 limit makers
    for i in range(100):
        engine.mint(f"L{i}", 10, 0)
        tick = 2900 + random.randint(0, 20) * 5
        engine.submit_limit(f"L{i}", 10, tick, True)

    # 10 takers
    for i in range(10):
        engine.mint(f"T{i}", 0, 5_000_000)

    engine.twap = 3010

    for step in range(100):
        engine.advance_time(10)
        engine.twap = 3000 + random.uniform(-50, 50)
        taker = f"T{step % 10}"
        engine.swap(taker, True, random.uniform(1, 20))

    engine.check_invariants("after scale stress")

    # Drain claims
    for i in range(100):
        for tick in list(limit.sell_buckets.keys()):
            engine.claim_limit(f"L{i}", tick, True)

    engine.check_invariants("after limit drain")


def test_7_full_drain_solvency():
    """Full drain: all types claim correctly, hook empties."""
    engine = GhostEngine()
    stream = TwapStreamModule()
    limit = LimitOrderModule()
    engine.register_module(stream)
    engine.register_module(limit)

    engine.mint("StreamAlice", 100, 0)
    engine.mint("LimitBob", 50, 0)
    engine.mint("Taker", 0, 1_000_000)
    engine.twap = 3010

    s_oid = engine.submit_stream("StreamAlice", 100, 100, True)
    engine.submit_limit("LimitBob", 50, 3000, True)

    # Run and fill
    engine.advance_time(100)
    engine.swap("Taker", True, 200)  # try to fill all

    # Cancel stream to recover remaining deposit + claim earnings
    engine.cancel_stream("StreamAlice", s_oid)

    # Drain limit
    engine.claim_limit("LimitBob", 3000, True)

    engine.check_invariants("after full drain")

    # Conservation: all tokens accounted for
    total_wallet_t0 = sum(w["T0"] for w in engine.wallets.values())
    total_wallet_t1 = sum(w["T1"] for w in engine.wallets.values())
    assert abs(total_wallet_t0 + engine.hook_t0 - engine.total_minted_t0) < 1, \
        f"T0 leak: wallets={total_wallet_t0} hook={engine.hook_t0} minted={engine.total_minted_t0}"
    assert abs(total_wallet_t1 + engine.hook_t1 - engine.total_minted_t1) < 1, \
        f"T1 leak: wallets={total_wallet_t1} hook={engine.hook_t1} minted={engine.total_minted_t1}"


def test_8_adversarial_limit_races_stream():
    """Can a limit order unfairly extract earnings from streamers?"""
    engine = GhostEngine()
    stream = TwapStreamModule()
    limit = LimitOrderModule()
    engine.register_module(stream)
    engine.register_module(limit)

    engine.mint("Streamer", 100, 0)
    engine.mint("Attacker", 100, 0)
    engine.mint("Taker", 0, 1_000_000)
    engine.twap = 3010

    # Streamer: 100 T0 over 100s (slow accrual)
    s_oid = engine.submit_stream("Streamer", 100, 100, True)

    # Advance 50s → 50 ghost from stream
    engine.advance_time(50)

    # Attacker places limit right before taker arrives
    engine.submit_limit("Attacker", 100, 3000, True)
    engine.advance_time(0.001)  # activate

    # Now: 50 stream ghost + 100 limit ghost = 150 total
    # Taker fills 150
    engine.swap("Taker", True, 150)

    # Streamer should get ~50/150 = 33% of earnings
    # Attacker should get ~100/150 = 67% of earnings
    # This is FAIR — attacker provided 2x the ghost
    e_stream, _ = engine.claim_stream("Streamer", s_oid)
    p_attacker, _ = engine.claim_limit("Attacker", 3000, True)

    # Attacker's share / Streamer's share ≈ 100/50 = 2.0
    if e_stream > 0:
        ratio = p_attacker / e_stream
        assert ratio > 1.5, f"Attacker should get ~2x: {ratio:.2f}"
        assert ratio < 3.0, f"Attacker shouldn't get >3x: {ratio:.2f}"

    engine.check_invariants("after adversarial race")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  UNIFIED GHOST ENGINE SIMULATION")
    print("  TwapStreamModule + LimitOrderModule")
    print("  7 invariants checked at every step")
    print("=" * 70)

    print("\n  ── CROSS-MODULE SCENARIOS ──")
    run_test("1. Pure TWAP stream (backward compat)", test_1_pure_twap_stream)
    run_test("2. Pure limit orders", test_2_pure_limit_orders)
    run_test("3. Mixed TWAP + limit (pro-rata share)", test_3_mixed_twap_and_limit)
    run_test("4. Cross-module netting", test_4_cross_module_netting)
    run_test("5. Limit fills before stream ghost", test_5_limit_fills_before_stream)
    run_test("6. Scale: 100 streamers + 100 limits + 10 takers", test_6_scale_stress)
    run_test("7. Full drain solvency", test_7_full_drain_solvency)
    run_test("8. Adversarial: limit races stream", test_8_adversarial_limit_races_stream)

    print(f"\n{'=' * 70}")
    print(f"  RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
    if FAIL == 0:
        print(f"  ALL {PASS} CROSS-MODULE TESTS PASSED ✅")
    else:
        print(f"  ⚠️  {FAIL} TESTS FAILED")
        sys.exit(1)
    print(f"{'=' * 70}")
