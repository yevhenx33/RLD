# Data Pipeline Indexer Architecture (Current + Roadmap)

> Launch scope note: this data-pipeline track is **non-launch-critical** for the Reth V2 baseline and is operated separately from the core simulation launch path.

## 1) Scope and Objectives

This document is the canonical architecture reference for the ClickHouse-backed rates/indexer stack in `data-pipeline`.

It covers:
- Current runtime topology and data flow.
- Current storage and API serving model.
- Health/readiness semantics.
- Known weak points and prioritized future steps.

Protocol-specific deep dives remain in:
- `data-pipeline/docs/AAVE_INDEXER.md`
- `data-pipeline/docs/MORPHO_INDEXER.md`

## 2) Runtime Topology (Current)

### Canonical services

- `graphql-api` (from `docker/docker-compose.infra.yml`)
  - Serves GraphQL and health endpoints on host `:5000`.
  - Uses shared ClickHouse client lifecycle with reconnect/reset behavior.
- Protocol workers (from `data-pipeline/docker-compose.yml`)
  - `aave_collector`, `aave_processor`
  - `morpho_collector`, `morpho_processor`
  - `chainlink_collector`, `chainlink_processor`
  - `sofr_collector` (offchain source; no processor role)

### Worker model

- `scripts/run_indexer.py` is the single entrypoint for all workers.
- Each worker is protocol-isolated (`--source ... --role ...`).
- Collectors and processors run as independent loops with per-cycle error isolation.

### Security runtime defaults

- ClickHouse clients are expected to authenticate via:
  - `CLICKHOUSE_USER`
  - `CLICKHOUSE_PASSWORD`
- GraphQL CORS should be explicit-origin only via:
  - `ENVIO_CORS_ORIGINS`
- Wildcard origins are intentionally excluded in API middleware.

## 3) Protocol and Cursor Semantics

Canonical IDs are centralized in `indexer/protocols.py`:
- `AAVE_MARKET`
- `MORPHO_MARKET`
- `FLUID_MARKET`
- `CHAINLINK_PRICES`
- `SOFR_RATES`
- `MORPHO_ALLOCATION`
- `MORPHO_VAULT`

Compatibility alias retained:
- `CHAINLINK` -> `CHAINLINK_PRICES` for `processor_state` back-compat.

Cursor/state tables:
- `processor_state` (protocol -> `last_processed_block`)
- `collector_state` (protocol -> `last_collected_block`)

## 4) End-to-End Data Flow (Current)

1. **Collectors ingest raw data**
   - EVM protocols: HyperSync -> raw tables (`aave_events`, `morpho_events`, `chainlink_events`, etc.).
   - Offchain protocol (SOFR): NY Fed API -> `raw_sofr_rates`.
   - Collectors write/update `collector_state`.

2. **Processors decode and merge**
   - Read raw event ranges by block window.
   - Decode protocol-specific payloads.
   - Merge normalized rows into protocol output tables (`aave_timeseries`, `morpho_timeseries`, etc.).
   - Update `processor_state`.

3. **API pre-aggregation write-through**
   - Processors upsert latest market snapshots into `api_market_latest` (ReplacingMergeTree).
   - Incremental MVs populate `api_market_timeseries_hourly_agg` from `unified_timeseries`.
   - Incremental MVs populate `api_protocol_tvl_entity_weekly_agg` from `unified_timeseries`.
   - Morpho processor writes `morpho_vault_allocations` snapshot rows for allocation API usage.

4. **GraphQL serving**
   - Canonical prefix: `/graphql` served by `indexer/api/graphql.py`.
   - Compatibility alias: `/envio-graphql` (deprecated).
   - Snapshot-heavy endpoints read from `api_market_latest`.
   - Historical endpoints still read from base timeseries tables.

## 5) ClickHouse Storage Model (Current)

### Core serving tables

- `aave_timeseries` (ReplacingMergeTree)
- `morpho_timeseries` (ReplacingMergeTree)
- `unified_timeseries` (Merge engine union used for reads)
- `chainlink_prices` (MergeTree)
- `raw_sofr_rates` (ReplacingMergeTree)

### State and support tables

- `processor_state` (ReplacingMergeTree)
- `collector_state` (ReplacingMergeTree)
- `api_market_latest` (ReplacingMergeTree)
- `api_market_timeseries_hourly_agg` (AggregatingMergeTree, month partition, TTL)
- `api_protocol_tvl_entity_weekly_agg` (AggregatingMergeTree, month partition, TTL)
- `morpho_vault_allocations` (ReplacingMergeTree)
- `morpho_vault_meta` (ReplacingMergeTree)
- `morpho_market_params` (dimension metadata)

### Important current behavior

- Timeseries ingestion is append-first by default; mutation rewrites are optional behind `CLICKHOUSE_MUTATION_REWRITE_ENABLED`.
- Inserts are batched via `CLICKHOUSE_INSERT_BATCH_SIZE` (default `20000`).
- Async insert safeguards are supported via `CLICKHOUSE_ASYNC_INSERT` + `CLICKHOUSE_WAIT_FOR_ASYNC_INSERT`.
- `api_market_latest` is bootstrapped on API startup if empty.

## 6) Health, Liveness, and Readiness Contract

API endpoints:
- `GET /livez` -> process liveness only (cheap probe).
- `GET /healthz` -> ClickHouse reachability + lag snapshots (`collectorLag`, `processingLag`).
- `GET /readyz` -> returns `503` when configured protocol lag exceeds threshold.

Config knobs:
- `INDEXER_READY_PROTOCOLS` (default launch-critical set: Aave/Morpho/Chainlink)
- `INDEXER_MAX_READY_LAG_BLOCKS`
- ClickHouse client tuning:
  - `CLICKHOUSE_CONNECT_TIMEOUT`
  - `CLICKHOUSE_SEND_RECEIVE_TIMEOUT`
  - `CLICKHOUSE_QUERY_RETRIES`
  - `CLICKHOUSE_AUTOGENERATE_SESSION_ID`

## 7) Current Weak Points / Risks

1. **Mutation-heavy merge strategy remains in hot paths**
   - Lightweight deletes are better than `ALTER ... DELETE`, but still rewrite-heavy at scale.
2. **Historical API queries are not fully pre-aggregated**
   - Some endpoints still aggregate directly from base tables.
3. **Partition/TTL lifecycle is not formalized**
   - Retention/archival policy is not encoded in table design yet.
4. **Async insert mode not enabled**
   - Batching is implemented, but small-burst ingestion can still create part pressure.
5. **Legacy runtime overlap still possible**
   - Older ad-hoc containers (for example duplicate morpho workers) can coexist and confuse ops unless cleaned.
6. **Transition fallback still present for vault allocations**
   - GraphQL still keeps sqlite fallback path for legacy data migration safety.

## 8) Future Steps (Prioritized)

The roadmap below follows the agreed hardening direction and is mapped to ClickHouse best-practice rules.

### P0 - Eliminate hot-path rewrite patterns

- Move from delete+reinsert windows to append-only versioned writes where feasible.
- Keep latest resolution in query layer (`argMax`) or dedicated latest tables.
- Target rules:
  - `insert-mutation-avoid-delete`
  - `insert-mutation-avoid-update`

### P0 - Expand pre-aggregation for API-critical reads

- Introduce incremental MV-backed aggregates for:
  - `marketTimeseries` (hour/day buckets)
  - `protocolTvlHistory`
- Keep `api_market_latest` for latest-state serving.
- Target rules:
  - `query-mv-incremental`
  - `schema-pk-filter-on-orderby`

### P1 - Formalize table lifecycle and retention

- Define partition strategy by data lifecycle (monthly partitions for long-lived series).
- Add TTL/retention rules by table class (raw events vs serving aggregates).
- Target rules:
  - `schema-partition-lifecycle`
  - `schema-partition-low-cardinality`

### P1 - Enable async insert safeguards where needed

- Enable `async_insert=1`, `wait_for_async_insert=1` for high-frequency small batches.
- Keep explicit batch sizing in workers.
- Target rules:
  - `insert-async-small-batches`
  - `insert-batch-size`

### P2 - Complete operational cleanup

- Remove legacy/duplicate worker containers from steady-state runbooks.
- Remove sqlite fallback after ClickHouse allocation history is fully backfilled and validated.
- Standardize on one compose ownership path per service.

## 9) Definition of Done for "Production-Ready"

Architecture hardening is considered complete when:
- No frequent mutation-based delete/update in steady-state ingestion.
- API p95 for snapshot/history endpoints is served from pre-aggregated paths.
- Partition + retention policy is implemented and tested.
- Lag/readiness reflects collector+processor freshness without false positives.
- Runtime ownership is singular (no duplicate legacy workers).
- Runbook and docs remain in sync with deployed compose surfaces.
