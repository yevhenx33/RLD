# Limit Engine Monte Carlo Audit Harness

This folder contains a deterministic Monte Carlo harness for validating the
proposed **LimitEngine v1** semantics before Solidity implementation.

## Model Under Test

- Engine scope: **LimitEngine-only**
- Trigger: **taker-touch activation** (no autonomous crossing loop)
- Activation: **one-way**
- Post-activation accounting: **global merged active pool per direction**
- Price source for executability checks: **router spot at intercept time**

## Benchmarks

Each generated event tape is replayed under three deterministic models:

1. `GhostLimitEngineSimulator` (model under test)
2. `FifoClobSimulator` (price-time priority benchmark)
3. `ProRataClobSimulator` (price-priority across levels, pro-rata within level)

## Exact Value Metric

All maker outcomes are compared in **quote numeraire** at final spot `P_T`.

For each maker:

- `quoteBalance` = remaining quote principal + earned quote proceeds
- `baseBalance` = remaining base principal + earned base proceeds
- `terminalValueQuote = quoteBalance + floor(baseBalance * P_T / PRICE_SCALE)`

This gives one comparable scalar payout per maker per path.

## Strict Pathwise Checks

For every path and every maker id:

- `ghostValueQuote >= fifoValueQuote`
- `ghostValueQuote >= proRataValueQuote`

Additionally, strict solvency/conservation checks are enforced per model:

- no negative order balances
- no negative pool balances
- input principal conservation by side:
  - total deposited input token = remaining input + consumed input
- output settlement conservation:
  - consumed output token = distributed output + model dust

## Rounding Conventions

To mirror on-chain behavior from `TwapEngine`/`GhostRouter` arithmetic:

- desired out amounts use floor division
- reverse-converted taker `inputConsumed` uses ceil division
- accumulator increments use floor division
- undistributed accumulator remainder is tracked as explicit dust

## Counterexample Extraction

On first strict-dominance failure, the harness runs a deterministic shrink pass:

- greedily removes events while preserving failure
- emits minimized event tape and violation summary

Artifacts are written as JSON/CSV/Markdown for reproducibility.

## Files

- `limit_engine_scenarios.py`: seeded event generation
- `limit_engine_models.py`: ghost/fifo/pro-rata simulators
- `limit_engine_report.py`: checks, shrinker, and report emitters
- `limit_engine_monte_carlo.py`: CLI runner and fixture generation

## Typical Commands

Quick audit (10k paths):

```bash
python contracts/sim/limit_engine_monte_carlo.py --paths 10000 --seed 1337 --output-dir contracts/sim/out
```

Stress sweep:

```bash
python contracts/sim/limit_engine_monte_carlo.py --paths 200000 --seed 1337 --output-dir contracts/sim/out
```

Regenerate Foundry replay fixture:

```bash
python contracts/sim/limit_engine_monte_carlo.py --write-replay-fixture contracts/test/twamm_v3/fixtures/limit_engine_replay_case_1.json
```
