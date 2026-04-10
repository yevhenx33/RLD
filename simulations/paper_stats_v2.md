================================================================================
PAPER-READY BACKTEST SECTION
================================================================================

**Simulation Parameters:**

- **Market selection**: 12 Morpho Blue USDC vaults with >$100k total supply, active throughout the evaluation period (April 2025 — April 2026)
- **Decay rate**: $F = -\ln(1 - \delta)$ with $\delta = 0.80$, yielding $F = 1.609$
- **Terminal failure definition**: Utilization $U_t \ge 0.99$ sustained for 7 consecutive days (168 hours)
- **Collateral**: $P_{max} = \$100$ escrow per unit in exogenous assets (simulated wstETH at 3.5\% staking yield)
- **Risk-free rate**: 4\% annualized (T-bill proxy for Sharpe/Sortino computation)

**Table A: Initial Market Allocation and Tiering**

| Collateral | Tier | Init. Rate | Init. Util. | EW Alloc. | RB Weight | RB Alloc. |
|---|---|---|---|---|---|---|
| WBTC | T1 | 4.0\% | 88.4\% | \$83,333 | 0.0\% | \$178,571 |
| cbBTC | T1 | 4.0\% | 88.3\% | \$83,333 | 0.0\% | \$178,571 |
| wstETH | T1 | 4.2\% | 88.8\% | \$83,333 | 0.0\% | \$178,571 |
| tBTC | T1 | 6.0\% | 80.2\% | \$83,333 | 0.0\% | \$178,571 |
| srUSD | T2 | 5.4\% | 79.4\% | \$83,333 | 0.0\% | \$59,524 |
| syrupUSDC | T2 | 4.4\% | 60.0\% | \$83,333 | 0.0\% | \$59,524 |
| sUSDe | T2 | 6.0\% | 82.8\% | \$83,333 | 0.0\% | \$59,524 |
| LBTC | T2 | 4.7\% | 90.1\% | \$83,333 | 0.0\% | \$59,524 |
| USR | T3 | 3.9\% | 83.3\% | \$83,333 | 0.0\% | \$11,905 |
| sdeUSD | T3 | 0.1\% | 90.0\% | \$83,333 | 0.0\% | \$11,905 |
| RLP | T3 | 5.9\% | 78.3\% | \$83,333 | 0.0\% | \$11,905 |
| USCC | T3 | 2.3\% | 60.3\% | \$83,333 | 0.0\% | \$11,905 |

**Default Events:** 4 of 12 markets (33\%) experienced terminal settlement:

| Market | Default Day | Classification |
|---|---|---|
| USR | Day 356.0 | Tier 3 (Exotic) |
| sdeUSD | Day 218.0 | Tier 3 (Exotic) |
| RLP | Day 356.0 | Tier 3 (Exotic) |
| USCC | Day 221.0 | Tier 3 (Exotic) |

All four defaulted markets belong to the exotic (Tier 3) tranche. Zero blue-chip (Tier 1) or intermediate (Tier 2) markets defaulted during the evaluation period.

**Table B: Portfolio Risk-Adjusted Performance**

| Metric | EW Passive | EW Underwriter | RB Passive | RB Underwriter |
|---|---|---|---|---|
| Terminal Value | \$699,469 | \$939,284 | \$996,362 | \$1,025,548 |
| Return | -30.05\% | -5.91\% | -0.36\% | +2.91\% |
| Ann. Volatility | 23.70\% | 11.53\% | 3.01\% | 11.34\% |
| Sharpe Ratio | -1.55 | -0.82 | -1.43 | -0.04 |
| Sortino Ratio | -0.82 | -0.76 | -0.85 | -0.04 |
| VaR (95\%, daily) | 0.007\% | -1.070\% | 0.006\% | -0.852\% |
| VaR (99\%, daily) | -0.005\% | -2.005\% | -0.004\% | -1.687\% |
| Max Drawdown | -32.46\% | -8.56\% | -3.48\% | -4.29\% |

Under equal-weight allocation, the passive depositor suffered a 30.1\% loss driven by complete capital destruction in four defaulted markets. The CDS underwriter's structural amortization reduced this to a 5.9\% loss — the exponential decay of the Normalization Factor erased 80\% of the default liability before settlement.

Under risk-budgeted allocation — weighting Tier 1 (blue-chip) collateral at $3\times$ and Tier 3 (exotic) at $0.2\times$ — the CDS underwriter achieved a strictly positive return of +2.91\% with a Sharpe ratio of -0.04 and Sortino ratio of -0.04, while the passive depositor realized -0.36\%. The 99\% daily Value at Risk of the risk-budgeted underwriter was -1.687\%, confirming bounded tail exposure.

================================================================================
RAW METRICS (for verification)
================================================================================

ew_passive:
  label: EW Passive
  terminal_value: 699468.5519671432
  terminal_return_pct: -30.052730789199167
  ann_return: -32.65195367393769
  ann_vol: 23.695631113570848
  sharpe: -1.5467810710872587
  sortino: -0.8172582792227232
  var_95: 0.006714183811885497
  var_99: -0.004862413023900453
  max_drawdown: -32.46334405706361
  mean_daily_ret: -0.08945740732585668
  n_days: 364

ew_cds:
  label: EW CDS
  terminal_value: 939283.8366780367
  terminal_return_pct: -5.906541608755056
  ann_return: -5.439454094728862
  ann_vol: 11.531968981262985
  sharpe: -0.8185466081348276
  sortino: -0.7629782574164593
  var_95: -1.0704288977892167
  var_99: -2.0049854132710268
  max_drawdown: -8.557924829521237
  mean_daily_ret: -0.014902613958161264
  n_days: 364

rb_passive:
  label: RB Passive
  terminal_value: 996361.8719530007
  terminal_return_pct: -0.36318372098722573
  ann_return: -0.31892822481365657
  ann_vol: 3.0136149519296858
  sharpe: -1.4331387034193432
  sortino: -0.8522921024065692
  var_95: 0.006064276101874012
  var_99: -0.004447736437007664
  max_drawdown: -3.482112870615476
  mean_daily_ret: -0.0008737759583935795
  n_days: 364

rb_cds:
  label: RB CDS
  terminal_value: 1025547.6157075587
  terminal_return_pct: 2.906368317161201
  ann_return: 3.515538944166229
  ann_vol: 11.34363124142248
  sharpe: -0.042707757817859036
  sortino: -0.042133588741943825
  var_95: -0.8524894164633269
  var_99: -1.6870414169425196
  max_drawdown: -4.29208779518276
  mean_daily_ret: 0.0096316135456609
  n_days: 364
