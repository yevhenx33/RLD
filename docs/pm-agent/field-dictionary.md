# Field Dictionary — Canonical Definitions

> Authoritative reference for every field returned by the RLD Analytics GraphQL API. Use this to avoid misinterpreting units, edge cases, or null semantics.

---

## Universal Fields

### `timestamp`
- **Type:** `Int` (Unix seconds)
- **Note:** Never milliseconds. Multiply by 1000 for JavaScript `Date` constructor.
- **Edge case:** Can be `0` for genesis-block events or system-generated rows.

### `entityId`
- **Type:** `String`
- **Format varies by protocol:**
  - **Aave:** `0x{reserve_address}` (lowercase, 42-char hex)
  - **Morpho:** `0x{market_hash}` (64-char market ID from `keccak256(params)`)
  - **Fluid:** `0x{token_address}` (lowercase, 42-char hex)
- **Used for:** URL routing, SWR cache keys, API lookups

### `protocol`
- **Type:** `String` (LowCardinality)
- **Valid values:** `"AAVE_MARKET"`, `"MORPHO_MARKET"`, `"FLUID_MARKET"`
- **Never:** `"AAVE"`, `"Aave"`, `"aave"` (these are frontend display labels, not API constants)

---

## Financial Metrics

### `supplyUsd` / `borrowUsd`
- **Type:** `Float64`
- **Unit:** USD, absolute value
- **Precision:** Sub-cent precision retained from ClickHouse
- **Edge cases:**
  - Can be `0.0` for brand-new markets with no activity
  - Can be slightly negative (`-0.001`) due to floating-point rounding in state reconstruction — always clamp: `Math.max(0, value)`
  - Morpho markets with `oracleSupport: "UNSUPPORTED"` will have `supplyUsd = 0` even with non-zero token balances

### `supplyApy` / `borrowApy`
- **Type:** `Float64`
- **Unit:** Decimal fraction (0.05 = 5%)
- **Display:** Multiply by 100 and append `%`
- **Range:** Typically 0.0001–0.50 (0.01%–50%). Can exceed 1.0 (100%) during utilization spikes.
- **Edge cases:**
  - `0.0` for markets with no borrows (borrow APY) or no utilization
  - Can be `NaN` in rare cases — always guard: `Number(x) || 0`

### `utilization`
- **Type:** `Float64`
- **Unit:** Ratio (0–1)
- **Display:** Multiply by 100 and append `%`
- **Derivation:** `borrow_usd / supply_usd`
- **Edge cases:**
  - `0.0` when supply is zero
  - Can exceed `1.0` briefly during liquidations (borrow > supply due to bad debt)
  - Always clamp: `Math.max(0, Math.min(1, value))`

### `priceUsd`
- **Type:** `Float64`
- **Unit:** USD per 1 unit of the underlying token
- **Source:** Chainlink oracle snapshots (Aave, Morpho) or protocol-native oracles (Fluid)
- **Edge cases:**
  - `0.0` for tokens without supported price feeds
  - Stale prices if oracle hasn't updated — check `lastPricedTimestamp`

### `netWorth`
- **Type:** `Float64`
- **Unit:** USD
- **Derivation:** `supplyUsd - borrowUsd` (computed server-side)
- **Only available in:** `lendingDataPage.markets`

---

## Aave-Specific Risk Fields

### `ltv`
- **Type:** `Float64`
- **Unit:** Ratio (0–1), e.g. 0.80 = 80%
- **Meaning:** Maximum loan-to-value ratio before liquidation eligibility

### `liquidationThreshold`
- **Type:** `Float64`
- **Unit:** Ratio (0–1)
- **Meaning:** Health factor trigger point. If weighted collateral / debt crosses this, liquidation is permitted.

### `liquidationPenalty`
- **Type:** `Float64`
- **Unit:** Ratio, e.g. 0.05 = 5% penalty
- **Meaning:** Bonus paid to liquidators

### `emodeCategory`
- **Type:** `UInt8`
- **Values:** `0` = standard mode, `1`+ = E-Mode category ID
- **E-Mode unlocks higher LTV/thresholds for correlated assets** (e.g. ETH/stETH)

### `emodeLtv` / `emodeLiquidationThreshold` / `emodeLiquidationPenalty`
- Same semantics as base fields, but apply when user is in E-Mode
- Only meaningful when `emodeCategory > 0`

### `emodeLabel`
- **Type:** `String`
- **Examples:** `"ETH correlated"`, `"Stablecoins"`
- **Default:** `""` when not in E-Mode

### `healthFactor`
- **Type:** `Float64 | null`
- **Unit:** Dimensionless ratio
- **Interpretation:**
  - `> 1.0` = Safe
  - `= 1.0` = Liquidation threshold
  - `< 1.0` = Eligible for liquidation
  - `null` = No debt (infinite health factor)
- **Display:** Show `∞` or `—` for null values

---

## Morpho-Specific Fields

### `lltv`
- **Type:** `Float64`
- **Unit:** Ratio (0–1)
- **Meaning:** Liquidation LTV — the single threshold that triggers liquidation in Morpho Blue
- **Key difference from Aave:** Morpho has no separate `ltv` and `liquidationThreshold` — just one `lltv`

### `oracleSupport` / `pricingStatus`
- **Type:** `String`
- **Values:** `"CHAINLINK"`, `"UNSUPPORTED"`, `"PARTIAL"`
- `UNSUPPORTED` means USD pricing is unavailable — `supplyUsd`/`borrowUsd` will be `0.0`
- Use this to show/hide price-dependent UI or display warning badges

### `collateralSymbol` / `collateralUsd`
- **Type:** `String` / `Float64`
- **Meaning:** Morpho markets are isolated pairs (loan asset ↔ collateral asset)
- Only populated for Morpho and Fluid markets

### `supplyAssets` / `borrowAssets` / `collateralAssets`
- **Type:** `String` (uint256-scale raw values)
- **Unit:** Raw token amounts at native decimals
- **Use case:** Precise on-chain calculations. For display, prefer `supplyUsd`/`borrowUsd`.

### `irm`
- **Type:** `String` (Ethereum address)
- **Meaning:** Interest Rate Model contract address

---

## Flow Fields (Aave Only)

### `supplyInflowUsd` / `supplyOutflowUsd`
- **Type:** `Float64`
- **Unit:** USD
- **Meaning:** Total deposit (inflow) and withdrawal (outflow) volume in the period
- **Convention:** Outflows are returned as positive values from the API. The frontend negates them for charting: `supplyOutflowUsd: -Math.abs(value)`

### `netSupplyFlowUsd` / `netBorrowFlowUsd`
- **Type:** `Float64`
- **Unit:** USD
- **Meaning:** `inflow - outflow` for the period. Can be negative.

### `cumulativeSupplyNetInflowUsd` / `cumulativeBorrowNetInflowUsd`
- **Type:** `Float64`
- **Unit:** USD
- **Meaning:** Running sum of net flows from the beginning of the returned timeseries
- **Note:** The API provides pre-computed cumulative values. The frontend falls back to client-side accumulation if the API value is `NaN`.

---

## Pendle Fields

### `assetType`
- **Type:** `String`
- **Values:** `"PT"` (Principal Token), `"YT"` (Yield Token), `"SY"` (Standardized Yield)

### `expiry`
- **Type:** `Int` (Unix timestamp)
- **Meaning:** When the Pendle market matures. After expiry, `matured = true`.

### `open` / `high` / `low` / `close` / `volume`
- **Type:** `Float64`
- **Unit:** USD (price) / USD (volume)
- **Resolution:** Depends on `timeFrame` parameter: `"hour"`, `"day"`, `"week"`

---

## MetaMorpho Vault Fields

### `totalAssets` / `totalSupply`
- **Type:** `String` (uint256-scale)
- **Meaning:** Total underlying assets in vault / total vault shares outstanding

### `sharePriceUsd`
- **Type:** `Float64`
- **Derivation:** `(totalAssets / totalSupply) * assetPriceUsd`

### `isCanonicalTvl`
- **Type:** `Boolean`
- **Meaning:** `true` if this vault's TVL should be counted in protocol-level aggregates (avoids double-counting with underlying Morpho markets)

### `allocationShare`
- **Type:** `Float64`
- **Unit:** Ratio (0–1)
- **Meaning:** What fraction of the vault's total assets are allocated to a specific Morpho market

---

## Fluid Product Fields

### `productType`
- **Type:** `String`
- **Values:** `"FTOKEN"`, `"VAULT"`, `"DEX"`, `"REVENUE"`, `"STETH"`

### `liquidityUsd` / `volumeUsd` / `feesUsd`
- **Type:** `Float64`
- **Only available for:** DEX product type

### `positionCount`
- **Type:** `Int`
- **Meaning:** Number of active positions in the product

### `snapshotStatus`
- **Type:** `String`
- **Values:** `"OK"`, `"ERROR"`, `"STALE"`
- **Use for:** Displaying data quality badges

---

## Freshness Envelope

Every page-level query includes a `freshness` block:

```graphql
freshness {
  ready         # Boolean — true if data is considered current
  status        # "ready" | "stale" | "degraded"
  generatedAt   # Unix timestamp of when the response was assembled
}
```

**Frontend behavior:**
- `ready = true` → show data normally
- `ready = false` → show data with a "stale data" warning badge
- `status = "degraded"` → some protocols may have gaps; show per-protocol coverage

---

## Null / Missing Value Conventions

| Field Type | Null Meaning | Safe Default |
|-----------|-------------|-------------|
| `Float64` | Not applicable or not computed | `0.0` |
| `String` | Empty or not applicable | `""` |
| `Int` | Not applicable | `0` |
| `Boolean` | Not applicable | `false` |
| `healthFactor` | No debt (infinite HF) | Display as `∞` or `—` |
| `collateralSymbol` | Not a paired market (Aave) | `null` — hide collateral column |
| `oracleSupport` | Not evaluated | `null` — hide oracle badge |
