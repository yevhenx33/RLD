================================================================================
ENHANCED CDS PAPER — BACKTEST DESCRIPTIVE STATISTICS
================================================================================

Evaluation Period: April 7, 2025 → April 6, 2026 (365 days)
Initial Capital: $1,000,000
Number of Markets: 12
Decay Rate F = -ln(1 - 0.8) = 1.6094
P_max = $100 (per unit)

### Table 1: Initial Market Allocation

| Market | Collateral | Tier | Init. Rate | Init. Util. | EW Alloc. | RB Weight | RB Alloc. |
|---|---|---|---|---|---|---|---|
| `0x3a85e61975` | WBTC | T1 | 4.0% | 88.4% | $83,333 | 17.9% | $178,571 |
| `0x64d65c9a2d` | cbBTC | T1 | 4.0% | 88.3% | $83,333 | 17.9% | $178,571 |
| `0xb323495f7e` | wstETH | T1 | 4.2% | 88.8% | $83,333 | 17.9% | $178,571 |
| `0xe4cfbee9af` | tBTC | T1 | 6.0% | 80.2% | $83,333 | 17.9% | $178,571 |
| `0xbfed072fae` | srUSD | T2 | 5.4% | 79.4% | $83,333 | 6.0% | $59,524 |
| `0x729badf297` | syrupUSDC | T2 | 4.4% | 60.0% | $83,333 | 6.0% | $59,524 |
| `0x85c7f4374f` | sUSDe | T2 | 6.0% | 82.8% | $83,333 | 6.0% | $59,524 |
| `0xbf02d6c685` | LBTC | T2 | 4.7% | 90.1% | $83,333 | 6.0% | $59,524 |
| `0x8e7cc042d7` | USR | T3 | 3.9% | 83.3% | $83,333 | 1.2% | $11,905 |
| `0x0f9563442d` | sdeUSD | T3 | 0.1% | 90.0% | $83,333 | 1.2% | $11,905 |
| `0xe1b65304ed` | RLP | T3 | 5.9% | 78.3% | $83,333 | 1.2% | $11,905 |
| `0x1a9ccaca2d` | USCC | T3 | 2.3% | 60.3% | $83,333 | 1.2% | $11,905 |

### Default Events

- **Defaulted markets**: 4/12 (33%)
  - `USR`: default triggered at day 356.0 (mean util: 84.1%, max rate: 172.2%)
  - `sdeUSD`: default triggered at day 218.0 (mean util: 92.9%, max rate: 297995.8%)
  - `RLP`: default triggered at day 356.0 (mean util: 86.1%, max rate: 357.1%)
  - `USCC`: default triggered at day 221.0 (mean util: 83.0%, max rate: 61.7%)
- **Surviving markets**: 8/12

### Table 2: Per-Market Terminal Values (Equal-Weight)

| Market | Defaulted | Passive Final | CDS Final | Passive P&L | CDS P&L |
|---|---|---|---|---|---|
| WBTC | No | $86,655 | $86,213 | $+3,322 | $+2,879 |
| cbBTC | No | $86,617 | $86,274 | $+3,284 | $+2,941 |
| wstETH | No | $86,646 | $86,375 | $+3,312 | $+3,042 |
| tBTC | No | $87,801 | $87,676 | $+4,468 | $+4,342 |
| srUSD | No | $88,421 | $87,737 | $+5,087 | $+4,403 |
| syrupUSDC | No | $87,897 | $86,544 | $+4,564 | $+3,210 |
| sUSDe | No | $87,741 | $86,148 | $+4,407 | $+2,814 |
| LBTC | No | $87,691 | $86,575 | $+4,358 | $+3,242 |
| USR | **YES** | $0 | $69,258 | $-83,333 | $-14,075 |
| sdeUSD | **YES** | $0 | $51,635 | $-83,333 | $-31,699 |
| RLP | **YES** | $0 | $70,912 | $-83,333 | $-12,422 |
| USCC | **YES** | $0 | $53,938 | $-83,333 | $-29,395 |

### Table 3: Per-Market Terminal Values (Risk-Budgeted)

| Market | Tier | RB Alloc. | Passive Final | CDS Final | Passive P&L | CDS P&L |
|---|---|---|---|---|---|---|
| WBTC | T1 | $178,571 | $185,690 | $184,741 | $+7,118 | $+6,170 |
| cbBTC | T1 | $178,571 | $185,608 | $184,874 | $+7,036 | $+6,302 |
| wstETH | T1 | $178,571 | $185,669 | $185,090 | $+7,098 | $+6,519 |
| tBTC | T1 | $178,571 | $188,146 | $187,877 | $+9,574 | $+9,305 |
| srUSD | T2 | $59,524 | $63,158 | $62,669 | $+3,634 | $+3,145 |
| syrupUSDC | T2 | $59,524 | $62,784 | $61,817 | $+3,260 | $+2,293 |
| sUSDe | T2 | $59,524 | $62,672 | $61,534 | $+3,148 | $+2,010 |
| LBTC | T2 | $59,524 | $62,636 | $61,840 | $+3,113 | $+2,316 |
| USR | T3 | $11,905 | $0 | $9,894 | $-11,905 | $-2,011 |
| sdeUSD | T3 | $11,905 | $0 | $7,376 | $-11,905 | $-4,528 |
| RLP | T3 | $11,905 | $0 | $10,130 | $-11,905 | $-1,775 |
| USCC | T3 | $11,905 | $0 | $7,705 | $-11,905 | $-4,199 |

### Table 4: Portfolio Summary

**Equal-Weight:**
| Metric | Passive | CDS Underwriter |
|---|---|---|
| Initial Capital | $1,000,000 | $1,000,000 |
| Terminal Value | $699,469 | $939,284 |
| P&L | $-300,531 | $-60,716 |
| Return | -30.05% | -6.07% |

**Risk-Budgeted:**
| Metric | Passive | CDS Underwriter |
|---|---|---|
| Initial Capital | $1,000,000 | $1,000,000 |
| Terminal Value | $996,362 | $1,025,548 |
| P&L | $-3,638 | $+25,548 |
| Return | -0.36% | +2.55% |

### Table 5: Rate Distribution (Non-Defaulting Markets)

| Statistic | Value |
|---|---|
| Mean borrow APY (time-avg) | 5.62% |
| Median borrow APY (time-avg) | 5.98% |
| Max borrow APY (any snapshot) | 60.84% |
| Mean utilization (time-avg) | 85.52% |

### Table 6: Defaulting Market Characteristics

| Statistic | Value |
|---|---|
| Number of defaults | 4 |
| Mean days to default | 288 |
| Earliest default | Day 218 |
| Latest default | Day 356 |
| Mean util. of defaulting markets | 86.5% |
| EW capital exposed to defaults | $333,333 (33.3%) |
| EW residual CDS value post-default | $245,742 |
| RB capital exposed to defaults | $47,619 (4.8%) |
| RB residual CDS value post-default | $35,106 |

================================================================================
END OF STATISTICS
================================================================================
