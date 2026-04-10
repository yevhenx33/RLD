"""
Extracts descriptive statistics for the Enhanced CDS Paper backtest section.
Outputs markdown tables for:
  1. Initial allocation (both equal-weight and risk-budgeted)
  2. Per-market results at T=365d
  3. Portfolio-level summary statistics
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

DB_PATH = '/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db'

target_ts = int(datetime(2025, 4, 7, 0, 0, 0).timestamp())
end_ts = int(datetime(2026, 4, 6, 0, 0).timestamp())
SECONDS_PER_DAY = 86400

prime_markets = {
    '0x8e7cc042d7': 'USR', '0x3a85e61975': 'WBTC', '0x0f9563442d': 'sdeUSD',
    '0x64d65c9a2d': 'cbBTC', '0xb323495f7e': 'wstETH', '0xbfed072fae': 'srUSD',
    '0xe1b65304ed': 'RLP', '0x729badf297': 'syrupUSDC', '0x85c7f4374f': 'sUSDe',
    '0xbf02d6c685': 'LBTC', '0x1a9ccaca2d': 'USCC', '0xe4cfbee9af': 'tBTC'
}

INITIAL_CAPITAL = 1_000_000.0
DELTA = 0.80
F = -np.log(1 - DELTA)

# ── Tier system (risk-budgeted) ──
def get_tier_weight(symbol):
    tier_1 = {"WBTC", "cbBTC", "tBTC", "wstETH", "WETH"}
    tier_2 = {"LBTC", "srUSD", "syrupUSDC", "sUSDe"}
    if symbol in tier_1: return 3.0
    if symbol in tier_2: return 1.0
    return 0.2

raw_weights = {p: get_tier_weight(s) for p, s in prime_markets.items()}
tot_weight = sum(raw_weights.values())
rb_allocations = {p: INITIAL_CAPITAL * (w / tot_weight) for p, w in raw_weights.items()}
ew_allocation = INITIAL_CAPITAL / len(prime_markets)

conn = sqlite3.connect(DB_PATH)

results = []

for prefix, symbol in prime_markets.items():
    q = """SELECT timestamp, total_supply_assets, total_supply_shares, utilization, borrow_apy 
           FROM market_snapshots 
           WHERE market_id LIKE ? || '%' AND timestamp >= ? AND timestamp <= ? 
           ORDER BY timestamp ASC"""
    df = pd.read_sql_query(q, conn, params=(prefix, target_ts - SECONDS_PER_DAY, end_ts + SECONDS_PER_DAY))

    row = {
        'symbol': symbol,
        'prefix': prefix,
        'ew_alloc': ew_allocation,
        'rb_alloc': rb_allocations[prefix],
        'rb_weight_pct': (raw_weights[prefix] / tot_weight) * 100,
        'tier': 'T1' if get_tier_weight(symbol) == 3.0 else ('T2' if get_tier_weight(symbol) == 1.0 else 'T3'),
    }

    if df.empty:
        row.update({
            'initial_rate': None, 'initial_util': None, 'n_snapshots': 0,
            'defaulted': False, 'default_day': None,
            'mean_util': None, 'max_util': None, 'mean_rate': None, 'max_rate': None,
            'ew_passive_final': ew_allocation, 'ew_cds_final': ew_allocation,
            'rb_passive_final': rb_allocations[prefix], 'rb_cds_final': rb_allocations[prefix],
        })
        results.append(row)
        continue

    # T0 snapshot
    t0_row = df.iloc[np.argmin(np.abs(df['timestamp'] - target_ts))]
    sp0 = float(t0_row['total_supply_assets']) / float(t0_row['total_supply_shares']) if float(t0_row['total_supply_shares']) > 0 else 1.0
    initial_r = float(t0_row['borrow_apy'])
    initial_util = float(t0_row['utilization'])

    # Default detection (7-day sustained U >= 0.99)
    df['is_frozen'] = df['utilization'] >= 0.99
    df['block_id'] = (df['is_frozen'] != df['is_frozen'].shift()).cumsum()
    frozen_blocks = df[df['is_frozen']].groupby('block_id').agg(
        start_time=('timestamp', 'min'),
        end_time=('timestamp', 'max'),
        duration_sec=('timestamp', lambda x: x.max() - x.min())
    )
    default_timestamp = None
    sustained = frozen_blocks[frozen_blocks['duration_sec'] >= 7 * SECONDS_PER_DAY]
    if not sustained.empty:
        default_timestamp = sustained.iloc[0]['start_time'] + (7 * SECONDS_PER_DAY)

    # Descriptive stats
    row.update({
        'initial_rate': initial_r,
        'initial_util': initial_util,
        'n_snapshots': len(df),
        'defaulted': default_timestamp is not None,
        'default_day': int((default_timestamp - target_ts) / SECONDS_PER_DAY) if default_timestamp else None,
        'mean_util': df['utilization'].mean(),
        'max_util': df['utilization'].max(),
        'mean_rate': df['borrow_apy'].mean(),
        'max_rate': df['borrow_apy'].max(),
    })

    # Compute final values for both allocation strategies
    for label, capital in [('ew', ew_allocation), ('rb', rb_allocations[prefix])]:
        tokens_minted = capital / 100.0
        initial_price = min(100.0, 100.0 * initial_r)
        upfront_premium = tokens_minted * initial_price

        # T_end snapshot
        end_rows = df[df['timestamp'] <= end_ts]
        if end_rows.empty:
            end_row = df.iloc[-1]
        else:
            end_row = end_rows.iloc[-1]

        # Passive final
        if default_timestamp is not None:
            passive_final = 0.0
        else:
            sp_end = float(end_row['total_supply_assets']) / float(end_row['total_supply_shares']) if float(end_row['total_supply_shares']) > 0 else 1.0
            passive_final = capital * (sp_end / sp0)

        # CDS final
        dt_years = (end_ts - target_ts) / 31536000.0
        if default_timestamp is not None:
            dt_default_years = (default_timestamp - target_ts) / 31536000.0
            tokens_at_default = tokens_minted * np.exp(-F * dt_default_years)
            liability = tokens_at_default * 100.0
            cds_final = capital + upfront_premium - liability
        else:
            tokens_active = tokens_minted * np.exp(-F * dt_years)
            end_r = float(end_row['borrow_apy'])
            end_price = min(100.0, 100.0 * end_r)
            liability = tokens_active * end_price
            cds_final = capital + upfront_premium - liability

        row[f'{label}_passive_final'] = passive_final
        row[f'{label}_cds_final'] = cds_final

    results.append(row)

conn.close()

rdf = pd.DataFrame(results).sort_values('tier')

# ═══════════════════════════════════════════════════
# OUTPUT
# ═══════════════════════════════════════════════════
print("=" * 80)
print("ENHANCED CDS PAPER — BACKTEST DESCRIPTIVE STATISTICS")
print("=" * 80)
print(f"\nEvaluation Period: April 7, 2025 → April 6, 2026 (365 days)")
print(f"Initial Capital: ${INITIAL_CAPITAL:,.0f}")
print(f"Number of Markets: {len(prime_markets)}")
print(f"Decay Rate F = -ln(1 - {DELTA}) = {F:.4f}")
print(f"P_max = $100 (per unit)")
print()

# ── Table 1: Initial Allocation ──
print("### Table 1: Initial Market Allocation\n")
print("| Market | Collateral | Tier | Init. Rate | Init. Util. | EW Alloc. | RB Weight | RB Alloc. |")
print("|---|---|---|---|---|---|---|---|")
for _, r in rdf.iterrows():
    rate_str = f"{r['initial_rate']*100:.1f}%" if r['initial_rate'] is not None else "N/A"
    util_str = f"{r['initial_util']*100:.1f}%" if r['initial_util'] is not None else "N/A"
    print(f"| `{r['prefix']}` | {r['symbol']} | {r['tier']} | {rate_str} | {util_str} | ${r['ew_alloc']:,.0f} | {r['rb_weight_pct']:.1f}% | ${r['rb_alloc']:,.0f} |")

# ── Table 2: Default Events ──
defaults = rdf[rdf['defaulted']]
non_defaults = rdf[~rdf['defaulted']]
print(f"\n### Default Events\n")
print(f"- **Defaulted markets**: {len(defaults)}/{len(rdf)} ({len(defaults)/len(rdf)*100:.0f}%)")
if not defaults.empty:
    for _, r in defaults.iterrows():
        print(f"  - `{r['symbol']}`: default triggered at day {r['default_day']} (mean util: {r['mean_util']*100:.1f}%, max rate: {r['max_rate']*100:.1f}%)")
print(f"- **Surviving markets**: {len(non_defaults)}/{len(rdf)}")

# ── Table 3: Per-Market Final Results (Equal Weight) ──
print("\n### Table 2: Per-Market Terminal Values (Equal-Weight)\n")
print("| Market | Defaulted | Passive Final | CDS Final | Passive P&L | CDS P&L |")
print("|---|---|---|---|---|---|")
for _, r in rdf.iterrows():
    pnl_p = r['ew_passive_final'] - r['ew_alloc']
    pnl_c = r['ew_cds_final'] - r['ew_alloc']
    def_str = "**YES**" if r['defaulted'] else "No"
    print(f"| {r['symbol']} | {def_str} | ${r['ew_passive_final']:,.0f} | ${r['ew_cds_final']:,.0f} | ${pnl_p:+,.0f} | ${pnl_c:+,.0f} |")

# ── Table 4: Per-Market Final Results (Risk-Budgeted) ──
print("\n### Table 3: Per-Market Terminal Values (Risk-Budgeted)\n")
print("| Market | Tier | RB Alloc. | Passive Final | CDS Final | Passive P&L | CDS P&L |")
print("|---|---|---|---|---|---|---|")
for _, r in rdf.iterrows():
    pnl_p = r['rb_passive_final'] - r['rb_alloc']
    pnl_c = r['rb_cds_final'] - r['rb_alloc']
    print(f"| {r['symbol']} | {r['tier']} | ${r['rb_alloc']:,.0f} | ${r['rb_passive_final']:,.0f} | ${r['rb_cds_final']:,.0f} | ${pnl_p:+,.0f} | ${pnl_c:+,.0f} |")

# ── Summary statistics ──
print("\n### Table 4: Portfolio Summary\n")
for label, alloc_key, p_key, c_key in [
    ("Equal-Weight", 'ew_alloc', 'ew_passive_final', 'ew_cds_final'),
    ("Risk-Budgeted", 'rb_alloc', 'rb_passive_final', 'rb_cds_final')
]:
    total_alloc = rdf[alloc_key].sum()
    total_passive = rdf[p_key].sum()
    total_cds = rdf[c_key].sum()
    passive_ret = (total_passive / total_alloc - 1) * 100
    cds_ret = (total_cds / total_alloc - 1) * 100
    passive_pnl = total_passive - total_alloc
    cds_pnl = total_cds - total_alloc
    
    print(f"**{label}:**")
    print(f"| Metric | Passive | CDS Underwriter |")
    print(f"|---|---|---|")
    print(f"| Initial Capital | ${total_alloc:,.0f} | ${total_alloc:,.0f} |")
    print(f"| Terminal Value | ${total_passive:,.0f} | ${total_cds:,.0f} |")
    print(f"| P&L | ${passive_pnl:+,.0f} | ${cds_pnl:+,.0f} |")
    print(f"| Return | {passive_ret:+.2f}% | {cds_ret:+.2f}% |")
    print()

# ── Rate distribution stats ──
print("### Table 5: Rate Distribution (Non-Defaulting Markets)\n")
print("| Statistic | Value |")
print("|---|---|")
valid = rdf[(~rdf['defaulted']) & (rdf['mean_rate'].notna())]
if not valid.empty:
    print(f"| Mean borrow APY (time-avg) | {valid['mean_rate'].mean()*100:.2f}% |")
    print(f"| Median borrow APY (time-avg) | {valid['mean_rate'].median()*100:.2f}% |")
    print(f"| Max borrow APY (any snapshot) | {valid['max_rate'].max()*100:.2f}% |")
    print(f"| Mean utilization (time-avg) | {valid['mean_util'].mean()*100:.2f}% |")

# Defaulting markets stats
if not defaults.empty:
    print(f"\n### Table 6: Defaulting Market Characteristics\n")
    print("| Statistic | Value |")
    print("|---|---|")
    print(f"| Number of defaults | {len(defaults)} |")
    print(f"| Mean days to default | {defaults['default_day'].mean():.0f} |")
    print(f"| Earliest default | Day {defaults['default_day'].min():.0f} |")
    print(f"| Latest default | Day {defaults['default_day'].max():.0f} |")
    print(f"| Mean util. of defaulting markets | {defaults['mean_util'].mean()*100:.1f}% |")
    
    # EW: total capital lost to defaults vs premium captured
    ew_default_loss = defaults['ew_alloc'].sum()
    ew_default_cds_remaining = defaults['ew_cds_final'].sum()
    print(f"| EW capital exposed to defaults | ${ew_default_loss:,.0f} ({ew_default_loss/INITIAL_CAPITAL*100:.1f}%) |")
    print(f"| EW residual CDS value post-default | ${ew_default_cds_remaining:,.0f} |")
    
    rb_default_loss = defaults['rb_alloc'].sum()
    rb_default_cds_remaining = defaults['rb_cds_final'].sum()
    print(f"| RB capital exposed to defaults | ${rb_default_loss:,.0f} ({rb_default_loss/INITIAL_CAPITAL*100:.1f}%) |")
    print(f"| RB residual CDS value post-default | ${rb_default_cds_remaining:,.0f} |")

print("\n" + "=" * 80)
print("END OF STATISTICS")
print("=" * 80)
