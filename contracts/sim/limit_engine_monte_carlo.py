from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from limit_engine_models import (
    GhostLimitEngineSimulator,
    PathComparison,
    run_all_models,
)
from limit_engine_report import (
    DominanceFailure,
    PathCheckResult,
    run_path_checks,
    shrink_counterexample,
    write_counterexample_json,
    write_markdown_report,
    write_path_rows_csv,
    write_summary_json,
)
from limit_engine_scenarios import Scenario, ScenarioConfig, generate_scenario, scenario_to_json_dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Limit Engine Monte Carlo audit harness")
    parser.add_argument("--paths", type=int, default=10_000, help="Number of Monte Carlo paths")
    parser.add_argument("--seed", type=int, default=1337, help="Base RNG seed")
    parser.add_argument("--maker-count", type=int, default=40, help="Makers per path")
    parser.add_argument("--taker-steps", type=int, default=120, help="Taker events per path")
    parser.add_argument("--output-dir", type=Path, default=Path("contracts/sim/out"), help="Output directory")
    parser.add_argument("--no-shrink", action="store_true", help="Disable counterexample shrinking")
    parser.add_argument(
        "--write-replay-fixture",
        type=Path,
        default=None,
        help="Optional path to emit deterministic replay fixture for Foundry",
    )
    parser.add_argument("--replay-seed", type=int, default=20260419, help="Seed used for replay fixture generation")
    parser.add_argument(
        "--replay-maker-count",
        type=int,
        default=8,
        help="Maker count for replay fixture generation",
    )
    parser.add_argument(
        "--replay-taker-steps",
        type=int,
        default=20,
        help="Taker steps for replay fixture generation",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = ScenarioConfig(maker_count=args.maker_count, taker_steps=args.taker_steps)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, int]] = []
    dominance_failure_paths = 0
    solvency_failure_paths = 0
    total_dominance_failures = 0
    total_solvency_failures = 0
    worst_fifo_deficit = 0
    worst_prorata_deficit = 0

    first_failure: Optional[DominanceFailure] = None
    first_failure_scenario: Optional[Scenario] = None
    first_failure_check: Optional[PathCheckResult] = None
    first_failure_comparison: Optional[PathComparison] = None

    for path_index in range(args.paths):
        seed = args.seed + path_index
        scenario = generate_scenario(seed=seed, cfg=cfg)
        comparison = run_all_models(scenario)
        check = run_path_checks(path_index=path_index, comparison=comparison)

        if check.dominance_failures:
            dominance_failure_paths += 1
            total_dominance_failures += len(check.dominance_failures)
            if first_failure is None:
                first_failure = check.dominance_failures[0]
                first_failure_scenario = scenario
                first_failure_check = check
                first_failure_comparison = comparison
        if check.solvency_failures:
            solvency_failure_paths += 1
            total_solvency_failures += len(check.solvency_failures)

        worst_fifo_deficit = max(worst_fifo_deficit, -check.min_delta_vs_fifo)
        worst_prorata_deficit = max(worst_prorata_deficit, -check.min_delta_vs_prorata)

        rows.append(
            {
                "path_index": path_index,
                "seed": seed,
                "min_delta_vs_fifo": check.min_delta_vs_fifo,
                "min_delta_vs_prorata": check.min_delta_vs_prorata,
                "dominance_failure_count": len(check.dominance_failures),
                "solvency_failure_count": len(check.solvency_failures),
            }
        )

    summary = {
        "paths": args.paths,
        "seed": args.seed,
        "maker_count": args.maker_count,
        "taker_steps": args.taker_steps,
        "dominance_failure_paths": dominance_failure_paths,
        "solvency_failure_paths": solvency_failure_paths,
        "total_dominance_failures": total_dominance_failures,
        "total_solvency_failures": total_solvency_failures,
        "worst_fifo_deficit_quote": worst_fifo_deficit,
        "worst_prorata_deficit_quote": worst_prorata_deficit,
    }

    write_summary_json(output_dir / "summary.json", summary)
    write_path_rows_csv(output_dir / "path_metrics.csv", rows)
    write_markdown_report(
        output_dir / "audit_report.md",
        summary=summary,
        first_failure=first_failure,
        worst_fifo_deficit=worst_fifo_deficit,
        worst_prorata_deficit=worst_prorata_deficit,
    )

    if first_failure_scenario is not None and first_failure_check is not None and first_failure_comparison is not None:
        write_counterexample_json(
            output_dir / "counterexample_first.json",
            scenario=first_failure_scenario,
            path_check=first_failure_check,
            comparison=first_failure_comparison,
        )

        if not args.no_shrink:
            shrunk, shrunk_check, shrunk_comparison = shrink_first_failure(first_failure_scenario)
            write_counterexample_json(
                output_dir / "counterexample_shrunk.json",
                scenario=shrunk,
                path_check=shrunk_check,
                comparison=shrunk_comparison,
            )

    if args.write_replay_fixture is not None:
        write_replay_fixture(
            path=args.write_replay_fixture,
            seed=args.replay_seed,
            maker_count=args.replay_maker_count,
            taker_steps=args.replay_taker_steps,
        )


def shrink_first_failure(scenario: Scenario) -> Tuple[Scenario, PathCheckResult, PathComparison]:
    def still_fails(candidate: Scenario) -> bool:
        candidate_comparison = run_all_models(candidate)
        candidate_check = run_path_checks(path_index=0, comparison=candidate_comparison)
        return candidate_check.has_failure

    shrunk = shrink_counterexample(scenario, still_fails=still_fails)
    shrunk_comparison = run_all_models(shrunk)
    shrunk_check = run_path_checks(path_index=0, comparison=shrunk_comparison)
    return shrunk, shrunk_check, shrunk_comparison


def write_replay_fixture(path: Path, seed: int, maker_count: int, taker_steps: int) -> None:
    cfg = ScenarioConfig(
        maker_count=maker_count,
        taker_steps=taker_steps,
        min_order_amount=3_000,
        max_order_amount=45_000,
        min_taker_amount=2_000,
        max_taker_amount=35_000,
        drift_std=14_000,
        jump_probability_bps=450,
        jump_std=35_000,
        adversarial_spike_probability_bps=300,
        adversarial_spike_bps=900,
    )
    scenario = generate_scenario(seed=seed, cfg=cfg)
    outcome = GhostLimitEngineSimulator().run(scenario)

    events = scenario.events
    fixture = {
        "meta": {
            "seed": seed,
            "maker_count": maker_count,
            "taker_steps": taker_steps,
        },
        "scenario": scenario_to_json_dict(scenario),
        "events": {
            "kind": [int(e.kind) for e in events],
            "timestamp": [e.timestamp for e in events],
            "side": [int(e.side) for e in events],
            "amount": [e.amount for e in events],
            "maker_id": [e.maker_id for e in events],
            "tick": [e.tick for e in events],
            "spot_price": [e.spot_price for e in events],
            "sequence": [e.sequence for e in events],
        },
        "expected": {
            "quote_balances": outcome.maker_quote_balances,
            "base_balances": outcome.maker_base_balances,
            "terminal_value_quote": outcome.maker_terminal_value_quote,
            "dust_quote": outcome.dust_quote,
            "dust_base": outcome.dust_base,
            "final_spot_price": scenario.final_spot_price,
        },
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
