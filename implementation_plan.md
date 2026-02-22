# Implementation Plan: Comprehensive Cross-Asset Liquidation Testing

## Goal

To rigorously test the `PrimeBroker` and `RLDCore` liquidation flows across every possible combination of real collateral assets. This ensures that the two-phase `_unlockLiquidity` and `_sweepAssets` pipeline correctly calculates, unwinds, and extracts value without relying on mock contracts for the complex external DeFi integrations (Uniswap V4, TWAMM).

## Architecture Context: PrimeBroker Priority Queue

When `seize()` is called by `RLDCore`, the `PrimeBroker` fulfills the required value broadly in this priority:

1. **Cash (ERC20 Collateral)** - Direct held balance.
2. **wRLP (Position Token)** - Synthetic debt tokens used to directly cancel systemic debt.
3. **TWAMM Order** - Active Uniswap V4 TWAMM orders (canceled, unswapped+swapped returned to broker).
4. **V4 LP Position** - Uniswap V4 LP NFT via POSM (unwound proportionally to cover missing value).

## Testing Matrix & Paths to Cover

We will create a new dedicated integration test suite: `test/integration/LiquidationCrossAsset.t.sol`

### Phase 0: Setup, Registration & Boundary Validation - 5 Cases

Before liquidations can be tested, we must rigorously test the initialization, registration, and valuation boundary parameters of the underlying assets.

- [ ] **0a. Initial V4 Liquidity Provision**: Mints LP position across targeted price boundaries (in-range, out-of-range above, out-of-range below). Connects to POSM and asserts expected V4 pool state changes.
- [ ] **0b. V4 PrimeBroker Registration**: Ensures `setActiveV4Position()` correctly identifies NFT ownership, calculates NAV using the `V4ValuationModule`, and correctly ignores non-owned NFTs.
- [ ] **0c. Initial TWAMM Order Placement**: Places long-term TWAMM sell orders via the custom hook. Validates order keys, intervals, and swap rate mathematics.
- [ ] **0d. TWAMM PrimeBroker Registration**: Ensures `setActiveTwammOrder()` mathematically values the order using `TwammValuationModule` and enforces owner-matching.
- [ ] **0e. Price Oracle Divergence (TWAMM vs Spot)**: Asserts that if the TWAMM internal pool price aggressively diverges from the RLD Index/Spot Oracle, the NAV calculation handles the boundary shift smoothly without reverting.

### Tier 1: Single Asset Liquidations (Baseline Unwinding) - 8 Cases

- [ ] **1. Pure Cash**: Standard liquidation via pure balance transfer.
- [ ] **2. Pure wRLP**: Broker uses wRLP to unconditionally cancel systemic debt.
- **3. Pure TWAMM Unwinds (3 sub-cases)**:
  - [ ] **3a. Both Tokens Returned**: Liquidation cancels an active TWAMM where both unswapped input tokens and swapped output tokens are returned.
  - [ ] **3b. One Token Returned (Input Only)**: Order was just placed, no swaps executed yet. Returns only the input token.
  - [ ] **3c. One Token Returned (Output Only)**: Order fully executed but not withdrawn. Returns only the output token.
- **4. Pure V4 LP Unwinds (3 sub-cases)**:
  - [ ] **4a. Both Tokens Returned**: Position is highly active (in-range), unwinding returns a mix of Token0 and Token1.
  - [ ] **4b. Token0 Only Returned**: Position is entirely out-of-range (above current tick), unwinding yields 100% Token0.
  - [ ] **4c. Token1 Only Returned**: Position is entirely out-of-range (below current tick), unwinding yields 100% Token1.

### Tier 2: Priority Cascades & Fallbacks (Partial Unwinds) - 16 Cases

- [ ] **5. Cash + wRLP Waterfall**: Cash falls short; broker supplements the remainder by burning wRLP directly to Core.
- **6. Cash + TWAMM Waterfall (3 sub-cases)**: Cash falls short; TWAMM is canceled. Validates the combined extracted value satisfies the liquidator.
  - [ ] 6a. Cash + TWAMM (Both Tokens)
  - [ ] 6b. Cash + TWAMM (Input Only)
  - [ ] 6c. Cash + TWAMM (Output Only)
- **7. Cash + V4 LP Partial Unwind (3 sub-cases)**: Cash covers 30% of the seize amount. Broker unwinds _exactly_ enough V4 liquidity to cover the remaining 70%, preserving the rest of the LP position.
  - [ ] 7a. Cash + V4 LP (Both Tokens)
  - [ ] 7b. Cash + V4 LP (Token0 Only)
  - [ ] 7c. Cash + V4 LP (Token1 Only)
- **8. TWAMM + V4 LP Waterfall (9 sub-cases)**: User has no cash/wRLP. TWAMM is forcibly canceled, and the V4 position is partially unwound for the remaining balance. Tests every cross-product of token returns.
  - [ ] 8a-8c. TWAMM (Both) + {V4 Both, V4 Token0, V4 Token1}
  - [ ] 8d-8f. TWAMM (Input) + {V4 Both, V4 Token0, V4 Token1}
  - [ ] 8g-8i. TWAMM (Output) + {V4 Both, V4 Token0, V4 Token1}

### Tier 3: The "Everything" Edge Case - 9 Cases

- **9. The Full Stack Seizure (9 sub-cases)**: User holds Cash, wRLP, an active TWAMM order, and a V4 LP position. Liq requires all four asset types to be tapped sequentially. Asserts exact balance transitions across all 4 levels and verifies V4 unwinds only the trailing remainder.
  - [ ] 9a-9c. Cash + wRLP + TWAMM (Both) + {V4 Both, V4 Token0, V4 Token1}
  - [ ] 9d-9f. Cash + wRLP + TWAMM (Input) + {V4 Both, V4 Token0, V4 Token1}
  - [ ] 9g-9i. Cash + wRLP + TWAMM (Output) + {V4 Both, V4 Token0, V4 Token1}

### Tier 4: Structurally Insolvent Edge Cases - 9 Cases

- **10. Debt Squeeze (Negative Equity + Maximum Unwind) (9 sub-cases)**: User possesses all 4 asset types across every permutation of TWAMM/V4 token returns, but the total mathematical Net Account Value is strictly less than the required `seizeAmount`.
  - Asserts that TWAMM is completely canceled and V4 LP is entirely unwound (100% liquidity removed).
  - Asserts that `RLDCore` forces a proportional debt write-down (squashing the debt coverage to exactly match the extracted value) without reverting.
  - [ ] 10a-10c. Insolvent Stack + TWAMM (Both) + {V4 Both, V4 Token0, V4 Token1}
  - [ ] 10d-10f. Insolvent Stack + TWAMM (Input) + {V4 Both, V4 Token0, V4 Token1}
  - [ ] 10g-10i. Insolvent Stack + TWAMM (Output) + {V4 Both, V4 Token0, V4 Token1}

## Proposed Changes

### `test/integration/LiquidationCrossAsset.t.sol`

[NEW] `LiquidationCrossAsset.t.sol`
Will inherit from a base setup that deploys the _actual_ Uniswap V4 PoolManager, PositionManager, and the real TWAMM hooks.

**Test Setup Requirements:**

- Deploy V4 `PoolManager` and `PositionManager`.
- Deploy real `TWAMM` hook and initialize a V4 pool with it.
- Deploy real `DutchLiquidationModule`, `V4ValuationModule`, and `TwammValuationModule`.
- Helper functions: `_provideV4Liquidity()`, `_placeTwammOrder()`.

## User Review Required

> [!IMPORTANT]  
> Because we are using the real Uniswap V4 and TWAMM components, we need to ensure our test environment can compile and link against the correct V4 periphery versions. The worktree might need access to specifically pinned V4 dependencies if it doesn't already have them.

Please review this matrix. Once approved, I will build out the integration test suite.
