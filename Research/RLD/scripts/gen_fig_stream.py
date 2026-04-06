#!/usr/bin/env python3
"""
Generate Figure: Liquidity Crisis — The "Stream Finance" Default (Nov 2025)
Publication-quality dual-axis chart for LaTeX whitepaper.
Uses euler_stream_case.csv dataset.
"""

import csv
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────────
CSV_PATH = "../datasets/euler_stream_case.csv"
OUTPUT   = "../figures/stream_finance_crisis.png"

# ── Load CSV ────────────────────────────────────────────────────────────────
dates, supply_apy, borrow_apy, borrows, deposits = [], [], [], [], []

with open(CSV_PATH, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        ts = row['Timestamp'].strip()
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        dates.append(dt)
        supply_apy.append(float(row['Supply APY (%)']))
        borrow_apy.append(float(row['Borrow APY (%)']))
        borrows.append(float(row['Total Borrows']))
        deposits.append(float(row['Total Deposits']))

dates = np.array(dates)
supply_apy = np.array(supply_apy)
borrow_apy = np.array(borrow_apy)
borrows = np.array(borrows)
deposits = np.array(deposits)

print(f"Loaded {len(dates)} data points")
print(f"Date range: {dates[0].date()} → {dates[-1].date()}")
print(f"Borrow APY range: {borrow_apy.min():.2f}% – {borrow_apy.max():.2f}%")
print(f"Max borrows: ${borrows.max()/1e6:.1f}M, Max deposits: ${deposits.max()/1e6:.1f}M")

# ── Classic LaTeX Style ─────────────────────────────────────────────────────
plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Computer Modern Roman', 'CMU Serif', 'DejaVu Serif', 'Times New Roman'],
    'font.size': 10,
    'axes.labelsize': 11,
    'axes.titlesize': 12,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 8.5,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'axes.linewidth': 0.6,
    'grid.linewidth': 0.4,
    'lines.linewidth': 1.0,
    'text.usetex': False,
})

fig, ax1 = plt.subplots(figsize=(7.0, 3.5))

# ── Left axis: APY (%) ─────────────────────────────────────────────────────
color_borrow = '#2c2c2c'
color_supply = '#666666'

ax1.plot(dates, borrow_apy, color=color_borrow, linewidth=0.9, label='Borrow APY', zorder=3)
ax1.plot(dates, supply_apy, color=color_supply, linewidth=0.7, linestyle='-', alpha=0.7, label='Supply APY', zorder=3)
ax1.set_ylabel('APY (%)')
ax1.set_ylim(-2, 80)
ax1.tick_params(axis='y')

# ── Right axis: Volume (USD) ───────────────────────────────────────────────
ax2 = ax1.twinx()

ax2.fill_between(dates, 0, borrows, alpha=0.15, color='#333333', label='Total Borrows', zorder=1, step='mid')
ax2.plot(dates, deposits, color='#888888', linewidth=0.7, linestyle='--', label='Total Deposits', zorder=2)

ax2.set_ylabel('Volume (USD)')
ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: f'${x/1e6:.0f}M' if x < 1e9 else f'${x/1e9:.1f}B'))
ax2.set_ylim(bottom=0)

# ── Find and annotate the default event ─────────────────────────────────────
# The crisis is when borrows ≈ deposits (100% utilization) and APY spikes
# This happens around early November 2025

# Find the peak event: where borrow APY is highest
crisis_idx = np.argmax(borrow_apy)
crisis_date = dates[crisis_idx]
crisis_apy = borrow_apy[crisis_idx]

# Also find the period where utilization = borrows/deposits approaches 1.0
util_ratio = np.where(deposits > 1000, borrows / deposits, 0)
max_util_idx = np.argmax(util_ratio)
max_util_date = dates[max_util_idx]

# Shade the crisis period (around Nov 4, 2025)
crisis_start = datetime(2025, 11, 3, tzinfo=timezone.utc)
crisis_end   = datetime(2025, 11, 12, tzinfo=timezone.utc)
ax1.axvspan(crisis_start, crisis_end, alpha=0.08, color='black', zorder=0)

# Add annotation arrow
# Find the highest APY near the crisis window
crisis_mask = (dates >= crisis_start) & (dates <= crisis_end)
if crisis_mask.any():
    crisis_window_apy = borrow_apy[crisis_mask]
    crisis_window_dates = dates[crisis_mask]
    peak_in_window = np.argmax(crisis_window_apy)
    peak_date = crisis_window_dates[peak_in_window]
    peak_apy = crisis_window_apy[peak_in_window]
else:
    peak_date = crisis_date
    peak_apy = crisis_apy

ax1.annotate(
    'Stream Default\n(100% Utilization)',
    xy=(peak_date, min(peak_apy, 70)),
    xytext=(peak_date + np.timedelta64(15, 'D'), 65),
    fontsize=8, ha='left', va='top',
    style='italic', color='#333333',
    arrowprops=dict(arrowstyle='->', color='#555555', lw=1.2),
    bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#aaaaaa', linewidth=0.5),
    zorder=5,
)

# Mark the frozen market period (post-default: borrows ≈ deposits, rates collapse)
frozen_start = datetime(2025, 11, 15, tzinfo=timezone.utc)
frozen_end   = datetime(2026, 2, 9, tzinfo=timezone.utc)
ax1.annotate(
    'Frozen Market\nAPY → 0%\nBad debt remains',
    xy=(datetime(2025, 12, 15, tzinfo=timezone.utc), 8),
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
ax1.set_title('Liquidity Crisis: The "Stream Finance" Default (Nov 2025)',
              fontweight='bold', pad=10)

# ── Tight layout & save ───────────────────────────────────────────────────
fig.tight_layout()
fig.savefig(OUTPUT, bbox_inches='tight', facecolor='white', edgecolor='none')
print(f"\n✓ Saved to {OUTPUT}")
plt.close()
