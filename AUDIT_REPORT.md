# Security Audit Report: RLDAaveOracle

**Date:** 2026-01-19
**Target:** `RLDAaveOracle.sol`
**Auditor:** Antigravity (Simulated Red Team)

## 1. Executive Summary

The `RLDAaveOracle` serves as a real-time reference oracle for the RLD Protocol, fetching the _current_ variable borrow rate of an asset on Aave V3. Its primary use case is providing a "Spot Check" for the main Symbiotic-powered oracle.

**Overall Risk Rating: HIGH** (if used as a primary oracle)
**Verified Risk Rating: MEDIUM** (as a reference/check oracle)

Key findings highlight the inherent volatility of spot rates and the theoretical potential for manipulation via massive Flash Loans, although capital requirements on Mainnet are extremely high.

## 2. Findings

### [CRITICAL] Spot Price Volatility & Manipulation Risk

**Description:** The oracle relies on `currentVariableBorrowRate`. This value is determined instantly by the pool's utilization ratio.
**Impact:** large trades or Flash Loans can significantly alter the utilization ratio within a single block, causing the rate to spike.
**PoC Evidence:**

- **Scenario:** Attacker uses flash loans to acquire ~$500M of Collateral (wstETH/WETH) and borrows ~460M USDC (60% of available liquidity).
- **Result:**
  - Initial Rate: ~4.5% ($4.50)
  - Final Rate: ~6.4% ($6.37)
  - **Price Deviation:** +41%
- **Maximum Theoretical Impact:** If utilization > 90% (Slope 2), rates can jump to >50-100% APY, causing a 20x price increase (capped at $100).

**Mitigation:**

- **Do not use for settlement** of large positions without validation.
- Use only as a **bounds check** relative to a Time-Weighted Average Price (TWAP) or Time-Weighted Average Rate (TWAR).
- The Symbiotic Oracle (TWAR) handles this correctly; this contract should strictly be a "sanity check".

### [LOW] Supply Cap Interactions

**Description:** Testing revealed that manipulating rates on Mainnet requires immense capital ($500M+) due to deep liquidity. Furthermore, Aave's Supply Caps on collateral assets (WETH, WBTC) act as a natural friction against single-transaction manipulation, forcing attackers to manage complex baskets of collateral.

### [INFO] Logic Verification

**Description:**

- **Math**: Correct. `P = K * r` is linear.
- **Scaling**: Aave uses RAY (1e27). Oracle outputs WAD (1e18). Division by 1e9 is correct.
- **Caps**: `RATE_CAP` (1e27) correctly prevents prices > $100.00.
- **Floor**: `MIN_PRICE` (1e14) prevents zero-price issues.

## 3. Conclusion

The contract performs its specific function (reading spot rates) correctly and safely within the bounds of Solidity. However, the _data source itself_ is volatile. The protocol must implement strict deviation thresholds (e.g., "Symbiotic Price cannot deviate more than X% from Spot") with the understanding that Spot can be 40-50% higher during high utilization periods.

## 4. Recommendations

1.  **Deviation Threshold**: Set a loose tolerance (e.g., 20-50%) for the comparison between Symbiotic and Spot. A tight tolerance (e.g., 1%) will cause frequent reversions during legitimate high-usage periods.
2.  **Circuit Breaker**: If Spot Rate > 50% APY ($50.00), pause protocol actions or require manual intervention, as this indicates extreme market stress or manipulation.
