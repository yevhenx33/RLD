# ADR 0001: Canonical Runtime Surfaces

- Status: Accepted
- Date: 2026-04-22

## Context

RLD operations had multiple compose entry points and overlapping deployment paths. This caused ambiguity about which runtime was canonical and increased operational drift risk.

## Decision

Adopt exactly three canonical compose surfaces for steady-state runtime operations:

1. `docker/reth/docker-compose.reth.yml` (simulation runtime)
2. `docker/docker-compose.infra.yml` (analytics + monitor)
3. `docker/docker-compose.frontend.yml` (frontend container)

Introduce `docker/scripts/stack.sh` as the canonical wrapper for start/stop/status/logs against those surfaces.

Legacy compose files remain compatibility artifacts and are excluded from primary runbooks.

## Consequences

- Operations teams use one documented path for lifecycle actions.
- Deployment automation is aligned to canonical files.
- Drift detection becomes simpler because expected runtime surfaces are fixed.
