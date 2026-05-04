# RLD Docker Services

This is the ownership map for the canonical RLD Docker runtime. Compose files use explicit project names and stable container names so `docker compose ls`, `docker ps`, dashboard snapshots, and runbooks describe the same topology.

## Canonical Stacks

| Stack | Compose file | Project name | Owner | Purpose |
| --- | --- | --- | --- | --- |
| ClickHouse | `docker/docker-compose.clickhouse.yml` | `rld-clickhouse` | Data Platform | Persistent analytics database |
| Analytics | `backend/analytics/docker-compose.yml` | `rld-analytics` | Data Platform | Rates analytics API and workers |
| Infra | `docker/docker-compose.infra.yml` | `rld-infra` | Protocol Ops | Monitor bot and runtime alerts |
| Simulation | `docker/reth/docker-compose.reth.yml` | `rld-reth` | Protocol Backend | Reth dev node, simulation indexer, execution bots, faucet |
| Frontend | `docker/docker-compose.frontend.yml` | `rld-frontend` | Frontend | SPA nginx edge container and API proxy routes |
| Docs | `docker/docker-compose.docs.yml` | `rld-docs` | Docs | Protocol documentation site |

`docker/scripts/stack.sh` is the canonical steady-state control surface and starts stacks in dependency order: ClickHouse, analytics, infra, simulation, frontend, docs.

## Service Ownership

| Container | Service | Owner | Persistence | Public host port | Health contract |
| --- | --- | --- | --- | --- | --- |
| `rld_clickhouse` | ClickHouse | Data Platform | `/mnt/data/clickhouse` bind mount | `127.0.0.1:8123`, `127.0.0.1:9000` | `clickhouse-client SELECT 1` |
| `rld_graphql_api` | Rates analytics GraphQL API | Data Platform | ClickHouse | `127.0.0.1:5000` | `GET /livez` |
| `rld_aave_worker` | Aave rates worker | Data Platform | ClickHouse | none | worker command healthcheck |
| `rld_chainlink_worker` | Chainlink prices worker | Data Platform | ClickHouse | none | worker command healthcheck |
| `rld_sofr_worker` | SOFR worker | Data Platform | ClickHouse | none | worker command healthcheck |
| `rld-monitor-bot` | Telegram monitor bot | Protocol Ops | `rld_monitor_bot_data` volume | `127.0.0.1:8083` | `GET /` |
| `rld-reth` | Reth dev node | Protocol Backend | `reth_reth-datadir` volume | `127.0.0.1:8545` | TCP check on `8545` |
| `rld-postgres` | Simulation Postgres | Protocol Backend | `reth_postgres-data-reth` volume | `127.0.0.1:5432` | `pg_isready` |
| `rld-indexer` | Simulation indexer API | Protocol Backend | Postgres | `127.0.0.1:8080` | `GET /healthz` |
| `rld-mm-daemon` | Market-maker daemon | Protocol Backend | none | none | simulation indexer health probe |
| `rld-chaos-trader` | Chaos trader | Protocol Backend | none | none | process command healthcheck |
| `rld-arb-bot` | Cross-market arb bot | Protocol Backend | `/tmp` status file | none | status-file freshness check |
| `rld-faucet` | Simulation faucet | Protocol Backend | none | `127.0.0.1:8088` | `GET /health` |
| `rld-frontend` | Frontend nginx | Frontend | built static assets bind-mounted read-only | `127.0.0.1:3000` | `GET /` |
| `rld-docs` | Protocol docs | Docs | none | `127.0.0.1:3001` | `GET /` |

## Boundaries

- Only loopback ports should be published by Docker. Public ingress stays in host nginx and Cloudflare.
- `rld_shared` is the single shared Docker network for cross-stack DNS.
- ClickHouse and Postgres data live outside container lifecycles and must not be pruned with containers/images.
- Morpho containers are intentionally excluded from this map until the Morpho runtime decision is made.

## Cleanup Policy

Safe routine cleanup:

```bash
docker builder prune -af
docker image prune -f
```

Do not run `docker volume prune` on this host unless every named volume has been audited against the service table above.
