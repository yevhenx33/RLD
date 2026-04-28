# Frontend Architecture Blueprint

This blueprint defines the RLD frontend architecture standard for future AI agents. The goal is strict role separation, minimal client work, and page rendering that is fast because the backend owns aggregation.

## North Star

Frontend pages must render backend-produced page models.

```text
data-pipeline indexers -> ClickHouse pre-aggregates -> page-level GraphQL -> one page request -> render
```

The frontend should not understand finance data deeply. It should not join raw series, compute weighted APYs, forward-fill histories, classify markets, or merge chart datasets when the data-pipeline can do it once and cache or pre-aggregate it.

## Role Separation

### Data Pipeline

The data-pipeline owns:

- Raw source ingestion.
- ClickHouse schema and materialized/pre-aggregated serving tables.
- Freshness/readiness status.
- Page-level GraphQL read models.
- Financial calculations, normalization, bucketing, joining, and chart-ready shaping.

### GraphQL API

The analytics GraphQL API owns:

- Stable page contracts.
- Thin reads from ClickHouse serving tables.
- Bounded response sizes.
- Freshness metadata on page payloads.
- Backwards-compatible generic fields only during migrations.

### Frontend

The frontend owns:

- Route params.
- One SWR request per page data model.
- Local UI state: tabs, toggles, hidden series, current page, modal state.
- Rendering already-shaped arrays and metric cards.

The frontend must not own:

- Protocol-level financial aggregation.
- Weighted APY calculations.
- Raw history joins.
- TVL forward-fill.
- Flow cumulative calculations.
- Cross-source freshness interpretation.

## Canonical Page Contracts

Analytics pages should use page-level GraphQL fields:

- `/data` -> `lendingDataPage(displayIn)`
- `/data/:protocol` -> `protocolMarketsPage(protocol)`
- `/data/:protocol/:marketId` -> `lendingPoolPage(protocol, entityId, timeseriesLimit, flowLimit)`

Each page payload should include:

- `freshness`: readiness, status, generated timestamp, version.
- `stats`: render-ready metric cards.
- `chartData` or named chart arrays: chart-ready points in display units.
- `rows` or `markets`: render-ready table rows.
- Optional warnings for stale, capped, partial, or degraded data.

## Request Budget

For analytics routes:

- Page data must come from one analytics GraphQL request.
- Analytics pages must not mount `SimulationProvider`.
- Analytics pages must not call simulation `/graphql`.
- Analytics pages must not call REST `/api/*` for page rendering.
- Header/shell status must not introduce separate polling unless explicitly justified.

For simulation/trading routes:

- Simulation polling is allowed only inside `SimulationRuntimeShell`.
- Wallet/RPC reads are allowed only for wallet-specific or execution-specific state.
- Shared headers must not call `useSim()` directly; simulation data must be passed in as optional props.

## Backend Pre-Aggregation Standard

Prefer ClickHouse serving tables and materialized views for page data:

- Latest market state: `api_market_latest`.
- Market timeseries: `api_market_timeseries_hourly_agg`.
- Protocol TVL history: `api_protocol_tvl_entity_weekly_agg`.
- Aave flow history: `api_aave_market_flow_daily_agg`.
- Chainlink weekly prices: `api_chainlink_price_weekly_agg`.

If a page resolver needs heavy Python loops over large result sets, that is a design smell. Move the work into a materialized view, aggregate table, or bounded page-specific read model.

## Frontend Data Access Rules

- Define endpoints in `frontend/src/api/endpoints.js`.
- Use `postGraphQL()` from `frontend/src/api/graphqlClient.js`.
- Use deterministic SWR keys.
- Keep query hooks data-only.
- Page components may define their page query, but they should not assemble several generic backend primitives into a page model.
- Keep stale data visible during refresh.
- Use local state only for presentation concerns.

## Shell Rules

- `AppProviders` should provide only global concerns that every route needs.
- Analytics routes use an analytics shell and do not mount simulation context.
- Simulation routes use `SimulationRuntimeShell`, which mounts `SimulationProvider`.
- Shared components such as `Header` must accept optional simulation props instead of calling simulation hooks directly.

## Migration Rules

When migrating an analytics page:

1. Add or update a page-level GraphQL field in `data-pipeline/indexer/api/graphql.py`.
2. Build the payload from ClickHouse pre-aggregates or bounded read-model queries.
3. Add backend tests for payload math, bounds, malformed values, and sorting.
4. Replace frontend generic queries with one page-level query.
5. Add or update frontend guardrails so the page cannot regress to generic multi-request composition.
6. Run lint, build, bundle/perf checks, and stack smoke.

Do not delete generic GraphQL fields until all consumers have migrated and smoke checks prove compatibility is no longer needed.

## Verification Checklist

Before finalizing frontend architecture changes, verify:

```bash
npm run check:analytics-contracts
npm run check:boundaries
npm run lint
npm run build
npm run check:bundle
npm run check:perf-smoke
```

For backend page-model changes, verify in the indexer image or an environment with the data-pipeline dependencies:

```bash
python -m py_compile indexer/api/graphql.py tests/test_page_models.py
python -m unittest discover -s tests -p 'test_*.py'
```

For deployed changes, verify:

```bash
bash docker/scripts/stack.sh smoke --allow-not-ready
```

Also manually check the browser network panel for analytics pages:

- `/data`: one analytics page request.
- `/data/:protocol`: one analytics page request.
- `/data/:protocol/:marketId`: one analytics page request.
- No simulation polling on analytics routes.

## Anti-Patterns

Avoid these:

- Global `SimulationProvider` around the whole app.
- Shared layout components directly calling `useSim()`.
- Page components issuing several analytics SWR requests for one screen.
- Frontend reducers computing protocol-wide totals from raw rows.
- Frontend joining TVL history with APY history.
- Request-time raw event scans for page endpoints.
- Adding compatibility aliases without a removal plan.

## Handoff Standard

Every frontend architecture change should report:

- Which page contracts were added or changed.
- Which routes were migrated.
- Whether analytics routes remain free of simulation polling.
- Which tests and guardrails passed.
- Any remaining compatibility fields or legacy paths.

