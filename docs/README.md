# RLD Docs Index

Operational docs are now aligned to a single Reth-only launch baseline.

## Core Runbooks

| Area | Document |
|---|---|
| Docker/infra operations | [`docker/README.md`](../docker/README.md) |
| Backend services + API contract | [`backend/README.md`](../backend/README.md) |
| API surface contract map | [`docs/api-surface.md`](./api-surface.md) |
| Runtime boundaries + ownership | [`docs/runtime-boundaries.md`](./runtime-boundaries.md) |
| Disaster recovery targets | [`docs/disaster-recovery.md`](./disaster-recovery.md) |
| Frontend architecture + guardrails | [`docs/frontend-architecture.md`](./frontend-architecture.md) |
| Contracts/tooling | [`contracts/README.md`](../contracts/README.md) |
| Frontend app | [`frontend/README.md`](../frontend/README.md) |
| Dashboard operations | [`docker/dashboard/README.md`](../docker/dashboard/README.md) |
| Architecture decisions (ADR) | [`docs/adr/README.md`](./adr/README.md) |

## Canonical Launch Surface

Use only these compose files for V2 launch operations:

- `docker/reth/docker-compose.reth.yml`
- `docker/docker-compose.infra.yml`
- `docker/docker-compose.frontend.yml`

Legacy compose paths are retained for compatibility but are not part of launch runbooks.

API ingress and internal endpoint exposure are defined in:

- `docs/api-surface.md`

## Quick Operations

```bash
# Start always-on infra
docker compose -f docker/docker-compose.infra.yml --env-file docker/.env up -d

# Start/rebuild simulation (Reth)
bash docker/reth/restart-reth.sh --fresh --with-users

# Start frontend
docker compose -f docker/docker-compose.frontend.yml --env-file docker/.env up -d

# Canonical stack controls
bash docker/scripts/stack.sh ps

# API contract acceptance gate
bash docker/scripts/stack.sh smoke
```

## Health Checks

```bash
curl -sf http://localhost:8080/healthz
curl -sf http://localhost:5000/livez
curl -sf http://localhost:5000/readyz
curl -sf http://localhost:8083/
curl -sf https://rld.fi/graphql
curl -sf https://rld.fi/analytics/graphql
curl -sf https://rld.fi/api/status
bash docker/scripts/stack.sh smoke --public-base https://rld.fi
```

## Scope Notes

- Reth simulation + rates + frontend + monitor bot are launch-critical.
- `data-pipeline` analytics docs remain available but are non-launch-critical for V2.
- GhostRouter/TWAP launch requires an in-house arb/route solver; do not assume external arbitrage keeps execution optimal.
