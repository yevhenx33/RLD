# [Data] Aave Core: Gross TVL vs Net Equity

This computes user-level net equity (`totalCollateralBase - totalDebtBase`) across all active Aave v3 Ethereum Core users.

## Snapshot

- Snapshot time: **2026-04-23 20:00:00 UTC**
- Active users (collateral>0 or debt>0): **83,526**
- Debtors: **27,450**
- Suppliers: **83,523**
- Material users (`collateral >= $100,000` or debt >= $100,000): **8,062**
- HF < 1 users: **656**

## Gross vs Net

- Gross collateral: **$20,602,085,158**
- Gross debt: **$9,906,187,026**
- Net equity (collateral - debt): **$10,695,898,132**

- Debt / collateral: **48.08%**
- Equity share of collateral: **51.92%**
- Collateral / equity (loop multiple proxy): **1.93x**
- Debt / equity: **0.93x**

## Concentration

- Top 1 debt share: **12.26%**
- Top 5 debt share: **29.37%**
- Top 10 debt share: **36.39%**
- Top 20 debt share: **45.93%**

## Largest Debt Account

- Address: `0xf0bb20865277abd641a307ece5ee04e79073416c`
- Collateral: **$1,337,910,508**
- Debt: **$1,214,738,471**
- Net equity: **$123,172,037**
- HF: **1.0463**

## Protocol Market Totals (api_market_latest)

- Sum supply across symbols: **$21,356,978,254**
- Sum borrow across symbols: **$9,946,385,443**
- Net supply-borrow: **$11,410,592,811**

| Symbol | Supply | Borrow | Utilization |
|---|---:|---:|---:|
| WETH | $5,340,257,686 | $5,340,931,936 | 85.00% |
| weETH | $3,433,757,791 | $139,098 | 0.00% |
| WBTC | $2,304,134,877 | $112,065,718 | 2.43% |
| wstETH | $2,197,119,882 | $70,386,265 | 2.08% |
| USDT | $1,931,547,123 | $1,929,741,638 | 89.92% |
| USDC | $1,772,809,550 | $1,763,275,175 | 89.56% |
| rsETH | $1,278,458,821 | $102,311 | 0.01% |
| cbBTC | $978,081,303 | $46,785,464 | 2.39% |
| osETH | $316,502,936 | $282 | 0.00% |
| sUSDe | $234,773,911 | $0 | 0.00% |
| USDe | $229,993,581 | $229,797,825 | 75.00% |
| USDTB | $179,149,664 | $139,992,886 | 62.51% |
| GHO | $165,123,561 | $97,959,070 | 0.00% |
| tBTC | $156,847,049 | $859,262 | 0.27% |
| LINK | $137,332,470 | $8,690,163 | 4.74% |
