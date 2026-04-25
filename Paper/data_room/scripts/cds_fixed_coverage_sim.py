#!/usr/bin/env python3
"""
Simulate fixed-coverage CDS funding.

The Parametric CDS paper models fixed absolute coverage C as:

    N(t) = C / P_max * exp(F * t)

where N(t) is the wCDS token balance required to keep coverage constant while
the token's effective coverage decays through NF(t)=exp(-F*t).

This script estimates:
  - initial wCDS needed at t=0
  - initial USDC buy cost
  - USDC that must be streamed through TWAMM to replenish wCDS
  - total USDC budget to post for the term
  - ending wCDS balance and estimated reclaim value if the user exits
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SimPoint:
    day: int
    year_t: float
    borrow_rate: float
    required_wcds: float
    nf: float
    market_price: float
    cumulative_stream_usdc: float
    reclaim_value: float


def fmt_usd(value: float) -> str:
    return f"${value:,.2f}"


def fmt_num(value: float, decimals: int = 6) -> str:
    return f"{value:,.{decimals}f}"


def linear_rate(t: float, start: float, end: float) -> float:
    return start + (end - start) * t


def simulate_fixed_coverage(
    coverage_usdc: float,
    term_days: int,
    start_borrow_apy: float,
    end_borrow_apy: float,
    r_max: float,
    target_utilization: float,
    steps: int,
) -> tuple[list[SimPoint], dict[str, float]]:
    if coverage_usdc <= 0:
        raise ValueError("coverage_usdc must be positive")
    if term_days <= 0:
        raise ValueError("term_days must be positive")
    if not 0 < r_max:
        raise ValueError("r_max must be positive")
    if not 0 < target_utilization < 1:
        raise ValueError("target_utilization must be in (0, 1)")

    term_years = term_days / 365.0
    steps = max(1, steps)
    dt = term_years / steps

    # Paper parameter: F = -ln(1 - target utilization)
    decay_rate = -math.log(1.0 - target_utilization)
    p_max = 100.0 * r_max

    # Buy enough wCDS at t=0 to cover C immediately.
    initial_wcds = coverage_usdc / p_max
    initial_price = 100.0 * start_borrow_apy
    initial_buy_usdc = initial_wcds * initial_price

    cumulative_stream = 0.0
    points: list[SimPoint] = []

    for i in range(steps + 1):
        t = i * dt
        day = round(t * 365.0)
        rate = linear_rate(t / term_years if term_years else 0.0, start_borrow_apy, end_borrow_apy)
        nf = math.exp(-decay_rate * t)
        required_wcds = (coverage_usdc / p_max) * math.exp(decay_rate * t)
        market_price = p_max * (rate / r_max) * nf
        reclaim_value = required_wcds * market_price

        if i > 0:
            # Midpoint rule for stream cost:
            # Stream = C * F * (r_t / r_max) dt
            t_mid = (i - 0.5) * dt
            rate_mid = linear_rate(t_mid / term_years if term_years else 0.0, start_borrow_apy, end_borrow_apy)
            cumulative_stream += coverage_usdc * decay_rate * (rate_mid / r_max) * dt

        points.append(
            SimPoint(
                day=day,
                year_t=t,
                borrow_rate=rate,
                required_wcds=required_wcds,
                nf=nf,
                market_price=market_price,
                cumulative_stream_usdc=cumulative_stream,
                reclaim_value=reclaim_value,
            )
        )

    ending = points[-1]
    total_posted = initial_buy_usdc + cumulative_stream
    net_cost_after_reclaim = total_posted - ending.reclaim_value

    summary = {
        "coverage_usdc": coverage_usdc,
        "term_years": term_years,
        "decay_rate": decay_rate,
        "p_max": p_max,
        "initial_wcds": initial_wcds,
        "initial_price": initial_price,
        "initial_buy_usdc": initial_buy_usdc,
        "stream_usdc": cumulative_stream,
        "total_posted_usdc": total_posted,
        "ending_wcds": ending.required_wcds,
        "ending_reclaim_usdc": ending.reclaim_value,
        "net_cost_after_reclaim": net_cost_after_reclaim,
        "net_cost_bps_of_coverage": (net_cost_after_reclaim / coverage_usdc) * 10_000.0,
    }
    return points, summary


def print_summary(summary: dict[str, float]) -> None:
    print("\n== Fixed Coverage CDS Simulation ==")
    print(f"Coverage target:          {fmt_usd(summary['coverage_usdc'])}")
    print(f"Term:                     {summary['term_years']:.4f} years")
    print(f"Decay F:                  {summary['decay_rate']:.6f}")
    print(f"P_max:                    ${summary['p_max']:.4f}")
    print()
    print("User funding:")
    print(f"Initial wCDS needed:      {fmt_num(summary['initial_wcds'])} wCDS")
    print(f"Initial wCDS price:       {fmt_usd(summary['initial_price'])}")
    print(f"Initial buy cost:         {fmt_usd(summary['initial_buy_usdc'])}")
    print(f"TWAMM stream budget:      {fmt_usd(summary['stream_usdc'])}")
    print(f"Total USDC to post:       {fmt_usd(summary['total_posted_usdc'])}")
    print()
    print("At term end, absent settlement/default:")
    print(f"Ending wCDS balance:      {fmt_num(summary['ending_wcds'])} wCDS")
    print(f"Estimated reclaim value:  {fmt_usd(summary['ending_reclaim_usdc'])}")
    print(f"Net premium after exit:   {fmt_usd(summary['net_cost_after_reclaim'])}")
    print(f"Net cost / coverage:      {summary['net_cost_bps_of_coverage']:.2f} bps")


def print_table(points: list[SimPoint], every_days: int) -> None:
    print("\n== Path Samples ==")
    print("day,borrow_apy,nf,required_wcds,market_price,cum_stream_usdc,reclaim_value")
    last_day = -1
    for point in points:
        if point.day != 0 and point.day != points[-1].day and point.day - last_day < every_days:
            continue
        print(
            f"{point.day},"
            f"{point.borrow_rate:.6f},"
            f"{point.nf:.6f},"
            f"{point.required_wcds:.6f},"
            f"{point.market_price:.6f},"
            f"{point.cumulative_stream_usdc:.2f},"
            f"{point.reclaim_value:.2f}"
        )
        last_day = point.day


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate fixed-coverage CDS economics.")
    parser.add_argument("--coverage", type=float, default=100_000.0, help="Target coverage in USDC.")
    parser.add_argument("--term-days", type=int, default=365, help="Coverage term in days.")
    parser.add_argument(
        "--borrow-apy",
        type=float,
        default=0.07440384626558765,
        help="Starting borrow APY as decimal, e.g. 0.0744 for 7.44%%.",
    )
    parser.add_argument(
        "--end-borrow-apy",
        type=float,
        default=None,
        help="Ending borrow APY as decimal. Defaults to --borrow-apy for flat-rate simulation.",
    )
    parser.add_argument("--r-max", type=float, default=0.75, help="Maximum borrow APY as decimal.")
    parser.add_argument("--target-utilization", type=float, default=0.90, help="Target utilization decimal.")
    parser.add_argument("--steps", type=int, default=365, help="Integration steps.")
    parser.add_argument("--table-every-days", type=int, default=30, help="Print one sample row per N days.")
    args = parser.parse_args()

    end_rate = args.borrow_apy if args.end_borrow_apy is None else args.end_borrow_apy
    points, summary = simulate_fixed_coverage(
        coverage_usdc=args.coverage,
        term_days=args.term_days,
        start_borrow_apy=args.borrow_apy,
        end_borrow_apy=end_rate,
        r_max=args.r_max,
        target_utilization=args.target_utilization,
        steps=args.steps,
    )
    print_summary(summary)
    print_table(points, args.table_every_days)


if __name__ == "__main__":
    main()
