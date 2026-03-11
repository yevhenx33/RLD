# RLD Indexer — GraphQL API Reference

The simulation indexer exposes a Strawberry GraphQL API at `POST /graphql` (port 8080). It replaces 7+ separate REST endpoints with a single request that returns exactly the fields you need.

**Endpoint:** `http://localhost:8080/graphql`
**Method:** `POST`
**Content-Type:** `application/json`

> **Playground:** Visit `http://localhost:8080/graphql` in a browser for the interactive GraphiQL explorer.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Core Concepts](#core-concepts)
- [Queries](#queries)
  - [latest](#latest)
  - [block](#block)
  - [marketInfo](#marketinfo)
  - [status](#status)
  - [volume](#volume)
  - [volumeHistory](#volumehistory)
  - [events](#events)
  - [lpPositions](#lppositions)
  - [allLpPositions](#alllppositions)
  - [twammOrders](#twammorders)
  - [bonds](#bonds)
  - [rates](#rates)
  - [ethPrices](#ethprices)
- [Types Reference](#types-reference)
- [Integration Patterns](#integration-patterns)
- [Error Handling](#error-handling)

---

## Quick Start

Fetch everything you need in a single request:

```bash
curl -X POST http://localhost:8080/graphql \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "{ latest { blockNumber market { normalizationFactor totalDebt indexPrice } pool { tick markPrice liquidity } brokers { address healthFactor } } }"
  }'
```

**Response:**

```json
{
  "data": {
    "latest": {
      "blockNumber": 24627705,
      "market": {
        "normalizationFactor": "999999969737333635",
        "totalDebt": "6846302000000",
        "indexPrice": "2887648941468543976"
      },
      "pool": {
        "tick": -10532,
        "markPrice": 2.911031,
        "liquidity": "13704676904781"
      },
      "brokers": [
        { "address": "0x9131...", "healthFactor": 6.07 },
        { "address": "0xcd80...", "healthFactor": 2.25 },
        { "address": "0x2cc6...", "healthFactor": 5.00 }
      ]
    }
  }
}
```

---

## Core Concepts

### Polling Model

The simulation indexer snapshots the on-chain state every block (~12s). Integrators should:

1. **Poll `latest`** on a 5–12s interval to get the most recent snapshot
2. **Use `marketInfo`** once on startup (cached 60s server-side) for contract addresses and risk parameters
3. **Combine queries** — GraphQL lets you fetch `latest`, `volume`, and `status` in a single request

### Number Encoding

| Type | Encoding | Example |
|------|----------|---------|
| Wei amounts (uint256) | String | `"6846302000000"` (6,846,302 USDC in 6-decimal) |
| Price (mark) | Float | `2.911031` (waUSDC per wRLP) |
| Factors / ratios | String (Ray, 1e18) | `"999999969737333635"` (≈1.0 NF) |
| Index price | String (Ray, 1e27) | `"2887648941468543976"` (≈2.89% APY) |
| Timestamps | Unix int | `1773237135` |
| Addresses | Hex string | `"0x9131ee7c..."` |

> **Why strings for large numbers?** Solidity uint256 values exceed JavaScript's `Number.MAX_SAFE_INTEGER`. Always use `BigInt` or a big-number library to parse them.

### Field Selection

Only request the fields you need. The server resolves each field independently — omitting `brokers` skips all broker DB queries. This is the primary advantage over REST.

```graphql
# Lightweight: only market + pool state (~2ms)
{ latest { blockNumber market { totalDebt } pool { markPrice } } }

# Heavy: includes all broker positions + LP data (~50ms)
{ latest { blockNumber brokers { address healthFactor lpPositions { tokenId liquidity } } } }
```

---

## Queries

### `latest`

Returns the most recently indexed block snapshot with market state, pool state, and broker positions.

```graphql
query LatestSnapshot {
  latest {
    blockNumber
    market {
      blockNumber
      blockTimestamp
      marketId
      normalizationFactor
      totalDebt
      lastUpdateTimestamp
      indexPrice
    }
    pool {
      poolId
      tick
      markPrice
      liquidity
      sqrtPriceX96
      token0Balance
      token1Balance
      feeGrowthGlobal0
      feeGrowthGlobal1
    }
    brokers {
      address
      collateral
      debt
      collateralValue
      debtValue
      healthFactor
      lpPositions {
        tokenId
        liquidity
        tickLower
        tickUpper
        entryTick
        entryPrice
        mintBlock
        isActive
        brokerAddress
      }
    }
  }
}
```

**Use case:** Main polling query for a trading terminal. Call every 5–12s.

---

### `block`

Returns the snapshot at a specific historical block number.

```graphql
query HistoricalBlock {
  block(blockNumber: 24627500) {
    blockNumber
    market { normalizationFactor totalDebt }
    pool { markPrice tick }
  }
}
```

**Arguments:**

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `blockNumber` | `Int` | Yes | Historical block to query |

**Use case:** Time-travel queries, debugging, chart backfilling.

---

### `marketInfo`

Returns market configuration, token metadata, contract addresses, and risk parameters. **Cached for 60 seconds** server-side — safe to call frequently.

```graphql
query MarketConfig {
  marketInfo {
    collateral { name symbol address }
    positionToken { name symbol address }
    brokerFactory
    infrastructure {
      brokerRouter
      brokerExecutor
      twammHook
      bondFactory
      basisTradeFactory
      poolManager
      v4Quoter
      v4PositionManager
      v4PositionDescriptor
      v4StateView
      universalRouter
      permit2
      poolFee
      tickSpacing
    }
    externalContracts {
      usdc
      ausdc
      aavePool
      susde
      usdcWhale
    }
    riskParams {
      minColRatio
      maintenanceMargin
      liqCloseFactor
      fundingPeriodSec
      debtCap
    }
  }
}
```

**Response (live):**

```json
{
  "data": {
    "marketInfo": {
      "collateral": { "name": "Wrapped aUSDC", "symbol": "waUSDC", "address": "0x6d48..." },
      "positionToken": { "name": "Wrapped RLD LP waUSDC", "symbol": "wRLPwaUSDC", "address": "0xB15f..." },
      "brokerFactory": "0xc339...",
      "infrastructure": {
        "brokerRouter": "0x401D...",
        "brokerExecutor": "0x212e...",
        "twammHook": "0xa2c2...",
        "bondFactory": "0x2bAe...",
        "basisTradeFactory": "0x69Ab...",
        "poolManager": "0x0000...4444...",
        "v4Quoter": "0x52f0...",
        "poolFee": 500,
        "tickSpacing": 5
      },
      "externalContracts": {
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "ausdc": "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
        "aavePool": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        "susde": "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497",
        "usdcWhale": "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"
      },
      "riskParams": {
        "minColRatio": 1.5,
        "maintenanceMargin": 1.1,
        "liqCloseFactor": 0.5,
        "fundingPeriodSec": 2592000,
        "debtCap": 604800
      }
    }
  }
}
```

**Key fields explained:**

| Field | Description |
|-------|-------------|
| `collateral` | The wrapped collateral token (waUSDC) used by the protocol |
| `positionToken` | The wrapped LP token (wRLP) representing positions |
| `brokerFactory` | Factory contract for creating new Prime Broker accounts |
| `infrastructure` | All protocol and Uniswap V4 contract addresses |
| `externalContracts` | Canonical mainnet token/protocol addresses (USDC, Aave, sUSDe) |
| `riskParams` | On-chain risk parameters read from RLDCore |
| `riskParams.minColRatio` | Minimum collateral ratio (1.5 = 150%) for opening positions |
| `riskParams.maintenanceMargin` | Liquidation threshold (1.1 = 110%) |
| `riskParams.fundingPeriodSec` | Funding interval in seconds (2592000 = 30 days) |

**Use case:** Call once on app startup to discover all contract addresses. Cache locally.

---

### `status`

Returns indexer operational status.

```graphql
query IndexerStatus {
  status {
    totalBlockStates
    totalEvents
    lastIndexedBlock
  }
}
```

**Response:**

```json
{
  "data": {
    "status": {
      "totalBlockStates": 479,
      "totalEvents": 223,
      "lastIndexedBlock": 24627705
    }
  }
}
```

**Use case:** Health monitoring, staleness detection. Compare `lastIndexedBlock` against on-chain `block_number` to measure indexer lag.

---

### `volume`

Returns aggregated trade volume over a time window, computed from Swap events.

```graphql
query TradingVolume {
  volume(hours: 24) {
    volumeUsd
    swapCount
    hours
  }
}
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `hours` | `Int` | `24` | Lookback window in hours |

---

### `volumeHistory`

Returns volume broken into time buckets for charting.

```graphql
query VolumeChart {
  volumeHistory(hours: 168, bucketHours: 1) {
    timestamp
    volumeUsd
    swapCount
  }
}
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `hours` | `Int` | `168` | Lookback window (default 7 days) |
| `bucketHours` | `Int` | `1` | Bucket size for aggregation |

**Use case:** Volume bar charts on trading dashboards.

---

### `events`

Returns recent protocol events (Swap, SubmitOrder, CancelOrder, etc.).

```graphql
query RecentEvents {
  events(limit: 10, eventName: "Swap") {
    id
    blockNumber
    txHash
    eventName
    timestamp
    data
  }
}
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `limit` | `Int` | `20` | Max events to return |
| `eventName` | `String` | `null` | Filter by event type |

The `data` field contains the raw event parameters as a JSON string. Parse it client-side for event-specific fields (e.g., `amount0`, `amount1` for Swaps).

---

### `lpPositions`

Returns all LP positions held by a specific broker (Prime Broker NFT).

```graphql
query BrokerLPs {
  lpPositions(brokerAddress: "0x9131ee7cda6d9e625d6c045bbf0878c355a88e7e") {
    tokenId
    liquidity
    tickLower
    tickUpper
    entryPrice
    mintBlock
    isActive
  }
}
```

**Arguments:**

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `brokerAddress` | `String` | Yes | The Prime Broker contract address |

**Fallback behavior:** If no LP data is found in the database, the resolver falls back to a live RPC query — scanning POSM Transfer events to discover positions. This ensures new brokers are supported immediately, even before the indexer catches up.

---

### `allLpPositions`

Returns all LP positions across all tracked brokers at the latest block.

```graphql
query AllLPs {
  allLpPositions {
    tokenId
    liquidity
    tickLower
    tickUpper
    isActive
    brokerAddress
  }
}
```

**Use case:** Portfolio overview, LP analytics dashboards.

---

### `twammOrders`

Returns TWAMM (Time-Weighted AMM) orders reconstructed from SubmitOrder/CancelOrder events.

```graphql
query TWAMMOrders {
  twammOrders(owner: "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC") {
    orderId
    owner
    amountIn
    sellRate
    expiration
    startEpoch
    zeroForOne
    blockNumber
    txHash
    isCancelled
  }
}
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `owner` | `String` | `null` | Filter by order owner address |

---

### `bonds`

Returns bond positions. Optionally filtered by owner or status.

```graphql
query BondPositions {
  bonds(owner: "0xf39F...", status: "active") {
    brokerAddress
    owner
    status
    notionalUsd
    bondId
    createdBlock
    createdTx
  }
}
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `owner` | `String` | `null` | Filter by bond owner |
| `status` | `String` | `null` | Filter by status (`"active"`, `"closed"`) |

---

### `rates`

Proxies to the rates-indexer API. Returns lending rate history for one or more symbols.

```graphql
query LendingRates {
  rates(symbols: ["USDC"], resolution: "1H", limit: 100) {
    symbol
    data {
      timestamp
      apy
      ethPrice
    }
  }
}
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `symbols` | `[String]` | Required | Token symbols (e.g., `["USDC"]`) |
| `resolution` | `String` | `"1H"` | Aggregation: `RAW`, `1H`, `4H`, `1D` |
| `limit` | `Int` | `50000` | Max data points |
| `startDate` | `String` | `null` | ISO date filter start |
| `endDate` | `String` | `null` | ISO date filter end |

**Response:**

```json
{
  "data": {
    "rates": [{
      "symbol": "USDC",
      "data": [
        { "timestamp": 1773212400, "apy": 2.887, "ethPrice": 2018.29 },
        { "timestamp": 1773216000, "apy": 2.888, "ethPrice": 2012.85 }
      ]
    }]
  }
}
```

**Use case:** Rate charts, funding rate displays, historical analysis.

---

### `ethPrices`

Proxies to the rates-indexer API. Returns ETH/USD price history.

```graphql
query EthPriceHistory {
  ethPrices(resolution: "1D", limit: 30) {
    timestamp
    price
  }
}
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `resolution` | `String` | `"1D"` | Aggregation: `RAW`, `1H`, `4H`, `1D` |
| `limit` | `Int` | `50000` | Max data points |
| `startDate` | `String` | `null` | ISO date filter start |
| `endDate` | `String` | `null` | ISO date filter end |

**Response:**

```json
{
  "data": {
    "ethPrices": [
      { "timestamp": 1773216000, "price": 2025.96 },
      { "timestamp": 1773183600, "price": 2046.21 }
    ]
  }
}
```

---

## Types Reference

### Snapshot

Root type returned by `latest` and `block` queries.

| Field | Type | Description |
|-------|------|-------------|
| `blockNumber` | `Int` | Indexed block number |
| `market` | `MarketState` | Protocol-level state |
| `pool` | `PoolState` | Uniswap V4 pool state |
| `brokers` | `[BrokerState]` | All tracked broker positions |

### MarketState

| Field | Type | Description |
|-------|------|-------------|
| `blockNumber` | `Int` | Block this state was captured at |
| `blockTimestamp` | `Int` | Unix timestamp |
| `marketId` | `String` | RLD market identifier (bytes32) |
| `normalizationFactor` | `String` | NF (1e18). Tracks funding payments — ratio of index to mark |
| `totalDebt` | `String` | Total system debt in collateral units (6 decimals for USDC) |
| `lastUpdateTimestamp` | `Int` | Last NF update timestamp |
| `indexPrice` | `String` | Oracle index price (Ray, 1e27). Represents APY as a rate |

### PoolState

| Field | Type | Description |
|-------|------|-------------|
| `poolId` | `String` | Uniswap V4 pool identifier (bytes32) |
| `tick` | `Int` | Current pool tick |
| `markPrice` | `Float` | Derived mark price (waUSDC per wRLP) |
| `liquidity` | `String` | Active liquidity in the current tick range |
| `sqrtPriceX96` | `String` | Raw Uniswap sqrtPrice (Q96 encoding) |
| `token0Balance` | `String` | Pool balance of token0 |
| `token1Balance` | `String` | Pool balance of token1 |
| `feeGrowthGlobal0` | `String` | Cumulative fees for token0 |
| `feeGrowthGlobal1` | `String` | Cumulative fees for token1 |

### BrokerState

| Field | Type | Description |
|-------|------|-------------|
| `address` | `String` | Prime Broker contract address |
| `collateral` | `String` | Raw collateral deposited (6 decimals) |
| `debt` | `String` | Raw debt owed (6 decimals) |
| `collateralValue` | `String` | Collateral value in USDC terms |
| `debtValue` | `String` | Debt value in USDC terms |
| `healthFactor` | `Float` | Collateral/debt ratio. <1.1 = liquidatable |
| `lpPositions` | `[LPPosition]` | LP positions held by this broker |

### LPPosition

| Field | Type | Description |
|-------|------|-------------|
| `tokenId` | `Int` | Uniswap V4 POSM NFT token ID |
| `liquidity` | `String` | Position liquidity |
| `tickLower` | `Int` | Lower tick bound |
| `tickUpper` | `Int` | Upper tick bound |
| `entryTick` | `Int?` | Tick at time of mint |
| `entryPrice` | `Float?` | Price at time of mint |
| `mintBlock` | `Int?` | Block when position was created |
| `isActive` | `Bool` | Whether this is the broker's active position |
| `brokerAddress` | `String?` | Owning broker address |

### MarketInfo

| Field | Type | Description |
|-------|------|-------------|
| `collateral` | `TokenInfo` | Collateral token metadata (waUSDC) |
| `positionToken` | `TokenInfo` | Position token metadata (wRLP) |
| `brokerFactory` | `String` | BrokerFactory contract address |
| `infrastructure` | `Infrastructure` | All protocol contract addresses |
| `externalContracts` | `ExternalContracts` | Canonical mainnet token addresses |
| `riskParams` | `RiskParams` | On-chain risk parameters |

### Infrastructure

All protocol and Uniswap V4 infrastructure contract addresses.

| Field | Type | Description |
|-------|------|-------------|
| `brokerRouter` | `String` | Router for broker operations (deposit, withdraw) |
| `brokerExecutor` | `String` | Atomic multicall executor |
| `twammHook` | `String` | TWAMM hook contract |
| `bondFactory` | `String` | Bond mint/close factory |
| `basisTradeFactory` | `String` | Basis trade (Morpho flash loan) factory |
| `poolManager` | `String` | Uniswap V4 PoolManager |
| `v4Quoter` | `String` | Uniswap V4 Quoter |
| `v4PositionManager` | `String` | Uniswap V4 POSM for LP positions |
| `v4PositionDescriptor` | `String` | POSM NFT descriptor |
| `v4StateView` | `String` | State view for reading pool state |
| `universalRouter` | `String` | Uniswap Universal Router |
| `permit2` | `String` | Permit2 contract |
| `poolFee` | `Int` | Pool fee tier (500 = 0.05%) |
| `tickSpacing` | `Int` | Pool tick spacing |

### ExternalContracts

Canonical mainnet addresses for external tokens/protocols. These are the same addresses on both mainnet and the Anvil fork.

| Field | Type | Description |
|-------|------|-------------|
| `usdc` | `String` | USDC token |
| `ausdc` | `String` | Aave V3 aUSDC |
| `aavePool` | `String` | Aave V3 lending pool |
| `susde` | `String` | Ethena sUSDe token |
| `usdcWhale` | `String` | Whale address for simulation faucet |

### RiskParams

On-chain risk parameters read from `RLDCore.getMarketConfig()`.

| Field | Type | Description |
|-------|------|-------------|
| `minColRatio` | `Float` | Minimum collateral ratio (1.5 = 150%) |
| `maintenanceMargin` | `Float` | Liquidation threshold (1.1 = 110%) |
| `liqCloseFactor` | `Float` | Fraction closeable per liquidation (0.5 = 50%) |
| `fundingPeriodSec` | `Int` | Funding period in seconds |
| `debtCap` | `Int` | Maximum system debt |

---

## Integration Patterns

### 1. Frontend Dashboard (polling)

The recommended pattern for a React/Vue/Svelte frontend:

```javascript
// Combined query — single request fetches everything the UI needs
const DASHBOARD_QUERY = `{
  latest {
    blockNumber
    market { normalizationFactor totalDebt indexPrice blockTimestamp }
    pool { markPrice tick liquidity }
    brokers { address collateral debt healthFactor }
  }
  volume(hours: 24) { volumeUsd swapCount }
  status { lastIndexedBlock }
}`;

// Poll every 5 seconds
setInterval(async () => {
  const res = await fetch('http://localhost:8080/graphql', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: DASHBOARD_QUERY }),
  });
  const { data } = await res.json();
  updateUI(data);
}, 5000);
```

### 2. Startup Initialization

Fetch contract addresses once on app boot:

```javascript
const INIT_QUERY = `{
  marketInfo {
    collateral { name symbol address }
    positionToken { name symbol address }
    brokerFactory
    infrastructure {
      brokerRouter brokerExecutor twammHook bondFactory
      basisTradeFactory poolManager v4Quoter
    }
    externalContracts { usdc ausdc aavePool susde usdcWhale }
    riskParams { minColRatio maintenanceMargin liqCloseFactor }
  }
}`;

// Call once, cache locally
const { data } = await fetchGraphQL(INIT_QUERY);
const contracts = data.marketInfo;
```

### 3. Rate Charts (historical data)

```javascript
const RATE_CHART_QUERY = `{
  rates(symbols: ["USDC"], resolution: "1H", limit: 720) {
    symbol
    data { timestamp apy ethPrice }
  }
  ethPrices(resolution: "1H", limit: 720) {
    timestamp price
  }
}`;
```

### 4. User Position Tracking

```javascript
const USER_QUERY = `{
  lpPositions(brokerAddress: "${brokerAddr}") {
    tokenId liquidity tickLower tickUpper entryPrice isActive
  }
  bonds(owner: "${userAddr}") {
    brokerAddress status notionalUsd bondId
  }
  twammOrders(owner: "${userAddr}") {
    orderId amountIn sellRate expiration isCancelled
  }
}`;
```

### 5. Python Integration

```python
import requests

GRAPHQL_URL = "http://localhost:8080/graphql"

def query_indexer(query: str, variables: dict = None) -> dict:
    resp = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables})
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise Exception(f"GraphQL errors: {data['errors']}")
    return data["data"]

# Example: get latest state
result = query_indexer("{ latest { blockNumber market { totalDebt } pool { markPrice } } }")
print(f"Block: {result['latest']['blockNumber']}")
print(f"Debt: {int(result['latest']['market']['totalDebt']) / 1e6:.2f} USDC")
print(f"Price: {result['latest']['pool']['markPrice']:.4f}")
```

---

## Error Handling

### Response Format

GraphQL always returns HTTP 200 — check the `errors` array:

```json
{
  "data": { "latest": null },
  "errors": [
    {
      "message": "Database connection failed",
      "path": ["latest"],
      "locations": [{ "line": 1, "column": 3 }]
    }
  ]
}
```

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `marketInfo` returns `null` | Indexer hasn't discovered contracts yet | Wait for deployer to finish, indexer retries automatically |
| `latest.market` is `null` | No blocks indexed yet | Check `status.lastIndexedBlock` — if 0, indexer is still starting |
| `rates` returns empty `data` | Rates-indexer unreachable | Ensure `rates-indexer` container is running and healthy |
| `lpPositions` slow first call | Fallback to live RPC scan | Normal for new brokers — subsequent calls use DB cache |
| Very large `totalDebt` values | Wei encoding, not human-readable | Divide by `10^decimals` (6 for USDC) |

### Recommended Error Handling

```javascript
async function fetchGraphQL(query) {
  const res = await fetch('http://localhost:8080/graphql', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });

  const json = await res.json();

  if (json.errors) {
    console.error('GraphQL errors:', json.errors);
    // Partial data may still be available in json.data
  }

  return json;
}
```
