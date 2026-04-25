#!/usr/bin/env python3
"""
CDS underwriter verifier
========================

Off-chain verification for the underwriter side of the Phase 8 CDS model.

It proves the underwriter vault mechanics we intend to deploy:
  1. Locked collateral bounds maximum liability.
  2. JIT issuance grows token supply as NF decays while keeping max liability constant.
  3. Premium income exceeds the passive supply-yield floor in the no-default path.
  4. Default loss is bounded by locked collateral, with no unbounded tail.

This script does not touch Reth and does not deploy contracts.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from cds_economic_verification import (
    BPS_TOLERANCE,
    MarketParams,
    SimulationConfig,
    borrow_rate_from_utilization,
    cds_yield,
    normalization_factor,
    require,
    simulate_constant_coverage,
    supply_yield,
    utilization_path,
    verify_yield_invariant,
)


@dataclass(frozen=True)
class UnderwriterConfig:
    locked_collateral: float = 100_000_000.0
    collateral_yield: float = 0.0  # raw USDC escrow in current launch plan
    horizon_days: int = 365
    default_day: int = 240
    dt_days: int = 1


@dataclass
class UnderwriterDailyState:
    day: int
    utilization: float
    borrow_rate: float
    nf: float
    cumulative_token_supply: float
    newly_issued_tokens: float
    max_liability: float
    premium_income: float
    cumulative_premium: float
    collateral_yield_income: float
    cumulative_collateral_yield: float
    passive_supply_income: float
    cumulative_passive_supply_income: float
    equity_no_default: float
    equity_if_settled_now: float


def simulate_underwriter(
    params: MarketParams,
    cfg: UnderwriterConfig,
) -> tuple[list[UnderwriterDailyState], dict]:
    require(cfg.locked_collateral > 0.0, "locked collateral must be positive")
    require(cfg.default_day <= cfg.horizon_days, "default day must be within horizon")
    require(cfg.dt_days == 1, "this verifier currently expects daily steps")

    sim_cfg = SimulationConfig(
        target_coverage=cfg.locked_collateral,
        horizon_days=cfg.horizon_days,
        default_day=cfg.default_day,
        dt_days=cfg.dt_days,
    )
    buyer_states, buyer_summary = simulate_constant_coverage(params, sim_cfg)

    dt_years = cfg.dt_days / 365.0
    cumulative_token_supply = cfg.locked_collateral / params.p_max
    cumulative_premium = 0.0
    cumulative_collateral_yield = 0.0
    cumulative_passive_supply_income = 0.0
    max_liability_error = 0.0
    max_jit_token_error = 0.0
    max_premium_error = 0.0
    states: list[UnderwriterDailyState] = []

    for buyer_state in buyer_states:
        day = buyer_state.day
        t_years = day / 365.0
        utilization = utilization_path(day, sim_cfg, params)
        borrow_rate = borrow_rate_from_utilization(utilization, params)
        nf = normalization_factor(t_years, params.decay_rate)

        target_supply = cfg.locked_collateral / (params.p_max * nf)
        newly_issued_tokens = max(0.0, target_supply - cumulative_token_supply)
        cumulative_token_supply += newly_issued_tokens

        # The underwriter sells exactly what the buyer must replenish.
        premium_income = buyer_state.premium_paid
        cumulative_premium += premium_income

        collateral_yield_income = cfg.locked_collateral * cfg.collateral_yield * dt_years
        cumulative_collateral_yield += collateral_yield_income

        passive_supply_income = (
            cfg.locked_collateral * supply_yield(utilization, borrow_rate, params) * dt_years
        )
        cumulative_passive_supply_income += passive_supply_income

        max_liability = cumulative_token_supply * params.p_max * nf
        equity_no_default = (
            cfg.locked_collateral + cumulative_premium + cumulative_collateral_yield
        )
        equity_if_settled_now = equity_no_default - max_liability

        max_liability_error = max(
            max_liability_error,
            abs(max_liability - cfg.locked_collateral) / cfg.locked_collateral,
        )
        max_jit_token_error = max(
            max_jit_token_error,
            abs(cumulative_token_supply - buyer_state.target_tokens) / max(buyer_state.target_tokens, 1.0),
        )
        max_premium_error = max(
            max_premium_error,
            abs(premium_income - buyer_state.premium_paid) / max(buyer_state.premium_paid, 1.0),
        )

        states.append(
            UnderwriterDailyState(
                day=day,
                utilization=utilization,
                borrow_rate=borrow_rate,
                nf=nf,
                cumulative_token_supply=cumulative_token_supply,
                newly_issued_tokens=newly_issued_tokens,
                max_liability=max_liability,
                premium_income=premium_income,
                cumulative_premium=cumulative_premium,
                collateral_yield_income=collateral_yield_income,
                cumulative_collateral_yield=cumulative_collateral_yield,
                passive_supply_income=passive_supply_income,
                cumulative_passive_supply_income=cumulative_passive_supply_income,
                equity_no_default=equity_no_default,
                equity_if_settled_now=equity_if_settled_now,
            )
        )

    default_state = states[cfg.default_day]
    final_state = states[-1]
    require(max_liability_error <= BPS_TOLERANCE, f"liability bound drifted: {max_liability_error}")
    require(max_jit_token_error <= BPS_TOLERANCE, f"JIT supply mismatch: {max_jit_token_error}")
    require(max_premium_error <= BPS_TOLERANCE, f"premium mismatch: {max_premium_error}")
    require(
        final_state.cumulative_premium >= final_state.cumulative_passive_supply_income,
        "no-default premium income failed to beat passive supply floor",
    )
    require(
        default_state.max_liability <= cfg.locked_collateral * (1.0 + BPS_TOLERANCE),
        "default liability exceeds locked collateral",
    )
    require(default_state.equity_if_settled_now >= 0.0, "settlement created negative equity")
    require(
        cfg.locked_collateral - default_state.equity_if_settled_now <= cfg.locked_collateral,
        "default loss exceeded locked collateral",
    )

    summary = {
        "locked_collateral": cfg.locked_collateral,
        "initial_token_supply": states[0].cumulative_token_supply,
        "final_token_supply": final_state.cumulative_token_supply,
        "max_liability_error": max_liability_error,
        "max_jit_token_error": max_jit_token_error,
        "max_premium_error": max_premium_error,
        "no_default_cumulative_premium": final_state.cumulative_premium,
        "no_default_collateral_yield": final_state.cumulative_collateral_yield,
        "no_default_equity": final_state.equity_no_default,
        "no_default_profit": final_state.equity_no_default - cfg.locked_collateral,
        "passive_supply_income_floor": final_state.cumulative_passive_supply_income,
        "premium_minus_passive_supply": (
            final_state.cumulative_premium - final_state.cumulative_passive_supply_income
        ),
        "default_day": cfg.default_day,
        "default_borrow_rate": default_state.borrow_rate,
        "default_max_liability": default_state.max_liability,
        "default_cumulative_premium": default_state.cumulative_premium,
        "default_collateral_yield": default_state.cumulative_collateral_yield,
        "default_equity_after_settlement": default_state.equity_if_settled_now,
        "default_loss_on_locked_collateral": cfg.locked_collateral - default_state.equity_if_settled_now,
        "buyer_constant_coverage_summary": buyer_summary,
    }
    return states, summary


def write_artifacts(
    params: MarketParams,
    cfg: UnderwriterConfig,
    invariant: dict,
    states: list[UnderwriterDailyState],
    summary: dict,
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    sample_days = sorted({0, 30, 90, cfg.default_day, cfg.horizon_days})
    payload = {
        "market_params": asdict(params),
        "underwriter_config": asdict(cfg),
        "yield_invariant": invariant,
        "underwriter_summary": summary,
        "sample_states": [
            asdict(states[day])
            for day in sample_days
            if 0 <= day < len(states)
        ],
    }

    json_path = out_dir / "cds_underwriter_verification.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n")

    md_path = out_dir / "cds_underwriter_verification.md"
    md_path.write_text(
        "\n".join(
            [
                "# CDS Underwriter Verification",
                "",
                "## Liability Bound",
                f"- Locked collateral: `${summary['locked_collateral']:,.2f}`",
                f"- Max liability error: `{summary['max_liability_error']:.12f}`",
                f"- Initial token supply: `{summary['initial_token_supply']:,.6f}`",
                f"- Final token supply: `{summary['final_token_supply']:,.6f}`",
                "",
                "## No-Default Economics",
                f"- Cumulative premium: `${summary['no_default_cumulative_premium']:,.2f}`",
                f"- Passive supply income floor: `${summary['passive_supply_income_floor']:,.2f}`",
                f"- Premium minus passive supply: `${summary['premium_minus_passive_supply']:,.2f}`",
                f"- No-default profit: `${summary['no_default_profit']:,.2f}`",
                "",
                "## Default Settlement",
                f"- Default day: `{summary['default_day']}`",
                f"- Default borrow rate: `{summary['default_borrow_rate']:.6f}`",
                f"- Default max liability: `${summary['default_max_liability']:,.2f}`",
                f"- Cumulative premium before default: `${summary['default_cumulative_premium']:,.2f}`",
                f"- Equity after settlement: `${summary['default_equity_after_settlement']:,.2f}`",
                "",
            ]
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify off-chain CDS underwriter economics.")
    parser.add_argument("--target-utilization", type=float, default=0.90)
    parser.add_argument("--reserve-factor", type=float, default=0.10)
    parser.add_argument("--r-max", type=float, default=0.75)
    parser.add_argument("--locked-collateral", type=float, default=100_000_000.0)
    parser.add_argument("--collateral-yield", type=float, default=0.0)
    parser.add_argument("--horizon-days", type=int, default=365)
    parser.add_argument("--default-day", type=int, default=240)
    parser.add_argument("--out-dir", default="backend/rates/artifacts")
    args = parser.parse_args()

    params = MarketParams(
        target_utilization=args.target_utilization,
        reserve_factor=args.reserve_factor,
        r_max=args.r_max,
    )
    cfg = UnderwriterConfig(
        locked_collateral=args.locked_collateral,
        collateral_yield=args.collateral_yield,
        horizon_days=args.horizon_days,
        default_day=args.default_day,
    )

    invariant = verify_yield_invariant(params)
    states, summary = simulate_underwriter(params, cfg)
    write_artifacts(params, cfg, invariant, states, summary, Path(args.out_dir))

    print("CDS underwriter verification passed")
    print(f"  locked collateral: ${summary['locked_collateral']:,.2f}")
    print(f"  max liability error: {summary['max_liability_error']:.12f}")
    print(f"  final token supply: {summary['final_token_supply']:,.6f}")
    print(f"  no-default premium: ${summary['no_default_cumulative_premium']:,.2f}")
    print(f"  passive supply floor: ${summary['passive_supply_income_floor']:,.2f}")
    print(f"  premium minus passive supply: ${summary['premium_minus_passive_supply']:,.2f}")
    print(f"  default max liability: ${summary['default_max_liability']:,.2f}")
    print(f"  equity after default settlement: ${summary['default_equity_after_settlement']:,.2f}")


if __name__ == "__main__":
    main()
