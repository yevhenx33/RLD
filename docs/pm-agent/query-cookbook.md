# Query Cookbook — Copy-Paste Recipes

> Ready-to-use GraphQL queries organized by frontend page type. Each recipe includes the query string, variables, and the expected response shape for destructuring.

---

## Recipe 1: Landing Page Dashboard

**Page:** `LendingDataPage.jsx`  
**What it shows:** Aggregate stats bar, TVL chart, rate chart, market table with filtering

```js
// api/apiQueries.js
export const LENDING_DATA_QUERY = `
  query LendingDataHub($displayIn: String!) {
    lendingDataPage(displayIn: $displayIn) {
      freshness { ready status generatedAt }
      stats {
        totalSupplyUsd
        totalBorrowUsd
        averageSupplyApy
        averageBorrowApy
        marketCount
      }
      chartData {
        timestamp
        tvl
        averageSupplyApy
        averageBorrowApy
      }
      markets {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
        netWorth
      }
    }
  }
`;

// Usage in hook
const variables = { displayIn: "USD" };
const data = await apiGraphQL("LendingDataHub", { query: LENDING_DATA_QUERY, variables });

// Destructure
const page = data.lendingDataPage;
const stats = page.stats;           // { totalSupplyUsd, totalBorrowUsd, ... }
const chartData = page.chartData;   // [{ timestamp, tvl, averageSupplyApy, averageBorrowApy }]
const markets = page.markets;       // [{ entityId, symbol, protocol, supplyUsd, ... }]
```

---

## Recipe 2: Protocol Market Directory

**Page:** Protocol-specific market listing (e.g. "All Aave Markets")  
**What it shows:** Protocol aggregate stats + market table

```js
export const PROTOCOL_MARKETS_QUERY = `
  query ProtocolMarketsByProtocol($protocol: String!) {
    protocolMarketsPage(protocol: $protocol) {
      freshness { ready status generatedAt }
      stats {
        totalSupplyUsd
        totalBorrowUsd
        averageUtilization
        averageSupplyApy
        averageBorrowApy
        marketCount
      }
      rows {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
        collateralSymbol
        lltv
        isTrapped
      }
    }
  }
`;

// Variables
const variables = { protocol: "AAVE_MARKET" };
// Also valid: "MORPHO_MARKET", "FLUID_MARKET"
```

---

## Recipe 3: Individual Market Page (Pool Deep-Dive)

**Page:** `AaveMarketPage.jsx` (used for Aave, Morpho, Fluid)  
**What it shows:** Market stats bar, APY chart, TVL chart, supply/borrow flow charts

```js
export const MARKET_PAGE_QUERY = `
  query MarketPage($protocol: String!, $marketId: String!, $timeseriesLimit: Int!, $flowLimit: Int!) {
    marketPage(
      protocol: $protocol
      marketId: $marketId
      timeseriesLimit: $timeseriesLimit
      flowLimit: $flowLimit
    ) {
      freshness { ready status generatedAt }
      market {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
      }
      rateChart {
        timestamp
        supplyApy
        borrowApy
        utilization
        supplyUsd
        borrowUsd
      }
      flowChart {
        timestamp
        supplyInflowUsd
        supplyOutflowUsd
        borrowInflowUsd
        borrowOutflowUsd
        netSupplyFlowUsd
        netBorrowFlowUsd
        cumulativeSupplyNetInflowUsd
        cumulativeBorrowNetInflowUsd
      }
    }
  }
`;

// Variables
const variables = {
  protocol: "AAVE_MARKET",
  marketId: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", // USDC reserve address
  timeseriesLimit: 500,
  flowLimit: 500,
};
```

---

## Recipe 4: Pendle Market Page

**Page:** `PendleMarketPage.jsx`  
**What it shows:** PT/YT/SY asset cards, latest prices

```js
export const PENDLE_MARKET_QUERY = `
  query PendleMarket($search: String!) {
    pendleMarketPage(search: $search) {
      marketAddress
      freshness { ready status generatedAt }
      assets {
        assetAddress
        assetType
        symbol
        marketAddress
        expiry
        active
        matured
      }
      latestPrices {
        assetAddress
        assetType
        symbol
        priceUsd
        timestamp
      }
    }
  }
`;

// Variables — search by market address or symbol
const variables = { search: "0x..." }; // or "sUSDe"
```

---

## Recipe 5: Rate Comparison Widget

**Page:** Landing page rate chart, rate comparison sections  
**What it shows:** Historical APY for multiple stablecoins

```js
export const HISTORICAL_RATES_QUERY = `
  query HistoricalRates($resolution: String!, $limit: Int!) {
    historicalRates(symbols: ["USDC", "DAI", "USDT"], resolution: $resolution, limit: $limit) {
      timestamp
      symbol
      apy
      price
    }
  }
`;

// Variables
const variables = { resolution: "4H", limit: 17520 };

// Post-processing: filter by symbol on client side
const usdcRates = data.historicalRates.filter(r => r.symbol === "USDC");
```

---

## Recipe 6: Protocol TVL Comparison Chart

**What it shows:** Weekly TVL stacked/line chart across Aave, Euler, Fluid, Morpho

```js
const TVL_HISTORY_QUERY = `
  query ProtocolTvlHistory($displayIn: String!) {
    protocolTvlHistory(displayIn: $displayIn) {
      date
      aave
      euler
      fluid
      morpho
    }
  }
`;

// Variables
const variables = { displayIn: "USD" }; // also "ETH", "BTC"

// Returns: [{ date: "2025-01-06", aave: 12345678.90, euler: 0, fluid: 5678901.23, morpho: 9012345.67 }]
```

---

## Recipe 7: Aave Account Explorer

**What it shows:** Paginated account list sorted by health factor, with position breakdown

```js
const AAVE_ACCOUNTS_QUERY = `
  query AaveAccounts($first: Int, $after: String, $orderBy: String!, $minDebtUsd: Float!, $maxHealthFactor: Float) {
    aaveAccounts(
      first: $first
      after: $after
      orderBy: $orderBy
      minDebtUsd: $minDebtUsd
      maxHealthFactor: $maxHealthFactor
    ) {
      nodes {
        address
        totalCollateralUsd
        totalDebtUsd
        healthFactor
        emodeCategory
        positions {
          reserve
          symbol
          supplyUsd
          debtUsd
          collateralEnabled
          liquidationThreshold
        }
      }
      pageInfo { hasNextPage endCursor }
      totalCount
    }
  }
`;

// Variables — show accounts closest to liquidation
const variables = {
  first: 20,
  orderBy: "HEALTH_FACTOR_ASC",
  minDebtUsd: 1000,
  maxHealthFactor: 1.5,
};
```

---

## Recipe 8: MetaMorpho Vault Dashboard

**What it shows:** Vault registry table, TVL, allocation pie chart, flow history

```js
// Step 1: Vault list
const VAULTS_QUERY = `
  query {
    metamorphoVaults(limit: 100) {
      vaultAddress
      name
      assetSymbol
      tvlUsd
      sharePriceUsd
      isCanonicalTvl
      lastSnapshotTimestamp
    }
  }
`;

// Step 2: Single vault detail (allocations + flows)
const VAULT_DETAIL_QUERY = `
  query VaultDetail($vaultAddress: String!) {
    metamorphoVaultAllocations(vaultAddress: $vaultAddress) {
      marketId
      suppliedUsd
      allocationShare
      cap
      timestamp
    }
    metamorphoVaultFlows(vaultAddress: $vaultAddress, limit: 500) {
      timestamp
      depositUsd
      withdrawUsd
      netFlowUsd
      eventCount
    }
  }
`;
```

---

## Recipe 9: Fluid Product Explorer

**What it shows:** Multi-product-type table (fTokens, Vaults, DEX pools) with token-level decomposition

```js
const FLUID_SNAPSHOTS_QUERY = `
  query FluidProducts($productType: String) {
    fluidProductSnapshots(productType: $productType, limit: 200) {
      productType
      productId
      symbol
      supplyUsd
      borrowUsd
      supplyApy
      borrowApy
      utilization
      ltv
      liquidationThreshold
      positionCount
      pricingStatus
    }
  }
`;

// Variables — null for all types, or specific:
const variables = { productType: "FTOKEN" }; // "VAULT", "DEX", "REVENUE", "STETH"
```

---

## Recipe 10: System Health Check

**What it shows:** Readiness gate, per-protocol lag monitoring, coverage stats

```js
export const API_STATUS_QUERY = `
  query ApiStatus {
    apiStatus {
      ready
      status
      version
      generatedAt
      protocols {
        protocol
        ready
        status
        freshness {
          collectorLag
          processingLag
          status
          issues { code severity message }
        }
        coverage {
          total indexed priced unpriced unsupported partial status
        }
      }
    }
  }
`;
```

---

## Recipe 11: Standalone Timeseries (Embeddable Chart)

**What it shows:** Embeddable rate/TVL chart for any market

```js
const MARKET_TIMESERIES_QUERY = `
  query MarketSeries($entityId: String!, $resolution: String!, $limit: Int!) {
    marketTimeseries(entityId: $entityId, resolution: $resolution, limit: $limit) {
      timestamp
      supplyApy
      borrowApy
      utilization
      supplyUsd
      borrowUsd
    }
  }
`;

// Variables
const variables = {
  entityId: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
  resolution: "1D",  // "RAW", "1H", "4H", "1D", "1W"
  limit: 365,
};
```

---

## Quick Reference: Query → Variable Mapping

| Query Name | Required Variables | Optional Variables |
|-----------|-------------------|-------------------|
| `lendingDataPage` | `displayIn: String!` | — |
| `protocolMarketsPage` | `protocol: String!` | — |
| `marketPage` | `protocol`, `marketId`, `timeseriesLimit`, `flowLimit` | — |
| `historicalRates` | `resolution: String!`, `limit: Int!` | — |
| `latestRates` | — | — |
| `protocolTvlHistory` | — | `displayIn` |
| `marketTimeseries` | `entityId: String!` | `resolution`, `limit` |
| `marketFlowTimeseries` | `entityId: String!` | `resolution`, `limit` |
| `aaveAccounts` | — | `first`, `after`, `orderBy`, `minDebtUsd`, `maxHealthFactor` |
| `aaveAccount` | `address: String!` | `deploymentId` |
| `morphoMarketEvents` | — | `marketId`, `eventName`, `limit` |
| `morphoMarketPositions` | — | `marketId`, `user`, `limit` |
| `metamorphoVaults` | — | `vaultAddress`, `limit` |
| `pendleMarketPage` | `search: String!` | — |
| `pendleEthPriceHistory` | `address: String!` | `timeFrame`, `startTs`, `endTs`, `limit` |
| `apiStatus` | — | — |
