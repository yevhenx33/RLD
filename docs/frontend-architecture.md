# Frontend Architecture (SWR + Boundaries)

This document defines the frontend runtime boundaries, data-access contract, and guardrails that prevent architecture drift.

## Layer Topology

- `frontend/src/app`: application composition (`providers`, `routes`, `AppShell`, route prefetch policy).
- `frontend/src/pages`: route-level entry pages only; no reusable domain logic.
- `frontend/src/features`: feature-owned modules (for example, explore page verticals).
- `frontend/src/components/shared`: reusable UI building blocks with no feature/page coupling.
- `frontend/src/charts/primitives`: chart primitives reused by multiple surfaces.
- `frontend/src/hooks/queries`: SWR query hooks only (no UI imports).
- `frontend/src/api`: transport clients, endpoints, and query-key factories.

## Route and Shell Model

- `frontend/src/main.jsx` mounts `BrowserRouter` + `AppProviders` + `AppRoutes`.
- `frontend/src/app/routes.jsx` is the only place that maps URL paths to pages.
- `frontend/src/app/AppShell.jsx` is the single shell implementation used for both public and app routes.
- Public routes (`/`, `/intel`) and app routes keep URL compatibility with previous structure.
- Route intent prefetching is centralized in `frontend/src/app/prefetchRoutes.js` and triggered from primary navigation hover.

## Data Contract (SWR)

- Canonical endpoints are defined in `frontend/src/api/endpoints.js`.
- GraphQL transport behavior is standardized in `frontend/src/api/graphqlClient.js`.
- Deterministic SWR key factories are defined in `frontend/src/api/queryKeys.js`.
- Global retry/backoff/error behavior is configured in `frontend/src/app/providers.jsx` via `SWRConfig`.
- New query hooks should be created in `frontend/src/hooks/queries` before being consumed by pages/components.

## Reliability Conventions

- Prefer SWR refresh intervals over manual `setInterval` loops for network polling.
- Keep stale data visible on transient fetch errors.
- Use deterministic SWR keys for all server-state requests.
- Do not mix page shell error boundaries with data-fetching error states.

## Performance Conventions

- Keep entry shell dependencies minimal; large feature modules stay lazy.
- Chart and trading-heavy modules should stay outside the initial route bundle.
- Chunking strategy in `frontend/vite.config.js` aligns chunks to feature boundaries (trading, explore, strategies) and critical vendor groups.
- Guardrails:
  - `npm run check:bundle` enforces bundle-size budgets.
  - `npm run check:boundaries` enforces module-boundary import rules.
  - `npm run check:perf-smoke` enforces initial-route and key-route smoke budgets.

## Migration Checklist (Route/Module Moves)

Use this checklist whenever moving a route/component between layers:

- [ ] Preserve existing URL path behavior and parameters.
- [ ] Add or update page wrapper in `frontend/src/pages`.
- [ ] Keep old module path as a temporary shim when large import surfaces still exist.
- [ ] Ensure data fetching goes through `api/graphqlClient` + SWR key factories.
- [ ] Verify route prefetch hints for high-traffic nav targets.
- [ ] Run `npm run lint`, `npm run build`, and all guardrail checks.
