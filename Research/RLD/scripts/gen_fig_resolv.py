#!/usr/bin/env python3
"""
Generate Figure: Liquidity Crisis — The "Resolv" Default (Mar 2026)
Publication-quality dual-axis chart for LaTeX whitepaper.
Uses morpho_enriched_final.db dataset for USR market.
"""

import sqlite3
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────────
DB_PATH = "/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db"
OUTPUT  = "../figures/resolv_liquidity_crisis.png"
MARKET_ID = '0x8e7cc042d7' # USR (Resolv) Core Default Market

# ── Load Data ───────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)
start_ts = int(datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc).timestamp())
end_ts = int(datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc).timestamp())

q = '''
SELECT timestamp, total_supply_assets, total_borrow_assets, borrow_apy, supply_apy
FROM market_snapshots
WHERE market_id LIKE ? || '%' AND timestamp BETWEEN ? AND ?
ORDER BY timestamp ASC
'''
df = pd.read_sql_query(q, conn, params=(MARKET_ID, start_ts, end_ts))
conn.close()

if df.empty:
    print("Error: No data found for Resolv in specified date range.")
    exit(1)

dates = pd.to_datetime(df['timestamp'], unit='s', utc=True).values
supply_apy = df['supply_apy'].astype(float).values * 100
borrow_apy = df['borrow_apy'].astype(float).values * 100
borrows = df['total_borrow_assets'].astype(float).values / 1e6
deposits = df['total_supply_assets'].astype(float).values / 1e6

# ── Classic LaTeX Style ─────────────────────────────────────────────────────
plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Computer Modern Roman', 'CMU Serif', 'DejaVu Serif', 'Times New Roman'],
    'font.size': 9,
    'axes.labelsize': 10,
    'axes.titlesize': 11,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'legend.fontsize': 7.5,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'axes.linewidth': 0.6,
    'grid.linewidth': 0.4,
    'lines.linewidth': 1.0,
    'text.usetex': False,
})

fig, ax1 = plt.subplots(figsize=(7.0, 3.5))

# ── Left axis: APY (%) ─────────────────────────────────────────────────────
color_borrow = '#e74c3c'
color_supply = '#2ecc71'

ax1.plot(dates, borrow_apy, color=color_borrow, linewidth=0.9, label='Borrow APY', zorder=3)
ax1.plot(dates, supply_apy, color=color_supply, linewidth=0.7, linestyle='-', alpha=0.7, label='Supply APY', zorder=3)
ax1.set_ylabel('APY (%)')
ax1.set_ylim(-2, 105) # Resolv hit ~100% APY, give it some headroom
ax1.tick_params(axis='y')

# ── Right axis: Volume (USD) ───────────────────────────────────────────────
ax2 = ax1.twinx()

ax2.fill_between(dates, 0, borrows, alpha=0.15, color='#f39c12', label='Total Borrows', zorder=1, step='mid')
ax2.plot(dates, deposits, color='#2980b9', linewidth=1.5, linestyle='--', label='Total Deposits', zorder=2)

ax2.set_ylabel('Volume (USD)')
ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: f'${x/1e6:.1f}M' if x >= 1e6 else f'${x/1e3:.0f}K'))
ax2.set_ylim(bottom=0, top=max(deposits.max(), borrows.max()) * 1.2)

# ── Find and annotate the default event ─────────────────────────────────────
# Find the peak event: where borrow APY is highest
crisis_idx = np.argmax(borrow_apy)
peak_date = dates[crisis_idx]
peak_apy = borrow_apy[crisis_idx]

# Shade the crisis period (rough approximation around peak)
crisis_start = peak_date - np.timedelta64(1, 'D')
crisis_end   = peak_date + np.timedelta64(10, 'D')
ax1.axvspan(crisis_start, crisis_end, alpha=0.08, color='black', zorder=0)

# Add annotation arrow (mirroring Stream style but adjusted for this data)
ax1.annotate(
    'Resolv Default\n(100% Utilization)',
    xy=(peak_date, min(peak_apy, 95)),
    xytext=(peak_date - np.timedelta64(25, 'D'), 55),
    fontsize=8, ha='left', va='top',
    style='italic', color='#333333',
    arrowprops=dict(arrowstyle='->', color='#555555', lw=1.2),
    bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#aaaaaa', linewidth=0.5),
    zorder=5,
)

# Mark the frozen market period (post-default)
ax1.annotate(
    'Frozen Market\nAPY \u2192 0%\nBad debt remains',
    xy=(peak_date + np.timedelta64(20, 'D'), 8),
    fontsize=7, ha='center', va='bottom',
    color='#666666', style='italic',
    bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#cccccc', linewidth=0.4),
    zorder=5,
)

# ── X-axis formatting ─────────────────────────────────────────────────────
ax1.xaxis.set_major_locator(mdates.MonthLocator())
ax1.xaxis.set_minor_locator(mdates.WeekdayLocator(byweekday=0))
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%d-%b'))
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=35, ha='right', fontsize=8)

# ── Grid ───────────────────────────────────────────────────────────────────
ax1.grid(True, axis='y', linestyle=':', alpha=0.3, color='gray')
ax1.grid(True, axis='x', linestyle=':', alpha=0.15, color='gray', which='major')

# ── Combined Legend ────────────────────────────────────────────────────────
lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2,
           loc='upper left', framealpha=0.95, edgecolor='#cccccc',
           fancybox=False, ncol=2)

# ── Title ──────────────────────────────────────────────────────────────────
ax1.set_title('Liquidity Crisis: The "Resolv" Default (Mar 2026)',
              fontweight='bold', pad=10)

# ── Tight layout & save ───────────────────────────────────────────────────
fig.tight_layout()
fig.savefig(OUTPUT, bbox_inches='tight', facecolor='white', edgecolor='none')
print(f"\n✓ Saved to {OUTPUT}")
plt.close()
