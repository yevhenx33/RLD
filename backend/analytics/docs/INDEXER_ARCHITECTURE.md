# Analytics Indexer Architecture Companion

> Canonical blueprint: `docs/blueprints/analytics-indexer-architecture.md`.
> Launch scope note: this analytics track is non-launch-critical for the Reth V2 baseline and is operated separately from the core simulation launch path.
> Validation methodology: `backend/analytics/docs/INDEXING_METHODOLOGY.md`.

This document is the local companion for the ClickHouse-backed rates analytics stack in `backend/analytics`. It summarizes implementation details that support the blueprint.

## Runtime Topology

The steady-state Compose owner is `backend/analytics/docker-compose.yml`, project name `rld-analytics`.

Canonical services:

- `migrate_clickhouse` / container `rld_clickhouse_migrate`: one-shot ClickHouse schema, user, backfill, and aggregate rebuild job.
- `graphql_api` / container `rld_graphql_api`: analytics GraphQL and health API on host loopback `127.0.0.1:5000`.
- `aave_worker` / container `rld_aave_worker`: combined Aave collector and processor.
- `chainlink_worker` / container `rld_chainlink_worker`: combined Chainlink collector and processor.
- `sofr_worker` / container `rld_sofr_worker`: SOFR offchain collector.

`analytics.scripts.rld_indexer` is the operator CLI and Compose entrypoint. `analytics.scripts.run_indexer` still contains the lower-level worker loop used by the CLI.

## Protocol And Cursor Semantics

Canonical protocol IDs are centralized in `analytics/protocols.py`:

- `AAVE_MARKET`
- `FLUID_MARKET`
- `CHAINLINK_PRICES`
- `SOFR_RATES`

Compatibility alias retained:

- `CHAINLINK` -> `CHAINLINK_PRICES` for `processor_state` back-compat.

Cursor/state tables:

- `processor_state` stores protocol `last_processed_block`.
- `collector_state` stores protocol `last_collected_block`.
- `source_status` stores richer collector/processor freshness snapshots.

## Data Flow

1. Collectors ingest raw data.
   - EVM protocols: HyperSync -> raw ClickHouse tables such as `aave_events` and `chainlink_events`.
   - Offchain protocol: NY Fed API -> `raw_sofr_rates`.
   - Collectors update `collector_state` and `source_status`.
2. Processors decode and merge.
   - Raw event ranges are read by bounded block windows.
   - Protocol source classes decode payloads.
   - Normalized rows are merged into protocol output tables such as `aave_timeseries`.
   - Processors update `processor_state` and `source_status`.
3. Serving paths are maintained.
   - Latest snapshots: `api_market_latest`.
   - Canonical page-series mirror: `market_timeseries`.
   - Aggregates: `api_market_timeseries_hourly_agg`, `api_protocol_tvl_entity_weekly_agg`, `api_aave_market_flow_daily_agg`, `api_chainlink_price_weekly_agg`.
4. GraphQL serves analytics page models.
   - Canonical prefix: `/graphql`.
   - Compatibility alias: `/envio-graphql` with deprecation headers.
   - Snapshot-heavy endpoints read from serving tables.
   - Historical endpoints should prefer pre-aggregated or bounded read-model paths.

## ClickHouse Storage Model

Core tables:

- `aave_timeseries` (ReplacingMergeTree)
- `unified_timeseries` (Merge engine union used for reads)
- `market_timeseries` (ReplacingMergeTree, monthly partition, TTL)
- `chainlink_prices` (MergeTree)
- `raw_sofr_rates` (ReplacingMergeTree)

State and support tables:

- `processor_state`
- `collector_state`
- `source_status`
- `api_market_latest`
- `api_market_timeseries_hourly_agg`
- `api_protocol_tvl_entity_weekly_agg`
- `api_aave_market_flow_daily_agg`
- `api_chainlink_price_weekly_agg`

Important current behavior:

- Timeseries ingestion is append-first by default.
- Mutation rewrites are optional behind `CLICKHOUSE_MUTATION_REWRITE_ENABLED`.
- Inserts are batched via `CLICKHOUSE_INSERT_BATCH_SIZE`.
- Async insert safeguards are supported through `CLICKHOUSE_ASYNC_INSERT` and `CLICKHOUSE_WAIT_FOR_ASYNC_INSERT`.
- Serving tables can be bootstrapped and rebuilt through `analytics.scripts.rld_indexer`.

## Health And Readiness

API endpoints:

- `GET /livez`: process liveness only.
- `GET /healthz`: ClickHouse reachability plus `collectorLag`, `processingLag`, and `sourceStatus`.
- `GET /readyz`: returns `503` when configured protocol lag exceeds threshold.
- `GET /status`: full source status.
- `GET /metrics`: Prometheus text metrics.

Config knobs:

- `INDEXER_READY_PROTOCOLS`
- `INDEXER_MAX_READY_LAG_BLOCKS`
- `INDEXER_MAX_READY_SOFR_BUSINESS_DAYS`
- `CLICKHOUSE_CONNECT_TIMEOUT`
- `CLICKHOUSE_SEND_RECEIVE_TIMEOUT`
- `CLICKHOUSE_QUERY_RETRIES`
- `CLICKHOUSE_AUTOGENERATE_SESSION_ID`

## Current Weak Points / Risks

1. Some historical API reads still aggregate from base tables rather than fully pre-aggregated paths.
2. Raw-event retention and archival policy is not fully formalized.
3. Legacy ad-hoc containers may still exist on hosts and should not be treated as steady-state services.
4. New protocol sources must be added with explicit protocol IDs, raw tables, source status updates, and readiness semantics.

## Production-Ready Definition

Architecture hardening is complete when:

- No frequent mutation-based delete/update is required in steady-state ingestion.
- API p95 for snapshot/history endpoints is served from pre-aggregated or bounded read-model paths.
- Partition and retention policy is documented and implemented by table class.
- Lag/readiness reflects collector and processor freshness without false positives.
- Runtime ownership is singular through `backend/analytics/docker-compose.yml`.
- This companion, the blueprint, and runbooks stay aligned with deployed service names.
