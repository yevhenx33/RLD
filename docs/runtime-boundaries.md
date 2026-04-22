# RLD Runtime Boundaries Inventory

This document defines the canonical runtime component inventory for production operations, including ownership, trust boundaries, and service-level intent.

## Ownership Model

| Domain | Primary owner | Responsibility |
| --- | --- | --- |
| Edge + web delivery | Platform/Ops | Host nginx, TLS termination, frontend container lifecycle |
| Simulation runtime | Protocol backend | Reth, simulation indexer, simulation PostgreSQL, faucet, execution daemons |
| Analytics runtime | Data platform | ClickHouse, protocol collectors/processors, Envio GraphQL API |
| Monitoring + incident response | Platform/Ops | Health probes, dashboard status pipelines, alerting hooks, backup/restore |

## Runtime Components

| Layer | Component | Entry point | Network exposure | Data sensitivity | Owner |
| --- | --- | --- | --- | --- | --- |
| Edge | Host nginx reverse proxy | `docker/nginx/rld-frontend.conf` | Public HTTPS | Medium | Platform/Ops |
| UI | Frontend nginx + static assets | `docker/docker-compose.frontend.yml` + `frontend/nginx.conf` | Loopback (`127.0.0.1:3000`) | Low-Medium | Platform/Ops |
| Simulation | Reth node | `docker/reth/docker-compose.reth.yml` | Loopback (`127.0.0.1:8545`) | Medium | Protocol backend |
| Simulation | Indexer API (GraphQL + REST) | `backend/indexers/main.py`, `backend/indexers/api/graphql.py` | Loopback (`127.0.0.1:8080`) | High | Protocol backend |
| Simulation | PostgreSQL state DB | `docker/reth/docker-compose.reth.yml` | Loopback (`127.0.0.1:5432`) | High | Protocol backend |
| Simulation | Faucet service | `docker/reth/Dockerfile.faucet` | Loopback (`127.0.0.1:8088`) | High | Protocol backend |
| Simulation | MM/chaos daemons | `backend/services/combined_daemon.py`, `backend/tools/chaos_daemon.py` | Internal Docker network | High | Protocol backend |
| Analytics | ClickHouse | `docker/docker-compose.clickhouse.yml` | Internal + loopback only | High | Data platform |
| Analytics | Collectors/processors | `data-pipeline/scripts/run_indexer.py` | Internal/host-local | High | Data platform |
| Analytics | Envio GraphQL API | `data-pipeline/indexer/api/graphql.py` | Loopback (`127.0.0.1:5000`) | Medium-High | Data platform |
| Ops | Monitor bot | `backend/services/monitor_bot.py` | Loopback (`127.0.0.1:8083`) | Medium | Platform/Ops |
| Ops | Dashboard status service | `docker/dashboard/live_status_server.py` | Loopback (`127.0.0.1:8091`) | Low-Medium | Platform/Ops |

## Trust Boundaries

1. **Internet -> Edge boundary**
   - Boundary control: Cloudflare + host nginx.
   - Security objective: only explicitly routed paths can reach internal services.
2. **Edge -> Internal app boundary**
   - Boundary control: frontend nginx path allowlist and upstream proxy policy.
   - Security objective: deny-by-default for non-whitelisted API paths.
3. **Simulation -> Analytics boundary**
   - Boundary control: dedicated interfaces and explicit API contracts (`/graphql`, `/envio-graphql`).
   - Security objective: avoid control-plane coupling and isolate freshness incidents.
4. **Control plane -> Data plane boundary**
   - Boundary control: admin tokens, scoped environment variables, container-level access.
   - Security objective: no unauthenticated destructive operations.
5. **Ops -> Runtime boundary**
   - Boundary control: health/readiness checks, restart scripts, backup jobs.
   - Security objective: bounded blast radius with deterministic recovery paths.

## Critical Service-Level Intent

| Domain | SLI | Intent |
| --- | --- | --- |
| Simulation indexer | `GET /healthz` availability | Keep simulation state readable for frontend and daemons |
| Analytics GraphQL | `GET /readyz` lag budget | Preserve freshness for rates and protocol analytics |
| Edge frontend | Root route + proxy path availability | Preserve user-facing routing and API reachability |
| Chain state | Reth block progression and txpool health | Keep market state and execution loops live |
| Recovery posture | Backup success + restore validation | Ensure recoverability for Postgres, ClickHouse, and chain state |

## Canonical Deployment Surfaces

- Simulation stack: `docker/reth/docker-compose.reth.yml`
- Analytics/infra stack: `docker/docker-compose.infra.yml`
- Frontend stack: `docker/docker-compose.frontend.yml`
- Node/database backup and status scripts: `docker/scripts/`

Legacy compose files are compatibility artifacts and not the source of truth for steady-state operations.
