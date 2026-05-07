# Frontend Wiring Guide — Adding a New Data Page

> Step-by-step instructions for wiring a new frontend page to the analytics GraphQL API, following established patterns.

---

## Architecture Invariants

Before you start, these rules are non-negotiable:

1. **All API data flows through GraphQL** — no direct ClickHouse or REST calls from frontend
2. **SWR is the only caching layer** — no manual `setInterval`, no `localStorage` caching
3. **One query per page** — batch everything the page needs into a single GraphQL query
4. **Stale-while-revalidate** — never clear existing data on fetch error
5. **Deterministic SWR keys** — use `queryKeys.js` factory functions

---

## Step-by-Step: New Page

### Step 1: Define the GraphQL Query

**File:** `frontend/src/api/apiQueries.js`

```js
export const MY_NEW_PAGE_QUERY = `
  query MyNewPage($protocol: String!, $limit: Int!) {
    protocolMarkets(protocol: $protocol) {
      entityId
      symbol
      supplyUsd
      borrowUsd
      supplyApy
      borrowApy
      utilization
      lltv
      oracleSupport
    }
    marketTimeseries(entityId: "0x...", resolution: "1D", limit: $limit) {
      timestamp
      supplyApy
      borrowApy
    }
  }
`;
```

> [!TIP]
> If your page needs data from multiple query roots, combine them in a single query.
> GraphQL executes all root fields in parallel on the server side.

### Step 2: Add a SWR Query Key

**File:** `frontend/src/api/queryKeys.js`

```js
export const queryKeys = {
  // ... existing keys ...
  
  apiMyNewPage: (url, protocol) => [url, "api.my-new-page.v1", { protocol }],
};
```

**Key format:** `[url, "api.{descriptive-name}.v{version}", { ...variables }]`

- The URL ensures cache isolation between API and simulation endpoints
- The version suffix (`v1`) allows safe cache invalidation on schema changes
- Variables are serialized by SWR for cache key identity

### Step 3: Create a Data Hook

**File:** `frontend/src/hooks/queries/useMyNewPageData.js`

```js
import useSWR from "swr";
import { API_GRAPHQL_URL } from "../../api/endpoints";
import { apiGraphQL } from "../../api/apiClient";
import { MY_NEW_PAGE_QUERY } from "../../api/apiQueries";
import { queryKeys } from "../../api/queryKeys";
import { REFRESH_INTERVALS } from "../../config/refreshIntervals";

export function useMyNewPageData(protocol) {
  const { data, error, isLoading } = useSWR(
    queryKeys.apiMyNewPage(API_GRAPHQL_URL, protocol),
    ([, , variables]) =>
      apiGraphQL("MyNewPage", {
        query: MY_NEW_PAGE_QUERY,
        variables: {
          protocol: variables.protocol,
          limit: 365,
        },
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    }
  );

  return { data, error, isLoading };
}
```

### Step 4: Build the Page Component

**File:** `frontend/src/pages/app/MyNewPage.jsx`

```jsx
import React, { useMemo } from "react";
import { Loader2 } from "lucide-react";
import { useMyNewPageData } from "../../hooks/queries/useMyNewPageData";

export default function MyNewPage() {
  const { data, isLoading } = useMyNewPageData("AAVE_MARKET");

  const markets = useMemo(() => {
    return (data?.protocolMarkets || []).map((m) => ({
      ...m,
      supplyUsd: Math.max(0, Number(m.supplyUsd) || 0),
      borrowUsd: Math.max(0, Number(m.borrowUsd) || 0),
      supplyApy: Math.max(0, Number(m.supplyApy) || 0),
      borrowApy: Math.max(0, Number(m.borrowApy) || 0),
      utilization: Math.max(0, Math.min(1, Number(m.utilization) || 0)),
    }));
  }, [data]);

  if (isLoading && markets.length === 0) {
    return (
      <div className="min-h-screen bg-[#050505] flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-cyan-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      {/* Your page layout here */}
    </div>
  );
}
```

### Step 5: Register the Route

**File:** `frontend/src/app/routes.jsx`

```jsx
import { lazy } from "react";
const MyNewPage = lazy(() => import("../pages/app/MyNewPage"));

// Inside route definitions:
{ path: "my-new-page", element: <MyNewPage /> }
```

---

## Common Patterns

### Pattern: Safe Numeric Parsing

API returns `Float64` but JSON can produce `null`, `NaN`, or negative values from division-by-zero.

```js
// Always clamp and default
const supplyUsd = Math.max(0, Number(raw.supplyUsd) || 0);
const utilization = Math.max(0, Math.min(1, Number(raw.utilization) || 0));
```

### Pattern: APY Display Formatting

APYs come as decimals (0.05 = 5%). Always multiply by 100 for display:

```js
const formatApy = (value) => `${(value * 100).toFixed(2)}%`;
```

### Pattern: Currency Formatting

```js
const formatCurrency = (value) => {
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
};
```

### Pattern: Protocol Slug → API Key Mapping

When routing from URL slugs to API protocol constants:

```js
// frontend/src/lib/protocolConfig.js
const SLUG_TO_PROTOCOL = {
  aave: "AAVE_MARKET",
  morpho: "MORPHO_MARKET",
  fluid: "FLUID_MARKET",
};

export function apiProtocolForSlug(slug) {
  return SLUG_TO_PROTOCOL[slug?.toLowerCase()] || "AAVE_MARKET";
}
```

### Pattern: Charting with RLDPerformanceChart

```jsx
import RLDPerformanceChart from "../../charts/primitives/RLDPerformanceChart";

<RLDPerformanceChart
  data={tsData}                    // Array of objects with timestamp + value keys
  resolution="1D"                  // Affects axis tick density
  areas={[
    { key: "supplyApy", color: "#34d399", name: "Supply APY", format: "percent" },
    { key: "borrowApy", color: "#22d3ee", name: "Borrow APY", format: "percent" },
  ]}
  referenceLines={[{ y: 0, stroke: "#52525b" }]}  // Optional zero line
/>
```

**Format options:** `"percent"` (auto × 100), `"dollar"` ($-prefix with auto scaling), `"asset"` (raw number)

---

## File Checklist for New Pages

```
frontend/src/
├── api/
│   ├── apiQueries.js        ← Add query string
│   └── queryKeys.js         ← Add SWR key factory
├── hooks/
│   └── queries/
│       └── useMyData.js     ← Add data hook
├── pages/
│   └── app/
│       └── MyPage.jsx       ← Add page component
└── app/
    └── routes.jsx           ← Register route
```

---

## Anti-Patterns to Avoid

| ❌ Don't | ✅ Do | Why |
|----------|-------|-----|
| `fetch()` directly in component | Use `apiGraphQL()` via SWR hook | Centralized error handling, caching, dedup |
| Hardcode API URL | Use `API_GRAPHQL_URL` from endpoints.js | Environment-aware configuration |
| `useEffect + useState` for data | `useSWR` with query key factory | Automatic revalidation, dedup, error retry |
| Create inline query strings | Define in `apiQueries.js` | Reusability, single source of truth |
| Parse raw API numbers directly | `Math.max(0, Number(x) \|\| 0)` | Protect against NaN/null/negative |
| Multiple separate queries | Single combined GQL query | Fewer network roundtrips, atomic loading state |
| Manual `setInterval` polling | `refreshInterval` in SWR config | Pause on tab blur, deduplicate with cache |
