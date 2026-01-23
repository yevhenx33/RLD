# Funding Model Math: Comprehensive Scenarios

**Base Configuration:**

- **Index Price**: $5.00 (Representing 5% Interest Rate)
- **Funding Period**: 30 Days (2,592,000 seconds)
- **Logic**: Inverted (Mark > Index = Debt Decrease)

---

## Scenario A: The "Normal" Market (1% Premium)

_Traders are slightly bullish on interest rates._

- **Mark Price**: $5.05
- **Deviation**: +$0.05 (+1%)

**Step-by-Step:**

1.  **Base Rate**: (5.05 - 5.00) / 5.00 = **+0.01 (+1%)**
2.  **Inverted Rate (Per 30 Days)**: **-0.01**
3.  **One Day Scaling**: -0.01 / 30 = **-0.000333...**
4.  **Impact (1 Day)**:
    - User Debt: 1,000 WRLP
    - Multiplier: exp(-0.000333) ≈ **0.99966**
    - New Debt: **999.66 WRLP**
    - **Result**: Short earns **0.34 WRLP** (Annualized ~12% APY).

---

## Scenario B: The "Bull Run" Spike (50% Premium)

_Massive speculation. Borrowing demand explodes._

- **Mark Price**: $7.50
- **Deviation**: +$2.50 (+50%)

**Step-by-Step:**

1.  **Base Rate**: (7.50 - 5.00) / 5.00 = **+0.50 (+50%)**
2.  **Inverted Rate (Per 30 Days)**: **-0.50**
3.  **One Day Scaling**: -0.50 / 30 = **-0.0166...**
4.  **Impact (1 Day)**:
    - User Debt: 1,000 WRLP
    - Multiplier: exp(-0.0166) ≈ **0.9835**
    - New Debt: **983.5 WRLP**
    - **Result**: Short earns **16.5 WRLP** in one day.
    - **Safety**: This massive payout strongly incentivizes arb bots to sell Mark down.

---

## Scenario C: The "Bear Crash" (20% Discount)

_Nobody wants to borrow. RLP dumps below fair value._

- **Mark Price**: $4.00
- **Deviation**: -$1.00 (-20%)

**Step-by-Step:**

1.  **Base Rate**: (4.00 - 5.00) / 5.00 = **-0.20 (-20%)**
2.  **Inverted Rate (Per 30 Days)**: **+0.20** (Negative \* Negative = Positive)
3.  **One Day Scaling**: +0.20 / 30 = **+0.0066...**
4.  **Impact (1 Day)**:
    - User Debt: 1,000 WRLP
    - Multiplier: exp(0.0066) ≈ **1.0066**
    - New Debt: **1,006.6 WRLP**
    - **Result**: Short **PAYS** 6.6 WRLP.
    - **Logic**: Paying funding discourages holding shorts, helping Price recover to $5.

---

## Scenario D: The "Death Spiral" Edge Case (99% Discount)

_Protocol insolvency fear. Price collapses to near zero._

- **Mark Price**: $0.05
- **Deviation**: -$4.95 (-99%)

**Step-by-Step:**

1.  **Base Rate**: (0.05 - 5.00) / 5.00 = **-0.99 (-99%)**
2.  **Inverted Rate (Per 30 Days)**: **+0.99**
3.  **One Day Scaling**: +0.99 / 30 = **+0.033**
4.  **Impact (1 Day)**:
    - User Debt: 1,000 WRLP
    - Multiplier: exp(0.033) ≈ **1.0335**
    - New Debt: **1,033.5 WRLP**
    - **Result**: Shorts are punished heavily (~3.3% daily interest).
    - **Survival**: Even with a 99% crash, the mechanism remains stable. Debt increases, but doesn't explode infinitely in a single block.

---

## Scenario E: The "Moon" Edge Case (mark = $1000)

_Hyperinflation of the derivative._

- **Mark Price**: $1000.00
- **Deviation**: +$995.00 (+19,900%)

**Step-by-Step:**

1.  **Base Rate**: 199.0 (19900%)
2.  **Inverted Rate**: -199.0
3.  **One Day Scaling**: -199 / 30 = **-6.63**
4.  **Impact (1 Day)**:
    - User Debt: 1,000 WRLP
    - Multiplier: exp(-6.63) ≈ **0.0013**
    - New Debt: **1.3 WRLP**
    - **Result**: Debt is effectively wiped out.
    - **Analysis**: In this impossible scenario, Shorts win everything appropriately (since they Shorted something that went to Infinity, but the Funding rebases their debt to near zero value).
