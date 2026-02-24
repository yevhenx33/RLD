# Known Issues & TODOs

Tracked issues discovered during penetration testing, code review, and roadmap planning.

---

## Existing Issues (from Penetration Testing)

### 🟡 `deltaCollateral` parameter is unused in `RLDCore.modifyPosition()`

**Files:** `PrimeBroker.sol` (L924-941), `RLDCore.sol` (L301-388)

**Issue:** `PrimeBroker.modifyPosition()` accepts `deltaCollateral` and passes it to `Core.lock()` → `Core.modifyPosition()`, but **Core ignores the value entirely**. Collateral is not tracked in Core — solvency is inferred from `broker.getNetAccountValue()`.

```solidity
// RLDCore.modifyPosition() L299-300:
/// @param deltaCollateral Unused - kept for interface compatibility (collateral in broker)
```

**Impact:**

- A caller could pass arbitrary `deltaCollateral` values with no on-chain effect
- The value is only used in the `PositionModified` event emission, which could mislead indexers/UIs
- Not exploitable — solvency checks use NAV, not the passed value

**Options:**

1. **Remove the parameter** — breaking change to `IPrimeBroker` and `IRLDCore` interfaces
2. **Keep but document** — current approach, minimal risk
3. **Validate consistency** — require `deltaCollateral` matches actual balance change (gas cost)

**Decision:** TBD

---

### 🟡 JIT collateral approval lingering after `lockAcquired`

**File:** `PrimeBroker.sol` (L974-976)

**Issue:** When depositing collateral, `lockAcquired()` approves Core to pull tokens:

```solidity
if (deltaCollateral > 0) {
    ERC20(collateralToken).approve(CORE, uint256(deltaCollateral));
}
```

This approval is never revoked after the lock callback completes, leaving a **lingering ERC20 allowance** from broker → Core.

**Impact:** Low. Core is trusted immutable infrastructure. A Core compromise would be catastrophic regardless of this approval.

**Fix:** Add `approve(CORE, 0)` after the lock completes, or use `increaseAllowance`/`decreaseAllowance` pattern.

---

### 🟢 `applyFunding()` external wrapper — remove before production

**File:** `RLDCore.sol` (L548-554)

```solidity
/// @notice External wrapper to apply funding for testing purposes.
/// @dev TODO: REMOVE BEFORE PRODUCTION
function applyFunding(MarketId id) external { ... }
```

Marked for removal in the source code itself.

---

## Roadmap

### ✅ P0-6: Normalization Factor Influence on Debt & Liquidation — RESOLVED

**Priority:** P0 — Critical Correctness
**Files:** `StandardFundingModel.sol`, `RLDCore.sol`

**Status:** Exponent clamping (`MAX_EXPONENT`, `MIN_EXPONENT`) and `NormFactorCollapseBlocked` guard already implemented in `StandardFundingModel.sol`. NF cannot reach zero or overflow.

---

### ✅ P0-7: Bad Debt Socialization — RESOLVED (NF Bleeding Implemented)

**Priority:** P0 — Critical Correctness
**Files:** `IRLDCore.sol`, `RLDCore.sol`, `BadDebtBleeding.t.sol`

**Solution:** Gradual NF bleeding. When a liquidation creates negative equity, unbacked wRLP principal is transferred to a global `state.badDebt` counter. Each `_applyFunding()` call bleeds a chunk into the normalization factor over 7 days, socializing the cost across all borrowers.

**Changes:**

- `MarketState.badDebt` — new `uint128` field tracking unbacked principal
- `_settleLiquidation()` — detects bad debt **after** seize, transfers to `state.badDebt`, clears user position
- `_applyFunding()` — `chunk = badDebt × dt / 7d`, with `minChunk` floor (0.0001% of supply) and overshoot ceiling
- Events: `BadDebtRegistered`, `BadDebtSocialized`

**Tests (6/6 pass):**

- T40: Bad debt registered, user cleared
- T41: NF bleeds over 14d → `badDebt = 0`, NF +4.6%
- T42: Stacking two events → fully resolved
- T43: No bad debt when insolvent but not underwater
- T44: No bleeding when `badDebt = 0`

### 🟡 P1-3: Comprehensive Fuzzing

**Priority:** P1 — Pre-Audit
**Files:** New `test/invariant/` directory

**Key Invariants:**

- `normFactor ∈ [MIN, MAX]` for all `(rate, dt)` inputs
- `solvent ⟹ NAV ≥ debt × maintenanceMargin`
- `totalDebt == Σ(debtPrincipal)` across all positions
- `seizeAmount ≤ availableCollateral`
- `ghost attribution ≤ stream total`

---

### 🟡 P1-4: Final Logic Review

**Priority:** P1 — Pre-Audit
**Scope:** Manual review of all critical paths

**Checklist:**

- [ ] Liquidation close factor safety (50% always safe?)
- [ ] Token ordering in all V4 interactions
- [ ] `seize()` completeness (TWAMM + LP + collateral)
- [ ] Funding rate extremes (1000%, -100%)
- [ ] Oracle staleness / zero price
- [ ] Permit2 cross-chain replay
- [ ] NFT transfer → operator list behavior
- [ ] Config timelock front-running

---

### � P1-2: Updated Simulation & Front-End Integration

**Priority:** P1 — **NEXT UP**
**Scope:** Re-deploy contracts with all fixes (bad debt, funding, liquidation) and integrate with front-end for user flow testing.

**Steps:**

1. Re-compile contracts, re-generate ABIs
2. Re-deploy to Docker simulation stack
3. Run full scenario suite (long, short, leverage, close, liquidate)
4. Integrate front-end actions panel (TWAP, LP, Loop, Batch)
5. Test user flows end-to-end via UI before final audit prep
6. Verify indexer event parsing with new `BadDebtRegistered`/`BadDebtSocialized` events

---

### ✅ P2-1: Multiple Broker Types — RESOLVED (Deferred to Post-Launch)

**Priority:** P2 — Feature Expansion
**Status:** Architecture discussion completed. Modular broker design documented. Implementation deferred — will revisit after V1 launch if needed.

**Design docs:** See `modular_broker_design.md` and `multi_broker_architecture.md` in project artifacts.

**Key decisions:**

- PDLP integration can be achieved within V1 by extending PrimeBroker with a new module (no Core changes)
- Full modular broker (pluggable `IBrokerModule` with configurable liquidation priorities) designed for V2
- Aave-style versioned deployments preferred over upgradeable proxies

---

### 🟢 P2-5: Optimizations (Gas / Security / Architecture)

**Priority:** P2 — Polish

**Gas:** Cache oracle prices, `EnumerableSet` for operators, remove unused params
**Security:** Revoke lingering approval, remove `applyFunding()` wrapper, add pool key validation to LSE
**Architecture:** Sunset `LeverageShortExecutor` (BrokerRouter covers same flow), keep `BrokerExecutor` for generic multicall

---

### 🟢 P2-8: Oracle Design (Symbiotic-Powered)

**Priority:** P2 — Infrastructure Evolution
**Files:** New `SymbioticRateOracle.sol`, `SymbioticMiddleware.sol`

**Goal:** Replace Aave rate dependency with decentralized, restaking-secured multi-source oracle.

**Phases:**

1. **A — Compatibility**: New oracle wraps existing Aave feed (same interface)
2. **B — Multi-source**: Add Compound V3 + Morpho Blue, use median
3. **C — Operator attestation**: Symbiotic restaking operators attest to rates
4. **D — Governance**: Operator set management, rate feed curation

**Key constraint:** `IRLDOracle.getIndexPrice()` interface stays unchanged — oracle swap is invisible to Core.
