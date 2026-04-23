# Proposal Reply: Balanced USDC IRM Update (Looping-Aware)

This note proposes a revised USDC IRM configuration that keeps the original proposal's direction (higher post-kink pricing, lower optimal utilization) while explicitly constraining modeled liquidation sensitivity from looped positions.

## Objective

- Preserve meaningful rate-based supply attraction.
- Reduce incremental liquidation pressure from highly looped users.
- Keep 30-day modeled material debt-at-risk near the ~$60M boundary discussed in governance.

## Data and Modeling Basis

- **User universe:** Envio-reconstructed positive USDC debt users (`Borrow - Repay - LiquidationCall` debt leg), then joined with live `getUserAccountData`.
- **Population used for sensitivity:** 9,036 users with non-zero live debt; 1,408 material users (`debt >= $100k`).
- **Snapshot state (2026-04-23 07:00 UTC):**
  - USDC supply: ~$1.804B
  - USDC borrow: ~$1.769B
  - True utilization: 98.06%
  - Observed USDC variable APR: 11.68%
- **Projection assumptions:**
  - Static collateral prices.
  - No user intervention (no top-up, repay, migration).
  - Non-USDC debt accrues at current debt-weighted baseline (7.50% APR).

## Key Threshold Discovery

Using user-level HF and debt composition, 30-day crossing thresholds are discrete:

- Account `0x0591926d5d3b9cc48ae6efb8db68025ddc3adfa5` crosses by 30d at approx **17.93%** USDC APR.
- Account `0xb0f76a4c60c6c993ac30bdda24c5f7f98a03249c` crosses by 30d at approx **9.73%** USDC APR.
- Account `0x9e283ba6d80faaf1bee7270e21eee5c375266611` crosses by 30d at approx **35.32%** USDC APR.

This produces a stepwise risk profile: around ~18% APR, modeled 30-day material debt-at-risk jumps from ~$0.9M to about ~$61M.

## Candidate Comparison

All rates below are evaluated at current utilization (98.06%) and pinned stress utilization (99.87%).

| Model | Slope 2 | U* | Borrow APR @ 98.06% | Borrow APR @ 99.87% | Material users <=7d | Material users <=30d | Material debt <=30d |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current observed | - | - | 11.68% | - | 0 | 1 | $0.90M |
| Strict 60M guard | 18% | 91% | 17.63% | 21.24% | 0 | 1 | $0.90M |
| **Balanced candidate** | **32%** | **87%** | **30.73%** | **35.18%** | **0** | **2** | **$61.06M** |
| Original interim | 40% | 87% | 37.54% | 43.10% | 0 | 3 | $61.29M |
| Original target | 50% | 85% | 47.04% | 53.07% | 0 | 3 | $61.29M |

## Recommended Balanced Model

**Recommended parameters (single-step):**

- `baseVariableBorrowRate`: 0.00
- `variableRateSlope1`: 0.035
- `variableRateSlope2`: **0.32**
- `optimalUsageRatio`: **0.87**
- `reserveFactor`: 0.10

### Why this is the balance point

- Keeps directional intent of the original proposal:
  - stronger post-kink pricing,
  - lower U* than status quo.
- Delivers materially stronger pricing than today:
  - 11.68% -> 30.73% at current utilization.
- Reduces liquidation pressure vs original interim:
  - material users <=30d: **3 -> 2**
  - material debt <=30d: **$61.29M -> $61.06M**
  - removes the third marginal material account from 30-day crossing under current-util assumptions.

## Fastest Material Accounts Under Balanced Candidate

| Address | HF now | Debt | USDC share | Days to liquidation |
|---|---:|---:|---:|---:|
| `0xb0f76a4c60c6c993ac30bdda24c5f7f98a03249c` | 1.0075 | $903,834 | 93.5% | 10.75 |
| `0x0591926d5d3b9cc48ae6efb8db68025ddc3adfa5` | 1.0125 | $60,157,937 | 84.5% | 19.00 |
| `0x9e283ba6d80faaf1bee7270e21eee5c375266611` | 1.0241 | $226,847 | 94.2% | 33.80 |

For reference, under original interim these are approximately 9.07d, 16.10d, and 28.49d.

## Governance Framing

- If governance requires a **hard** <=$60M 30-day envelope under this snapshot model, use the strict guard profile (`S2=18%`, `U*=91%`), but clearing power is much weaker.
- If governance prefers a practical balance between market-clearing pressure and loop-risk containment, use the **balanced candidate** (`S2=32%`, `U*=87%`).

## Artifacts

- Parameter sweep: `scripts/artifacts/usdc_irm_balanced_sweep_2026-04-23.csv`
- Base user snapshot: `scripts/artifacts/usdc_hf_sorted_envio_reconstruction_2026-04-23.csv`
- Full scenario report: `scripts/artifacts/usdc_irm_sensitivity_report_2026-04-23.md`
