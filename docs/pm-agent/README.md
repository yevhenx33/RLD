# Product Manager Agent — Data Blueprint

> **Purpose:** Provide any AI agent or human building frontend features with a complete, authoritative understanding of what data exists in the RLD analytics backend, how to source it via GraphQL, and how to wire it into the frontend using established patterns.

## Contents

| Document | Purpose |
|----------|---------|
| [data-catalog.md](./data-catalog.md) | Every data domain, what fields are available, and which GraphQL queries expose them |
| [query-cookbook.md](./query-cookbook.md) | Copy-paste GraphQL queries for every page type, with variables and expected shapes |
| [frontend-wiring.md](./frontend-wiring.md) | How to add a new page end-to-end: hook → query → queryKey → component |
| [field-dictionary.md](./field-dictionary.md) | Canonical field definitions, units, and edge cases for all API response fields |

## Architecture Context

```
┌─────────────────────────────────────────────────────────────────┐
│  ClickHouse (30+ tables)                                        │
│  ─ aave_timeseries, morpho_chainlink_timeseries, fluid_timeseries│
│  ─ market_timeseries (canonical cross-protocol serving table)    │
│  ─ api_market_latest, pre-aggregated rollups                     │
└────────────────────────┬────────────────────────────────────────┘
                         │  Strawberry GraphQL + FastAPI
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  /analytics/graphql  (28 query endpoints)                       │
│  ─ See data-catalog.md for complete mapping                     │
└────────────────────────┬────────────────────────────────────────┘
                         │  fetch() via graphqlClient.js
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Frontend  (React + SWR)                                        │
│  ─ api/apiQueries.js  → GraphQL query strings                   │
│  ─ api/queryKeys.js   → Deterministic SWR cache keys            │
│  ─ api/apiClient.js   → apiGraphQL() wrapper                    │
│  ─ hooks/             → useSWR-based data hooks                 │
│  ─ pages/             → Page components consuming hooks          │
└─────────────────────────────────────────────────────────────────┘
```

## Key Constraints

1. **API endpoint:** `/analytics/graphql` (configured via `VITE_PUBLIC_API_BASE`)
2. **Transport:** POST with JSON body, no subscriptions, no mutations
3. **Caching:** SWR with deterministic keys from `queryKeys.js`
4. **Units:** APYs are decimals (0.05 = 5%), USD values are raw floats, timestamps are Unix seconds
5. **Protocols:** `AAVE_MARKET`, `MORPHO_MARKET`, `FLUID_MARKET` (always use these exact string constants)
