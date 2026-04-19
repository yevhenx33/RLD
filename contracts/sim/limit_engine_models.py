from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Iterable, List, Tuple


PRICE_SCALE = 1_000_000
EF_SCALE = 10**18


class EventKind(IntEnum):
    MAKER_SUBMIT = 0
    TAKER_FLOW = 1


class MakerSide(IntEnum):
    # Maker deposits quote and buys base. Executable when spot <= tick.
    BUY_BASE = 0
    # Maker deposits base and buys quote. Executable when spot >= tick.
    SELL_BASE = 1


@dataclass(frozen=True)
class Event:
    kind: EventKind
    timestamp: int
    side: MakerSide
    amount: int
    maker_id: int = -1
    tick: int = 0
    spot_price: int = 0
    sequence: int = 0


@dataclass(frozen=True)
class Scenario:
    seed: int
    maker_count: int
    events: List[Event]
    final_spot_price: int


@dataclass
class ModelOutcome:
    model_name: str
    maker_quote_balances: List[int]
    maker_base_balances: List[int]
    maker_terminal_value_quote: List[int]
    solvency_checks: Dict[str, bool]
    solvency_details: Dict[str, int]
    dust_quote: int = 0
    dust_base: int = 0


@dataclass
class PathComparison:
    ghost: ModelOutcome
    fifo: ModelOutcome
    prorata: ModelOutcome


def mul_div_floor(a: int, b: int, d: int) -> int:
    if d == 0:
        raise ZeroDivisionError("division by zero")
    return (a * b) // d


def mul_div_ceil(a: int, b: int, d: int) -> int:
    if d == 0:
        raise ZeroDivisionError("division by zero")
    if a == 0 or b == 0:
        return 0
    return ((a * b) + d - 1) // d


def allocate_pro_rata(total: int, weights: Iterable[int]) -> List[int]:
    ws = list(weights)
    if total == 0:
        return [0] * len(ws)
    total_weight = sum(ws)
    if total_weight == 0:
        return [0] * len(ws)

    out: List[int] = []
    running_weight = 0
    running_alloc = 0
    for w in ws:
        running_weight += w
        expected = (total * running_weight) // total_weight
        part = expected - running_alloc
        out.append(part)
        running_alloc = expected
    return out


def is_executable(side: MakerSide, tick: int, spot_price: int) -> bool:
    if side == MakerSide.BUY_BASE:
        return spot_price <= tick
    return spot_price >= tick


@dataclass
class _GhostMaker:
    side: MakerSide
    tick: int
    pending_input: int
    active_shares: int
    earnings_factor_last: int


@dataclass
class _ActivePool:
    remaining_input: int = 0
    total_shares: int = 0
    earnings_factor: int = 0
    distributed_output: int = 0


class GhostLimitEngineSimulator:
    """Global-merge, activation-only limit engine model."""

    def run(self, scenario: Scenario) -> ModelOutcome:
        makers: Dict[int, _GhostMaker] = {}
        pending_ticks: Dict[MakerSide, Dict[int, List[int]]] = {
            MakerSide.BUY_BASE: {},
            MakerSide.SELL_BASE: {},
        }
        active_pools: Dict[MakerSide, _ActivePool] = {
            MakerSide.BUY_BASE: _ActivePool(),
            MakerSide.SELL_BASE: _ActivePool(),
        }

        consumed_principal_quote_buybase = 0
        consumed_principal_base_sellbase = 0
        consumed_output_base_buybase = 0
        consumed_output_quote_sellbase = 0
        dust_base = 0
        dust_quote = 0

        for event in scenario.events:
            if event.kind == EventKind.MAKER_SUBMIT:
                maker = _GhostMaker(
                    side=event.side,
                    tick=event.tick,
                    pending_input=event.amount,
                    active_shares=0,
                    earnings_factor_last=0,
                )
                makers[event.maker_id] = maker
                pending_ticks[event.side].setdefault(event.tick, []).append(event.maker_id)
                continue

            # Taker flow:
            flow_side = event.side
            pool = active_pools[flow_side]
            self._activate_eligible(flow_side, event.spot_price, makers, pending_ticks, pool)

            if flow_side == MakerSide.BUY_BASE:
                desired_out_quote = mul_div_floor(event.amount, event.spot_price, PRICE_SCALE)
                filled_out_quote = min(desired_out_quote, pool.remaining_input)
                input_consumed_base = mul_div_ceil(filled_out_quote, PRICE_SCALE, event.spot_price)

                pool.remaining_input -= filled_out_quote
                consumed_principal_quote_buybase += filled_out_quote
                consumed_output_base_buybase += input_consumed_base

                if pool.total_shares > 0 and input_consumed_base > 0:
                    delta_ef = mul_div_floor(input_consumed_base, EF_SCALE, pool.total_shares)
                    pool.earnings_factor += delta_ef
                    distributed = mul_div_floor(delta_ef, pool.total_shares, EF_SCALE)
                    pool.distributed_output += distributed
                    dust_base += input_consumed_base - distributed
            else:
                desired_out_base = mul_div_floor(event.amount, PRICE_SCALE, event.spot_price)
                filled_out_base = min(desired_out_base, pool.remaining_input)
                input_consumed_quote = mul_div_ceil(filled_out_base, event.spot_price, PRICE_SCALE)

                pool.remaining_input -= filled_out_base
                consumed_principal_base_sellbase += filled_out_base
                consumed_output_quote_sellbase += input_consumed_quote

                if pool.total_shares > 0 and input_consumed_quote > 0:
                    delta_ef = mul_div_floor(input_consumed_quote, EF_SCALE, pool.total_shares)
                    pool.earnings_factor += delta_ef
                    distributed = mul_div_floor(delta_ef, pool.total_shares, EF_SCALE)
                    pool.distributed_output += distributed
                    dust_quote += input_consumed_quote - distributed

        maker_quote, maker_base = self._finalize_maker_balances(scenario.maker_count, makers, active_pools)
        maker_values = [
            q + mul_div_floor(b, scenario.final_spot_price, PRICE_SCALE) for q, b in zip(maker_quote, maker_base)
        ]

        initial_quote_buybase = sum(
            m.pending_input + m.active_shares for m in makers.values() if m.side == MakerSide.BUY_BASE
        )
        initial_base_sellbase = sum(
            m.pending_input + m.active_shares for m in makers.values() if m.side == MakerSide.SELL_BASE
        )

        remaining_quote_buybase = sum(
            maker_quote[maker_id]
            for maker_id, maker in makers.items()
            if maker.side == MakerSide.BUY_BASE
        )
        remaining_base_sellbase = sum(
            maker_base[maker_id]
            for maker_id, maker in makers.items()
            if maker.side == MakerSide.SELL_BASE
        )

        distributed_base_buybase = sum(
            maker_base[maker_id] for maker_id, maker in makers.items() if maker.side == MakerSide.BUY_BASE
        )
        distributed_quote_sellbase = sum(
            maker_quote[maker_id] for maker_id, maker in makers.items() if maker.side == MakerSide.SELL_BASE
        )
        claim_dust_base = active_pools[MakerSide.BUY_BASE].distributed_output - distributed_base_buybase
        claim_dust_quote = active_pools[MakerSide.SELL_BASE].distributed_output - distributed_quote_sellbase
        dust_base += claim_dust_base
        dust_quote += claim_dust_quote

        checks = {
            "non_negative_pool_buybase": active_pools[MakerSide.BUY_BASE].remaining_input >= 0,
            "non_negative_pool_sellbase": active_pools[MakerSide.SELL_BASE].remaining_input >= 0,
            "non_negative_dust_base": dust_base >= 0,
            "non_negative_dust_quote": dust_quote >= 0,
            "principal_conservation_buybase": (
                initial_quote_buybase == remaining_quote_buybase + consumed_principal_quote_buybase
            ),
            "principal_conservation_sellbase": (
                initial_base_sellbase == remaining_base_sellbase + consumed_principal_base_sellbase
            ),
            "output_conservation_buybase": (
                consumed_output_base_buybase == distributed_base_buybase + dust_base
            ),
            "output_conservation_sellbase": (
                consumed_output_quote_sellbase == distributed_quote_sellbase + dust_quote
            ),
        }

        details = {
            "initial_quote_buybase": initial_quote_buybase,
            "remaining_quote_buybase": remaining_quote_buybase,
            "consumed_principal_quote_buybase": consumed_principal_quote_buybase,
            "initial_base_sellbase": initial_base_sellbase,
            "remaining_base_sellbase": remaining_base_sellbase,
            "consumed_principal_base_sellbase": consumed_principal_base_sellbase,
            "consumed_output_base_buybase": consumed_output_base_buybase,
            "distributed_base_buybase": distributed_base_buybase,
            "dust_base": dust_base,
            "claim_dust_base": claim_dust_base,
            "consumed_output_quote_sellbase": consumed_output_quote_sellbase,
            "distributed_quote_sellbase": distributed_quote_sellbase,
            "dust_quote": dust_quote,
            "claim_dust_quote": claim_dust_quote,
        }

        return ModelOutcome(
            model_name="ghost_global_merge",
            maker_quote_balances=maker_quote,
            maker_base_balances=maker_base,
            maker_terminal_value_quote=maker_values,
            solvency_checks=checks,
            solvency_details=details,
            dust_quote=dust_quote,
            dust_base=dust_base,
        )

    @staticmethod
    def _activate_eligible(
        side: MakerSide,
        spot_price: int,
        makers: Dict[int, _GhostMaker],
        pending_ticks: Dict[MakerSide, Dict[int, List[int]]],
        pool: _ActivePool,
    ) -> None:
        eligible_ticks = [tick for tick in pending_ticks[side].keys() if is_executable(side, tick, spot_price)]
        for tick in eligible_ticks:
            maker_ids = pending_ticks[side].pop(tick)
            for maker_id in maker_ids:
                maker = makers[maker_id]
                if maker.pending_input == 0:
                    continue
                shares = maker.pending_input
                maker.pending_input = 0
                maker.active_shares += shares
                maker.earnings_factor_last = pool.earnings_factor
                pool.total_shares += shares
                pool.remaining_input += shares

    @staticmethod
    def _finalize_maker_balances(
        maker_count: int,
        makers: Dict[int, _GhostMaker],
        active_pools: Dict[MakerSide, _ActivePool],
    ) -> Tuple[List[int], List[int]]:
        maker_quote = [0] * maker_count
        maker_base = [0] * maker_count

        for maker_id, maker in makers.items():
            if maker.side == MakerSide.BUY_BASE:
                maker_quote[maker_id] += maker.pending_input
            else:
                maker_base[maker_id] += maker.pending_input

        for side in (MakerSide.BUY_BASE, MakerSide.SELL_BASE):
            pool = active_pools[side]
            active_order_ids = sorted(
                maker_id for maker_id, maker in makers.items() if maker.side == side and maker.active_shares > 0
            )
            shares = [makers[maker_id].active_shares for maker_id in active_order_ids]
            remaining_alloc = allocate_pro_rata(pool.remaining_input, shares)

            for idx, maker_id in enumerate(active_order_ids):
                maker = makers[maker_id]
                claim = mul_div_floor(
                    maker.active_shares,
                    pool.earnings_factor - maker.earnings_factor_last,
                    EF_SCALE,
                )
                if side == MakerSide.BUY_BASE:
                    maker_quote[maker_id] += remaining_alloc[idx]
                    maker_base[maker_id] += claim
                else:
                    maker_base[maker_id] += remaining_alloc[idx]
                    maker_quote[maker_id] += claim

        return maker_quote, maker_base


@dataclass
class _BookOrder:
    side: MakerSide
    tick: int
    remaining_input: int
    arrival_seq: int
    initial_input: int
    earned_quote: int = 0
    earned_base: int = 0


class _ClobBase:
    model_name: str

    def __init__(self) -> None:
        self._orders: Dict[int, _BookOrder] = {}

    def run(self, scenario: Scenario) -> ModelOutcome:
        self._orders = {}
        consumed_quote_buybase = 0
        consumed_base_sellbase = 0
        output_base_buybase = 0
        output_quote_sellbase = 0

        for event in scenario.events:
            if event.kind == EventKind.MAKER_SUBMIT:
                self._orders[event.maker_id] = _BookOrder(
                    side=event.side,
                    tick=event.tick,
                    remaining_input=event.amount,
                    arrival_seq=event.sequence,
                    initial_input=event.amount,
                )
                continue

            side = event.side
            if side == MakerSide.BUY_BASE:
                desired_quote = mul_div_floor(event.amount, event.spot_price, PRICE_SCALE)
                consumed_by_order = self._consume_input(side, event.spot_price, desired_quote)
                consumed_quote = sum(consumed_by_order.values())
                taker_base_consumed = mul_div_ceil(consumed_quote, PRICE_SCALE, event.spot_price)
                base_alloc = allocate_pro_rata(taker_base_consumed, consumed_by_order.values())

                for alloc, maker_id in zip(base_alloc, consumed_by_order.keys()):
                    self._orders[maker_id].earned_base += alloc

                consumed_quote_buybase += consumed_quote
                output_base_buybase += taker_base_consumed
            else:
                desired_base = mul_div_floor(event.amount, PRICE_SCALE, event.spot_price)
                consumed_by_order = self._consume_input(side, event.spot_price, desired_base)
                consumed_base = sum(consumed_by_order.values())
                taker_quote_consumed = mul_div_ceil(consumed_base, event.spot_price, PRICE_SCALE)
                quote_alloc = allocate_pro_rata(taker_quote_consumed, consumed_by_order.values())

                for alloc, maker_id in zip(quote_alloc, consumed_by_order.keys()):
                    self._orders[maker_id].earned_quote += alloc

                consumed_base_sellbase += consumed_base
                output_quote_sellbase += taker_quote_consumed

        maker_quote = [0] * scenario.maker_count
        maker_base = [0] * scenario.maker_count
        for maker_id, order in self._orders.items():
            if order.side == MakerSide.BUY_BASE:
                maker_quote[maker_id] = order.remaining_input + order.earned_quote
                maker_base[maker_id] = order.earned_base
            else:
                maker_quote[maker_id] = order.earned_quote
                maker_base[maker_id] = order.remaining_input + order.earned_base

        maker_values = [
            q + mul_div_floor(b, scenario.final_spot_price, PRICE_SCALE) for q, b in zip(maker_quote, maker_base)
        ]

        initial_quote_buybase = sum(
            order.initial_input for order in self._orders.values() if order.side == MakerSide.BUY_BASE
        )
        initial_base_sellbase = sum(
            order.initial_input for order in self._orders.values() if order.side == MakerSide.SELL_BASE
        )
        # For CLOB models output is fully allocated (no dust).
        checks = {
            "non_negative_remaining": all(order.remaining_input >= 0 for order in self._orders.values()),
            "principal_conservation_buybase": (
                initial_quote_buybase == consumed_quote_buybase + sum(
                    order.remaining_input for order in self._orders.values() if order.side == MakerSide.BUY_BASE
                )
            ),
            "principal_conservation_sellbase": (
                initial_base_sellbase == consumed_base_sellbase + sum(
                    order.remaining_input for order in self._orders.values() if order.side == MakerSide.SELL_BASE
                )
            ),
            "output_conservation_buybase": (
                output_base_buybase == sum(order.earned_base for order in self._orders.values() if order.side == MakerSide.BUY_BASE)
            ),
            "output_conservation_sellbase": (
                output_quote_sellbase == sum(order.earned_quote for order in self._orders.values() if order.side == MakerSide.SELL_BASE)
            ),
        }
        details = {
            "consumed_quote_buybase": consumed_quote_buybase,
            "consumed_base_sellbase": consumed_base_sellbase,
            "output_base_buybase": output_base_buybase,
            "output_quote_sellbase": output_quote_sellbase,
        }

        return ModelOutcome(
            model_name=self.model_name,
            maker_quote_balances=maker_quote,
            maker_base_balances=maker_base,
            maker_terminal_value_quote=maker_values,
            solvency_checks=checks,
            solvency_details=details,
        )

    def _consume_input(self, side: MakerSide, spot_price: int, desired_input_out: int) -> Dict[int, int]:
        if desired_input_out == 0:
            return {}

        consumed: Dict[int, int] = {}
        remaining_need = desired_input_out
        for level in self._priority_levels(side, spot_price):
            if remaining_need == 0:
                break
            level_orders = list(level)
            if not level_orders:
                continue
            level_available = sum(self._orders[maker_id].remaining_input for maker_id in level_orders)
            level_take = min(level_available, remaining_need)
            allocations = self._allocate_level(level_orders, level_take)

            for maker_id, take in allocations.items():
                self._orders[maker_id].remaining_input -= take
                consumed[maker_id] = consumed.get(maker_id, 0) + take
            remaining_need -= level_take

        return consumed

    def _priority_levels(self, side: MakerSide, spot_price: int) -> Iterable[Iterable[int]]:
        raise NotImplementedError

    def _allocate_level(self, maker_ids: List[int], level_take: int) -> Dict[int, int]:
        raise NotImplementedError


class FifoClobSimulator(_ClobBase):
    model_name = "fifo_clob"

    def _priority_levels(self, side: MakerSide, spot_price: int) -> Iterable[Iterable[int]]:
        eligible = [
            maker_id
            for maker_id, order in self._orders.items()
            if order.side == side and order.remaining_input > 0 and is_executable(side, order.tick, spot_price)
        ]
        eligible.sort(key=lambda maker_id: self._orders[maker_id].arrival_seq)
        return ([maker_id] for maker_id in eligible)

    def _allocate_level(self, maker_ids: List[int], level_take: int) -> Dict[int, int]:
        maker_id = maker_ids[0]
        available = self._orders[maker_id].remaining_input
        return {maker_id: min(available, level_take)}


class ProRataClobSimulator(_ClobBase):
    model_name = "prorata_clob"

    def _priority_levels(self, side: MakerSide, spot_price: int) -> Iterable[Iterable[int]]:
        by_tick: Dict[int, List[int]] = {}
        for maker_id, order in self._orders.items():
            if order.side != side or order.remaining_input == 0:
                continue
            if not is_executable(side, order.tick, spot_price):
                continue
            by_tick.setdefault(order.tick, []).append(maker_id)

        if side == MakerSide.BUY_BASE:
            sorted_ticks = sorted(by_tick.keys(), reverse=True)
        else:
            sorted_ticks = sorted(by_tick.keys())
        return (by_tick[tick] for tick in sorted_ticks)

    def _allocate_level(self, maker_ids: List[int], level_take: int) -> Dict[int, int]:
        weights = [self._orders[maker_id].remaining_input for maker_id in maker_ids]
        allocs = allocate_pro_rata(level_take, weights)
        return {maker_id: alloc for maker_id, alloc in zip(maker_ids, allocs)}


def run_all_models(scenario: Scenario) -> PathComparison:
    ghost = GhostLimitEngineSimulator().run(scenario)
    fifo = FifoClobSimulator().run(scenario)
    prorata = ProRataClobSimulator().run(scenario)
    return PathComparison(ghost=ghost, fifo=fifo, prorata=prorata)
