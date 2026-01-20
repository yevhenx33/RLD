# V4 Deployment Flow: Step-by-Step

This document details the execution flow of `RLDMarketFactory.deployMarketV4` for deploying a new Rate-Linked Product (RLP) market integrated with Uniswap V4.

## Initial Call Parameters

The deployment is initiated by calling `deployMarketV4` with the following parameters:

- **`underlyingPool`**: The lending protocol pool address (e.g., Aave V3 Pool) used to query borrow rates.
- **`underlyingToken`**: The asset being lent/borrowed (e.g., `USDC`).
- **`collateralToken`**: The collateral asset (e.g., `aUSDC`). Used for naming the position token and configuring the Core.
- **`marketType`**: The type of market, typically `RLP` (0).
- **`minColRatio`**: Minimum Collateralization Ratio (e.g., `1.2e18` for 120%).
- **`maintenanceMargin`**: Maintenance Margin threshold (e.g., `1.1e18` for 110%).
- **`liquidationModule`**: Address of the contract handling liquidations (e.g., `StaticLiquidationModule`).
- **`liquidationParams`**: Configuration bytes for the liquidation module (optional).
- **`spotOracle`**: Address of the Spot Oracle (e.g., Chainlink) for the collateral token.
- **`rateOracle`**: Address of the Rate Oracle (e.g., `RLDAaveOracle`) that converts borrow rates to Index Prices.
- **`oraclePeriod`**: Time Weighted Average Price (TWAP) period in seconds for the Singleton Oracle (e.g., `3600`).
- **`poolFee`**: Uniswap V4 Pool Fee tier (e.g., `3000` for 0.3%).
- **`tickSpacing`**: Uniswap V4 Tick Spacing (e.g., `60`).

---

## Detailed Execution Steps

### Step 1: Deploy Position Token (`WrappedRLP`)

The factory creates a cloned token contract representing the user's debt/position.

1.  **Clone**: `Clones.clone(WRAPPED_RLP_IMPL)` creates a lightweight proxy pointing to the `WrappedRLP` implementation.
2.  **Fetch Symbol**: The factory calls `ERC20(collateralToken).symbol()` to get the collateral's symbol (e.g., `"aUSDC"`).
3.  **Initialize**: The clone is initialized with:
    - `_underlying`: The `underlyingToken` address (`USDC`).
    - `_collateralSymbol`: The fetched symbol (`"aUSDC"`).
4.  **Result**:
    - New Token Name: `"Wrapped RLP aUSDC"`
    - New Token Symbol: `"wRLPaUSDC"`

### Step 2: Configure V4 Pool Parameters

The factory prepares the deterministic unique key for the Uniswap V4 Pool.

1.  **Wrap Addresses**: The contract converts token addresses into the V4 `Currency` type.
    - `currency0`: The lower address between `wRLPaUSDC` and `USDC`.
    - `currency1`: The higher address.
2.  **Create PoolKey**: A `PoolKey` struct is constructed with:
    - `currency0` & `currency1` (Sorted)
    - `fee`: `poolFee` parameter.
    - `tickSpacing`: `tickSpacing` parameter.
    - `hooks`: The address of the immutable TWAMM Hook contract.

### Step 3: Calculate Initial Price (`initSqrtPrice`)

The factory automatically determines the correct starting price for the pool based on the current lending rate.

1.  **Query Oracle**: Calls `IRLDOracle(rateOracle).getIndexPrice(underlyingPool, underlyingToken)`.
    - **Input**: Aave Pool Address, USDC Address.
    - **Output**: `indexPrice` in WAD (18 decimals).
    - _Example_: A 4.5% rate returns `4.5e18`.
2.  **Determine Direction**: Uniswap V4 prices are always expressed as `Amount1 / Amount0`.
    - **If `wRLPaUSDC` is Token0 (Base)**: Price = `indexPrice`. (1 wRLP buys X USDC).
    - **If `wRLPaUSDC` is Token1 (Quote)**: Price = `1 / indexPrice`. (1 USDC buys Y wRLP).
3.  **Math Calculation**:
    - Take Square Root: `sqrt(Price)`.
    - Convert to Q96 Format: `(Root * 2^96) / 1e9` (Adjusting for 18-decimal fixed point math).
4.  **Result**: `initSqrtPrice` ready for pool initialization.

### Step 4: Initialize Uniswap Pool

The factory explicitly initializes the pool on the `PoolManager`.

1.  **Call**: `poolManager.initialize(key, initSqrtPrice)`.
    - This creates the pool state in the Uniswap V4 Core contract.
    - The pool is now ready for trading, with the price pegged to the current interest rate.
2.  **Output**: Returns the current tick, confirming the pool exists.

### Step 5: Register with Singleton Oracle

The factory registers the new pool with the RLD Singleton Oracle to enable TWAP tracking.

1.  **Call**: `UniswapV4SingletonOracle(SINGLETON_V4_ORACLE).registerPool(...)`.
    - `asset`: The `wRLPaUSDC` address (identifies the market).
    - `poolKey`: The unique V4 `PoolKey`.
    - `hook`: The TWAMM Hook address (source of observations).
    - `period`: The configured `oraclePeriod` (e.g., 3600 seconds).

### Step 6: Create Market in Core

The factory officially registers the market logic in the central `RLDCore` contract.

1.  **Construct `MarketAddresses`**:
    - `collateralToken`: `aUSDC`.
    - `underlyingToken`: `USDC`.
    - `rateOracle`: The passed Aave Oracle adapter.
    - `spotOracle`: The passed Chainlink Oracle adapter.
    - **`markOracle`**: Hardcoded to the `SINGLETON_V4_ORACLE`.
    - `liquidationModule`: The explicit module address.
    - `positionToken`: The newly deployed `wRLPaUSDC`.
2.  **Construct `MarketConfig`**:
    - Sets `minColRatio`, `maintenanceMargin`, etc.
3.  **Call**: `CORE.createMarket(addresses, config)`.
4.  **Output**: Returns a unique `marketId`.

### Step 7: Finalize & Link

The factory hands over control of the position token to the Core protocol.

1.  **Link ID**: Calls `WrappedRLP(wRLPaUSDC).setMarketId(marketId)`.
    - Binds the token to its specific market logic.
2.  **Transfer Owner**: Calls `WrappedRLP(wRLPaUSDC).transferOwnership(CORE)`.
    - **Critical**: Only `RLDCore` can now mint or burn `wRLPaUSDC`. The factory relinquishes control.
3.  **Return**: The function concludes by returning the `marketId` and relevant addresses to the caller.
