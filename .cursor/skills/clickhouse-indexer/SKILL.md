---
name: clickhouse-indexer
description: Work with the RLD ClickHouse analytics indexer. Use when modifying, debugging, deploying, hardening, or explaining the data-pipeline indexer, GraphQL rates API, ClickHouse tables, readiness, workers, source_status, market_timeseries, migrations, or operational runbooks.
---

# RLD ClickHouse Indexer

## Scope

Use this skill for the ClickHouse analytics stack only:

- `data-pipeline/indexer/`
- `data-pipeline/scripts/`
- `data-pipeline/docker-compose.yml`
- `docker/docker-compose.infra.yml` only for `rld_graphql_api`
- ClickHouse tables used by the rates API

Do not mix this with the simulation indexer under `backend/indexers/` unless the user explicitly asks.

## Architecture

Current single-VPS runtime:

```text
HyperSync/RPC -> aave_worker       -> aave_events      -> aave_timeseries + market_timeseries
HyperSync/RPC -> chainlink_worker  -> chainlink_events -> chainlink_prices
NY Fed API    -> sofr_worker       -> raw_sofr_rates

workers -> source_status
market_timeseries + serving aggregates + source_status -> read-only GraphQL API
```

Critical sources:

- `AAVE_MARKET`
- `CHAINLINK_PRICES`
- `SOFR_RATES`

Core tables:

- `source_status`: canonical readiness/freshness state.
- `market_timeseries`: canonical serving timeseries, ordered by `(protocol, entity_id, timestamp)`.
- `api_market_latest`
- `api_market_timeseries_hourly_agg`
- `api_protocol_tvl_entity_weekly_agg`
- `api_chainlink_price_weekly_agg`

Legacy compatibility tables may still exist, but new readiness should prefer `source_status`.

## Important Files

- `data-pipeline/config.toml`: default source/API/ClickHouse config.
- `data-pipeline/indexer/config.py`: config loader, maps config to environment defaults.
- `data-pipeline/indexer/state.py`: `source_status` helpers.
- `data-pipeline/indexer/schema.py`: ClickHouse DDL, serving view definitions, view rebuild helpers.
- `data-pipeline/indexer/api/graphql.py`: read-only GraphQL/API serving.
- `data-pipeline/scripts/rld_indexer.py`: unified operator CLI.
- `data-pipeline/scripts/run_indexer.py`: worker entrypoint.
- `data-pipeline/scripts/smoke_clickhouse_indexer.py`: smoke test.
- `data-pipeline/docs/CLICKHOUSE_INDEXER_RUNBOOK.md`: operator runbook.

## Operating Commands

Build immutable image:

```bash
export RLD_INDEXER_IMAGE=rld_indexer_node:$(git rev-parse --short HEAD)
docker build -t "$RLD_INDEXER_IMAGE" -f data-pipeline/Dockerfile.indexer data-pipeline
```

Deploy:

```bash
docker compose -f data-pipeline/docker-compose.yml --env-file data-pipeline/.env up -d --remove-orphans
```

Run migration:

```bash
python data-pipeline/scripts/rld_indexer.py migrate --backfill
```

Run smoke test:

```bash
python data-pipeline/scripts/rld_indexer.py smoke
```

Check status:

```bash
python data-pipeline/scripts/rld_indexer.py status --json
```

List/rebuild serving views:

```bash
python data-pipeline/scripts/rld_indexer.py views list
python data-pipeline/scripts/rld_indexer.py views rebuild
```

Backup:

```bash
python data-pipeline/scripts/rld_indexer.py backup
```

## Health Contract

- `/livez`: cheap process liveness, safe for Docker healthcheck.
- `/healthz`: ClickHouse reachability plus freshness details.
- `/readyz`: readiness gate for `AAVE_MARKET`, `CHAINLINK_PRICES`, `SOFR_RATES`.
- `/status`: compact dashboard/alert payload.
- `/metrics`: Prometheus text exposition.

Readiness semantics:

- Aave: collector scanned-block lag and processor block lag.
- Chainlink: collector scanned-block lag and processor block lag. Do not use latest event block as latest scanned block; Chainlink is sparse.
- SOFR: business days since latest `raw_sofr_rates.timestamp`.

## Coding Rules

- Keep API startup read-only. Do not add DDL/backfill to `get_clickhouse_client()` or request handlers.
- Put schema changes in `data-pipeline/indexer/schema.py` and apply via `rld_indexer.py migrate`.
- Workers may write raw/source/serving tables. API should read only.
- Update `source_status` for every successful collector/processor cycle.
- Use `market_timeseries` for new API timeseries reads unless a specialized table is clearly required.
- Avoid hot-path `ALTER TABLE DELETE`. Prefer append/idempotent writes and `ReplacingMergeTree`/aggregate rebuild commands.
- Keep Docker healthchecks on `/livez`, not `/readyz`, to avoid restart loops during upstream data stalls.
- After changes, run syntax checks, YAML validation, lints when relevant, and `rld_indexer.py smoke`.

## Common Investigations

Chainlink shows huge lag:

1. Check `source_status FINAL` for `CHAINLINK_PRICES`.
2. Verify `last_scanned_block` advances even when no price events are emitted.
3. Check `/readyz`; lag should be `source_head_block - last_scanned_block`.

SOFR not ready:

1. Check latest `raw_sofr_rates.timestamp`.
2. Check business-day lag in `/status`.
3. Verify NY Fed API access from the VPS.

API cannot reach ClickHouse:

1. Check `CLICKHOUSE_HOST` in `rld_graphql_api`.
2. For bridge-networked API, prefer `host.docker.internal` plus `extra_hosts`.
3. Workers use host networking and can use `127.0.0.1`.

Runtime drift:

1. Inspect image IDs and `INDEXER_VERSION`.
2. Rebuild with immutable tag.
3. Recreate with `--remove-orphans`.
4. Run smoke test.
