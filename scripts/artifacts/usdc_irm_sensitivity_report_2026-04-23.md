# Reply: USDC IRM User Sensitivity Update (Envio-Reconstructed)

As of **2026-04-23 07:00:00 UTC**, this update measures user-level sensitivity to the proposed USDC IRM changes using event-reconstructed debt users and live Aave account risk state.

## 1) Data and Method

- **User universe:** all addresses with positive net USDC debt from Envio indexer reconstruction (`Borrow - Repay - LiquidationCall` debt leg).
- **Risk state:** live `getUserAccountData` for each user (HF, total debt, collateral).
- **USDC debt share:** event-reconstructed USDC debt / live total debt, clamped to `[0, 1]` for stress modeling.
- **Debt accrual model:** continuous compounding approximation consistent with per-second accrual.
- **Isolation of proposal impact:** non-USDC debt APR held unchanged at current debt-weighted baseline.

## 2) Current State (USDC on Ethereum Core)

- Supply: **$1,803,583,630**
- Borrow: **$1,768,645,111**
- Available liquidity: **$34,938,518**
- True utilization (`borrow/supply`): **98.06%**
- Observed variable borrow APR: **11.68%**
- Observed supply APR: **10.32%**
- Non-USDC debt baseline APR (debt-weighted): **7.50%**

At this snapshot, the pool remains highly utilized but is **not fully pinned**; utilization has moved off absolute saturation, with liquidity buffer partially restored.

## 3) Implied USDC Borrow APR Under Proposal

- Interim (`S2=40%, U*=87%`) at current util: **37.54%**
- Target (`S2=50%, U*=85%`) at current util: **47.04%**
- Interim at pinned `U=99.87%`: **43.10%**
- Target at pinned `U=99.87%`: **53.07%**

## 4) User Sensitivity and Time-to-Liquidation

Population analyzed: **9,036** users, including **1,408** material users (`debt >= $100k`).

### Current observed APR

- **All users:** 20 users within 7d, 40 users within 30d
- **All users debt-at-risk:** $243 (7d), $915,044 (30d)
- **Material users:** 0 users within 7d, 1 users within 30d
- **Material debt-at-risk:** $0 (7d), $903,834 (30d)
- **Material TTL percentiles (days):** p05=467.2, p25=1260.2, p50=1859.0

### Interim @ current util

- **All users:** 28 users within 7d, 62 users within 30d
- **All users debt-at-risk:** $8,941 (7d), $61,391,030 (30d)
- **Material users:** 0 users within 7d, 3 users within 30d
- **Material debt-at-risk:** $0 (7d), $61,288,618 (30d)
- **Material TTL percentiles (days):** p05=249.4, p25=530.6, p50=816.1

### Target @ current util

- **All users:** 33 users within 7d, 74 users within 30d
- **All users debt-at-risk:** $10,926 (7d), $61,421,058 (30d)
- **Material users:** 0 users within 7d, 3 users within 30d
- **Material debt-at-risk:** $0 (7d), $61,288,618 (30d)
- **Material TTL percentiles (days):** p05=206.9, p25=444.3, p50=681.8

### Interim @ 99.87% util

- **All users:** 30 users within 7d, 70 users within 30d
- **All users debt-at-risk:** $9,879 (7d), $61,399,298 (30d)
- **Material users:** 0 users within 7d, 3 users within 30d
- **Material debt-at-risk:** $0 (7d), $61,288,618 (30d)
- **Material TTL percentiles (days):** p05=222.4, p25=475.6, p50=729.9

### Target @ 99.87% util

- **All users:** 36 users within 7d, 80 users within 30d
- **All users debt-at-risk:** $915,035 (7d), $70,274,704 (30d)
- **Material users:** 1 users within 7d, 5 users within 30d
- **Material debt-at-risk:** $903,834 (7d), $70,142,038 (30d)
- **Material TTL percentiles (days):** p05=187.6, p25=405.2, p50=620.2

## 5) Most Sensitive Material Accounts (Fastest TTL)

Below are fastest-to-liquidation material accounts under **Interim @ current util** scenario:


| Address                                      | HF now | Debt (USD)  | USDC share | Days to liquidation |
| -------------------------------------------- | ------ | ----------- | ---------- | ------------------- |
| `0xb0f76a4c60c6c993ac30bdda24c5f7f98a03249c` | 1.0075 | $903,834    | 93.5%      | 9.1                 |
| `0x0591926d5d3b9cc48ae6efb8db68025ddc3adfa5` | 1.0125 | $60,157,937 | 84.5%      | 16.1                |
| `0x9e283ba6d80faaf1bee7270e21eee5c375266611` | 1.0241 | $226,847    | 94.2%      | 28.5                |
| `0x8a93aae912e40dad3b64120c74dd27269acc1df7` | 1.0311 | $805,890    | 95.2%      | 36.4                |
| `0x2304eb247ae2dcf91ba372b458fc7e4ccc55ee42` | 1.0325 | $8,047,530  | 99.4%      | 36.8                |
| `0xf2035c797bc9ef9f3396e747e8b73907d420770c` | 1.0311 | $209,231    | 83.4%      | 40.1                |
| `0x6f10b22517ab7a3f5aa722e62a6917a3a7c2e2b8` | 1.0280 | $1,614,480  | 71.4%      | 40.5                |
| `0xd9154e919707845d2e3a77ae3831bf6bd2f757f8` | 1.0461 | $132,756    | 97.9%      | 52.4                |
| `0xc0c7a4627fcdd79c6aeb1f408111fb77a2318526` | 1.0468 | $9,686,653  | 87.4%      | 57.9                |
| `0xc9003eb18755671491d3ce21022d4805921ecf94` | 1.0559 | $147,627    | 97.8%      | 63.3                |


## 6) Interpretation

- The long tail of dust accounts dominates count-based stress metrics; debt-weighted metrics are more informative for systemic risk.
- With utilization below full saturation at this snapshot, projected liquidation clocks are materially longer than fully pinned stress assumptions.
- Re-pinning toward ~100% utilization still compresses liquidation clocks sharply under interim/target curves, concentrated in low-HF, high-USDC-share wallets.

## 7) Caveats

- Projections assume static collateral prices and no user intervention (repay/top-up/position migration).
- Non-USDC debt accrual is held constant at a market-wide baseline; user-specific non-USDC APR may differ.
- USDC debt share is reconstructed from event flow and mapped to current debt for scenario weighting.

## 8) Artifacts

- User-level base snapshot: `/home/ubuntu/RLD/scripts/artifacts/usdc_hf_sorted_envio_reconstruction_2026-04-23.csv`
- User-level sensitivity projections: `/home/ubuntu/RLD/scripts/artifacts/usdc_irm_sensitivity_projection_2026-04-23.csv`
- Machine-readable summary: `/home/ubuntu/RLD/scripts/artifacts/usdc_irm_sensitivity_summary_2026-04-23.json`