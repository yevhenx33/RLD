"""
Generate standardized visual plots for the Stream Finance (Nov 2025) 
and Resolv (Mar 2026) liquidity crises using the central Morpho Database.
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

DB_PATH = '/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db'
OUTPUT_DIR = '/home/ubuntu/.gemini/antigravity/brain/caccad6e-ddc4-4180-9149-eb50f8230c58/artifacts'

# Connect to DB
conn = sqlite3.connect(DB_PATH)

def generate_crisis_plot(market_id, title, start_ts, end_ts, crisis_date_str, output_filename, freeze_annotation):
    q = '''
    SELECT timestamp, total_supply_assets, total_borrow_assets, borrow_apy, supply_apy
    FROM market_snapshots
    WHERE market_id LIKE ? || '%' AND timestamp BETWEEN ? AND ?
    ORDER BY timestamp ASC
    '''
    df = pd.read_sql_query(q, conn, params=(market_id, start_ts, end_ts))
    
    if df.empty:
        print(f"Skipping {title} - no data")
        return
        
    df['dt'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df['borrow_apy'] = df['borrow_apy'].astype(float) * 100
    df['supply_apy'] = df['supply_apy'].astype(float) * 100
    df['borrows'] = df['total_borrow_assets'].astype(float)
    df['deposits'] = df['total_supply_assets'].astype(float)
    
    # Classic style (non-LaTeX to avoid dependency errors on headless, but styled identically)
    plt.rcParams.update({
        'font.family': 'serif',
        'font.size': 10,
        'axes.labelsize': 11,
        'axes.titlesize': 12,
        'legend.fontsize': 8.5,
        'figure.dpi': 300,
        'savefig.dpi': 300,
    })
    
    fig, ax1 = plt.subplots(figsize=(7.5, 3.8))
    
    # APY Axis
    color_borrow = '#e74c3c' # red matching user chart
    color_supply = '#2ecc71' # green matching user chart
    
    ax1.plot(df['dt'], df['borrow_apy'], color=color_borrow, linewidth=1.2, label='Borrow APY', zorder=3)
    ax1.plot(df['dt'], df['supply_apy'], color=color_supply, linewidth=1.2, label='Supply APY', zorder=3)
    ax1.set_ylabel('APY (%)', fontsize=10)
    ax1.tick_params(axis='y', labelsize=9)
    
    # Volume Axis
    ax2 = ax1.twinx()
    ax2.fill_between(df['dt'], 0, df['borrows'], alpha=0.3, color='#f39c12', label='Total Borrows', zorder=1, step='mid')
    ax2.plot(df['dt'], df['deposits'], color='#2980b9', linewidth=1.5, linestyle='--', label='Total Deposits', zorder=2)
    ax2.set_ylabel('Volume (USD)', fontsize=10)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: f'${x/1e6:.0f}M'))
    ax2.set_ylim(bottom=0, top=max(df['deposits'].max(), df['borrows'].max()) * 1.2)
    ax2.tick_params(axis='y', labelsize=9)
    
    # Find Crisis Point
    crisis_idx = df['borrow_apy'].idxmax()
    peak_date = df.loc[crisis_idx, 'dt']
    peak_apy = df.loc[crisis_idx, 'borrow_apy']
    
    # Annotate crisis
    ax1.annotate(
        f'{crisis_date_str} Default\n(100% Utilization)',
        xy=(peak_date, peak_apy),
        xytext=(peak_date + pd.Timedelta(days=15), min(peak_apy, 90)),
        fontsize=9, ha='left', va='top',
        style='italic', color='black',
        arrowprops=dict(arrowstyle='->', color='black', lw=1.5),
        bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='gray', linewidth=0.5),
        zorder=5,
    )
    
    ax1.axvspan(peak_date - pd.Timedelta(hours=24), peak_date + pd.Timedelta(days=5), alpha=0.15, color='#e74c3c', zorder=0)
    
    # Annotate freeze/resolution
    ax1.annotate(
        freeze_annotation,
        xy=(peak_date + pd.Timedelta(days=30), 5),
        fontsize=8, ha='center', va='bottom',
        color='#555555', style='italic',
        bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#cccccc', linewidth=0.4),
        zorder=5,
    )
    
    # Formatting
    ax1.xaxis.set_major_locator(mdates.MonthLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=0, ha='center', fontsize=9)
    
    ax1.grid(True, axis='y', linestyle=':', alpha=0.5, color='gray')
    ax1.grid(True, axis='x', linestyle=':', alpha=0.3, color='gray', which='major')
    
    # Legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left', framealpha=0.95, edgecolor='gray', ncol=2)
    
    ax1.set_title(title, fontweight='bold', pad=12, fontsize=12)
    
    # Dynamic Y limits for APY to handle the massive jumps
    ax1.set_ylim(-2, min(100, peak_apy * 1.1))
    
    fig.tight_layout()
    output_path = f"{OUTPUT_DIR}/{output_filename}"
    fig.savefig(output_path, bbox_inches='tight', facecolor='white', edgecolor='none')
    print(f"Saved: {output_path}")
    plt.close()

# 1. Stream Finance Crisis (sdeUSD: 0x0f9563442d)
generate_crisis_plot(
    market_id='0x0f9563442d',
    title='Liquidity Crisis: The "Stream Finance" Default (Nov 2025)',
    start_ts=int(datetime(2025, 9, 1, 0, 0, tzinfo=timezone.utc).timestamp()),
    end_ts=int(datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc).timestamp()),
    crisis_date_str='Stream',
    output_filename='stream_finance_crisis.png',
    freeze_annotation='Frozen Market\nRates collapse as liquidity dies'
)

# 2. Resolv Crisis (USR: 0x8e7cc042d7)
generate_crisis_plot(
    market_id='0x8e7cc042d7',
    title='Liquidity Crisis: The "Resolv" Structural Freeze (Mar 2026)',
    start_ts=int(datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc).timestamp()),
    end_ts=int(datetime(2026, 4, 6, 0, 0, tzinfo=timezone.utc).timestamp()),
    crisis_date_str='Resolv',
    output_filename='resolv_liquidity_crisis.png',
    freeze_annotation='Terminal Default\n100% Locked Liquidity'
)

conn.close()
