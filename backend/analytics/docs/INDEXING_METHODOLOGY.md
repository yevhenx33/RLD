# Indexing Methodology

This document formalizes the event-indexing validation model used by the analytics stack. The core contract is:

1. collect the canonical raw onchain inputs,
2. replay those inputs through the deterministic protocol processor,
3. compare replayed state against direct RPC reads at an anchor block.

The goal is not only to produce dashboard rows. The goal is to prove that those rows are reproducible from chain data and that any serving snapshot can be traced back to either an event replay result or an explicitly documented external source.

## Scope

The methodology applies to event-sourced lending integrations such as Aave, Spark, Euler, Morpho, Fluid, and Compound. Compound V2/V3 currently has the most explicit end-to-end harness in `analytics.scripts.compound_ops`, but the same validation shape is the standard for new protocols.

Offchain feeds such as SOFR use a related source-of-truth check against their official API response rather than an EVM anchor block.

## Phase 1: Collection

Collection is append-first raw input capture.

For EVM protocols, the collector requests logs from HyperSync using each source class' `log_selection()` and routes returned logs through `source.route(log)`. The collector writes protocol-specific raw tables and records block timestamps so that replay never needs to infer time from wall-clock execution.

Collection invariants:

- The input range is explicit: `[from_block, to_block]`.
- The raw log identity is stable: contract, block number, transaction hash, log index, topics, and data.
- Logs are stored before interpretation. This keeps ABI or processor fixes replayable without re-querying the provider when the raw event is already present.
- Cursors are operational metadata, not correctness proofs. Correctness comes from replay and anchor checks.
- Collection progress is tracked through `collector_state` and `source_status`; these tables answer freshness, not financial accuracy.

Performance characteristics:

- Runtime is mainly proportional to block span and log density for the source selection.
- Sparse protocols are dominated by provider round trips; dense protocols are dominated by ClickHouse insert volume.
- Batching is controlled by the command's block-window size and ClickHouse insert settings. The expected tuning knob is the largest batch that keeps provider latency and ClickHouse active parts stable.
- Precision at this phase is binary: either the canonical log exists in the raw table with exact EVM fields, or it does not. Numeric precision is not introduced here.

## Phase 2: Processor Replay

Processor replay is deterministic state reconstruction from raw logs.

The processor reads raw rows ordered by block number and log index, reconstructs simulated log objects, decodes protocol events, and sends decoded rows into the source's `merge()` implementation. Protocol processors own their own state machine because accounting rules differ across protocols.

Replay invariants:

- Ordering is deterministic: block number, transaction hash where needed, and log index.
- Processor output must be a pure function of raw logs plus explicit bootstrap/config state.
- Protocol math stays in integer/raw-unit space until values are intentionally normalized for serving.
- Output rows retain the protocol ID and entity ID needed to compare against RPC reads.
- Reprocessing a bounded window should be idempotent at the logical row level. ClickHouse `ReplacingMergeTree` tables and optional bounded rewrites are implementation details for making that practical.

Serving output:

- Protocol-specific metric tables preserve source-specific fields.
- `market_timeseries` is the canonical generic market series.
- `api_market_latest` is the API-facing latest snapshot.
- Forward-filled hourly rows are allowed for chart continuity, but latest snapshots must use raw event-derived metric rows when the protocol has sparse updates. Compound V3 follows this rule so that quiet markets do not inherit synthetic timestamps from another market's activity.

Performance characteristics:

- Runtime is proportional to decoded event count and to the cost of protocol-specific state transitions.
- Replay is normally faster than collection once raw logs are local because it is ClickHouse-read plus Python state-machine work, not provider-bound.
- Numeric precision depends on the source processor. Required practice is to keep contract quantities as integers and apply token decimals, WAD scaling, APY formulas, and oracle prices at the last responsible point.

## Phase 3: Anchor Block Invariant Check

The anchor check compares indexed replay state to direct RPC calls at one block.

For Compound, `compound-anchor` resolves an anchor block, reads indexed state from ClickHouse, reads the same state directly from protocol contracts by `eth_call` at that block, and writes a run record plus any field-level diffs.

The invariant is:

```text
indexed_state(protocol, market, field, anchor_block)
  == rpc_state(protocol_contract, market, field, anchor_block)
```

with explicit tolerances for normalized floating values.

Anchor block selection:

- `--block-number N` pins the check to an exact historical block.
- `--block-mode processed` uses the latest block the processor claims to have replayed.
- `--block-mode latest` uses a recent confirmed RPC head when a freshness-oriented check is desired.

Compound V3 checked fields:

- market registry: active Comets and base token identity,
- market state: total supply base, total borrow base, utilization,
- interest model output: supply APY and borrow APY,
- collateral metadata: asset set, borrow collateral factor, liquidate collateral factor, liquidation factor, and supply cap.

Precision policy:

- Integer contract quantities should match exactly after unit normalization unless there is an explicitly documented bootstrap or accrual boundary.
- Collateral factors and risk parameters use WAD normalization and are checked at near machine precision. Current Compound metadata tolerance is `1e-12`.
- APYs are compared as normalized decimal rates. Current Compound anchor default tolerance is `1e-6`, equal to `0.01` basis points.
- Any diff larger than tolerance is persisted to `compound_rpc_anchor_diffs` with indexed value, RPC value, field, market, block, and drift.

Pass/fail policy:

- `OK`: no field exceeds tolerance.
- `DRIFT`: at least one field exceeds tolerance; the run is still recorded for diagnosis.
- `ERROR`: the invariant check could not complete.
- `--fail-on-drift` should be used in CI or release gates.

## End-To-End Replay Command Shape

Compound exposes the full methodology as one bounded workflow:

```bash
python -m analytics.scripts.rld_indexer compound-e2e \
  --protocol v3 \
  --from-block <from_block> \
  --to-block <to_block> \
  --batch-blocks 5000 \
  --block-mode processed \
  --fail-on-drift
```

This performs:

1. optional registry/state bootstrap,
2. bounded HyperSync collection,
3. bounded processor replay,
4. serving-table smoke checks,
5. final RPC anchor validation.

For a pure invariant check without collection/replay:

```bash
python -m analytics.scripts.rld_indexer compound-anchor \
  --protocol v3 \
  --block-mode processed \
  --fail-on-drift
```

## Current Compound V3 Measurement

Measured on May 12, 2026 from the VPS working tree against the production ClickHouse/RPC configuration:

```text
command: compound-anchor --protocol v3 --block-mode processed
anchor block: 25079057
markets checked: 6
drifted markets: 0
diffs: 0
status: OK
wall-clock time: 29.542 seconds
```

Interpretation:

- Precision: the indexed Compound V3 replay state matched direct RPC reads for all checked fields within configured tolerances at the processed anchor block.
- Time: the current anchor check is RPC-bound. It performs multiple `eth_call` reads per Comet and collateral asset, so elapsed time is expected to scale with `markets + collateral assets`.
- Serving consistency: after the latest snapshot fix, `api_market_latest` for all 6 Compound V3 markets matched the latest raw `compound_v3_comet_metrics` APY rows at `0.0` basis points drift.

## Performance Metrics To Track

Every end-to-end audit should report these numbers:

| Phase | Metric | Meaning |
| --- | --- | --- |
| Collection | blocks scanned | Width of the replay window. |
| Collection | raw logs inserted | Source event density and provider payload size. |
| Collection | elapsed seconds | Provider plus raw ClickHouse insert time. |
| Replay | decoded events | Number of events accepted by protocol decoder. |
| Replay | metric rows written | Number of normalized output rows. |
| Replay | elapsed seconds | Local deterministic processing cost. |
| Serving | latest rows | Count in `api_market_latest` for the protocol. |
| Serving | series rows | Count in `market_timeseries` for the protocol. |
| Anchor | checked markets | Number of protocol markets/contracts verified. |
| Anchor | checked fields | Total RPC-vs-indexed comparisons. |
| Anchor | drifted fields | Number of failed comparisons. |
| Anchor | max notional drift | Largest token/base-unit state mismatch. |
| Anchor | max APY drift | Largest decimal-rate mismatch. |
| Anchor | elapsed seconds | RPC-bound validation time. |

The current `compound_rpc_anchor_runs` table stores precision outcomes: status, checked markets, drifted markets, max notional drift, and max APY drift. Duration should be added to the run table if anchor performance is promoted to a dashboard or CI trend.

## Acceptance Criteria For A New Protocol

A new protocol integration is complete only when all of the following are true:

- Raw collection is bounded, cursor-tracked, and replayable.
- Processor replay can rebuild protocol state from stored raw inputs.
- Latest snapshots do not rely on synthetic chart fill rows when market updates are sparse.
- A direct source-of-truth invariant exists: RPC anchor for EVM protocols or official API anchor for offchain feeds.
- The invariant records run-level status and field-level diffs.
- Precision tolerances are explicit in code and documented in this methodology.
- A recent audit report includes precision and timing for collection, replay, serving smoke, and anchor validation.
