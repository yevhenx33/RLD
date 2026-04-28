# RLD Backend (Reth V2)

Python services used by the RLD simulation and monitoring stack.

## Services

| Service | Entry point | Dockerfile | Internal port | Typical host port | Launch critical |
|---|---|---|---|---|---|
| Simulation indexer | `indexers/main.py` | `docker/indexer/Dockerfile` | `8080` | `8080` | Yes |
| Rates API/indexer | `start_rates.sh` | `backend/Dockerfile.rates` | `8080` | `8081` | Yes |
| Telegram monitor bot | `services/monitor_bot.py` | `backend/Dockerfile.bot` | `8080` | `8083` | Yes |

## Simulation Indexer API Contract

Primary contract (launch baseline):
- `POST /graphql`
- `GET /healthz`
- `GET /config`

Compatibility REST endpoints (kept for older UI paths):
- `GET /api/market-info`
- `GET /api/status`
- `GET /api/events`
- `GET /api/volume`
- `GET /api/latest`
- `GET /api/price-history`

Admin/reset endpoint:
- `POST /admin/reset`
- Requests must include `X-Admin-Token` matching `INDEXER_ADMIN_TOKEN`.
- Unsafe reset bypass (`INDEXER_ALLOW_UNSAFE_ADMIN_RESET=true`) is for local-only workflows.
- `INDEXER_ADMIN_INTERNAL_ONLY=true` keeps reset limited to `INDEXER_ADMIN_ALLOWED_CLIENTS`; avoid broad private subnet allowlists.
- `INDEXER_EXPOSE_INTERNAL_ERRORS=false` suppresses raw exception leakage in API responses.

Rates/analytics API contract (served by `data-pipeline`):
- GraphQL: `POST /graphql` (canonical)
- GraphQL alias: `POST /envio-graphql` (temporary compatibility path)
- Alias responses include `Deprecation`, `Link`, `Sunset`, and `Warning` headers for rollout signaling.
- Health/liveness/readiness: `GET /healthz`, `GET /livez`, `GET /readyz`
- Oracle feed: `GET /api/v1/oracle/usdc-borrow-apy`

See `docs/api-surface.md` for the full public and internal routing map.

## Canonical Deployment Context

Backend services are launched through compose files in `docker/`:
- `docker/reth/docker-compose.reth.yml` (simulation indexer)
- `docker/docker-compose.infra.yml` (rates + monitor bot)

Recommended full launch flow:

```bash
docker network create rld_shared 2>/dev/null || true
docker compose -f docker/docker-compose.infra.yml --env-file docker/.env up -d
bash docker/reth/restart-reth.sh --fresh --with-users
docker compose -f docker/docker-compose.frontend.yml --env-file docker/.env up -d
bash docker/scripts/stack.sh ps
bash docker/scripts/stack.sh smoke
```

## Local Development

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run rates API locally:

```bash
MAINNET_RPC_URL=... PORT=8080 python3 start_rates.sh
```

Run monitor bot locally:

```bash
TELEGRAM_BOT_TOKEN=... RATES_API_BASE_URL=http://localhost:5000 python3 services/monitor_bot.py
```

## Scope Note

`data-pipeline` analytics services are intentionally out of the launch-critical path for the Reth V2 baseline.
