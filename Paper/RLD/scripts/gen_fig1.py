#!/usr/bin/env python3
"""
Generate Figure 1: Interest Rate Sensitivity vs. Market Moves
Style: Colored matplotlib (matching Stream Finance chart style)
Uses aave_usdc_rates_eth_prices.csv dataset.
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
CSV_PATH = "../datasets/aave_usdc_rates_eth_prices.csv"
OUTPUT   = "../figures/rate_sensitivity.png"

# ── Load CSV ────────────────────────────────────────────────────────────────
dates, apys, eth_prices = [], [], []

with open(CSV_PATH, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        dt = datetime.strptime(row['date_utc'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        dates.append(dt)
        apys.append(float(row['apy_pct']))
        eth_prices.append(float(row['eth_price_usd']))

dates = np.array(dates)
apys = np.array(apys)
eth_prices = np.array(eth_prices)

# 7-day moving average for APY smoothing
window = 7
apys_ma = np.convolve(apys, np.ones(window)/window, mode='same')
apys_ma[:window//2] = apys[:window//2]
apys_ma[-window//2:] = apys[-window//2:]

print(f"Loaded {len(dates)} data points")
print(f"Date range: {dates[0].date()} → {dates[-1].date()}")
print(f"APY range: {apys.min():.2f}% – {apys.max():.2f}%")
print(f"ETH range: ${eth_prices.min():.0f} – ${eth_prices.max():.0f}")

# ── Plot (Colored matplotlib style matching reference) ──────────────────────
fig, ax1 = plt.subplots(figsize=(10, 5))

# ── Left axis: APY (%) ─────────────────────────────────────────────────────
color_borrow = '#d62728'   # Red (matching reference Borrow APY)
color_raw    = '#d62728'

# Raw APY as thin transparent line
ax1.plot(dates, apys, color=color_raw, linewidth=0.4, alpha=0.3)
# 7d MA as solid red line
ax1.plot(dates, apys_ma, color=color_borrow, linewidth=1.2, label='Borrow APY (7d MA)')

ax1.set_ylabel('APY (%)', fontsize=11)
ax1.set_ylim(-1, max(apys) * 1.15)
ax1.tick_params(axis='y', labelcolor=color_borrow)

# ── Right axis: ETH Price (USD) ────────────────────────────────────────────
ax2 = ax1.twinx()
color_eth_fill = '#FFD4B2'  # Peach fill (matching reference Total Borrows fill)
color_eth_line = '#2ca02c'  # Green line

# Peach fill under ETH price
ax2.fill_between(dates, 0, eth_prices, alpha=0.35, color=color_eth_fill, label='ETH Price (area)', zorder=1)
# Green line for ETH price
ax2.plot(dates, eth_prices, color=color_eth_line, linewidth=1.0, label='ETH Price', zorder=2)

ax2.set_ylabel('ETH Price (USD)', fontsize=11)
ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: f'${x:,.0f}'))
ax2.set_ylim(bottom=0)

# ── Annotate Key Events ─────────────────────────────────────────────────────

# BTC Rally period (Oct 2024 – Jan 2025): ETH went from ~$2500 to ~$4000, rates spiked
rally_start = datetime(2024, 10, 1, tzinfo=timezone.utc)
rally_end   = datetime(2025, 1, 15, tzinfo=timezone.utc)
ax1.axvspan(rally_start, rally_end, alpha=0.12, color='#ff6b6b', zorder=0)  # Pink shading like reference

# Find peak APY in rally window
rally_mask = (dates >= rally_start) & (dates <= rally_end)
if rally_mask.any():
    rally_apys = apys_ma[rally_mask]
    rally_dates_w = dates[rally_mask]
    peak_idx = np.argmax(rally_apys)
    peak_date = rally_dates_w[peak_idx]
    peak_apy = rally_apys[peak_idx]
    
    ax1.annotate(
        'Bull Rally\nAPY surge +237%',
        xy=(peak_date, peak_apy),
        xytext=(peak_date + np.timedelta64(60, 'D'), peak_apy + 3),
        fontsize=9, fontweight='bold', ha='left', va='bottom',
        arrowprops=dict(arrowstyle='->', color='black', lw=1.5),
    )

# Stream Finance Default (Nov 2025)
stream_date = datetime(2025, 11, 4, tzinfo=timezone.utc)
if stream_date <= dates[-1]:
    # Find APY around stream date
    stream_mask = (dates >= datetime(2025, 11, 1, tzinfo=timezone.utc)) & \
                  (dates <= datetime(2025, 11, 10, tzinfo=timezone.utc))
    if stream_mask.any():
        stream_apy = apys_ma[stream_mask].max()
    else:
        stream_apy = 5.0
    
    ax1.annotate(
        'Stream Default\nRate collapse',
        xy=(stream_date, stream_apy),
        xytext=(stream_date - np.timedelta64(90, 'D'), stream_apy + 8),
        fontsize=9, fontweight='bold', ha='right', va='bottom',
        arrowprops=dict(arrowstyle='->', color='black', lw=1.5),
    )

# ── X-axis formatting ─────────────────────────────────────────────────────
ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
ax1.xaxis.set_minor_locator(mdates.MonthLocator())
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%d-%b-%y'))
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=35, ha='right', fontsize=9)

ax1.set_xlabel('Date', fontsize=11)

# ── Grid ───────────────────────────────────────────────────────────────────
ax1.grid(True, axis='y', linestyle='-', alpha=0.15, color='gray')
ax1.grid(True, axis='x', linestyle='-', alpha=0.1, color='gray', which='major')

# ── Combined Legend ────────────────────────────────────────────────────────
lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
# Remove the fill area duplicate from legend  
ax1.legend(lines1 + [lines2[-1]], labels1 + [labels2[-1]],
           loc='upper left', fontsize=9, framealpha=0.9)

# ── Title ──────────────────────────────────────────────────────────────────
ax1.set_title('Interest Rate Sensitivity vs. Market Moves (Aave USDC)',
              fontsize=13, fontweight='bold', pad=12)

# ── Save ───────────────────────────────────────────────────────────────────
fig.tight_layout()
fig.savefig(OUTPUT, dpi=200, bbox_inches='tight', facecolor='white', edgecolor='none')
print(f"\n✓ Saved to {OUTPUT}")
plt.close()
