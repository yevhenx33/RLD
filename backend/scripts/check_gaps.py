import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import pandas as pd
from datetime import datetime, timedelta

from config import DB_PATH, ASSETS

# Configuration
# DB_PATH imported from config
GAP_THRESHOLD_MINUTES = 60  # Report gaps larger than this (e.g. 1 hour)

def check_single_table(conn, symbol, table_name, decimals):
    print(f"\n🔵 TYPE: {symbol} ({table_name})")
    
    try:
        query = f"SELECT timestamp, block_number FROM {table_name} ORDER BY timestamp ASC"
        df = pd.read_sql_query(query, conn)
    except Exception as e:
        print(f"⚠️  Error reading table {table_name}: {e}")
        return

    if df.empty:
        print("❌ Table is empty.")
        return

    # 1. Basic Stats
    total_count = len(df)
    start_ts = df['timestamp'].iloc[0]
    end_ts = df['timestamp'].iloc[-1]
    
    start_date = datetime.fromtimestamp(start_ts)
    end_date = datetime.fromtimestamp(end_ts)
    duration = end_date - start_date
    
    print("-" * 40)
    print(f"✅ Total Data Points: {total_count:,}")
    print(f"📅 Date Range:       {start_date}  ->  {end_date}")
    
    # 2. Gap Analysis
    # Calculate difference between consecutive rows
    df['prev_ts'] = df['timestamp'].shift(1)
    df['diff'] = df['timestamp'] - df['prev_ts']
    
    # Filter for gaps larger than threshold
    gap_threshold_seconds = GAP_THRESHOLD_MINUTES * 60
    gaps = df[df['diff'] > gap_threshold_seconds]

    if gaps.empty:
        print(f"✅ PERFECT! No gaps > {GAP_THRESHOLD_MINUTES} min.")
    else:
        print(f"⚠️  Found {len(gaps)} gaps (> {GAP_THRESHOLD_MINUTES} min):")
        print(f"{'Start of Gap':<20} | {'End of Gap':<20} | {'Duration':<10} | {'missing blocks'}")
        print("-" * 80)
        
        for index, row in gaps.iterrows():
            gap_end = datetime.fromtimestamp(row['timestamp'])
            gap_start = datetime.fromtimestamp(row['prev_ts'])
            gap_duration = row['diff']
            
            hours = int(gap_duration // 3600)
            mins = int((gap_duration % 3600) // 60)
            duration_str = f"{hours}h {mins}m"
            missing_blocks = int(gap_duration / 12)
            
            print(f"{str(gap_start):<20} | {str(gap_end):<20} | {duration_str:<10} | ~{missing_blocks}")

def check_data_integrity():
    print(f"🔍 Connecting to {DB_PATH}...")
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    
    for symbol, config in ASSETS.items():
        if config['type'] == 'onchain':
            check_single_table(conn, symbol, config['table'], config['decimals'])
            
    # Also check SOFR if available
    print("\n🟣 ASSET: SOFR (Risk Free Rate)")
    try:
        check_single_table(conn, "SOFR", "sofr_rates", 2)
    except:
        pass

    conn.close()

if __name__ == "__main__":
    check_data_integrity()