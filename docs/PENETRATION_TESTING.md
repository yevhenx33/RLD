# Penetration Testing Plan: Broker & Periphery Contracts

Step-by-step attack surface analysis for untested contracts. Liquidation/seize paths are **already covered** by `test/integration/liquidation/` (11 test files). This document focuses on everything else.

## Target Contracts

| Contract                                                                                                          | Lines | Priority            |
| ----------------------------------------------------------------------------------------------------------------- | ----- | ------------------- |
| [`PrimeBroker.sol`](file:///home/ubuntu/RLD/contracts/src/rld/broker/PrimeBroker.sol)                             | 1,229 | 🔴 Critical         |
| [`BrokerRouter.sol`](file:///home/ubuntu/RLD/contracts/src/periphery/BrokerRouter.sol)                            | 551   | 🟠 High             |
| [`BrokerExecutor.sol`](file:///home/ubuntu/RLD/contracts/src/periphery/BrokerExecutor.sol)                        | 114   | 🟡 Medium           |
| [`LeverageShortExecutor.sol`](file:///home/ubuntu/RLD/contracts/src/periphery/LeverageShortExecutor.sol)          | 162   | 🟡 Medium           |
| [`JitTwammBrokerModule.sol`](file:///home/ubuntu/RLD/contracts/src/rld/modules/broker/JitTwammBrokerModule.sol)   | 372   | 🟡 Medium           |
| [`UniswapV4BrokerModule.sol`](file:///home/ubuntu/RLD/contracts/src/rld/modules/broker/UniswapV4BrokerModule.sol) | 131   | 🟢 Low              |
| [`TwammBrokerModule.sol`](file:///home/ubuntu/RLD/contracts/src/rld/modules/broker/TwammBrokerModule.sol)         | 165   | 🟢 Low (deprecated) |

---

## Phase 1: PrimeBroker — Initialization & Access Control

> The broker is where user funds live. Every public/external function is an attack surface.

### 1.1 Clone Initialization

| #   | Test                                              | Attack Vector                                                                                                                                       | Severity    |
| --- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| 1   | `initialize()` reverts on second call             | Re-initialization hijack: attacker calls `initialize()` on an already-live broker to overwrite `CORE`, `marketId`, `collateralToken`, steal funds   | 🔴 Critical |
| 2   | `initialize()` reverts with `_core == address(0)` | Zero-core hijack: broker accepts zero core, all solvency checks bypass                                                                              | 🔴 Critical |
| 3   | `initialize()` sets all cached fields correctly   | Verify `marketId`, `collateralToken`, `positionToken`, `underlyingToken`, `underlyingPool`, `rateOracle`, `hook` are all correctly cached from Core | 🟠 High     |
| 4   | `initialize()` pre-approves `_initialOperators`   | BrokerRouter should be auto-operator after init                                                                                                     | 🟡 Medium   |
| 5   | Implementation template has `CORE = address(1)`   | Direct `initialize()` on the un-cloned implementation must revert                                                                                   | 🔴 Critical |

### 1.2 Ownership via NFT

| #   | Test                                                                  | Attack Vector                                                          | Severity    |
| --- | --------------------------------------------------------------------- | ---------------------------------------------------------------------- | ----------- |
| 6   | `onlyOwner` modifier checks `IERC721(factory).ownerOf(brokerAddress)` | Verify only NFT holder can call owner-only functions                   | 🔴 Critical |
| 7   | NFT transfer changes broker owner                                     | After NFT transfer, old owner loses access, new owner gains access     | 🟠 High     |
| 8   | `onlyAuthorized` allows both owner AND operators                      | Operator can call authorized functions; non-operator/non-owner reverts | 🟠 High     |

### 1.3 Operator Management

| #   | Test                                                           | Attack Vector                                                         | Severity    |
| --- | -------------------------------------------------------------- | --------------------------------------------------------------------- | ----------- |
| 9   | `setOperator()` — only owner can add operators                 | Attacker sets themselves as operator                                  | 🔴 Critical |
| 10  | `setOperator()` — operators can only revoke **themselves**     | Operator tries to add another operator or revoke a different operator | 🔴 Critical |
| 11  | `setOperator()` — non-owner non-operator reverts               | Random address tries `setOperator`                                    | 🟠 High     |
| 12  | `setOperatorWithSignature()` — valid signature grants operator | Happy path: owner signs, executor becomes operator                    | 🟠 High     |
| 13  | `setOperatorWithSignature()` — replayed signature reverts      | Nonce must increment; same signature fails on second use              | 🔴 Critical |
| 14  | `setOperatorWithSignature()` — forged signature reverts        | Wrong signer, wrong broker address, wrong nonce                       | 🔴 Critical |
| 15  | `setOperatorWithSignature()` — cross-broker signature rejected | Signature for broker A used on broker B                               | 🔴 Critical |
| 16  | `operatorNonces` increments correctly per caller               | Each executor has independent nonce counter                           | 🟡 Medium   |

---

## Phase 2: PrimeBroker — Position Management

### 2.1 `modifyPosition()` (Lock Pattern)

| #   | Test                                               | Attack Vector                                          | Severity    |
| --- | -------------------------------------------------- | ------------------------------------------------------ | ----------- |
| 17  | Happy path: deposit collateral + mint debt         | Verify Core state, wRLP minted, collateral transferred | 🟠 High     |
| 18  | Happy path: repay debt + withdraw collateral       | Verify debt burned, collateral returned                | 🟠 High     |
| 19  | `onlyAuthorized` guard                             | Non-owner/non-operator reverts                         | 🟠 High     |
| 20  | Solvency check after modification                  | Increasing debt beyond maintenance margin reverts      | 🔴 Critical |
| 21  | Lock callback: only Core can call `lockAcquired()` | Attacker calls `lockAcquired()` directly → must revert | 🔴 Critical |
| 22  | Reentrancy guard on `modifyPosition()`             | Attempt reentrant call during lock callback            | 🔴 Critical |
| 23  | Zero-delta modification is no-op                   | `modifyPosition(0, 0)` doesn't change state            | 🟢 Low      |

### 2.2 Token Withdrawals

| #   | Test                                                         | Attack Vector                                | Severity    |
| --- | ------------------------------------------------------------ | -------------------------------------------- | ----------- |
| 24  | `withdrawCollateral()` — post-withdrawal solvency check      | Withdraw that makes broker insolvent reverts | 🔴 Critical |
| 25  | `withdrawCollateral()` — happy path transfers correct amount | Recipient receives exact amount              | 🟠 High     |
| 26  | `withdrawPositionToken()` — post-withdrawal solvency check   | Same as above but for wRLP                   | 🔴 Critical |
| 27  | `withdrawUnderlying()` — post-withdrawal solvency check      | Same for underlying token                    | 🔴 Critical |
| 28  | `withdrawCollateral()` — `onlyAuthorized` guard              | Non-owner/non-operator cannot withdraw       | 🔴 Critical |
| 29  | Withdraw more than balance reverts                           | Underflow/ERC20 revert                       | 🟡 Medium   |

---

## Phase 3: PrimeBroker — Position Tracking (NAV)

### 3.1 `setActiveV4Position()`

| #   | Test                                                      | Attack Vector                                                         | Severity    |
| --- | --------------------------------------------------------- | --------------------------------------------------------------------- | ----------- |
| 30  | Cannot track a V4 NFT owned by someone else               | Attacker sets `activeTokenId` to a whale's LP position to inflate NAV | 🔴 Critical |
| 31  | Ownership check: `POSM.ownerOf(tokenId) == address(this)` | Must verify broker actually holds the NFT                             | 🔴 Critical |
| 32  | Post-update solvency check                                | Switching from large to small LP position triggers insolvency check   | 🟠 High     |
| 33  | Setting `tokenId = 0` clears tracking                     | Verify NAV drops by LP value; solvency re-checked                     | 🟡 Medium   |
| 34  | `onlyAuthorized` guard                                    | Non-owner/non-operator reverts                                        | 🟠 High     |

### 3.2 `setActiveTwammOrder()`

| #   | Test                                                  | Attack Vector                                                           | Severity    |
| --- | ----------------------------------------------------- | ----------------------------------------------------------------------- | ----------- |
| 35  | Cannot track a TWAMM order owned by someone else      | Attacker sets `activeTwammOrder` to another user's order to inflate NAV | 🔴 Critical |
| 36  | Ownership check: `orderKey.owner == address(this)`    | Verify broker is actual order owner                                     | 🔴 Critical |
| 37  | Post-update solvency check                            | Switching orders triggers solvency verification                         | 🟠 High     |
| 38  | `clearActiveV4Position()` + `clearActiveTwammOrder()` | Clearing tracked positions + solvency check                             | 🟡 Medium   |

### 3.3 `submitTwammOrder()` / `cancelTwammOrder()`

| #   | Test                                                       | Attack Vector                                                      | Severity    |
| --- | ---------------------------------------------------------- | ------------------------------------------------------------------ | ----------- |
| 39  | `submitTwammOrder()` — happy path with auto-tracking       | Order submitted, `activeTwammOrder` set, solvency checked          | 🟠 High     |
| 40  | `submitTwammOrder()` — JIT approval and revoke             | Sell token approval granted to hook, then revoked after submission | 🟠 High     |
| 41  | `submitTwammOrder()` — no lingering allowances             | After submission, hook should have zero allowance                  | 🔴 Critical |
| 42  | `cancelTwammOrder()` — returns proceeds + refund to broker | Both buy and sell tokens returned                                  | 🟠 High     |
| 43  | `cancelTwammOrder()` — clears `activeTwammOrder`           | Tracking state reset after cancel                                  | 🟡 Medium   |
| 44  | `cancelTwammOrder()` — reverts with "No active order"      | Cancel with no tracked order                                       | 🟡 Medium   |

### 3.4 `getNetAccountValue()` correctness

| #   | Test                                                | Attack Vector                                 | Severity    |
| --- | --------------------------------------------------- | --------------------------------------------- | ----------- |
| 45  | NAV = cash + wRLP value + V4 LP value + TWAMM value | All four components sum correctly             | 🔴 Critical |
| 46  | NAV with zero debt, zero assets = 0                 | Empty broker returns 0                        | 🟢 Low      |
| 47  | NAV counts wRLP at index price (not 1:1)            | wRLP balance × Aave index price               | 🟠 High     |
| 48  | NAV ignores V4 LP if ownership check fails          | LP NFT transferred away → value drops to 0    | 🔴 Critical |
| 49  | NAV ignores TWAMM if ownership check fails          | Order cancelled externally → value drops to 0 | 🔴 Critical |

---

## Phase 4: BrokerRouter — Trading Flows

> Router is a pre-approved operator on every broker. It handles USDC→waUSDC wrapping and V4 swaps.

### 4.1 Deposit Flows

| #   | Test                                                                        | Attack Vector                                | Severity    |
| --- | --------------------------------------------------------------------------- | -------------------------------------------- | ----------- |
| 50  | `deposit()` — full wrapping pipeline: USDC → Aave → aUSDC → waUSDC → broker | Token amounts match at each hop              | 🟠 High     |
| 51  | `deposit()` — Permit2 signature validation                                  | Invalid signature reverts                    | 🟠 High     |
| 52  | `depositWithApproval()` — same pipeline, standard ERC20 approve             | Happy path                                   | 🟡 Medium   |
| 53  | `deposit()` — `onlyBrokerAuthorized` guard                                  | Non-owner/non-operator of the broker reverts | 🔴 Critical |
| 54  | `deposit()` — router never holds user funds after tx                        | Router balances are zero post-call           | 🟠 High     |
| 55  | `deposit()` — invalid deposit route reverts                                 | Unregistered collateral token → revert       | 🟡 Medium   |
| 56  | `setDepositRoute()` — only owner can set routes                             | Non-owner reverts                            | 🟠 High     |

### 4.2 Long Flow (collateral → wRLP)

| #   | Test                                                                       | Attack Vector                      | Severity    |
| --- | -------------------------------------------------------------------------- | ---------------------------------- | ----------- |
| 57  | `executeLong()` — happy path: withdraw collateral → V4 swap → deposit wRLP | End-to-end balance changes correct | 🟠 High     |
| 58  | `executeLong()` — `onlyBrokerAuthorized` guard                             | Non-authorized reverts             | 🔴 Critical |
| 59  | `executeLong()` — router never holds tokens post-tx                        | No token residuals in router       | 🟠 High     |
| 60  | `closeLong()` — swap wRLP back to collateral                               | Reverse of executeLong             | 🟠 High     |

### 4.3 Short Flow (mint debt → sell wRLP → deposit proceeds)

| #   | Test                                                                                            | Attack Vector                                      | Severity    |
| --- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------- | ----------- |
| 61  | `executeShort()` — atomic: deposit collateral + mint debt + swap wRLP → collateral + re-deposit | All steps in one tx                                | 🟠 High     |
| 62  | `executeShort()` — solvency holds after entire operation                                        | Post-short position is solvent                     | 🔴 Critical |
| 63  | `closeShort()` — buy back wRLP, repay debt                                                      | Debt reduced, excess collateral returned to broker | 🟠 High     |
| 64  | `closeShort()` — leftover collateral returned to broker (not router)                            | No residuals in router                             | 🟠 High     |

### 4.4 V4 Callback Security

| #   | Test                                                                  | Attack Vector                                                        | Severity    |
| --- | --------------------------------------------------------------------- | -------------------------------------------------------------------- | ----------- |
| 65  | `unlockCallback()` — only callable by PoolManager                     | Attacker directly calls `unlockCallback()` → revert `NotPoolManager` | 🔴 Critical |
| 66  | `unlockCallback()` — settles correct amounts for both swap directions | Token settlement math verified for `zeroForOne` and `oneForZero`     | 🟠 High     |

---

## Phase 5: BrokerExecutor — Atomic Multicall

| #   | Test                                                            | Attack Vector                                                                                                                                                                        | Severity    |
| --- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------- |
| 67  | `execute()` — happy path: sign → set operator → calls → revoke  | Full lifecycle                                                                                                                                                                       | 🟠 High     |
| 68  | `execute()` — operator is ALWAYS revoked (even on call failure) | If one call in the batch reverts, operator revoked before revert propagates? **NO — entire tx reverts atomically. Operator was never "set" from on-chain perspective.** Verify this. | 🟠 High     |
| 69  | `execute()` — replayed signature reverts                        | Nonce consumed after first use                                                                                                                                                       | 🔴 Critical |
| 70  | `execute()` — calls can target ANY contract                     | Router via executor → verify calls route correctly                                                                                                                                   | 🟡 Medium   |
| 71  | `execute()` — reentrancy guard prevents nesting                 | Executor calls itself → reverts                                                                                                                                                      | 🟡 Medium   |
| 72  | `getMessageHash()` / `getEthSignedMessageHash()` consistency    | Hashes match expected EIP-191 format                                                                                                                                                 | 🟡 Medium   |

---

## Phase 6: LeverageShortExecutor — Atomic Leverage

| #   | Test                                                           | Attack Vector                                                        | Severity    |
| --- | -------------------------------------------------------------- | -------------------------------------------------------------------- | ----------- |
| 73  | `executeLeverageShort()` — happy path end-to-end               | Deposit collateral → mint debt → swap → re-deposit → revoke operator | 🟠 High     |
| 74  | `executeLeverageShort()` — post-execution solvency             | Leveraged position stays solvent at target LTV                       | 🔴 Critical |
| 75  | `executeLeverageShort()` — operator always revoked             | Even on revert, no lingering operator status                         | 🟠 High     |
| 76  | `executeLeverageShort()` — executor never holds tokens post-tx | All proceeds transferred back to broker                              | 🟠 High     |
| 77  | `unlockCallback()` — only callable by PoolManager              | Same as Router callback check                                        | 🔴 Critical |
| 78  | `calculateOptimalDebt()` — math correctness                    | Various LTV targets produce correct debt amounts                     | 🟡 Medium   |
| 79  | `executeLeverageShort()` — excessive leverage reverts          | Target LTV that would breach maintenance margin                      | 🔴 Critical |

---

## Phase 7: Valuation Modules — NAV Manipulation

### 7.1 JitTwammBrokerModule (ghost-aware)

| #   | Test                                                                      | Attack Vector                                                | Severity  |
| --- | ------------------------------------------------------------------------- | ------------------------------------------------------------ | --------- |
| 80  | Three-term valuation: sellRefund + buyOwed + ghost                        | All three terms calculated correctly                         | 🟠 High   |
| 81  | Ghost attribution: `totalGhost × order.sellRate / stream.sellRateCurrent` | Pro-rata ghost split is accurate                             | 🟠 High   |
| 82  | Ghost discount applied correctly                                          | Ghost value is discounted (conservative, never inflates NAV) | 🟠 High   |
| 83  | Empty/expired order returns 0                                             | No leftover value from expired orders                        | 🟡 Medium |
| 84  | Unknown token returns 0                                                   | Token not matching collateral or position → 0 value          | 🟡 Medium |

### 7.2 UniswapV4BrokerModule

| #   | Test                                                          | Attack Vector                              | Severity  |
| --- | ------------------------------------------------------------- | ------------------------------------------ | --------- |
| 85  | LP value: `getAmountsForLiquidity()` → price each token → sum | Correct decomposition at current tick      | 🟠 High   |
| 86  | Collateral token priced 1:1, position token at index price    | Pricing logic correct for both pool tokens | 🟠 High   |
| 87  | Zero liquidity returns 0                                      | Edge case: burned/empty LP position        | 🟡 Medium |
| 88  | Out-of-range LP position valued correctly                     | LP fully in one token when out-of-range    | 🟡 Medium |

---

## Phase 8: Cross-Contract Interaction Attacks

| #   | Test                                               | Attack Vector                                                                                                | Severity    |
| --- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ----------- |
| 89  | Executor → Router → Broker chain: no token leakage | Funds always end up in broker, never stuck in router or executor                                             | 🔴 Critical |
| 90  | Flash loan + NAV manipulation                      | Attacker flash-loans to temporarily inflate/deflate oracle → manipulate solvency check                       | 🔴 Critical |
| 91  | Broker re-initialization after factory upgrade     | Even if factory is upgraded, existing brokers cannot be re-initialized                                       | 🔴 Critical |
| 92  | Operator-only functions during liquidation         | Core calls `seize()` mid-liquidation while operator calls `withdrawCollateral()` simultaneously (reentrancy) | 🔴 Critical |
| 93  | Multiple brokers sharing same market: isolation    | Broker A's operations don't affect Broker B's state                                                          | 🟠 High     |

---

## Execution Order

```
Phase 1 → Phase 2 → Phase 3    (PrimeBroker: deepest attack surface)
     ↓
Phase 4 → Phase 5 → Phase 6    (Periphery: user-facing flows)
     ↓
Phase 7                         (Valuation: NAV accuracy)
     ↓
Phase 8                         (Cross-contract: integration attacks)
```

## Summary

| Phase             | Tests  | Critical | High   | Medium | Low   |
| ----------------- | ------ | -------- | ------ | ------ | ----- |
| 1. Init & ACL     | 16     | 8        | 5      | 3      | 0     |
| 2. Position Mgmt  | 13     | 6        | 5      | 1      | 1     |
| 3. Tracking & NAV | 20     | 7        | 8      | 3      | 2     |
| 4. Router Flows   | 17     | 4        | 10     | 3      | 0     |
| 5. Executor       | 6      | 1        | 3      | 2      | 0     |
| 6. Leverage       | 7      | 3        | 3      | 1      | 0     |
| 7. Valuation      | 9      | 0        | 5      | 4      | 0     |
| 8. Cross-Contract | 5      | 4        | 1      | 0      | 0     |
| **Total**         | **93** | **33**   | **40** | **17** | **3** |

### Already Covered (Not Re-Tested)

- `seize()` cascade: cash → TWAMM cancel → V4 LP unwind → transfer to liquidator
- `forceSettle()` integration
- All liquidation permutations (11 test files in `test/integration/liquidation/`)
