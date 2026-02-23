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

## V2 Roadmap

### 🔴 P0-6: Normalization Factor Influence on Debt & Liquidation

**Priority:** P0 — Critical Correctness
**Files:** `StandardFundingModel.sol`, `RLDCore.sol`, `DutchLiquidationModule.sol`

**Problem:** Normalization factor is applied lazily (`_applyFunding` only on interaction). For large `dt` (days/weeks without market interaction), `exp(-rate × dt / period)` can produce extreme normFactor changes, potentially:

- Silently forgiving debt (normFactor → 0 at extreme negative rates)
- Exploding debt beyond liquidation buffer
- Overflow/underflow in `expWad` at extreme exponents

**Approach:**

1. Add `MAX_NORM_FACTOR_DELTA` bounds per update
2. Chunk large `dt` into `fundingPeriod`-sized segments
3. Add invariant fuzz tests for normFactor bounds

---

### 🔴 P0-7: Bad Debt Socialization

**Priority:** P0 — Critical Correctness
**Files:** `RLDCore.sol` (L789-800), new `InsuranceFund.sol` (optional)

**Problem:** After `_settleLiquidation()`, if `seizeAmount > availableCollateral`, remaining `debtPrincipal > 0` stays on books forever. This:

- Permanently blocks broker from becoming solvent
- Inflates `totalDebt`, distorting utilization
- Creates unbacked wRLP supply

**Approach:**

1. **Phase 1 — Write-off**: After seize, if `NAV == 0 && debtPrincipal > 0`, zero out debt, emit `BadDebtWriteOff`
2. **Phase 2 — Insurance fund**: Collect fraction of funding fees into reserve; cover bad debt from reserve
3. **Phase 3 — Socialization**: Spread losses across short holders via normFactor adjustment

---

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

### 🟡 P1-2: Updated Simulation

**Priority:** P1 — Post-Fix Validation
**Scope:** Re-deploy contracts with 5 bug fixes to simulation environment

**Steps:**

1. Re-compile contracts, re-generate ABIs
2. Re-deploy to Docker simulation stack
3. Run full scenario suite (long, short, leverage, close, liquidate)
4. Verify indexer event parsing with corrected `closeShort` behavior
5. Update dashboard if needed

---

### 🟢 P2-1: Multiple Broker Types

**Priority:** P2 — Feature Expansion
**Files:** New `SimpleBroker.sol`, `SimpleBrokerFactory.sol`, modified `BrokerVerifier.sol`

**Goal:** Support collateral-only brokers (waUSDC-only, no position minting) alongside full PrimeBrokers in the same market.

**Approach:**

1. New `SimpleBroker` — stripped PrimeBroker: deposit/withdraw only, no debt
2. Upgraded `BrokerVerifier` — accept `address[]` factories instead of single factory
3. Core already handles `debtPrincipal == 0` as "always solvent"

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
