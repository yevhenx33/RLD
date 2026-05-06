# Analytics Indexer Architecture Blueprint

This blueprint defines the architecture, invariants, and review standard for the ClickHouse-backed rates analytics stack under `backend/analytics`. Treat it as the source of truth for future work on protocol rates ingestion, analytics GraphQL page models, ClickHouse serving tables, and analytics readiness.

## Scope

This blueprint covers:

- `backend/analytics`
- `backend/analytics/docker-compose.yml`
- ClickHouse rates tables and serving materialized views
- Analytics GraphQL on host loopback port `5000`
- Protocol workers for Aave, Chainlink, SOFR, and future rates sources

It does not cover the simulation indexer, Reth runtime manifest, trading execution, or frontend route gating except where those systems consume analytics page models. The simulation indexer remains the source of truth for runtime execution config via `GET /api/runtime-manifest`.

## Runtime Topology

The steady-state analytics stack is the `rld-analytics` Compose project in `backend/analytics/docker-compose.yml`.

Canonical services:

- `migrate_clickhouse` / container `rld_clickhouse_migrate`: one-shot schema, user, backfill, and aggregate rebuild job.
- `graphql_api` / container `rld_graphql_api`: analytics FastAPI/Strawberry API, exposed only on `127.0.0.1:5000`.
- `aave_worker` / container `rld_aave_worker`: combined Aave collector and processor loop.
- `chainlink_worker` / container `rld_chainlink_worker`: combined Chainlink collector and processor loop.
- `sofr_worker` / container `rld_sofr_worker`: SOFR offchain collector loop.

All services use the shared `rld_shared` Docker network and reach ClickHouse internally as `rld_clickhouse:8123`. ClickHouse host ports must remain loopback-bound according to `docs/blueprints/operational-security.md`.

## Worker Model

`analytics.scripts.rld_indexer` is the operator CLI and Compose entrypoint.

Important commands:

- `python -m analytics.scripts.rld_indexer migrate --backfill --rebuild-views`
- `python -m analytics.scripts.rld_indexer worker --source AAVE_MARKET`
- `python -m analytics.scripts.rld_indexer worker --source CHAINLINK_PRICES`
- `python -m analytics.scripts.rld_indexer worker --source SOFR_RATES`
- `python -m analytics.scripts.rld_indexer status`
- `python -m analytics.scripts.rld_indexer smoke`
- `python -m analytics.scripts.rld_indexer views list|rebuild`

`analytics.scripts.run_indexer` still owns the lower-level worker loop implementation. New operational docs should reference `rld_indexer` first and only mention `run_indexer` as an internal helper.

Each worker is protocol-isolated by `--source`. For EVM protocols, the `worker` role runs collection and processing in one process cycle. For offchain sources such as SOFR, the same command runs only the collector path because there is no raw EVM processor role.

## Data Flow

Analytics data moves through four layers:

1. Collectors ingest raw source data.
   - EVM sources read HyperSync and write raw ClickHouse event tables such as `aave_events` and `chainlink_events`.
   - Offchain sources read external APIs and write source tables such as `raw_sofr_rates`.
   - Collectors update `collector_state` and `source_status`.
2. Processors decode and normalize.
   - Processors read bounded raw ranges by block or source cursor.
   - Protocol source classes decode payloads and merge normalized rows into protocol output tables such as `aave_timeseries`.
   - Processors update `processor_state` and `source_status`.
3. Serving tables and materialized views shape API reads.
   - Latest market state is written to `api_market_latest`.
   - Long-lived normalized page data is mirrored into `market_timeseries`.
   - Aggregate tables include `api_market_timeseries_hourly_agg`, `api_protocol_tvl_entity_weekly_agg`, `api_aave_market_flow_daily_agg`, and `api_chainlink_price_weekly_agg`.
4. GraphQL serves page models.
   - Canonical endpoint: `GET/POST /graphql`.
   - Deprecated compatibility alias: `/envio-graphql`.
   - Page-level analytics resolvers should read serving tables or bounded read models, not scan raw event tables at request time.

## API And Readiness Contract

The analytics API runs on host loopback `127.0.0.1:5000`.

Endpoints:

- `GET /livez`: process liveness only.
- `GET /healthz`: ClickHouse reachability plus lag and source snapshots.
- `GET /readyz`: readiness gate; returns `503` when configured protocol lag exceeds thresholds.
- `GET /status`: detailed source status.
- `GET /metrics`: Prometheus text metrics.
- `/graphql`: canonical analytics GraphQL.
- `/envio-graphql`: deprecated GraphQL alias with deprecation headers.

Readiness defaults are controlled by:

- `INDEXER_READY_PROTOCOLS`
- `INDEXER_MAX_READY_LAG_BLOCKS`
- `INDEXER_MAX_READY_SOFR_BUSINESS_DAYS`

Launch-critical frontend execution gates must not depend on analytics readiness. Analytics can be degraded while the Reth simulation stack remains ready.

## Security And Operations

Runtime defaults:

- API CORS must use explicit origins from `ENVIO_CORS_ORIGINS`; wildcard origins are intentionally filtered out.
- ClickHouse clients must authenticate with `CLICKHOUSE_USER` and `CLICKHOUSE_PASSWORD` or the role-specific API, worker, and migration credentials.
- Worker inserts should keep bounded batching through `CLICKHOUSE_INSERT_BATCH_SIZE`.
- Async insert safeguards are expected for workers through `CLICKHOUSE_ASYNC_INSERT` and `CLICKHOUSE_WAIT_FOR_ASYNC_INSERT`.
- Public clients must not receive raw exception strings.

Operational ownership:

- `docker/scripts/stack.sh` starts ClickHouse before analytics.
- `backend/analytics/docker-compose.yml` is the single steady-state Compose owner for analytics services.
- Legacy ad-hoc analytics containers should not be treated as part of steady state.

## Relationship To Other Blueprints

- `frontend-architecture.md` defines how frontend analytics pages consume page-level GraphQL payloads.
- `frontend-indexer-core-ops.md` defines simulation runtime manifest requirements and frontend execution gates.
- `operational-security.md` defines host binding, secrets, CORS, and admin-surface constraints.
