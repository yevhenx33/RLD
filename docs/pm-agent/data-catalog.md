# Data Catalog — What Exists & Where to Find It

> Every data domain indexed by the RLD analytics backend, mapped to the GraphQL queries that expose it.

---

## 1. Cross-Protocol Lending Overview

**Use case:** Dashboard landing page showing aggregate TVL, rates, and market table across all protocols.

### GraphQL Query: `lendingDataPage`

```graphql
query LendingDataHub($displayIn: String!) {
  lendingDataPage(displayIn: $displayIn) {
    freshness { ready status generatedAt }
    stats {
      totalSupplyUsd     # Aggregate supply across all protocols
      totalBorrowUsd     # Aggregate borrow across all protocols
      averageSupplyApy   # Weighted average supply APY
      averageBorrowApy   # Weighted average borrow APY
      marketCount        # Total indexed markets
    }
    chartData {
      timestamp          # Unix seconds
      tvl                # Total value locked in display unit
      averageSupplyApy   # Period average supply APY (decimal)
      averageBorrowApy   # Period average borrow APY (decimal)
    }
    markets {
      entityId           # Unique market identifier
      symbol             # Asset symbol (e.g. "WETH")
      protocol           # "AAVE_MARKET" | "MORPHO_MARKET" | "FLUID_MARKET"
      supplyUsd          # Total supply in USD
      borrowUsd          # Total borrow in USD
      supplyApy          # Current supply APY (decimal)
      borrowApy          # Current borrow APY (decimal)
      utilization        # Borrow/Supply ratio (0–1)
      netWorth           # Supply - Borrow in USD
    }
  }
}
```

**Variables:** `{ "displayIn": "USD" }` — also supports `"ETH"`, `"BTC"`

**Backend source:** `market_timeseries` + `api_market_latest` tables

---

## 2. Protocol-Level Market Listing

**Use case:** Protocol-specific market directory (e.g. "all Aave markets", "all Morpho markets").

### GraphQL Query: `protocolMarketsPage`

```graphql
query ProtocolMarketsByProtocol($protocol: String!) {
  protocolMarketsPage(protocol: $protocol) {
    freshness { ready status generatedAt }
    stats {
      totalSupplyUsd
      totalBorrowUsd
      averageUtilization    # Protocol-wide average utilization
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
      collateralSymbol      # Morpho/Fluid only — paired collateral asset
      lltv                  # Morpho only — liquidation LTV
      isTrapped             # true if market has supply but no borrow capacity
    }
  }
}
```

**Variables:** `{ "protocol": "AAVE_MARKET" }` — one of `AAVE_MARKET`, `MORPHO_MARKET`, `FLUID_MARKET`

---

## 3. Individual Market Deep-Dive (Pool Page)

**Use case:** Single-market detail page with rate timeseries + capital flow analysis.

### GraphQL Query: `marketPage`

```graphql
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
    rateChart {            # Hourly timeseries
      timestamp
      supplyApy
      borrowApy
      utilization
      supplyUsd
      borrowUsd
    }
    flowChart {            # Daily flow data (Aave only currently)
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
```

**Variables:** `{ "protocol": "AAVE_MARKET", "marketId": "0x...", "timeseriesLimit": 500, "flowLimit": 500 }`

> [!NOTE]
> `flowChart` returns meaningful data only for Aave markets. Morpho/Fluid markets return empty arrays for flow data — the events table structure differs.

---

## 4. Market Detail with Extended Fields

**Use case:** Rich market cards, comparison tables, or risk dashboards.

### GraphQL Query: `protocolMarkets`

```graphql
query {
  protocolMarkets(protocol: "MORPHO_MARKET", entityId: "0x...") {
    entityId
    symbol
    protocol
    supplyUsd
    borrowUsd
    supplyApy
    borrowApy
    utilization
    collateralSymbol
    collateralUsd
    lltv
    oracle
    pricingStatus          # "CHAINLINK" | "UNSUPPORTED" | "PARTIAL"
    loanAsset              # Loan token symbol
    loanToken              # Loan token address
    loanDecimals
    collateralAsset        # Collateral token symbol
    collateralToken        # Collateral token address
    collateralDecimals
    loanPriceUsd
    collateralPriceUsd
    supplyAssets            # Raw token amount (string, uint256-scale)
    borrowAssets
    collateralAssets
    irm                    # Interest rate model address
    oracleSupport          # "CHAINLINK" | "UNSUPPORTED"
    isActive
    hasSupply
    hasBorrow
    hasCollateral
    lastEventTimestamp
    lastPricedTimestamp
  }
}
```

**When to use:** When you need more than just APY/TVL — e.g. risk parameters, oracle status, token addresses.

---

## 5. Timeseries Data (Standalone)

**Use case:** Embedding rate or TVL charts anywhere without the full page payload.

### GraphQL Query: `marketTimeseries`

```graphql
query {
  marketTimeseries(entityId: "0x...", resolution: "1H", limit: 2000) {
    timestamp
    supplyApy
    borrowApy
    utilization
    supplyUsd
    borrowUsd
  }
}
```

**Resolutions:** `"RAW"`, `"1H"`, `"4H"`, `"1D"`, `"1W"`

### GraphQL Query: `marketFlowTimeseries`

```graphql
query {
  marketFlowTimeseries(entityId: "0x...", resolution: "1D", limit: 2000) {
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
```

---

## 6. Protocol TVL History

**Use case:** Cross-protocol TVL comparison chart (weekly resolution).

### GraphQL Query: `protocolTvlHistory`

```graphql
query {
  protocolTvlHistory(displayIn: "USD") {
    date              # "YYYY-MM-DD" string
    aave              # Weekly TVL in display unit
    euler
    fluid
    morpho
  }
}
```

---

## 7. Historical Rates

**Use case:** Landing page rate chart, rate comparison widgets.

### GraphQL Query: `historicalRates`

```graphql
query HistoricalRates($resolution: String!, $limit: Int!) {
  historicalRates(symbols: ["USDC", "DAI", "USDT"], resolution: $resolution, limit: $limit) {
    timestamp
    symbol
    apy                 # Supply APY for this symbol at this timestamp
    price               # USD price of the asset
  }
}
```

**Variables:** `{ "resolution": "4H", "limit": 17520 }`

---

## 8. Latest Rates Snapshot

**Use case:** Header/sidebar rate tickers, real-time rate display.

### GraphQL Query: `latestRates`

```graphql
query {
  latestRates {
    timestamp
    usdc               # USDC supply APY (decimal)
    dai                # DAI supply APY
    usdt               # USDT supply APY
    sofr               # SOFR reference rate
    susde              # sUSDe yield
    ethPrice           # ETH/USD price
  }
}
```

---

## 9. Aave Account-Level Data

**Use case:** Account explorer, liquidation risk dashboards.

### 9a. Aggregate Stats

```graphql
query {
  aaveAccountStats(minDebtUsd: 100) {
    deploymentId
    activeAccounts
    debtAccounts
    collateralAccounts
    totalCollateralUsd
    totalDebtUsd
    weightedLiquidationThreshold
    accountsBelowHf125        # Accounts with HF < 1.25
    accountsBelowHf1          # Accounts at liquidation risk
    freshness {
      latestEventBlock
      latestIndexTimestamp
      reconstructionStatus
    }
  }
}
```

### 9b. Account List (Paginated)

```graphql
query {
  aaveAccounts(
    first: 20
    orderBy: "HEALTH_FACTOR_ASC"
    minDebtUsd: 1000
    maxHealthFactor: 1.5
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
```

### 9c. Single Account Profile

```graphql
query {
  aaveAccount(address: "0x...") {
    address
    totalCollateralUsd
    totalDebtUsd
    healthFactor
    emodeCategory
    positions { reserve symbol supplyUsd debtUsd collateralEnabled liquidationThreshold }
  }
}
```

### 9d. Account Health History

```graphql
query {
  aaveAccountProfileHistory(address: "0x...", limit: 500) {
    timestamp
    totalCollateralUsd
    totalDebtUsd
    netWorthUsd
    healthFactor
    emodeCategory
    positionCount
  }
}
```

---

## 10. Morpho Granular Data

### 10a. Market Events

```graphql
query {
  morphoMarketEvents(marketId: "0x...", limit: 100) {
    timestamp
    blockNumber
    txHash
    eventName            # Supply, Withdraw, Borrow, Repay, Liquidate, AccrueInterest
    caller
    onBehalf
    assets               # Raw uint256 string
    shares
    repaidAssets
    seizedAssets
    badDebtAssets
    interestAssets
  }
}
```

### 10b. Market Positions

```graphql
query {
  morphoMarketPositions(marketId: "0x...", limit: 500) {
    marketId
    user
    supplyShares
    borrowShares
    collateralAssets
    estimatedSupplyAssets
    estimatedBorrowAssets
    collateralUsd
    healthFactor
    lastEventTimestamp
  }
}
```

---

## 11. MetaMorpho Vaults

### 11a. Vault Registry

```graphql
query {
  metamorphoVaults(limit: 100) {
    vaultAddress
    name
    assetSymbol
    assetAddress
    totalAssets
    totalSupply
    sharePriceUsd
    tvlUsd
    isCanonicalTvl
    lastSnapshotTimestamp
  }
}
```

### 11b. Vault Allocation Breakdown

```graphql
query {
  metamorphoVaultAllocations(vaultAddress: "0x...") {
    marketId
    cap
    suppliedAssets
    suppliedUsd
    allocationShare        # 0–1
    timestamp
  }
}
```

### 11c. Vault Flow History

```graphql
query {
  metamorphoVaultFlows(vaultAddress: "0x...", limit: 500) {
    timestamp
    assetSymbol
    depositUsd
    withdrawUsd
    netFlowUsd
    eventCount
  }
}
```

---

## 12. Fluid Protocol Data

### 12a. Contract Registry

```graphql
query {
  fluidContracts(productType: "FTOKEN", activeOnly: true) {
    chainId
    productType            # "FTOKEN" | "VAULT" | "DEX" | "REVENUE" | "STETH"
    contract
    factory
    name
    createdBlock
    active
    resolver
    metadata
  }
}
```

### 12b. Product Snapshots

```graphql
query {
  fluidProductSnapshots(productType: "FTOKEN", limit: 100) {
    productType
    productId
    timestamp
    symbol
    supplyUsd
    borrowUsd
    collateralUsd
    liquidityUsd
    supplyApy
    borrowApy
    utilization
    ltv
    liquidationThreshold
    positionCount
    pricingStatus
    oracleStatus
  }
}
```

### 12c. Product Token Decomposition

```graphql
query {
  fluidProductComponents(productId: "0x...") {
    componentType          # "SUPPLY" | "BORROW" | "COLLATERAL"
    token
    symbol
    rawAmount
    decimals
    priceUsd
    amountUsd
    pricingStatus
  }
}
```

---

## 13. Pendle Derivatives Data

### 13a. Asset Registry

```graphql
query {
  pendleEthAssets(assetTypes: ["PT", "YT"], activeOnly: true, search: "sUSDe") {
    assetAddress
    assetType              # "PT" | "YT" | "SY"
    symbol
    marketAddress
    expiry                 # Unix timestamp
    active
    matured
  }
}
```

### 13b. Market Page (Full Payload)

```graphql
query PendleMarket($search: String!) {
  pendleMarketPage(search: $search) {
    marketAddress
    freshness { ready status generatedAt }
    assets { assetAddress assetType symbol marketAddress expiry active matured }
    latestPrices { assetAddress assetType symbol priceUsd timestamp }
  }
}
```

### 13c. Price History (OHLCV)

```graphql
query {
  pendleEthPriceHistory(address: "0x...", timeFrame: "hour", limit: 720) {
    timestamp
    open
    high
    low
    close
    volume
  }
}
```

---

## 14. System Health & Readiness

### GraphQL Query: `apiStatus`

```graphql
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
        total
        indexed
        priced
        unpriced
        unsupported
        partial
        status
      }
    }
  }
}
```

**Use case:** Readiness gates, health monitoring, admin dashboards.

---

## Data Availability Matrix

| Data Domain | Query | Aave | Morpho | Fluid | Pendle | Cross-Protocol |
|------------|-------|------|--------|-------|--------|----------------|
| Market listing | `lendingDataPage` | ✅ | ✅ | ✅ | — | ✅ |
| Protocol markets | `protocolMarketsPage` | ✅ | ✅ | ✅ | — | — |
| Rate timeseries | `marketTimeseries` | ✅ | ✅ | ✅ | — | — |
| Flow timeseries | `marketFlowTimeseries` | ✅ | ❌ | ❌ | — | — |
| Full pool page | `marketPage` | ✅ | ✅ | ✅ | — | — |
| TVL history | `protocolTvlHistory` | ✅ | ✅ | ✅ | — | ✅ |
| Account-level | `aaveAccount*` | ✅ | — | — | — | — |
| Market positions | `morphoMarketPositions` | — | ✅ | — | — | — |
| Vault lifecycle | `metamorphoVault*` | — | ✅ | — | — | — |
| Product snapshots | `fluidProduct*` | — | — | ✅ | — | — |
| OHLCV pricing | `pendleEthPriceHistory` | — | — | — | ✅ | — |
| Risk params | `protocolMarkets` | ✅ (LTV, e-mode) | ✅ (LLTV) | ✅ (LTV) | — | — |
| Oracle health | `protocolMarkets` | — | ✅ | ✅ | — | — |
| Latest rates | `latestRates` | ✅ | — | — | — | ✅ (SOFR, sUSDe) |
