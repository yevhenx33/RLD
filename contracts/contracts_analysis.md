# RLD Contracts Architecture & Whitepaper Walkthrough

This document analyzes the RLD protocol's contract architecture, mapping the codebase directly to the financial primitives defined in the RLD Whitepaper (Synthetic Bonds, CDS, and Fixed-Income Structuring).

> [!IMPORTANT]
> **Implementation Status**: This walkthrough covers the v1 implementation. While Synthetic Bonds are fully supported, the CDS (Credit Default Swap) features described in the Whitepaper are partially implemented. The core "Instant Liquidation" mechanics exist, but the advanced "Global Settlement" and "Insurance Vault" safety features are not present in this version.

---

## 1. High-Level Architecture: The "Rate-Level Perp" Engine

The Whitepaper describes RLD as a "Collateralized Debt Position (CDP) architecture" that tracks interest rates. In the code, this is implemented via a Hub-and-Spoke model, separating Liability Management (Core) from Asset Management (Broker).

- **`RLDCore` (The Central Bank)**: The singleton state manager. It tracks the "Index Price" ($P = K \cdot r_t$) via the `normalizationFactor` and manages the debt of every user.
- **`PrimeBroker` (The Vault)**: A "Smart Margin Account" owned by the user. It holds all collateral and executes strategy logic.
- **Flash Accounting**: `RLDCore` enables "Leveraged Loops" (e.g., minting Short RLP and immediately resupplying proceeds) via the `lock()` mechanism, allowing capital efficiency without upfront capital.

---

## 2. Key Concepts & Code Mapping

### A. The PrimeBroker (The User's Vault)

_Concept_: Unlike standard protocols where users deposit assets into a giant pool, RLD uses an **Isolated Vault** model. Every position owner deploys their own smart contract wallet, called a `PrimeBroker`.

- **Identity**:
  - **NFT Ownership**: The `PrimeBroker` is owned by an NFT (ERC721). Transferring the NFT transfers control of the entire position (collateral + active strategies).
  - **Permissioned Entry**: `RLDCore` maintains a `BrokerVerifier` whitelist. Only valid `PrimeBroker` contracts created by the official Factory can open positions. This guarantees that `RLDCore` can trust the valuation logic reported by the Broker.
- **Interaction Flow (Audit Guide)**: When a user wants to modify their position (e.g., Mint Debt), the interaction follows a strict "Sandwich" pattern to ensure atomicity and solvency.
  1.  **Initiation**: User calls `PrimeBroker.modifyPosition(deltaCollateral, deltaDebt)`.
  2.  **Lock Entry**: The Broker calls `RLDCore.lock(data)`.
  3.  **State Update (Core)**: `RLDCore` records the Lock Holder and calls back the broker via `lockAcquired(data)`.
  4.  **Execution (Broker)**: Inside `lockAcquired`, `RLDCore` triggers the actual accounting changes (`modifyPosition` on itself).
      - If Debt is minted: `RLDCore` credits the debt balance.
      - If Collateral is moved: `RLDCore` approves transfers, but the Broker is responsible for physically moving the tokens to/from itself.
  5.  **Strategy Execution**: Still inside the lock, the Broker can swap the minted tokens or enter AMM positions (the "Flash Loop").
  6.  **Solvency Check**: `RLDCore` exits the lock. It calls `_checkSolvencyOfTouched()`.
  7.  **Valuation Report**: Core asks: `IPrimeBroker(user).getNetAccountValue()`. The Broker iterates its assets (Cash + TWAMM + V4 LP) and returns the total ETH/USD value.
  8.  **Finalization**: If `NAV >= DebtValue * Ratio`, the transaction succeeds.

### B. The Rate-Level Perp (RLP) Implementation

The RLP is designed as a **Rate-Tracking CDP System**. Users deposit collateral and mint "Short RLP" tokens, which represent debt that grows/shrinks with the Interest Rate.

#### 1. The CDP Mechanic (Collateralization)

- **Concept**: A User (Broker) must post assets > debt to ensure solvency.
- **Code Implementation**:
  - **Asset Custody**: The `PrimeBroker` contract physically holds the ERC20 collateral (e.g., aUSDC). It is an "Isolated Vault".
  - **Value Reporting**: `RLDCore` does not track collateral balances. Instead, it asks the Broker: "How much are you worth?" via the verified `getNetAccountValue()` call described above.

#### 2. Scaling the Interest Rate ($P = K \cdot r_t$)

- **Concept**: The "Price" of the RLP token is not determined by market buyers/sellers primarily, but by the **Oracle Feed** of the interest rate. $P = 100 \times Rate$.
- **Code Implementation**:
  - **Oracle Integration**: The `RLDMarketFactory` is configured with a `rateOracle` (e.g., Aave Wrapper).
  - **Debt Valuation**: When checking solvency or liquidating, `RLDCore` fetches the rate:
    ```solidity
    // RLDCore.sol
    uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(...);
    // indexPrice represents (100 * Rate)
    uint256 debtValue = trueDebt.mulWad(indexPrice);
    ```

#### 3. Continuous Funding (Normalization Factor)

- **Concept**: To align the User's Debt with the Market Price of RLP (if it deviates from Index), the protocol charges/pays Funding.
- **Code Implementation**:
  - **Normalization Factor**: `state.normalizationFactor` is a global scalar that starts at 1.0.
  - **Equation**: `True Debt = Base Debt * Normalization Factor`.
  - **Mechanism**: Every time a user touches the market, `_applyFunding(id)` is called. If Market Price > Index Price (Bullish), Funding is **Positive**, `normalizationFactor` **Decreases**, effectively paying Short sellers by shrinking their debt.

### C. Synthetic Bonds (Fixed Yield)

- **Whitepaper Concept**: Users "lock" a yield by minting Short RLP (which profits if rates drop) and using a **TWAMM Unwind** to linearly reduce the hedge size over time, matching the bond's duration (Section 3.3).
- **Code Implementation**: `PrimeBroker.sol` + `TWAMM.sol`.
  - **The Bond Structure**: A "Synthetic Bond" isn't a single token; it's a **state** of the `PrimeBroker`. The broker holds the collateral (e.g., aUSDC) and an **Active TWAMM Order** (`activeTwammOrder`).
  - **`TwammBrokerModule`**: This critical module (`TwammBrokerModule.sol`) allows `RLDCore` to value the running TWAMM order as collateral. It calculates `Value = Refund (Unsold) + Earnings (Bought)`. This allows the user to maintain solvency while their hedge linearly unwinds, perfectly matching the "programmable maturity" described in the paper.

### D. Dynamic Liquidation & Waterfall Seizure

- **Concept**: The safety valve. If Rate Spikes cause Insolvency, the protocol seizes collateral equal to the Bad Debt + a Dynamic Bonus.

#### Max Liquidation Share (Close Factor)

- **Verified Code Logic**: `RLDCore.sol` enforces a **50% Limit**. Even if insolvent, a liquidator can only seize half the user's debt per transaction (0.50e18), preventing instant total wipeouts.

#### Calculation (DutchLiquidationModule)

- **Mechanism**: A Health-Dependent Auction.
- **Formula**: $$ Bonus = BaseDiscount + Slope \times (1 - HealthScore) $$
- **Effect**: The worse the Health Score, the BIGGER the discount offered to liquidators. This ensures that the "riskiest" positions are attractive to clear first.

#### Execution Flow (The Interaction)

1.  **Trigger**: Liquidator calls `RLDCore.liquidate(targetUser, debtToCover)`.
2.  **Assessment**: `RLDCore` calculates `seizeAmount` via the Module.
3.  **Payment**: `RLDCore` pulls funds/tokens from Liquidator to repay the debt.
4.  **Seizure**: `RLDCore` commands `PrimeBroker.seize(seizeAmount)`.
    - **Waterfall Execution**: The `PrimeBroker` pays the liquidator using the "Least Destructive" asset first to minimize market impact:
      - **Priority 1 (Idle Cash)**: Zero market impact. Acts as a firewall.
      - **Priority 2 (TWAMM)**: Cancels active orders. Liquidity neutral (removes future pressure).
      - **Priority 3 (V4 LP)**: Unwinds Uniswap V4 positions. **Most Destructive**. This reduces the Liquidity Pool depth. It is done last to avoid triggering a "Death Spiral" of slippage and cascading liquidations.

### E. Service Components

- **CDS (Solvency Insurance)**: Partially implemented. Core payouts work (via Liquidation), but the "Global Settlement" (Section 6.2) described in the Whitepaper is not present in v1. We rely on market-driven Keepers.
- **Curator (Risk Manager)**:
  - **Definition**: The `curator` address defined in `RLDCore` storage.
  - **Powers (v1 Status)**: **None** (Phantom Role).
  - **Implementation**: While `RLDCore.sol` defines an `onlyCurator` modifier, a code audit reveals that no functions actually use this modifier. The `curator` field is initialized during `createMarket` but remains inert.
- **Immutability Analysis**:
  - **Risk Configs**: `minColRatio`, `maintenanceMargin` are Immutable. They cannot be updated.
  - **Oracles**: The `rateOracle` and `spotOracle` addresses are Immutable.
  - **Conclusion**: RLD v1 Markets are "Set and Forget". The protocol relies entirely on the initial parameterization being correct. If market conditions change drastically (requiring higher LTVs), a **New Market** must be deployed.

---

## 3. Comprehensive Interaction Walkthrough

### Scenario A: Market Deployment Process

_Goal: Deploy a new RLD Market for obtaining fixed yield on Aave USDC._

The deployment is orchestrated by the `RLDMarketFactory`. It is a complex, multi-step process that sets up the entire infrastructure for a new rate market.

1.  **Parameter Preparation (DeployParams)**: The deployer constructs a `DeployParams` struct.
    - `underlyingPool` (Aave V3), `underlyingToken` (USDC), `collateralToken` (aUSDC).
    - Risk Params (`minColRatio` 120%, `liquidationCloseFactor` 50%).
    - Oracles (Chainlink Spot, Aave Rate).
    - V4 Config (Fee 0.3%).
2.  **Execution Flow (`createMarket`)**:
    - **Phase 1 (Validation)**: Fail-fast checks (LTV > 100%).
    - **Phase 2 (Precompute)**: Deterministic ID generation.
    - **Phase 3 (Infrastructure)**: Deploys `PrimeBrokerFactory` and `BrokerVerifier` customized for this market.
    - **Phase 4 (Assets)**: Deploys the `wRLP` Clone token.
    - **Phase 5 (V4 Mechanics)**:
      - Fetches Rate. Calculates $P_{init}$.
      - Initializes V4 Pool (`IPoolManager.initialize`).
      - **Safety**: Sets Price Boundaries (1e-4 to 1e2) in TWAMM Hook.
    - **Phase 6 (Registration)**: Writes config to `RLDCore`. Transfers `wRLP` ownership to Core.

### Scenario B: Creating a "Synthetic Bond" (The Lender's Loop)

_Goal: Lock in a 10% yield on $100k aUSDC for 1 year, packaged as a tradable NFT Bond._

**Reference**: Whitepaper Section 3.3 ("Fixed-Rate Lending via Synthetic Bonds") and Section 3.4 ("Natural Over-Collateralization").

This process transforms a standard variable-rate lending position (aUSDC) into a **Fixed-Maturity, Fixed-Yield Instrument**. Crucially, in RLD, this "Bond" is not an ERC20 token but a **Smart Account (PrimeBroker)** wrapped in an NFT.

#### 1. Infrastructure Setup (The Vault)

- **Action**: User calls `PrimeBrokerFactory.createBroker()`.
- **Result**:
  - Deploys a new `PrimeBroker` contract (Cloned).
  - Mints an **ERC721 NFT** to the user. `TokenID` = Address of the Broker.
  - _Effect_: The user now owns a "shell" vault.

#### 2. Financial Engineering (The Structure)

To create the "Bond", the user executes a "Leveraged Loop" combined with a "Programmed Unwind".

- **Step A: Leverage (Minting Debt)**
  - User calls `broker.modifyPosition(deltaCollateral, deltaDebt)`.
  - **Flash Execution**: Inside `lockAcquired`, the broker mints **Short RLP** debt.
  - **Natural Hedge**: The minted RLP is sold for USDC, deposited into Aave for more aUSDC, and added as collateral. This creates the "Synthetic Short" exposure described in WP Section 3.4.

- **Step B: Maturity Programming (The TWAMM)**
  - User calls `broker.submitTwammOrder(...)` with:
    - `duration`: `31,536,000` (1 Year).
    - `amountIn`: The total logic size of the hedge.
  - **Mechanism**: This activates the `activeTwammOrder` in `PrimeBroker.sol`.
  - **Whitepaper Logic**: As time passes, the TWAMM linearly sells the Short RLP position. This reduces the debt exposure exactly in sync with the reducing "time-to-maturity", ensuring the "Bond" behaves correctly (duration approaches zero).

#### 3. Metadata "Labeling" (The Packaging)

- **Concept**: RLD v1 allows users to "Label" their structured product.
- **Action**: User calls `broker.setBondMetadata({...})`.
  - `bondType`: `FIXED_YIELD` (0).
  - `principal`: `100,000e6`.
  - `rate`: `1000` (10.00%).
  - `maturityDate`: `block.timestamp + 365 days`.
- **Visuals**: The `BondMetadataRenderer` reads this struct and generates an **On-Chain SVG** (Green background for Yield, displaying "10% Yield").
- **Tradeability**: The user can now sell the **NFT**. The buyer receives the _entire_ solvent position, active TWAMM unwind, and the "Bond" visual identity.

> [!WARNING]
> **Gap Analysis: User-Attested Metadata**
> A critical finding in `PrimeBroker.sol` (lines 297-303) is that the metadata is **Manual**.
>
> - **Current State**: The user _self-attests_ the Yield and Maturity via `setBondMetadata`. The protocol does _not_ verify that the active TWAMM order actually matches the claimed "10% Yield".
> - **Risk**: A malicious user could create a broker with 0% yield but set metadata claiming "100% Yield" to trick an NFT buyer.
> - **Resolution (V2)**: Future versions should auto-calculate `BondMetadata` by reading `activeTwammOrder.sellRate` and `debtPrincipal` directly, ensuring the label is "True to Code". For V1, buyers must verify the underlying `activeTwammOrder` state before purchasing the NFT.

### Scenario C: Default Event (CDS Payout)

> [!WARNING]
> **Not Implemented in V1**
> The manual code review confirms that the **Credit Default Swap (CDS) Payout Mechanism** described in the whitepaper (Section 6.2 "Global Settlement") is **NOT implemented** in the current codebase.
>
> - **Current Behavior**: Liquidation is purely handled by individual `LiquidationModule` calls. There is no protocol-wide "Default State" or "Insurance Fund Payout" logic present in `RLDCore`.
> - **Implication**: Protection relies entirely on over-collateralization and individual Keeper actions. There is no shared insurance pool payout in V1.

---

## 4. RLDCore Structure Breakdown (`src/rld/core/RLDCore.sol`)

### Key Logic Blocks

#### 1. Flash Accounting (`lock`)

- **Logic**: Uses `TransientStorage` to eliminate intermediate storage writes, saving significant gas.
- **Check**: `_checkSolvencyOfTouched()` ensures that strictly every account modified during the transaction ends up Solvenet. Leveraged loops are atomic.

#### 2. Position Management (`modifyPosition`)

- **Access**: Only callable by the `LockHolder` (the PrimeBroker).
- **Logic**:
  - Updates `pos.debtPrincipal`.
  - Calls `wRLP.mint()` or `burn()`.
  - Records the position as "Touched" for the end-of-tx solvency check.

#### 3. Solvency Engine (`_isSolvent`)

- **Equation**: `BrokerNAV >= DebtValue * MinCollateralRatio`.
- **Trust Model**: Core **trusts** `IPrimeBroker(user).getNetAccountValue()`.
  - _Security_: This relies on the `BrokerVerifier`. Core only interacts with Brokers deployed by the canonical Factory.

---

## 5. Module Architecture Walkthrough

The RLD System is highly modular. `RLDCore` delegates complex logic to specialized external contracts to maintain a clean codebase and allow for upgradeability/extensions.

### A. Liquidation Modules (`src/rld/modules/liquidation`)

_Purpose_: Determining "How much collateral to seize?" during a liquidation event.

#### 1. `DutchLiquidationModule.sol` (Primary)

This module implements a **Time-Decaying Dutch Auction** to efficiently find the fair market price for distressed assets without relying on potentially stale or manipulated standard, spot oracles.

- **Mechanism**:
  - The "Price" of the debt starts high (above market) and decays over time.
  - **Formula**: `Price(t) = OraclePrice * (1 + delta * decay(t))`.
  - Liquidators call `liquidate()` when the decayed price hits their profit target.
  - _Effect_: Ensures the protocol recovers the maximum possible value for the seized collateral, minimizing loss for the insolvent user (or bad debt for the system).
- **Interaction**: Called by `RLDCore.liquidate()`. It returns the `seizeAmount` (collateral to take) for a given `repayAmount` (debt burned).

### B. Broker Modules (`src/rld/modules/broker`)

_Purpose_: Extending `PrimeBroker` capabilities to hold and value complex assets (TWAMM positions, V4 LP Tokens) without bloating the main Broker contract.

#### 1. `TwammBrokerModule.sol`

- **Role**: Manages the valuation and seizure of active TWAMM orders.
- **Logic**:
  - **Value (`getValue`)**: Calculates the "Mark-to-Market" value of an ongoing TWAMM. It queries the `ITWAMM` hook to see how much has been sold/bought and pending earnings.
  - **Seizure (`seize`)**: When a broker is liquidated, this module knows how to **Cancel** the TWAMM order, retrieve the refund, and transfer it to the liquidator.
- **Interaction**: `PrimeBroker` delegates to this module in `getNetAccountValue()` and `seize()`.

#### 2. `UniswapV4BrokerModule.sol`

- **Role**: Manages Uniswap V4 Liquidity Positions held by the Broker.
- **Logic**:
  - **Value (`getValue`)**: Queries the Uniswap V4 `PositionManager` to get the underlying token amounts (Principal + Fees) for a specific NFT ID.
  - **Seizure (`seize`)**: Can burn/collect the LP position to pay off liquidators.

### C. Oracle Modules (`src/rld/modules/oracles`)

_Purpose_: Providing reliable price feeds and interest rates.

#### 1. `UniswapV4SingletonOracle.sol`

- **Role**: The bridge between RLD and Uniswap V4's TWAP (Time-Weighted Average Price).
- **Mechanism**:
  - Implements `ISpotOracle` interface.
  - Uses V4's `observe()` function to calculate the Geomean TWAP over a configured period (e.g., 30 minutes).
  - _Use Case_: Acts as the primary price feed for `RLDCore` solvency checks, resistant to flash-loan manipulation.

#### 2. `RLDAaveOracle.sol`

- **Role**: The "Rate Oracle".
- **Mechanism**:
  - Queries Aave V3's `getReserveData` to fetch the current **Liquidity Rate** (Supply APY).
  - This rate drives the Funding Payments in RLD. If Aave rates spike, RLD Insurers (Shorts) receive massive payouts from Longs.

### D. Funding Models (`src/rld/modules/funding`)

#### 1. `StandardFundingModel.sol`

- **Purpose**: translation of "Interest Rate" into "Funding Payment".
- **Logic**:
  - Inputs: `RateOracle` value, Time-delta.
  - Output: `normalizationFactor`.
  - Essentially calculates Compound Interest: `NewFactor = OldFactor * (1 + Rate * dt)`.

### E. Verifiers (`src/rld/modules/verifier`)

#### 1. `BrokerVerifier.sol`

- **Purpose**: Security Gatekeeper.
- **Logic**:
  - `RLDCore` trusts `PrimeBroker.getNetAccountValue()`. This is dangerous if a user acts as their own malicious broker.
  - `BrokerVerifier` maintains a registry of **Valid Factories**.
  - When a Broker interacts with Core, Core checks: `Verifier.verify(broker)`.
  - The Verifier confirms: "Yes, this Broker was deployed by the Official Factory (Verified Code)".
  - _Result_: Users can only use safe, audited Broker code to hold positions.

---

## 6. Uniswap V4 TWAMM Hook Deep Dive

_Architecture of the "Time Machine" (`contracts/src/twamm/TWAMM.sol`)_

The TWAMM (Time-Weighted Average Market Maker) Hook is the engine that allows RLD to offer "Programmed Maturity" for Synthetic Bonds. Instead of dumping a $1M short position instantly (crashing the price), it sells it smoothly over 1 year, block by block.

### A. The "Virtual Order" Mechanism

Traditional AMMs process swaps instantly. TWAMM processes them over time.

- **The Pools**: The Hook maintains two "Virtual Order Pools": `OrderPool0For1` and `OrderPool1For0`.
- **The Rate**: Each pool tracks a global `sellRate` (Tokens per Second).
  - _Example_: If Alice sells 100 tokens over 100 seconds, she adds `1 token/sec` to the rate.
- **Lazy Execution**: The EVM is not continuous. The Hook only updates when a user interacts with the pool (Swap, Add/Remove Liquidity).
  - On interaction, `executeTWAMMOrders()` calculates the `secondsElapsed` since the last update.
  - `AmountSold = sellRate * secondsElapsed`.
  - It nets the two pools (e.g., Pool A sells 100, Pool B sells 80 -> Net Sell 20 from Pool A).
  - It executes a **Single Swap** against the Uniswap V4 Pool for this net amount.

### B. Functionality & Flow

1.  **Order Submission (`submitOrder`)**:
    - User sends tokens to the Hook.
    - Hook adds `amount / duration` to the `sellRate`.
    - Hook records an `Order` struct with the user's `earningsFactor` (for claiming outputs later).
2.  **Execution (`executeTWAMMOrders`)**:
    - Triggered automatically via `beforeSwap`, `beforeAddLiquidity`, `beforeRemoveLiquidity`.
    - Moves price gradually, mimicking a continuous stream of orders.
3.  **Proactive Safety (`PriceBounds`)**:
    - Added in V4 Mechanics Phase 5.
    - **Logic**: In `_afterSwap`, the Hook checks `poolManager.getSlot0()`.
    - **Constraint**: If `SqrtPrice` deviates outside the `0.0001` - `100` range, the transaction reverts.
    - **Reason**: Prevents "Fat Finger" errors and malicious price manipulation attempts that could destabilize the Lending Market.

### C. Attack Surface & Mitigations

#### 1. "Sandwiching" the Time-Step

- **Attack**: A MEV searcher sees a transaction that will trigger a large TWAMM update (e.g., it's been 1 hour since the last touch). They know the TWAMM will sell a huge chunk.
  - _Step 1_: Front-run: Sell huge amount to push price down.
  - _Step 2_: User Tx executes -> TWAMM sells huge chunk at the _suppressed_ price.
  - _Step 3_: Back-run: Buy back profit.
- **Mitigation (RLD Approach)**:
  - **High Frequency Updates**: RLD encourages frequent interaction. The shorter the time delta, the smaller the TWAMM impact.
  - **Arb Interventions**: Keepers are incentivized to keep the pool price aligned with CEXs, reducing the profitability of pure manipulation.

#### 2. Liquidity Boundary Evasion

- **Attack**: A malicious user tries to add concentrated liquidity at price `$1,000,000` to manipulate the Oracle.
- **Mitigation**: The `_beforeAddLiquidity` hook explicitly checks the `tickLower` and `tickUpper`.
- **Code Enforcement**: `if (lowerSqrt < min || upperSqrt > max) revert("LP Range Out of Bounds");`. This makes it impossible to provision liquidity outside the safe bounds.

#### 3. Gas Griefing

- **Attack**: Submitting thousands of micro-orders to bloat the `sellRate` calculation.
- **Mitigation**: The `OrderPool` math is O(1) regardless of the number of users. It aggregates rates into a single global variable. Individual order management only costs gas for the specific user entering/exiting.

---

## 7. Operational Pipelines & Workflows

_Detailed breakdown of critical system flows beyond the basic lending loops._

### A. Funding Accrual Pipeline (`Lazy Update`)

Unlike traditional DeFi protocols that might use a `crank` or `accrueInterest()` cron job, RLD uses a **Lazy Update** pattern for efficiency.

1.  **Trigger**: `RLDCore._applyFunding()` is called _only_ when a user interacts with the market (via `modifyPosition` or `liquidate`).
2.  **Calculation**: It calculates the time delta (`block.timestamp - lastUpdateTimestamp`).
3.  **Update**: It multiplies the global `normalizationFactor` by the accumulated interest rate over that delta.
    - `NewFactor = OldFactor * (1 + Rate * dt)`
4.  **Effect**: Since `TrueDebt = Principal * NormalizationFactor`, simply updating this scalar instanly updates the debt balance of _every single user_ in the market, without iterating through them.

### B. Collateral Management (Aave Integration)

The Whitepaper describes "Natural Over-Collateralization" (depositing into Aave). This is handled via the **Generalized Execution** pipeline in `PrimeBroker`.

1.  **Action**: User calls `broker.execute(target, data)`.
2.  **Target**: `AaveAdapter` or Aave Pool address.
3.  **Data**: `supply(token, amount, brokerAddress, 0)`.
4.  **Result**: The Broker swaps raw USDC for aUSDC (Aave Interest Bearing Token).
5.  **Audit**: `RLDCore` doesn't explicitly know about this swap. However, since `PrimeBroker.getNetAccountValue()` counts all ERC20s (including aUSDC), the system automatically recognizes the new collateral value.

### C. V4 Liquidity Provisioning

Users can act as Limit LPs on Uniswap V4 via their Broker.

1.  **Approve**: User calls `broker.execute(...)` to approve the `UniswapV4PositionManager`.
2.  **Mint**: User calls `broker.execute(...)` to call `PositionManager.mint(...)`.
3.  **Custody**: The generic V4 NFT is minted to the `PrimeBroker` address.
4.  **Valuation**: The `UniswapV4BrokerModule` reads this NFT ID, queries the Uniswap State to get the underlying token amounts (Principals + Fees), and adds it to the Broker's Total Value.

### D. Position Token Lifecycle (wRLP)

The `PositionToken` (wRLP) represents the "Short Debt" of the user. It is Transferable Debt.

1.  **Minting**: When a user opens debt via `modifyPosition(..., +debt)`, `RLDCore` calls `wRLP.mint(user, debtAmount)`.
2.  **Transfer**: The user holds this ERC20 in their _personal wallet_ (not the Broker). They can sell it on an AMM.
3.  **Repayment**: To close the debt, the user must buy back wRLP.
4.  **Burning**: When calling `modifyPosition(..., -debt)`, `RLDCore` pulls wRLP from the user's wallet and calls `wRLP.burn`.
    - _Note_: If the user sold the wRLP, they must re-acquire it to unlock their collateral.

### E. Immutability (The "Fossilization" Pipeline)

RLD V1 Markets are designed to be "Set and Forget".

1.  **Deployment**: Parameters (`LTV`, `Oracle`, `LiquidationPenalty`) are set in `RLDMarketFactory.createMarket`.
2.  **Storage**: These are written to `marketConfigs[id]` in `RLDCore`.
3.  **Safety**: There is **NO** function in `RLDCore` to update `marketConfigs`. Even the `Curator` role has no power to change risk parameters or oracles on live markets.
4.  **Upgrade Path**: If parameters need to change (e.g., Aave Oracle deprecation), a **New Market** must be deployed, and users must migrate manually. This prevents governance attacks on live positions.

### F. Curator Protocol Fee (Treasury Pipeline)

The Curator (Owner) has granular control over protocol revenue.

1.  **Configuration**:
    - The Curator calls `TWAMM.setProtocolFee(poolKey, feePips)`.
    - **Constraint**: The fee is hard-capped at **0.05%** (`500 pips`) in the contract code, preventing extortion logic.
2.  **Collection (The Swap Tax)**:
    - On every **Exact Input** swap, the TWAMM hook intercepts the transaction in `_beforeSwap`.
    - It calculates `Fee = AmountIn * ProtocolFee`.
    - It explicitly transfers this fee from the User to the Hook contract.
    - _Note_: Exact Output swaps are currently exempt from this fee in V1 to maintain strict solvency safety.
3.  **Claiming**:
    - The Curator calls `TWAMM.claimProtocolFees(currency, recipient)`.
    - The contract pushes accumulated fees to the specified Treasury address.

---

## 8. Code Audit Findings (Verified v1.0)

This section documents specific findings from a line-by-line review of the codebase against the architecture verification.

### A. Security & Logic Verification

- **Liquidation Safety**: The `RLDCore.liquidate` function **strictly enforces** the `liquidationCloseFactor`. `if (debtToCover > principal * closeFactor) revert CloseFactorExceeded();`. This confirms the safety valve preventing total seizures in a single block.
- **Flash Lock Integrity**: `RLDCore` correctly uses `TransientStorage` keys (`LOCK_HOLDER_KEY`, `TOUCHED_COUNT_KEY`) to manage the lock lifecycle. Usage of `tstore`/`tload` ensures gas efficiency for the "Sandwich" operations.
- **Solvency Trust Model**: `RLDCore` relies entirely on `IPrimeBroker(user).getNetAccountValue()`. This highlights the critical importance of the `BrokerVerifier` which remains the **primary defense** against malicious brokers.

### B. Confirmed Implementation Gaps / Issues

1.  **Metadata Unit Mismatch (`BondMetadataRenderer.sol`)**:
    - _Issue_: `string.concat(meta.principal.toString(), " WEI")`.
    - _Finding_: The renderer hardcodes the suffix "WEI". If a user creates a bond with `100,000e6` (100k USDC), the UI will display "100000000000 WEI" instead of "100,000 USDC", potentially confusing buyers.
2.  **Unverified Metadata (`PrimeBroker.sol`)**:
    - _Issue_: `setBondMetadata`.
    - _Finding_: Confirmed that users can set any `BondMetadata` struct without validation. A user effectively "Labels" their own position.
3.  **No Global Settlement**:
    - _Finding_: Confirmed absence of `emergencyShutdown` or `globalSettlement` in `RLDCore`. The `CDS` protection mentioned in the Whitepaper relies solely on the `DutchLiquidationModule` incentives in V1.

### C. File Structure Validation

- The `contracts` folder structure perfectly matches the modular architecture described:
  - `src/rld/core`: `RLDCore`, `RLDMarketFactory`.
  - `src/rld/broker`: `PrimeBroker` (The isolated vault).
  - `src/twamm`: `TWAMM` (The hook).
  - `src/rld/modules`: Correctly separates `Liquidation`, `Broker`, `Oracle` logic.

### D. Conclusion

The codebase is a faithful implementation of the **Rate-Level Perp (RLP)** engine described in the Whitepaper. The core financial primitives (Flash Accounting, Hub-and-Spoke Debt, Modular Liquidation) are fully functional. The implementation gaps (Global Settlement, Auto-Metadata) are consistent with a V1 "MVP" scope.
