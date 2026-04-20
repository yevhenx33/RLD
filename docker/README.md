# RLD Docker Runbook (Reth V2)

Single operational truth for launch-grade backend/infra orchestration.

## Canonical Compose Set

Use only:

1. `docker/reth/docker-compose.reth.yml` - simulation runtime (reth, postgres, indexer, bots, faucet)
2. `docker/docker-compose.infra.yml` - always-on infra (rates-indexer, monitor-bot)
3. `docker/docker-compose.frontend.yml` - frontend nginx container

## Launch-Critical Services

| Service | Compose file | Host port |
|---|---|---|
| Frontend | `docker-compose.frontend.yml` | `3000` |
| Simulation indexer | `reth/docker-compose.reth.yml` | `8080` |
| Rates API | `docker-compose.infra.yml` | `8081` |
| Monitor bot | `docker-compose.infra.yml` | `8083` |
| Reth RPC | `reth/docker-compose.reth.yml` | `8545` |
| Faucet | `reth/docker-compose.reth.yml` | `8088` |

All launch services share `rld_shared`.

## First-Time Setup

```bash
docker network create rld_shared 2>/dev/null || true
docker compose -f docker/docker-compose.infra.yml --env-file docker/.env up -d
bash docker/reth/restart-reth.sh --fresh --with-users
docker compose -f docker/docker-compose.frontend.yml --env-file docker/.env up -d
```

## Daily Ops

```bash
# Fast simulation recycle from snapshot
bash docker/reth/restart-reth.sh --from-snapshot --with-users

# Infra status
docker compose -f docker/docker-compose.infra.yml --env-file docker/.env ps

# Simulation status
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env ps
```

## Frontend + Edge Routing

- Host nginx config: `docker/nginx/rld-frontend.conf`
- `https://rld.fi/` proxies to `127.0.0.1:3000`
- `https://rld.fi/dashboard/` proxies to `127.0.0.1:8090`
- Frontend container proxy contract:
  - `/graphql` -> indexer
  - `/api/rates` and `/rates-graphql` -> rates-indexer
  - `/api/faucet` -> host faucet service
  - only explicit simulation compatibility `/api/*` routes are allowed

## Automation and Observability

Canonical user cron entries:

```cron
* * * * * /home/ubuntu/RLD/docker/scripts/generate-status.sh >> /home/ubuntu/RLD/logs/status-gen.log 2>&1
0 * * * * /home/ubuntu/RLD/docker/scripts/collect-logs.sh >> /home/ubuntu/RLD/logs/cron.log 2>&1
0 3 * * * /home/ubuntu/RLD/docker/scripts/backup-databases.sh >> /home/ubuntu/RLD/logs/backup-cron.log 2>&1
```

Notes:
- `anvil-rotate.sh` is legacy and intentionally skipped when Reth runtime is detected.
- `generate-status.sh` now reports whether status/backup cron jobs are scheduled.

## Legacy/Deprecated Compose Files

Kept for compatibility only; not part of launch runbooks:

- `docker/docker-compose.yml` (legacy Anvil path, used internally by genesis bootstrap)
- `docker/docker-compose.rates.yml` (legacy analytics path)
- `docker/docker-compose.bot.yml` (bot-only compatibility path)
- `frontend/docker-compose.yml` (legacy frontend compose)
