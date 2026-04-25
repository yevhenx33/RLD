# ClickHouse Indexer Runbook

## Health Checks

- Liveness: `GET http://127.0.0.1:5000/livez`
- Health: `GET http://127.0.0.1:5000/healthz`
- Readiness: `GET http://127.0.0.1:5000/readyz`
- Full status: `GET http://127.0.0.1:5000/status`
- Prometheus metrics: `GET http://127.0.0.1:5000/metrics`

Readiness is source-specific:

- `AAVE_MARKET`: collector scanned-block lag and processor event-block lag.
- `CHAINLINK_PRICES`: collector scanned-block lag and processor event-block lag. Chainlink is sparse, so readiness must not use latest event block as latest scanned block.
- `SOFR_RATES`: business days since latest SOFR data timestamp.

## Configuration

Default config lives in `data-pipeline/config.toml`. Runtime environment variables still win over config values.

Use `RLD_INDEXER_CONFIG=/path/to/config.toml` to point workers, CLI, migrations, and API at a different config.

## Deploy

Build a tagged image and recreate services with that immutable tag:

```bash
export RLD_INDEXER_IMAGE=rld_indexer_node:$(git rev-parse --short HEAD)
docker build -t "$RLD_INDEXER_IMAGE" -f data-pipeline/Dockerfile.indexer data-pipeline
docker compose -f data-pipeline/docker-compose.yml --env-file data-pipeline/.env up -d --remove-orphans
python data-pipeline/scripts/rld_indexer.py smoke
```

## Migrations

Run migrations explicitly before serving traffic:

```bash
python data-pipeline/scripts/rld_indexer.py migrate --backfill
```

Use `--backfill` when initializing new serving tables from existing data.

Serving view operations:

```bash
python data-pipeline/scripts/rld_indexer.py views list
python data-pipeline/scripts/rld_indexer.py views rebuild
```

## Backups

Daily snapshot:

```bash
python data-pipeline/scripts/rld_indexer.py backup
```

Keep at least seven daily backups and test restore monthly on a disposable host.

## Common Incidents

- `/livez` fails: API process is down. Restart `graphql_api`, then inspect logs.
- `/healthz` fails with ClickHouse unavailable: check `rld_clickhouse`, disk space, and API `CLICKHOUSE_HOST`.
- `/readyz` fails for Aave/Chainlink: inspect `/status` and `source_status FINAL`; restart the affected worker if `last_success_at` is stale.
- `/readyz` fails for SOFR: verify NY Fed API access and latest row in `raw_sofr_rates`.
- Active parts are high: pause workers, let merges catch up, and inspect insert batch sizing.

## ClickHouse Users

Optional role separation is controlled by:

- `CLICKHOUSE_MANAGE_USERS=true`
- `CLICKHOUSE_API_USER` / `CLICKHOUSE_API_PASSWORD`
- `CLICKHOUSE_WORKER_USER` / `CLICKHOUSE_WORKER_PASSWORD`
- `CLICKHOUSE_MIGRATION_USER` / `CLICKHOUSE_MIGRATION_PASSWORD`

When these are unset, local development uses the default ClickHouse user.
