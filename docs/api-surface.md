# API Surface Contract

Single source of truth for API routing across edge, frontend nginx, dashboard nginx, and backend services.

## Canonical API Planes

| Plane | Service owner | Internal bind | Contract purpose |
|---|---|---|---|
| Simulation API | `backend/indexers/api/graphql.py` | `indexer:8080` | Simulation state, GraphQL, compatibility REST, control plane |
| Analytics/Rates API | `data-pipeline/indexer/api/graphql.py` | `rld_graphql_api:5000` | Historical analytics, rates GraphQL, readiness, oracle rate feed |
| Faucet API | `docker/reth/faucet_server.py` | `faucet:8088` | Funding endpoint for simulation wallets |

## Public Edge Contract (`https://rld.fi`)

These routes are public and stable for frontend and dashboard clients.

| Public path | Upstream service/path | Auth mode | Notes |
|---|---|---|---|
| `/` | `frontend:80` static SPA | none | Primary app shell |
| `/dashboard/` | `127.0.0.1:8090` | none | Internal dashboard UI exposed via edge |
| `/graphql` | `indexer:8080/graphql` | none | Simulation GraphQL |
| `/api/market-info` | `indexer:8080/api/market-info` | none | Compatibility REST |
| `/api/status` | `indexer:8080/api/status` | none | Compatibility REST |
| `/api/events` | `indexer:8080/api/events` | none | Compatibility REST |
| `/api/volume` | `indexer:8080/api/volume` | none | Compatibility REST |
| `/api/latest` | `indexer:8080/api/latest` | none | Compatibility REST |
| `/api/price-history` | `indexer:8080/api/price-history` | none | Required by dashboard finance charts |
| `/analytics/graphql` | `rld_graphql_api:5000/graphql` | none | Canonical analytics GraphQL ingress |
| `/envio-graphql` | `rld_graphql_api:5000/graphql` | none | Temporary compatibility alias |
| `/api/faucet` | `faucet:8088/faucet` | none (rate-limited) | Public faucet ingress |
| `/rpc` | `reth:8545/` | none | Public RPC proxy used by browser client |

Any other `/api/*` path at edge is intentionally denied by frontend nginx.

## Internal-Only Contract (Not Edge-Exposed)

| Internal path | Service/path | Auth mode | Exposure policy |
|---|---|---|---|
| `POST /admin/reset` | `indexer:8080/admin/reset` | `X-Admin-Token` required | internal/deployer only |
| `GET /config` | `indexer:8080/config` | none | internal daemons/deployer only |
| `GET /healthz` | `indexer:8080/healthz` | none | internal health |
| `GET /livez` | `rld_graphql_api:5000/livez` | none | internal liveness |
| `GET /readyz` | `rld_graphql_api:5000/readyz` | none | internal freshness readiness |
| `GET /healthz` | `rld_graphql_api:5000/healthz` | none | internal deep health |
| `GET /api/v1/oracle/usdc-borrow-apy` | `rld_graphql_api:5000/api/v1/oracle/usdc-borrow-apy` | none | internal rate consumers |
| `GET /health` | `faucet:8088/health` | none | internal health probe |

## Dashboard Runtime Notes

- When served behind `/dashboard/`, dashboard pages use root-absolute fetch paths (`/api/*`, `/graphql`), so they depend on the **public edge contract**, not only dashboard nginx local proxy rules.
- Route drift between dashboard expectations and frontend nginx allowlist is treated as a production bug.

## Deprecation Window

- Canonical analytics GraphQL prefix is `/graphql` on the analytics service.
- Canonical public ingress for analytics GraphQL is `/analytics/graphql`.
- Public alias `/envio-graphql` remains supported temporarily for compatibility.
- Consumers should migrate to canonical prefix usage through configured endpoint constants; alias removal requires coordinated frontend rollout.
- Alias calls emit `Deprecation`, `Link`, `Sunset`, and `Warning` headers to signal migration and removal horizon.

### Rollout Phases

1. **Compatibility phase (current):** keep alias enabled, emit deprecation headers, and run post-deploy smoke checks.
2. **Migration phase:** frontend and scripts use only canonical `/analytics/graphql` ingress.
3. **Removal phase:** remove `/envio-graphql` ingress + backend alias once smoke checks prove zero alias dependence.

## Production Acceptance Gates

Run these checks after each deploy and before closing rollout work:

```bash
# Internal contracts (simulation + analytics + faucet)
bash docker/scripts/stack.sh smoke

# Public edge contracts and deny-list checks
bash docker/scripts/stack.sh smoke --public-base https://rld.fi
```

Required pass criteria:

- All smoke checks pass (`exit code 0`).
- Public control-plane routes (`/healthz`, `/admin/reset`) stay blocked with `404`.
- Canonical analytics GraphQL route is healthy and alias route still emits deprecation headers during migration.
