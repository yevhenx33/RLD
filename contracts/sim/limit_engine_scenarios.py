from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from limit_engine_models import Event, EventKind, MakerSide, PRICE_SCALE, Scenario


@dataclass(frozen=True)
class ScenarioConfig:
    maker_count: int = 40
    taker_steps: int = 120
    min_order_amount: int = 5_000
    max_order_amount: int = 250_000
    min_taker_amount: int = 2_000
    max_taker_amount: int = 220_000
    min_price: int = int(0.70 * PRICE_SCALE)
    max_price: int = int(1.35 * PRICE_SCALE)
    start_price: int = PRICE_SCALE
    drift_std: int = 20_000
    jump_probability_bps: int = 600
    jump_std: int = 65_000
    adversarial_spike_probability_bps: int = 350
    adversarial_spike_bps: int = 1_500


def _clamp(v: int, lo: int, hi: int) -> int:
    return min(max(v, lo), hi)


def _sample_int(rng: random.Random, lo: int, hi: int) -> int:
    if lo == hi:
        return lo
    return rng.randint(lo, hi)


def _next_price(cfg: ScenarioConfig, rng: random.Random, current_price: int) -> int:
    drift = int(rng.gauss(0, cfg.drift_std))
    if rng.randint(1, 10_000) <= cfg.jump_probability_bps:
        drift += int(rng.gauss(0, cfg.jump_std))
    next_price = _clamp(current_price + drift, cfg.min_price, cfg.max_price)
    return next_price


def _apply_adversarial_spike(cfg: ScenarioConfig, rng: random.Random, side: MakerSide, spot_price: int) -> int:
    if rng.randint(1, 10_000) > cfg.adversarial_spike_probability_bps:
        return spot_price
    spike = (spot_price * cfg.adversarial_spike_bps) // 10_000
    if side == MakerSide.BUY_BASE:
        # BUY_BASE activation becomes easier when spot is pushed down.
        return _clamp(spot_price - spike, cfg.min_price, cfg.max_price)
    # SELL_BASE activation becomes easier when spot is pushed up.
    return _clamp(spot_price + spike, cfg.min_price, cfg.max_price)


def _sample_tick(cfg: ScenarioConfig, rng: random.Random, side: MakerSide, arrival_price: int) -> int:
    # Center around current spot and skew so some orders are immediately marketable while
    # others stay dormant for later activation.
    if side == MakerSide.BUY_BASE:
        lower = int(arrival_price * 0.88)
        upper = int(arrival_price * 1.12)
    else:
        lower = int(arrival_price * 0.88)
        upper = int(arrival_price * 1.12)
    tick = _sample_int(rng, _clamp(lower, cfg.min_price, cfg.max_price), _clamp(upper, cfg.min_price, cfg.max_price))
    return tick


def generate_scenario(seed: int, cfg: ScenarioConfig) -> Scenario:
    rng = random.Random(seed)
    prices: List[int] = []
    p = cfg.start_price
    for _ in range(cfg.taker_steps):
        p = _next_price(cfg, rng, p)
        prices.append(p)

    maker_arrivals: Dict[int, List[Event]] = {}
    for maker_id in range(cfg.maker_count):
        ts = _sample_int(rng, 0, cfg.taker_steps - 1)
        side = MakerSide(_sample_int(rng, 0, 1))
        amount = _sample_int(rng, cfg.min_order_amount, cfg.max_order_amount)
        tick = _sample_tick(cfg, rng, side, prices[ts])
        maker_event = Event(
            kind=EventKind.MAKER_SUBMIT,
            timestamp=ts,
            side=side,
            amount=amount,
            maker_id=maker_id,
            tick=tick,
            spot_price=prices[ts],
            sequence=maker_id,
        )
        maker_arrivals.setdefault(ts, []).append(maker_event)

    events: List[Event] = []
    seq = cfg.maker_count
    for ts in range(cfg.taker_steps):
        arrivals = maker_arrivals.get(ts, [])
        arrivals.sort(key=lambda e: e.maker_id)
        events.extend(arrivals)

        taker_side = MakerSide(_sample_int(rng, 0, 1))
        taker_amount = _sample_int(rng, cfg.min_taker_amount, cfg.max_taker_amount)
        raw_price = prices[ts]
        spot_price = _apply_adversarial_spike(cfg, rng, taker_side, raw_price)
        taker_event = Event(
            kind=EventKind.TAKER_FLOW,
            timestamp=ts,
            side=taker_side,
            amount=taker_amount,
            maker_id=-1,
            tick=0,
            spot_price=spot_price,
            sequence=seq,
        )
        events.append(taker_event)
        seq += 1

    final_spot = prices[-1]
    return Scenario(
        seed=seed,
        maker_count=cfg.maker_count,
        events=events,
        final_spot_price=final_spot,
    )


def scenario_to_json_dict(scenario: Scenario) -> Dict[str, object]:
    return {
        "seed": scenario.seed,
        "maker_count": scenario.maker_count,
        "final_spot_price": scenario.final_spot_price,
        "events": [
            {
                "kind": int(event.kind),
                "timestamp": event.timestamp,
                "side": int(event.side),
                "amount": event.amount,
                "maker_id": event.maker_id,
                "tick": event.tick,
                "spot_price": event.spot_price,
                "sequence": event.sequence,
            }
            for event in scenario.events
        ],
    }


def scenario_from_json_dict(raw: Dict[str, object]) -> Scenario:
    raw_events = raw["events"]
    events: List[Event] = []
    for item in raw_events:
        events.append(
            Event(
                kind=EventKind(item["kind"]),
                timestamp=item["timestamp"],
                side=MakerSide(item["side"]),
                amount=item["amount"],
                maker_id=item.get("maker_id", -1),
                tick=item.get("tick", 0),
                spot_price=item.get("spot_price", 0),
                sequence=item.get("sequence", 0),
            )
        )

    return Scenario(
        seed=raw["seed"],
        maker_count=raw["maker_count"],
        events=events,
        final_spot_price=raw["final_spot_price"],
    )


def write_scenario(path: Path, scenario: Scenario) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = scenario_to_json_dict(scenario)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_scenario(path: Path) -> Scenario:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return scenario_from_json_dict(payload)
