# [Data] WETH IRM Crisis Sensitivity (Aave v3 Ethereum Core)

This analysis reconstructs the live WETH-debt borrower set from Aave events and joins it with current `getUserAccountData` risk state to test IRM-rate scenarios.

## Why this matters

- Changing IRM primarily changes **debt accrual speed** and liquidation clocks; it does not directly unblock withdrawals if the blocker is a freeze/queue mechanism.
- This quantifies how much the Apr-20 WETH IRM flattening likely reduced liquidation pressure on loop-heavy accounts.

## Snapshot

- Snapshot time: **2026-04-23 19:00:00 UTC**
- WETH utilization: **100.01%**
- WETH observed borrow APR: **5.00%**
- WETH observed supply APR: **4.25%**
- WETH borrow / supply: **$5,338,396,294 / $5,337,794,356**
- On-chain available liquidity: **0.0065 WETH** (**$15**)
- WETH price used for event debt conversion: **$2,332.16**
- Non-WETH debt-weighted baseline APR: **12.47%**
- Users with positive reconstructed WETH debt: **2,328**
- Material users (`debt >= $100,000` and HF>=1): **451**

## Scenario APRs

| Scenario | APR |
|---|---:|
| Post-change flat (observed) | 5.00% |
| Pre-change regime (observed peak) | 8.35% |
| Hypothetical re-steepen (12%) | 12.00% |
| Hypothetical crisis (20%) | 20.00% |

Pre-change anchor APR was taken from **2026-04-19 11:00:00 UTC** at **8.35%**.

## Material Debt-at-Risk

| Scenario | 7d users | 7d debt-at-risk | 30d users | 30d debt-at-risk |
|---|---:|---:|---:|---:|
| Post-change flat (observed) | 0 | $0 | 2 | $15,264,313 |
| Pre-change regime (observed peak) | 1 | $2,853,248 | 5 | $24,817,667 |
| Hypothetical re-steepen (12%) | 1 | $2,853,248 | 7 | $105,130,240 |
| Hypothetical crisis (20%) | 2 | $15,264,313 | 17 | $158,847,258 |

## Charts

![Material WETH debt-at-risk bar chart](<UPLOAD_WETH_CHART_1_URL>)
_Local artifact: `/home/ubuntu/RLD/scripts/artifacts/chart_weth_material_debt_risk_7d_30d_2026-04-23.png`_

![Cumulative WETH debt-at-risk](<UPLOAD_WETH_CHART_2_URL>)
_Local artifact: `/home/ubuntu/RLD/scripts/artifacts/chart_weth_cumulative_material_debt_90d_2026-04-23.png`_

## Largest WETH-Debt Concentration

- Address: `0xf0bb20865277abd641a307ece5ee04e79073416c`
- Total debt: **$1,214,737,293**
- Reconstructed WETH debt: **507,259.44 WETH** (**$1,183,008,173**)
- Current HF: **1.0463**

| Scenario | Days to liquidation | HF @ 7d | HF @ 30d |
|---|---:|---:|---:|
| Post-change flat (observed) | 326.4 | 1.0453 | 1.0420 |
| Pre-change regime (observed peak) | 203.6 | 1.0447 | 1.0394 |
| Hypothetical re-steepen (12%) | 145.7 | 1.0441 | 1.0366 |
| Hypothetical crisis (20%) | 91.5 | 1.0427 | 1.0309 |

## Top 10 WETH-Debt Accounts (by total debt)

| Rank | Address | Total debt | WETH debt (events) | HF |
|---:|---|---:|---:|---:|
| 1 | `0xf0bb20865277abd641a307ece5ee04e79073416c` | $1,214,737,293 | $1,183,008,173 | 1.0463 |
| 2 | `0x9600a48ed0f931d0c422d574e3275a90d8b22745` | $1,003,638,802 | $636,117,840 | 1.0686 |
| 3 | `0xcdfa7efe670869c6b6be4375654e0b206ef49c89` | $257,568,152 | $258,523,910 | 1.0348 |
| 4 | `0xef417fce1883c6653e7dc6af7c6f85ccde84aa09` | $243,731,076 | $242,165,292 | 1.0368 |
| 5 | `0x893aa69fbaa1ee81b536f0fbe3a3453e86290080` | $189,776,342 | $189,223,011 | 1.0311 |
| 6 | `0xf7462251c14d2fb83c7ab96367a7985423c83010` | $171,730,521 | $171,926,452 | 1.0388 |
| 7 | `0x40e93a52f6af9fcd3b476aedadd7feabd9f7aba8` | $137,965,892 | $138,173,207 | 1.0529 |
| 8 | `0x1f4c1c2e610f089d6914c4448e6f21cb0db3adef` | $121,599,865 | $122,299,623 | 1.0261 |
| 9 | `0x973ddb8ee2c9cc87e853c8d46253840c63951683` | $108,896,324 | $109,123,316 | 1.0505 |
| 10 | `0x3edc842766cb19644f7181709a243e523be29c4c` | $88,012,079 | $87,339,366 | 1.0414 |

## Read-through for the crisis question

- Flattening from `Pre-change regime (observed peak)` to `Post-change flat (observed)` reduces modeled 30-day material debt-at-risk by **$9,553,354**.
- This supports the "save loopers" objective (slower debt growth, longer liquidation clocks).
- It does **not** by itself unfreeze collateral withdrawals; that requires liquidity-path/freeze-policy resolution.

