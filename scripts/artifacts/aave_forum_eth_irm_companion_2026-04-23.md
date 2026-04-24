# [Data] ETH Market IRM Change: Did Flattening WETH Help the Crisis?

This is a data-only companion note to the USDC discussion, focused on WETH on Aave v3 Ethereum Core.

Question tested: if WETH IRM was changed to protect loopers, how much liquidation pressure did that remove, and does it address collateral withdrawal freezes?

## TL;DR

- WETH is now around **85.00% utilization** at **5.00% variable borrow APR**.
- Re-running the same borrower set under a pre-change high-APR regime (**8.35%**) increases 30-day material debt-at-risk from **$15.23M** to **$24.77M**.
- Under tighter hypothetical APRs, modeled 30-day material debt-at-risk rises to **$104.92M (12%)** and **$158.54M (20%)**.
- So yes: the WETH IRM flattening appears to have reduced short-horizon liquidation pressure on loop-heavy books.
- But no: it does **not** directly solve collateral withdrawal freeze mechanics. IRM changes debt accrual speed; freeze/queue conditions are a separate constraint.

## Method

- User set reconstructed from `aave_events` with WETH debt leg:
  - `Borrow - Repay - LiquidationCall(debt asset = WETH)`.
- Live risk state from `getUserAccountData` for every reconstructed user.
- Static-price deterministic projection:
  - WETH debt share accrues at scenario APR.
  - Non-WETH debt accrues at current debt-weighted baseline APR.
  - No top-ups, migrations, or discretionary repayments.

## Snapshot (2026-04-23 19:00:00 UTC)

- WETH borrow / supply: **$5,340,931,936 / $5,340,257,686**
- WETH utilization: **85.00%**
- WETH variable borrow APR: **5.00%**
- WETH supply APR: **4.25%**
- Users with positive reconstructed WETH debt: **2,328**
- Material users (`debt >= $100k`, HF >= 1): **451**

## Scenario Stress Table (Material Users)


| Scenario                          | APR    | 7d users | 7d debt-at-risk | 30d users | 30d debt-at-risk |
| --------------------------------- | ------ | -------- | --------------- | --------- | ---------------- |
| Post-change flat (observed)       | 5.00%  | 0        | $0              | 2         | $15,234,398      |
| Pre-change regime (observed peak) | 8.35%  | 1        | $2,847,656      | 5         | $24,769,029      |
| Hypothetical re-steepen           | 12.00% | 1        | $2,847,656      | 7         | $104,924,238     |
| Hypothetical crisis               | 20.00% | 2        | $15,234,398     | 17        | $158,535,979     |


Interpretation: flattening from 8.35% to 5.00% reduces modeled 30-day material debt-at-risk by about **$9.53M** in this snapshot.

## Concentration Check

Largest WETH-debt concentration:

- Address: `0xf0bb20865277abd641a307ece5ee04e79073416c`
- Total debt: **$1.212B**
- Reconstructed WETH debt: **507,259 WETH** (~**$1.183B**)
- Current HF: **1.0463**

Modeled days-to-HF<1 for this address:

- **5.00% APR:** 327.3 days
- **8.35% APR:** 203.8 days
- **12.00% APR:** 145.7 days
- **20.00% APR:** 91.4 days

This is directionally consistent with the governance rationale that flatter WETH rates buy time for loop-heavy accounts.

## What this means for the broader crisis

- **WETH-side IRM flattening** can reduce liquidation cascades and forced deleveraging pressure.
- **USDC-side liquidity stress / queue behavior** is a different problem. Even if USDC debt is repaid, collateral withdrawal constraints can persist if liquidity paths remain impaired.
- The two levers are complementary, not contradictory:
  - Flatten WETH to avoid liquidation spirals.
  - Restore stablecoin clearing (for example through steeper post-kink pricing where needed) to re-open liquidity pathways.

## Reproducibility artifacts

- Summary: `/home/ubuntu/RLD/scripts/artifacts/weth_irm_crisis_sensitivity_summary_2026-04-23.json`
- Projection: `/home/ubuntu/RLD/scripts/artifacts/weth_irm_crisis_sensitivity_projection_2026-04-23.csv`
- Base snapshot: `/home/ubuntu/RLD/scripts/artifacts/weth_hf_sorted_envio_reconstruction_2026-04-23.csv`
- Report: `/home/ubuntu/RLD/scripts/artifacts/weth_irm_crisis_report_2026-04-23.md`
- Chart 1: `/home/ubuntu/RLD/scripts/artifacts/chart_weth_material_debt_risk_7d_30d_2026-04-23.png`
- Chart 2: `/home/ubuntu/RLD/scripts/artifacts/chart_weth_cumulative_material_debt_90d_2026-04-23.png`