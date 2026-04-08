"""
Plot Utilization Trajectories for 3 Defaulted Markets
"""
import sqlite3
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime

DB_PATH = '/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db'
OUTPUT_FILE = '/home/ubuntu/.gemini/antigravity/brain/caccad6e-ddc4-4180-9149-eb50f8230c58/artifacts/utilization_traps.png'

markets = [
    ('0x8e7cc042d7', 'USR', '#e74c3c'),
    ('0x0f9563442d', 'sdeUSD', '#2980b9'),
    ('0xe1b65304ed', 'RLP', '#8e44ad')
]

t_start = int(datetime(2025, 4, 1, 0, 0).timestamp())
t_end = int(datetime(2025, 4, 25, 0, 0).timestamp())

conn = sqlite3.connect(DB_PATH)

plt.figure(figsize=(12, 6))

for prefix, name, color in markets:
    q = '''
    SELECT timestamp, utilization
    FROM market_snapshots
    WHERE market_id LIKE ? || '%'
      AND timestamp BETWEEN ? AND ?
    ORDER BY timestamp ASC
    '''
    df = pd.read_sql_query(q, conn, params=(prefix, t_start, t_end))
    if not df.empty:
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
        df['utilization_pct'] = df['utilization'].astype(float) * 100
        
        plt.plot(df['datetime'], df['utilization_pct'], label=name, color=color, linewidth=2)

conn.close()

# Plot styling
plt.axhline(100, color='black', linestyle='--', alpha=0.5, label='100% (Complete Default)')
plt.axhline(90, color='orange', linestyle=':', alpha=0.7, label='90% (IRM Kink)')
plt.axvline(pd.to_datetime(datetime(2025, 4, 7, 0, 0), utc=True), color='green', linestyle='-', alpha=0.5, label='April 7 Snapshot')

plt.title('The Utilization Trap: Cascading Defaults (April 2025)', fontsize=14, fontweight='bold')
plt.xlabel('Date (UTC)', fontsize=12)
plt.ylabel('Utilization (%)', fontsize=12)
plt.ylim(0, 105)
plt.grid(True, linestyle='--', alpha=0.6)
plt.legend(loc='lower right')
plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:.0f}%'))

plt.tight_layout()
plt.savefig(OUTPUT_FILE, dpi=300)
print(f'Saved plot to {OUTPUT_FILE}')
