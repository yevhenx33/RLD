# RLD Bad Debt Handling Mechanism

## Overview

When a broker position becomes insolvent (collateral < debt), liquidation occurs. If the seized collateral is insufficient to cover the full debt, **bad debt** is created. This document formalizes the two-layer waterfall for absorbing bad debt in the RLD protocol.

---

## The Problem: Bad Debt Creation

```
Normal Liquidation:
├── Broker: $600k collateral, $500k debt
├── Health drops below threshold → liquidatable
├── Liquidator: Burns wRLP, receives collateral + bonus
└── Result: Debt cleared, no bad debt

Bad Debt Scenario:
├── Broker: $0 collateral, $500k debt (rapid price spike)
├── Liquidator: Nothing to seize!
├── 500k wRLP exists in circulation with NO backing
└── Result: Protocol has "hole" - unbacked wRLP tokens
```

---

## Two-Layer Waterfall

### Layer 1: wRLP Insurance Fund

**Mechanism:** The Insurance Fund holds wRLP tokens (not waUSDC). When bad debt occurs, it simply **burns** wRLP from its reserves to clear the unbacked debt.

**Step-by-Step:**

1. **Bad debt detected**
   - Broker X fully liquidated but `debtPrincipal > 0` remains
   - `broker.collateral == 0` (nothing left to seize)

2. **Check Insurance Fund balance**

   ```
   if (insuranceFund.wRLPBalance >= badDebtAmount) {
       // Fund can cover
   }
   ```

3. **Burn wRLP from fund**

   ```
   wRLP.burn(insuranceFund, badDebtAmount);
   insuranceFund.wRLPBalance -= badDebtAmount;
   ```

4. **Clear bad broker's debt**

   ```
   positions[marketId][badBroker].debtPrincipal = 0;
   marketState.totalDebt -= badDebtAmount;
   ```

5. **Result:** Bad debt absorbed instantly, no impact on other users.

---

### Layer 2: Gradual NF Socialization (Fallback)

**Mechanism:** If Insurance Fund is insufficient, remaining bad debt is spread across all wRLP holders via a gradual increase in the Normalization Factor over 30-90 days.

**Step-by-Step:**

1. **Calculate shortfall**

   ```
   shortfall = badDebtAmount - insuranceFund.wRLPBalance;
   ```

2. **Add to amortization queue**

   ```
   badDebtToAmortize += shortfall;
   amortizationEndTime = block.timestamp + 30 days;
   ```

3. **Modify funding calculation** (in `StandardFundingModel`)

   ```
   // Normal funding rate
   fundingRate = (markPrice - indexPrice) / indexPrice;

   // Add bad debt surcharge
   if (badDebtToAmortize > 0) {
       dailySurcharge = badDebtToAmortize / totalDebt / 30 days;
       fundingRate += dailySurcharge × dt;
   }
   ```

4. **NF increases gradually each block**

   ```
   newNF = oldNF × exp(fundingRate × dt / period);
   ```

5. **Effect on debtors**

   ```
   Example: $1M bad debt, $20M total debt (5% shortfall)

   Day 0:  User's 10k debt × NF 1.000 = 10,000 true debt
   Day 15: User's 10k debt × NF 1.025 = 10,250 true debt
   Day 30: User's 10k debt × NF 1.050 = 10,500 true debt

   Cost: 5% increase spread over 30 days (~0.17%/day)
   ```

6. **Result:** All wRLP debtors share cost pro-rata, smoothly over time.

---

## Insurance Fund Accumulation

The wRLP Insurance Fund is funded through:

| Source               | Mechanism                          |
| -------------------- | ---------------------------------- |
| **Minting Fee**      | X% of every wRLP minted → Fund     |
| **Liquidation Fee**  | Portion of liquidator bonus → Fund |
| **Protocol Revenue** | DAO allocates wRLP to Fund         |

---

## Complete Flow Diagram

```
                        BAD DEBT EVENT
                             │
                             ▼
              ┌──────────────────────────────┐
              │  Check Insurance Fund wRLP   │
              │     Balance ≥ Bad Debt?      │
              └──────────────────────────────┘
                      │              │
                     YES             NO
                      │              │
                      ▼              ▼
            ┌─────────────────┐  ┌─────────────────────────┐
            │ BURN wRLP from  │  │ Burn partial + Add      │
            │ Insurance Fund  │  │ shortfall to NF queue   │
            └─────────────────┘  └─────────────────────────┘
                      │              │
                      ▼              ▼
              ┌──────────────────────────────┐
              │     Clear Bad Debt Record    │
              │  positions[broker].debt = 0  │
              └──────────────────────────────┘
                             │
                             ▼
                       ✓ RESOLVED
```

---

## Why wRLP (Not waUSDC) for Insurance Fund?

| wRLP Insurance                                     | waUSDC Insurance               |
| -------------------------------------------------- | ------------------------------ |
| ✅ Direct burn (no market interaction)             | ❌ Must BUY wRLP from market   |
| ✅ Zero slippage                                   | ❌ Slippage on large purchases |
| ✅ Instant settlement                              | ❌ Need TWAMM or auction       |
| ✅ Auto-sizes with rate (worth more during crisis) | ❌ Fixed dollar value          |

**Critical insight:** During a crisis (rates spike), wRLP is MORE valuable. The Insurance Fund's wRLP holdings are worth MORE at exactly the moment bad debt occurs, providing natural counter-cyclical protection.

---

## Why Gradual NF (Not Instant)?

| Gradual NF                     | Instant NF                          |
| ------------------------------ | ----------------------------------- |
| ✅ No sudden shock             | ❌ May trigger cascade liquidations |
| ✅ Users can react             | ❌ Immediate impact                 |
| ✅ Smooth, predictable         | ❌ Unpredictable for users          |
| ✅ Fair distribution over time | ❌ Punishes current holders only    |

---

## Summary

1. **Layer 1:** wRLP Insurance Fund burns tokens → instant, isolated
2. **Layer 2:** Gradual NF increase → smooth, fair socialization
3. **Design mirrors CME:** Guarantee Fund → Member Socialization

This provides robust bad debt protection while minimizing user impact and avoiding market interventions.
