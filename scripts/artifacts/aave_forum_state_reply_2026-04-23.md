# [Data] USDC on Ethereum Core: Current State and User Sensitivity

This post is a data-only state update based on Envio event reconstruction + live account risk reads. It does **not** include a new parameter recommendation (that will be posted separately).

## TL;DR

- USDC remains highly utilized (**98.06%**), but is no longer fully pinned; available liquidity is about **$34,938,518**.
- Observed variable borrow APR is **11.68%** at snapshot time (2026-04-23 07:00:00 UTC).
- Under proposal scenarios at current utilization, modeled 30-day material debt-at-risk is concentrated around **$61,288,618**.
- Under pinned-utilization stress (99.87%), target-curve stress introduces one material <=7d account and raises 30-day material debt-at-risk to **$70,142,038**.

## Methodology

- **User set:** addresses with positive net USDC debt from event reconstruction (`Borrow - Repay - LiquidationCall` debt leg) from `aave_events`.
- **Risk state:** live `getUserAccountData` for HF, debt, collateral.
- **Debt mix modeling:** reconstructed USDC share applied to user-level debt growth; non-USDC debt accrues at current debt-weighted baseline APR.
- **Assumptions:** static prices, no top-ups/repay/migrations, deterministic accrual.

## Current State Snapshot

- Supply: **$1,803,583,630**
- Borrow: **$1,768,645,111**
- Available liquidity: **$34,938,518**
- Utilization (`borrow/supply`): **98.06%**
- Observed USDC variable borrow APR: **11.68%**
- Observed USDC supply APR: **10.32%**
- Population covered: **9,036** users
- Material users (`debt >= $100k`): **1,408**

## Chart 1: Material HF Distribution

Material HF distribution
*Local artifact: `/home/ubuntu/RLD/scripts/artifacts/chart_hf_distribution_material_2026-04-23.png`*

## Chart 2: Material Debt-at-Risk by Scenario

Material debt-at-risk bar chart
*Local artifact: `/home/ubuntu/RLD/scripts/artifacts/chart_material_debt_risk_7d_30d_2026-04-23.png`*

## Chart 3: Cumulative Material Debt-at-Risk (0-90 days)

Cumulative material debt-at-risk
*Local artifact: `/home/ubuntu/RLD/scripts/artifacts/chart_cumulative_material_debt_90d_2026-04-23.png`*

## Chart 4: Largest Address HF Path

Largest address HF trajectory
*Local artifact: `/home/ubuntu/RLD/scripts/artifacts/chart_largest_address_hf_trajectory_2026-04-23.png`*

## Scenario Table (Material Users)


| Scenario               | 7d users | 7d debt-at-risk | 30d users | 30d debt-at-risk |
| ---------------------- | -------- | --------------- | --------- | ---------------- |
| Current observed APR   | 0        | $0              | 1         | $903,834         |
| Interim @ current util | 0        | $0              | 3         | $61,288,618      |
| Target @ current util  | 0        | $0              | 3         | $61,288,618      |
| Interim @ 99.87% util  | 0        | $0              | 3         | $61,288,618      |
| Target @ 99.87% util   | 1        | $903,834        | 5         | $70,142,038      |


## Largest Address Deep-Dive: `0x0591926d5d3b9cc48ae6efb8db68025ddc3adfa5`

This address is the largest debt concentration in the sensitivity set and materially drives scenario-level debt-at-risk outcomes.

- Total debt: **$60,157,937**
- Reconstructed USDC debt: **$50,804,404** (84.45% of total debt)
- Non-USDC debt (modeled baseline accrual): **$9,353,533**
- Collateral: **$66,203,217**
- Current HF: **1.0125**
- Debt headroom to HF=1 under static prices: **$749,022** (1.25% debt growth buffer)


| Scenario               | Days to liquidation | HF @ 7d | HF @ 30d | USDC carry/day | USDC carry/30d | Share of scenario 30d material debt-at-risk |
| ---------------------- | ------------------- | ------- | -------- | -------------- | -------------- | ------------------------------------------- |
| Current observed APR   | 43.2                | 1.0104  | 1.0038   | $15,378        | $463,382       | 0.0%                                        |
| Interim @ current util | 16.1                | 1.0070  | 0.9894   | $44,385        | $1,348,555     | 98.2%                                       |
| Target @ current util  | 13.4                | 1.0059  | 0.9848   | $53,694        | $1,635,737     | 98.2%                                       |
| Interim @ 99.87% util  | 14.4                | 1.0064  | 0.9866   | $49,907        | $1,518,719     | 98.2%                                       |
| Target @ 99.87% util   | 12.2                | 1.0053  | 0.9820   | $59,288        | $1,809,076     | 85.8%                                       |


At current-utilization interim/target scenarios, this single address contributes ~98% of 30-day material debt-at-risk, highlighting strong single-name concentration.

## Most Sensitive Material Accounts

### Interim @ current utilization


| Address                                      | HF now | Debt        | USDC share | Days to liquidation |
| -------------------------------------------- | ------ | ----------- | ---------- | ------------------- |
| `0xb0f76a4c60c6c993ac30bdda24c5f7f98a03249c` | 1.0075 | $903,834    | 93.5%      | 9.1                 |
| `0x0591926d5d3b9cc48ae6efb8db68025ddc3adfa5` | 1.0125 | $60,157,937 | 84.5%      | 16.1                |
| `0x9e283ba6d80faaf1bee7270e21eee5c375266611` | 1.0241 | $226,847    | 94.2%      | 28.5                |
| `0x8a93aae912e40dad3b64120c74dd27269acc1df7` | 1.0311 | $805,890    | 95.2%      | 36.4                |
| `0x2304eb247ae2dcf91ba372b458fc7e4ccc55ee42` | 1.0325 | $8,047,530  | 99.4%      | 36.8                |
| `0xf2035c797bc9ef9f3396e747e8b73907d420770c` | 1.0311 | $209,231    | 83.4%      | 40.1                |
| `0x6f10b22517ab7a3f5aa722e62a6917a3a7c2e2b8` | 1.0280 | $1,614,480  | 71.4%      | 40.5                |
| `0xd9154e919707845d2e3a77ae3831bf6bd2f757f8` | 1.0461 | $132,756    | 97.9%      | 52.4                |


### Target @ current utilization


| Address                                      | HF now | Debt        | USDC share | Days to liquidation |
| -------------------------------------------- | ------ | ----------- | ---------- | ------------------- |
| `0xb0f76a4c60c6c993ac30bdda24c5f7f98a03249c` | 1.0075 | $903,834    | 93.5%      | 7.5                 |
| `0x0591926d5d3b9cc48ae6efb8db68025ddc3adfa5` | 1.0125 | $60,157,937 | 84.5%      | 13.4                |
| `0x9e283ba6d80faaf1bee7270e21eee5c375266611` | 1.0241 | $226,847    | 94.2%      | 23.6                |
| `0x8a93aae912e40dad3b64120c74dd27269acc1df7` | 1.0311 | $805,890    | 95.2%      | 30.1                |
| `0x2304eb247ae2dcf91ba372b458fc7e4ccc55ee42` | 1.0325 | $8,047,530  | 99.4%      | 30.4                |
| `0xf2035c797bc9ef9f3396e747e8b73907d420770c` | 1.0311 | $209,231    | 83.4%      | 33.4                |
| `0x6f10b22517ab7a3f5aa722e62a6917a3a7c2e2b8` | 1.0280 | $1,614,480  | 71.4%      | 34.0                |
| `0xd9154e919707845d2e3a77ae3831bf6bd2f757f8` | 1.0461 | $132,756    | 97.9%      | 43.4                |


## Interpretation

- Count-based risk is dominated by dust accounts; debt-weighted risk remains concentrated in a small set of loop-heavy wallets.
- At the current (not fully pinned) utilization, no material account is in <=7d liquidation under interim/target-at-current-util scenarios.
- Under pinned stress, the target curve notably tightens clocks for the most levered material accounts.

## Caveats

- This is sensitivity analysis, not a forecast of realized liquidations.
- It excludes collateral price shocks and behavioral responses.
- A separate post will propose a balanced parameter set.

## Reproducibility Artifacts

- Base user snapshot: `/home/ubuntu/RLD/scripts/artifacts/usdc_hf_sorted_envio_reconstruction_2026-04-23.csv`
- Projection dataset: `/home/ubuntu/RLD/scripts/artifacts/usdc_irm_sensitivity_projection_2026-04-23.csv`
- Summary dataset: `/home/ubuntu/RLD/scripts/artifacts/usdc_irm_sensitivity_summary_2026-04-23.json`