#!/usr/bin/env python3
"""
CDS economic verifier
=====================

Self-checking simulation for the Phase 8 CDS economic model.

It verifies:
  1. The PCDS yield invariant: Y_CDS >= r_supply.
  2. Continuous NF decay: NF(t) = exp(-F * t).
  3. Constant max-coverage maintenance via TWAP/TWAMM-style replenishment.
  4. Bounded payout behavior under a sustained rate/default shock.

This is intentionally off-chain. It does not deploy contracts and does not touch
Reth state.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


SECONDS_PER_YEAR = 31_536_000
BPS_TOLERANCE = 1e-4  # 1 bp absolute relative tolerance.


@dataclass(frozen=True)
class MarketParams:
    target_utilization: float = 0.90
    reserve_factor: float = 0.10
    r_min: float = 0.02
    r_kink: float = 0.08
    r_max: float = 0.75
    kink_convexity: float = 2.0
    scalar_k: float = 100.0

    @property
    def decay_rate(self) -> float:
        return -math.log(1.0 - self.target_utilization)

    @property
    def max_valid_rmax(self) -> float:
        return self.decay_rate / (1.0 - self.reserve_factor)

    @property
    def p_max(self) -> float:
        return self.scalar_k * self.r_max


@dataclass(frozen=True)
class SimulationConfig:
    target_coverage: float = 1_000_000.0
    horizon_days: int = 365
    default_day: int = 240
    dt_days: int = 1


@dataclass
class DailyState:
    day: int
    utilization: float
    borrow_rate: float
    nf: float
    target_tokens: float
    tokens_bought: float
    premium_paid: float
    maintained_max_coverage: float
    passive_max_coverage: float
    mark_value: float
    supply_yield: float
    cds_yield: float
    alpha: float


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def utilization_path(day: int, cfg: SimulationConfig, params: MarketParams) -> float:
    """Deterministic path: normal market, rising stress, then terminal freeze."""
    if day < cfg.default_day - 60:
        # Low-volatility operating zone around target.
        return 0.72 + 0.04 * math.sin(day / 17.0)
    if day < cfg.default_day:
        progress = (day - (cfg.default_day - 60)) / 60.0
        return 0.76 + (0.99 - 0.76) * progress * progress
    return 1.0


def borrow_rate_from_utilization(utilization: float, params: MarketParams) -> float:
    """Bounded, kinked IRM-like curve."""
    u = max(0.0, min(1.0, utilization))
    delta = params.target_utilization
    if u <= delta:
        return params.r_min + (params.r_kink - params.r_min) * (u / delta)

    x = (u - delta) / (1.0 - delta)
    convex = x ** params.kink_convexity
    return params.r_kink + (params.r_max - params.r_kink) * convex


def normalization_factor(t_years: float, decay_rate: float) -> float:
    return math.exp(-decay_rate * t_years)


def cds_yield(borrow_rate: float, params: MarketParams) -> float:
    return params.decay_rate * (borrow_rate / params.r_max)


def supply_yield(utilization: float, borrow_rate: float, params: MarketParams) -> float:
    return utilization * borrow_rate * (1.0 - params.reserve_factor)


def verify_yield_invariant(params: MarketParams) -> dict:
    require(0.0 < params.target_utilization < 1.0, "target utilization must be in (0, 1)")
    require(0.0 <= params.reserve_factor < 1.0, "reserve factor must be in [0, 1)")
    require(params.r_max > 0.0, "r_max must be positive")
    require(
        params.r_max <= params.max_valid_rmax + 1e-15,
        f"r_max violates invariant bound: {params.r_max} > {params.max_valid_rmax}",
    )

    min_alpha = float("inf")
    worst = None
    samples = 1_001
    for i in range(samples):
        utilization = i / (samples - 1)
        rate = borrow_rate_from_utilization(utilization, params)
        y_cds = cds_yield(rate, params)
        y_supply = supply_yield(utilization, rate, params)
        alpha = y_cds - y_supply
        if alpha < min_alpha:
            min_alpha = alpha
            worst = (utilization, rate, y_cds, y_supply)

    require(min_alpha >= -1e-12, f"yield invariant violated: alpha={min_alpha}")
    aggressive_rmax = params.max_valid_rmax * 1.25
    aggressive_terminal_alpha = params.decay_rate - aggressive_rmax * (1.0 - params.reserve_factor)
    require(
        aggressive_rmax > params.max_valid_rmax,
        "negative-control r_max should exceed validity bound",
    )
    require(
        aggressive_terminal_alpha < 0.0,
        "negative-control r_max should violate the terminal invariant",
    )
    return {
        "decay_rate": params.decay_rate,
        "max_valid_rmax": params.max_valid_rmax,
        "configured_rmax": params.r_max,
        "min_alpha": min_alpha,
        "worst_case": {
            "utilization": worst[0],
            "borrow_rate": worst[1],
            "y_cds": worst[2],
            "y_supply": worst[3],
        },
        "negative_control_aggressive_rmax": aggressive_rmax,
        "negative_control_terminal_alpha": aggressive_terminal_alpha,
    }


def simulate_constant_coverage(params: MarketParams, cfg: SimulationConfig) -> tuple[list[DailyState], dict]:
    require(cfg.default_day <= cfg.horizon_days, "default day must be within horizon")
    require(cfg.dt_days == 1, "this verifier currently expects daily steps")

    dt_years = cfg.dt_days / 365.0
    initial_tokens = cfg.target_coverage / params.p_max
    current_tokens = initial_tokens
    passive_tokens = initial_tokens
    cumulative_premium = 0.0
    cumulative_supply_yield = 0.0
    cumulative_continuous_premium = 0.0
    states: list[DailyState] = []

    max_coverage_error = 0.0
    max_daily_premium_error = 0.0

    for day in range(0, cfg.horizon_days + 1, cfg.dt_days):
        t_years = day / 365.0
        utilization = utilization_path(day, cfg, params)
        rate = borrow_rate_from_utilization(utilization, params)
        nf = normalization_factor(t_years, params.decay_rate)
        target_tokens = cfg.target_coverage / (params.p_max * nf)

        tokens_bought = max(0.0, target_tokens - current_tokens)
        # Buy at the beginning-of-step intrinsic CDS mark.
        mark_price = params.scalar_k * rate * nf
        premium_paid = tokens_bought * mark_price
        current_tokens += tokens_bought
        cumulative_premium += premium_paid

        continuous_premium = cfg.target_coverage * cds_yield(rate, params) * dt_years
        if day > 0:
            cumulative_continuous_premium += continuous_premium
            daily_error = abs(premium_paid - continuous_premium) / max(continuous_premium, 1.0)
            max_daily_premium_error = max(max_daily_premium_error, daily_error)

        y_supply = supply_yield(utilization, rate, params)
        y_cds = cds_yield(rate, params)
        cumulative_supply_yield += cfg.target_coverage * y_supply * dt_years

        maintained_max_coverage = current_tokens * params.p_max * nf
        passive_max_coverage = passive_tokens * params.p_max * nf
        mark_value = current_tokens * mark_price
        alpha = y_cds - y_supply
        coverage_error = abs(maintained_max_coverage - cfg.target_coverage) / cfg.target_coverage
        max_coverage_error = max(max_coverage_error, coverage_error)

        states.append(
            DailyState(
                day=day,
                utilization=utilization,
                borrow_rate=rate,
                nf=nf,
                target_tokens=target_tokens,
                tokens_bought=tokens_bought,
                premium_paid=premium_paid,
                maintained_max_coverage=maintained_max_coverage,
                passive_max_coverage=passive_max_coverage,
                mark_value=mark_value,
                supply_yield=y_supply,
                cds_yield=y_cds,
                alpha=alpha,
            )
        )

    default_state = states[cfg.default_day]
    final_state = states[-1]
    require(max_coverage_error <= BPS_TOLERANCE, f"constant coverage drifted by {max_coverage_error}")
    require(default_state.maintained_max_coverage >= cfg.target_coverage * (1.0 - BPS_TOLERANCE), "default payout under target")
    require(default_state.passive_max_coverage < cfg.target_coverage, "passive hold should decay")
    require(cumulative_premium + 1e-9 >= cumulative_supply_yield, "underwriter premium below passive supply yield")

    summary = {
        "initial_tokens": initial_tokens,
        "final_tokens": final_state.target_tokens,
        "max_coverage_error": max_coverage_error,
        "max_daily_premium_error_vs_continuous": max_daily_premium_error,
        "cumulative_premium_paid": cumulative_premium,
        "cumulative_continuous_premium": cumulative_continuous_premium,
        "cumulative_supply_yield_floor": cumulative_supply_yield,
        "default_day": cfg.default_day,
        "default_nf": default_state.nf,
        "default_borrow_rate": default_state.borrow_rate,
        "default_maintained_payout": default_state.maintained_max_coverage,
        "default_passive_payout": default_state.passive_max_coverage,
        "default_mark_value": default_state.mark_value,
        "final_maintained_max_coverage": final_state.maintained_max_coverage,
        "final_passive_max_coverage": final_state.passive_max_coverage,
    }
    return states, summary


def write_artifacts(
    params: MarketParams,
    cfg: SimulationConfig,
    invariant: dict,
    states: list[DailyState],
    summary: dict,
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "market_params": asdict(params),
        "simulation_config": asdict(cfg),
        "yield_invariant": invariant,
        "coverage_summary": summary,
        "sample_states": [asdict(states[i]) for i in sorted({0, 30, 90, cfg.default_day, cfg.horizon_days}) if i < len(states)],
    }
    json_path = out_dir / "cds_economic_verification.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n")

    md_path = out_dir / "cds_economic_verification.md"
    md_path.write_text(
        "\n".join(
            [
                "# CDS Economic Verification",
                "",
                "## Invariants",
                f"- Decay rate F: `{invariant['decay_rate']:.12f}`",
                f"- Configured r_max: `{invariant['configured_rmax']:.6f}`",
                f"- Max valid r_max: `{invariant['max_valid_rmax']:.6f}`",
                f"- Minimum alpha: `{invariant['min_alpha']:.12f}`",
                "",
                "## Constant Coverage",
                f"- Target coverage: `${cfg.target_coverage:,.2f}`",
                f"- Max coverage error: `{summary['max_coverage_error']:.12f}`",
                f"- Cumulative premium paid: `${summary['cumulative_premium_paid']:,.2f}`",
                f"- Passive supply yield floor: `${summary['cumulative_supply_yield_floor']:,.2f}`",
                "",
                "## Default Shock",
                f"- Default day: `{summary['default_day']}`",
                f"- Default borrow rate: `{summary['default_borrow_rate']:.6f}`",
                f"- Maintained payout: `${summary['default_maintained_payout']:,.2f}`",
                f"- Passive buy-and-hold payout: `${summary['default_passive_payout']:,.2f}`",
                "",
            ]
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify off-chain CDS economics.")
    parser.add_argument("--target-utilization", type=float, default=0.90)
    parser.add_argument("--reserve-factor", type=float, default=0.10)
    parser.add_argument("--r-max", type=float, default=0.75)
    parser.add_argument("--target-coverage", type=float, default=1_000_000.0)
    parser.add_argument("--horizon-days", type=int, default=365)
    parser.add_argument("--default-day", type=int, default=240)
    parser.add_argument("--out-dir", default="backend/rates/artifacts")
    args = parser.parse_args()

    params = MarketParams(
        target_utilization=args.target_utilization,
        reserve_factor=args.reserve_factor,
        r_max=args.r_max,
    )
    cfg = SimulationConfig(
        target_coverage=args.target_coverage,
        horizon_days=args.horizon_days,
        default_day=args.default_day,
    )

    invariant = verify_yield_invariant(params)
    states, summary = simulate_constant_coverage(params, cfg)
    write_artifacts(params, cfg, invariant, states, summary, Path(args.out_dir))

    print("CDS economic verification passed")
    print(f"  F: {params.decay_rate:.12f}")
    print(f"  r_max bound: configured={params.r_max:.6f}, max_valid={params.max_valid_rmax:.6f}")
    print(f"  min alpha: {invariant['min_alpha']:.12f}")
    print(f"  target coverage: ${cfg.target_coverage:,.2f}")
    print(f"  max coverage error: {summary['max_coverage_error']:.12f}")
    print(f"  default payout maintained: ${summary['default_maintained_payout']:,.2f}")
    print(f"  default payout passive: ${summary['default_passive_payout']:,.2f}")
    print(f"  cumulative premium: ${summary['cumulative_premium_paid']:,.2f}")
    print(f"  passive supply floor: ${summary['cumulative_supply_yield_floor']:,.2f}")


if __name__ == "__main__":
    main()
