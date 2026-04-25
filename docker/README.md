# RLD Docker Runbook (Reth V2)

Single operational truth for launch-grade backend/infra orchestration.

API ingress contract and internal exposure boundaries are defined in:
- `docs/api-surface.md`

## Canonical Compose Set

Use only:

1. `docker/reth/docker-compose.reth.yml` - simulation runtime (reth, postgres, indexer, bots, faucet)
2. `docker/docker-compose.infra.yml` - always-on infra (Envio GraphQL + monitor-bot)
3. `docker/docker-compose.frontend.yml` - frontend nginx container

## Launch-Critical Services

| Service | Compose file | Host port |
|---|---|---|
| Frontend | `docker-compose.frontend.yml` | `3000` |
| Simulation indexer | `reth/docker-compose.reth.yml` | `8080` |
| Envio GraphQL API | `docker-compose.infra.yml` | `5000` |
| Monitor bot | `docker-compose.infra.yml` | `8083` |
| Reth RPC | `reth/docker-compose.reth.yml` | `8545` |
| Faucet | `reth/docker-compose.reth.yml` | `8088` |

All launch services share `rld_shared`.

## Indexer Architecture Reference

For the current rates/indexer architecture and planned hardening roadmap, see:

- `data-pipeline/docs/INDEXER_ARCHITECTURE.md`
- `data-pipeline/docs/AAVE_INDEXER.md`

### Envio Health Contract

- Liveness: `GET /livez` (cheap process-level probe, used by Docker healthcheck).
- Service health: `GET /healthz` (includes ClickHouse reachability + both `collectorLag` and `processingLag` snapshots).
- Readiness: `GET /readyz` (returns `503` when either collector or processor lag exceeds threshold; does not imply process crash).
- Operational meaning:
  - `unhealthy` container -> restart/service-level incident.
  - `readyz=503` with healthy liveness -> data freshness incident (collector/processor lag), not API outage.

## First-Time Setup

```bash
docker network create rld_shared 2>/dev/null || true
docker compose -f docker/docker-compose.infra.yml --env-file docker/.env up -d
bash docker/reth/restart-reth.sh --fresh --with-users
docker compose -f docker/docker-compose.frontend.yml --env-file docker/.env up -d
# equivalent steady-state control after bootstrap:
bash docker/scripts/stack.sh ps
```

## Required Security Environment

Before first launch, ensure `docker/.env` defines strong non-default values:

- `DB_PASSWORD`
- `INDEXER_ADMIN_TOKEN`
- `CLICKHOUSE_PASSWORD`
- `INDEXER_CORS_ORIGINS` and `ENVIO_CORS_ORIGINS` (explicit origins only)
- `RETH_HTTP_CORS_DOMAIN` (explicit browser origins)

Production notes:

- `INDEXER_ADMIN_TOKEN` is required by compose and enforced by the simulation indexer.
- `INDEXER_ALLOW_UNSAFE_ADMIN_RESET` should remain `false` outside local development.
- ClickHouse is expected to run with authentication enabled.

## Daily Ops

```bash
# Fast simulation recycle from snapshot
bash docker/reth/restart-reth.sh --from-snapshot --with-users

# Canonical runtime status
bash docker/scripts/stack.sh ps
```

Canonical stack control commands:

```bash
bash docker/scripts/stack.sh up
bash docker/scripts/stack.sh down
bash docker/scripts/stack.sh restart
bash docker/scripts/stack.sh logs indexer
bash docker/scripts/stack.sh smoke
```

Acceptance gate notes:
- `stack.sh smoke` validates internal simulation/analytics/faucet contracts.
- `stack.sh smoke --public-base https://rld.fi` additionally validates edge routing contract and control-plane deny-list behavior.
- During analytics alias migration, smoke checks enforce `Deprecation`/`Sunset`/`Warning` headers on `/envio-graphql`.

## Frontend + Edge Routing

- Host nginx config: `docker/nginx/rld-frontend.conf`
- `https://rld.fi/` proxies to `127.0.0.1:3000`
- `https://rld.fi/dashboard/` proxies to `127.0.0.1:8090`
- Dashboard real-time API is provided by `docker/dashboard/live_status_server.py` on `127.0.0.1:8091` and proxied through `/dashboard/live-status*`
- Optional systemd unit for live API: `docker/dashboard/rld-dashboard-live.service`
- Frontend container proxy contract:
  - `/graphql` -> indexer
  - `/analytics/graphql` -> Envio GraphQL API (canonical)
  - `/envio-graphql` -> Envio GraphQL API (compatibility alias)
  - `/api/faucet` -> host faucet service
  - only explicit simulation compatibility `/api/*` routes are allowed
  - compatibility routes include `/api/price-history` for dashboard charts

## Automation and Observability

Canonical user cron entries:

```cron
* * * * * /home/ubuntu/RLD/docker/scripts/generate-status.sh >> /home/ubuntu/RLD/logs/status-gen.log 2>&1
0 * * * * /home/ubuntu/RLD/docker/scripts/collect-logs.sh >> /home/ubuntu/RLD/logs/cron.log 2>&1
0 3 * * * /home/ubuntu/RLD/docker/scripts/backup-databases.sh >> /home/ubuntu/RLD/logs/backup-cron.log 2>&1
30 3 * * * bash /home/ubuntu/RLD/docker/scripts/validate-backup-restore.sh >> /home/ubuntu/RLD/logs/restore-check.log 2>&1
*/5 * * * * /usr/bin/env python3 /home/ubuntu/RLD/docker/scripts/emit-alerts.py >> /home/ubuntu/RLD/logs/alerts.log 2>&1
```

Notes:
- `anvil-rotate.sh` is legacy and intentionally skipped when Reth runtime is detected.
- `generate-status.sh` now reports whether status/backup cron jobs are scheduled.
- `validate-backup-restore.sh` performs a non-destructive restore drill on latest backup.
- `emit-alerts.py` emits change-based alerts for critical/degraded stack states and recovery.

## Legacy/Deprecated Compose Files

Kept for compatibility only; not part of launch runbooks:

- `docker/docker-compose.yml` (legacy Anvil path, used internally by genesis bootstrap)
- `docker/docker-compose.rates.yml` (legacy analytics path)
- `docker/docker-compose.bot.yml` (bot-only compatibility path)
- `frontend/docker-compose.yml` (legacy frontend compose)
