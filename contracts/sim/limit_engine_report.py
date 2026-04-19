from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from limit_engine_models import ModelOutcome, PathComparison, Scenario
from limit_engine_scenarios import scenario_to_json_dict


@dataclass(frozen=True)
class DominanceFailure:
    path_index: int
    maker_id: int
    benchmark: str
    ghost_value_quote: int
    benchmark_value_quote: int
    deficit_quote: int


@dataclass(frozen=True)
class SolvencyFailure:
    path_index: int
    model_name: str
    check_name: str


@dataclass(frozen=True)
class PathCheckResult:
    path_index: int
    min_delta_vs_fifo: int
    min_delta_vs_prorata: int
    dominance_failures: List[DominanceFailure]
    solvency_failures: List[SolvencyFailure]

    @property
    def has_failure(self) -> bool:
        return bool(self.dominance_failures or self.solvency_failures)


def run_path_checks(path_index: int, comparison: PathComparison) -> PathCheckResult:
    ghost = comparison.ghost
    fifo = comparison.fifo
    prorata = comparison.prorata

    dominance_failures: List[DominanceFailure] = []
    min_fifo = 0
    min_prorata = 0
    maker_count = len(ghost.maker_terminal_value_quote)
    for maker_id in range(maker_count):
        ghost_value = ghost.maker_terminal_value_quote[maker_id]
        fifo_value = fifo.maker_terminal_value_quote[maker_id]
        prorata_value = prorata.maker_terminal_value_quote[maker_id]

        fifo_delta = ghost_value - fifo_value
        pro_delta = ghost_value - prorata_value
        min_fifo = min(min_fifo, fifo_delta)
        min_prorata = min(min_prorata, pro_delta)

        if fifo_delta < 0:
            dominance_failures.append(
                DominanceFailure(
                    path_index=path_index,
                    maker_id=maker_id,
                    benchmark="fifo",
                    ghost_value_quote=ghost_value,
                    benchmark_value_quote=fifo_value,
                    deficit_quote=-fifo_delta,
                )
            )
        if pro_delta < 0:
            dominance_failures.append(
                DominanceFailure(
                    path_index=path_index,
                    maker_id=maker_id,
                    benchmark="prorata",
                    ghost_value_quote=ghost_value,
                    benchmark_value_quote=prorata_value,
                    deficit_quote=-pro_delta,
                )
            )

    solvency_failures: List[SolvencyFailure] = []
    for outcome in (ghost, fifo, prorata):
        for check_name, ok in outcome.solvency_checks.items():
            if not ok:
                solvency_failures.append(
                    SolvencyFailure(
                        path_index=path_index,
                        model_name=outcome.model_name,
                        check_name=check_name,
                    )
                )

    return PathCheckResult(
        path_index=path_index,
        min_delta_vs_fifo=min_fifo,
        min_delta_vs_prorata=min_prorata,
        dominance_failures=dominance_failures,
        solvency_failures=solvency_failures,
    )


def shrink_counterexample(
    scenario: Scenario,
    still_fails: Callable[[Scenario], bool],
) -> Scenario:
    events = list(scenario.events)
    changed = True
    while changed and len(events) > 1:
        changed = False
        i = 0
        while i < len(events):
            candidate_events = events[:i] + events[i + 1 :]
            candidate = Scenario(
                seed=scenario.seed,
                maker_count=scenario.maker_count,
                events=candidate_events,
                final_spot_price=scenario.final_spot_price,
            )
            if still_fails(candidate):
                events = candidate_events
                changed = True
            else:
                i += 1

    return Scenario(
        seed=scenario.seed,
        maker_count=scenario.maker_count,
        events=events,
        final_spot_price=scenario.final_spot_price,
    )


def write_path_rows_csv(path: Path, rows: Sequence[Dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "path_index",
        "seed",
        "min_delta_vs_fifo",
        "min_delta_vs_prorata",
        "dominance_failure_count",
        "solvency_failure_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_summary_json(path: Path, summary: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def write_counterexample_json(
    path: Path,
    scenario: Scenario,
    path_check: PathCheckResult,
    comparison: PathComparison,
) -> None:
    payload = {
        "scenario": scenario_to_json_dict(scenario),
        "path_check": {
            "path_index": path_check.path_index,
            "min_delta_vs_fifo": path_check.min_delta_vs_fifo,
            "min_delta_vs_prorata": path_check.min_delta_vs_prorata,
            "dominance_failures": [df.__dict__ for df in path_check.dominance_failures],
            "solvency_failures": [sf.__dict__ for sf in path_check.solvency_failures],
        },
        "outcomes": {
            "ghost": _outcome_to_json(comparison.ghost),
            "fifo": _outcome_to_json(comparison.fifo),
            "prorata": _outcome_to_json(comparison.prorata),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_markdown_report(
    path: Path,
    summary: Dict[str, object],
    first_failure: Optional[DominanceFailure],
    worst_fifo_deficit: int,
    worst_prorata_deficit: int,
) -> None:
    lines: List[str] = []
    lines.append("# Limit Engine Monte Carlo Audit Report")
    lines.append("")
    lines.append("## Configuration")
    lines.append(f"- Paths: `{summary['paths']}`")
    lines.append(f"- Base seed: `{summary['seed']}`")
    lines.append(f"- Maker count per path: `{summary['maker_count']}`")
    lines.append(f"- Taker steps per path: `{summary['taker_steps']}`")
    lines.append("")
    lines.append("## Strict Pathwise Results")
    lines.append(f"- Dominance failures: `{summary['dominance_failure_paths']}` / `{summary['paths']}` paths")
    lines.append(f"- Solvency failures: `{summary['solvency_failure_paths']}` / `{summary['paths']}` paths")
    lines.append(f"- Worst ghost-vs-FIFO deficit (quote): `{worst_fifo_deficit}`")
    lines.append(f"- Worst ghost-vs-pro-rata deficit (quote): `{worst_prorata_deficit}`")
    lines.append("")
    if first_failure is None:
        lines.append("## Counterexample")
        lines.append("- No strict pathwise dominance failures observed in sampled paths.")
    else:
        lines.append("## First Counterexample")
        lines.append(f"- Path index: `{first_failure.path_index}`")
        lines.append(f"- Maker id: `{first_failure.maker_id}`")
        lines.append(f"- Benchmark: `{first_failure.benchmark}`")
        lines.append(f"- Deficit (quote): `{first_failure.deficit_quote}`")
    lines.append("")
    lines.append("## Recommendation")
    if summary["dominance_failure_paths"] > 0:
        lines.append(
            "- Strict per-maker dominance does not hold under global merge activation-only semantics; "
            "consider criterion relaxation or matching-policy redesign (e.g. per-tick active pools)."
        )
    else:
        lines.append("- No sampled failures; run higher path count (>=1M) before production decision.")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _outcome_to_json(outcome: ModelOutcome) -> Dict[str, object]:
    return {
        "model_name": outcome.model_name,
        "maker_quote_balances": outcome.maker_quote_balances,
        "maker_base_balances": outcome.maker_base_balances,
        "maker_terminal_value_quote": outcome.maker_terminal_value_quote,
        "solvency_checks": outcome.solvency_checks,
        "solvency_details": outcome.solvency_details,
        "dust_quote": outcome.dust_quote,
        "dust_base": outcome.dust_base,
    }
