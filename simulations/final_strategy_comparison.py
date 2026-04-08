"""
Produces a formal comparative backtest of Passive Supply vs CDS Underwriting
across 12 prime Morpho USDC markets.
"""
import sqlite3
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
DB_PATH = '/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db'
OUTPUT_CHART = '/home/ubuntu/.gemini/antigravity/brain/caccad6e-ddc4-4180-9149-eb50f8230c58/artifacts/strategy_comparison.png'

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
})

target_ts = int(datetime(2025, 4, 7, 0, 0, 0).timestamp())
end_ts = int(datetime(2026, 4, 6, 0, 0).timestamp())
SECONDS_PER_DAY = 86400

# 12 Prime Markets passed filtering
prime_markets = {
    '0x8e7cc042d7': 'USR', '0x3a85e61975': 'WBTC', '0x0f9563442d': 'sdeUSD', 
    '0x64d65c9a2d': 'cbBTC', '0xb323495f7e': 'wstETH', '0xbfed072fae': 'srUSD', 
    '0xe1b65304ed': 'RLP', '0x729badf297': 'syrupUSDC', '0x85c7f4374f': 'sUSDe', 
    '0xbf02d6c685': 'LBTC', '0x1a9ccaca2d': 'USCC', '0xe4cfbee9af': 'tBTC'
}

INITIAL_CAPITAL = 1_000_000.0
ESCROW_PER_MARKET = INITIAL_CAPITAL / 12
TOKENS_MINTED = ESCROW_PER_MARKET / 100.0
DELTA = 0.80
F = -np.log(1 - DELTA)

conn = sqlite3.connect(DB_PATH)

# Build a daily timeline
timeline_timestamps = np.arange(target_ts, end_ts + SECONDS_PER_DAY, SECONDS_PER_DAY)
dates = pd.to_datetime(timeline_timestamps, unit='s')

total_passive_value = np.zeros(len(timeline_timestamps))
total_cds_value = np.zeros(len(timeline_timestamps))

for prefix, collat in prime_markets.items():
    # Load all snapshots for this market
    q = "SELECT timestamp, total_supply_assets, total_supply_shares, utilization, borrow_apy FROM market_snapshots WHERE market_id LIKE ? || '%' AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC"
    df = pd.read_sql_query(q, conn, params=(prefix, target_ts - SECONDS_PER_DAY, end_ts + SECONDS_PER_DAY))
    
    if df.empty:
        # Carry flat escrow
        total_cds_value += ESCROW_PER_MARKET
        total_passive_value += ESCROW_PER_MARKET
        continue
    
    # Identify 7-day default traps
    df['is_frozen'] = df['utilization'] >= 0.99
    df['block_id'] = (df['is_frozen'] != df['is_frozen'].shift()).cumsum()
    frozen_blocks = df[df['is_frozen']].groupby('block_id').agg(
        start_time=('timestamp', 'min'),
        end_time=('timestamp', 'max'),
        duration_sec=('timestamp', lambda x: x.max() - x.min())
    )
    
    default_ts = None
    sustained = frozen_blocks[frozen_blocks['duration_sec'] >= 7 * SECONDS_PER_DAY]
    if not sustained.empty:
        default_ts = sustained.iloc[0]['start_time'] + (7 * SECONDS_PER_DAY)
        
    # Get T0 values
    t0_row = df.iloc[np.argmin(np.abs(df['timestamp'] - target_ts))]
    sp0 = float(t0_row['total_supply_assets']) / float(t0_row['total_supply_shares']) if float(t0_row['total_supply_shares']) > 0 else 1.0
    initial_r = float(t0_row['borrow_apy'])
    initial_price = min(100.0, 100.0 * initial_r)
    upfront_premium = TOKENS_MINTED * initial_price
    
    market_passive_val = np.zeros(len(timeline_timestamps))
    market_cds_val = np.zeros(len(timeline_timestamps))
    
    cds_fixed_post_default = None
    
    for i, ts in enumerate(timeline_timestamps):
        # Find closest snapshot <= ts
        valid_rows = df[df['timestamp'] <= ts]
        if valid_rows.empty:
            current_row = df.iloc[0]
        else:
            current_row = valid_rows.iloc[-1]
            
        is_default_active = (default_ts is not None) and (ts >= default_ts)
        
        # Passive Strategy
        if is_default_active:
            market_passive_val[i] = 0.0
        else:
            sp_current = float(current_row['total_supply_assets']) / float(current_row['total_supply_shares']) if float(current_row['total_supply_shares']) > 0 else 1.0
            growth = sp_current / sp0
            market_passive_val[i] = ESCROW_PER_MARKET * growth
            
        # CDS Strategy
        dt_years = (ts - target_ts) / 31536000.0
        if dt_years < 0: dt_years = 0
        
        if is_default_active:
            if cds_fixed_post_default is None:
                # Lock liability at exact default time
                dt_default_years = (default_ts - target_ts) / 31536000.0
                tokens_at_default = TOKENS_MINTED * np.exp(-F * dt_default_years)
                liability = tokens_at_default * 100.0
                cds_fixed_post_default = ESCROW_PER_MARKET + upfront_premium - liability
            market_cds_val[i] = cds_fixed_post_default
        else:
            tokens_active = TOKENS_MINTED * np.exp(-F * dt_years)
            current_r = float(current_row['borrow_apy'])
            current_price = min(100.0, 100.0 * current_r)
            liability = tokens_active * current_price
            
            # Equity = cash on hand - current liability
            equity = ESCROW_PER_MARKET + upfront_premium - liability
            market_cds_val[i] = equity
            
    total_passive_value += market_passive_val
    total_cds_value += market_cds_val

conn.close()

# Plotting
plt.figure(figsize=(7.0, 3.5))

plt.plot(dates, total_passive_value / 1e3, label='Passive Depositor (Lending Yield)', color='#e74c3c', linewidth=1.2)
plt.plot(dates, total_cds_value / 1e3, label='CDS Underwriter (Everlasting Option)', color='#2ecc71', linewidth=1.2)

plt.axhline(INITIAL_CAPITAL / 1e3, color='black', linestyle=':', alpha=0.5, label='Initial Capital ($1M)')

plt.title('Passive Lending vs. Structurally-Hedged CDS', fontweight='bold', pad=10)
plt.xlabel('Date (Year 2025-2026)')
plt.ylabel('Portfolio Value (Thousands USD)')

# Format axes
plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'${x:,.0f}k'))

plt.grid(True, linestyle=':', alpha=0.4, color='gray')
plt.legend(loc='best')

plt.tight_layout()
plt.savefig(OUTPUT_CHART, dpi=300)
print(f"Chart perfectly saved to {OUTPUT_CHART}")
