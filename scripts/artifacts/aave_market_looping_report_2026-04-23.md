# [Data] Aave Markets: Loop-Dominated Activity and Collateral Health

This report combines event-reconstructed user-level per-market exposures with live account-level equity to locate markets where activity appears most recursive.

## Definitions

- **High-loop user:** `collateral_to_equity >= 4x`, `debt_to_collateral >= 60%`, `HF >= 1`.
- **Extreme-loop user:** `collateral_to_equity >= 8x`, `debt_to_collateral >= 75%`, `HF >= 1`.
- **Equity backing ratio (supply side):** supply weighted by each supplier's `equity/collateral` (clipped to [0,1]).
- **Healthy collateral score:** `equity_backing_ratio * (1-supply_low_hf_share) * (1-supply_extreme_loop_share)`.

Material user threshold used upstream in account profiles: **$100,000**.

## Markets Most Loop-Dominated on Debt Side

Filter: debt >= **$50,000,000**; ranked by `debt_high_loop_share`.

| Symbol | Debt | High-loop debt share | Extreme-loop debt share | Debt low-HF share | Borrowers |
|---|---:|---:|---:|---:|---:|
| WETH | $4,858,248,713 | 97.7% | 92.5% | 95.1% | 2,341 |
| wstETH | $69,770,139 | 78.9% | 73.0% | 75.9% | 359 |
| USDe | $207,593,900 | 41.2% | 24.9% | 27.6% | 778 |
| USDTB | $139,255,106 | 31.3% | 30.2% | 43.4% | 332 |
| USDT | $1,784,372,341 | 10.0% | 3.7% | 7.6% | 8,935 |
| USDC | $1,625,705,511 | 5.6% | 4.3% | 6.5% | 9,085 |
| GHO | $95,562,569 | 2.0% | 1.0% | 4.2% | 1,428 |
| WBTC | $108,842,170 | 0.6% | 0.0% | 13.8% | 1,175 |
| DAI | $102,499,347 | 0.0% | 0.0% | 0.1% | 611 |

## Healthiest Collateral Markets

Filter: supply >= **$50,000,000**; ranked by `healthy_collateral_score`.

| Symbol | Supply | Equity backing ratio | Supply high-loop share | Supply extreme-loop share | Supply low-HF share |
|---|---:|---:|---:|---:|---:|
| EURC | $59,169,426 | 83.3% | 0.5% | 0.0% | 7.5% |
| cbETH | $72,267,126 | 76.2% | 1.1% | 1.1% | 1.4% |
| USDe | $3,992,754,505 | 74.8% | 3.4% | 0.6% | 2.0% |
| PT-eUSDE-14AUG2025 | $4,019,648,065 | 69.5% | 0.1% | 0.1% | 0.1% |
| USDT | $6,734,762,871 | 70.5% | 1.0% | 0.1% | 2.8% |
| USDC | $10,544,909,941 | 72.9% | 6.1% | 0.5% | 7.9% |
| PT-USDe-31JUL2025 | $1,307,269,986 | 65.6% | 0.0% | 0.0% | 0.0% |
| PT-USDe-27NOV2025 | $160,249,256 | 64.4% | 0.1% | 0.0% | 4.2% |
| USDTB | $181,124,838 | 61.3% | 0.0% | 0.0% | 0.1% |
| DAI | $405,477,332 | 58.9% | 0.2% | 0.1% | 0.7% |
| tBTC | $220,543,726 | 57.4% | 0.1% | 0.0% | 0.5% |
| XAUt | $58,511,235 | 54.4% | 0.0% | 0.0% | 0.0% |
| WBTC | $5,228,818,985 | 50.3% | 0.9% | 0.1% | 2.2% |
| LBTC | $155,204,153 | 49.7% | 0.9% | 0.6% | 0.9% |
| LINK | $400,422,004 | 51.4% | 3.6% | 0.1% | 5.6% |

## Most Synthetic / Recursively-Backed Collateral Markets

Filter: supply >= **$50,000,000**; ranked by `synthetic_or_looped_supply_ratio`.

| Symbol | Supply | Synthetic/looped supply ratio | Equity backing ratio | Supply extreme-loop share |
|---|---:|---:|---:|---:|
| GHO | $165,000,000 | 100.0% | 0.0% | 0.0% |
| PT-sUSDE-5FEB2026 | $64,157,540 | 100.0% | 0.0% | 0.0% |
| rsETH | $1,312,912,100 | 91.5% | 8.5% | 91.6% |
| osETH | $320,137,185 | 91.5% | 8.5% | 88.2% |
| PT-sUSDE-31JUL2025 | $221,452,331 | 90.5% | 9.5% | 0.9% |
| cbBTC | $69,359,720,876 | 86.9% | 13.1% | 6.5% |
| PT-sUSDE-7MAY2026 | $107,613,160 | 86.7% | 13.3% | 56.9% |
| weETH | $4,553,608,386 | 85.1% | 14.9% | 58.3% |
| USDG | $54,770,988 | 80.6% | 19.4% | 0.0% |
| PT-sUSDE-25SEP2025 | $177,599,909 | 78.8% | 21.2% | 1.3% |
| USDS | $306,975,438 | 77.5% | 22.5% | 0.4% |
| sUSDe | $809,038,885 | 77.4% | 22.6% | 24.0% |
| PT-USDe-25SEP2025 | $643,275,359 | 76.1% | 23.9% | 18.3% |
| PT-sUSDE-27NOV2025 | $92,790,780 | 66.9% | 33.1% | 7.9% |
| WETH | $46,020,120,877 | 63.7% | 36.3% | 0.2% |
