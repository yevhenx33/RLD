# ADR 0003: Docker Runtime Organization

## Status

Accepted.

## Context

The production VPS had several overlapping Docker ownership surfaces: a `docker` Compose project that mixed frontend, infra, ClickHouse, and historical services; a separate `backend/analytics` project; a `reth` simulation project; and a standalone protocol docs container. That made orphan warnings normal, hid service ownership, and allowed obsolete runtime containers to linger after deprecation.

## Decision

Use explicit Compose project names, stable container names, and one service ownership table.

Canonical project names:

- `rld-clickhouse`
- `rld-analytics`
- `rld-infra`
- `rld-reth`
- `rld-frontend`
- `rld-docs`

`docker/scripts/stack.sh` is the canonical steady-state control surface. It owns stack ordering and includes docs as a first-class runtime service. `backend/analytics` owns `rld_graphql_api`; infra no longer defines a duplicate GraphQL API service.

## Consequences

- Dashboard readiness includes protocol docs as its own stack.
- Ownership labels are applied in Compose for operational discovery.
- App-built images run as non-root where practical.
- ClickHouse has a real healthcheck.
- Historical Reth volume names are preserved to avoid accidental state loss during project renaming.
- Morpho runtime cleanup is deliberately deferred.

## Service Map

The current source of truth is `docker/SERVICES.md`.
