import sys
import os
import sqlite3
import time
from datetime import datetime

# Add backend to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from config import ASSETS

# Pointing to clean_rates.db
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "clean_rates.db")

def check_rates_gaps_3d():
    print(f"🔍 Connecting to {DB_PATH}...")
    try:
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    except sqlite3.OperationalError:
         # Fallback for systems where URI might be tricky or file locking issues
         conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    # Calculate timestamp for 3 days ago
    now = time.time()
    three_days_ago = now - (3 * 24 * 60 * 60)
    
    print(f"Checking for gaps since {datetime.fromtimestamp(three_days_ago)} (Timestamp: {int(three_days_ago)})")
    
    MAX_ISSUES_FOUND = False
    GAP_THRESHOLD = 3600 # 1 hour

    # Map Symbol to Column in clean_rates.db
    symbol_map = {
        "USDC": "usdc_rate",
        "DAI": "dai_rate",
        "USDT": "usdt_rate",
        "SOFR": "sofr_rate"
    }

    for symbol, config in ASSETS.items():
        if config['type'] != 'onchain':
            continue
            
        col_name = symbol_map.get(symbol)
        if not col_name:
            continue

        print(f"\n🔵 ASSET: {symbol} (Column: {col_name} in hourly_stats)")

        query = f"""
        SELECT timestamp 
        FROM hourly_stats
        WHERE timestamp >= ? AND {col_name} IS NOT NULL
        ORDER BY timestamp ASC
        """
        
        try:
            cursor.execute(query, (three_days_ago,))
            timestamps = [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f"⚠️  Error querying table hourly_stats for {symbol}: {e}")
            continue

        if not timestamps:
            print("❌ No data found for the last 3 days.")
            MAX_ISSUES_FOUND = True
            continue

        total_count = len(timestamps)
        print(f"   Found {total_count} data points.")

        gaps_found = 0
        print(f"   {'Start Time':<25} | {'End Time':<25} | {'Duration'}")

        # 1. Internal Gaps
        for i in range(1, len(timestamps)):
            prev_ts = timestamps[i-1]
            curr_ts = timestamps[i]
            diff = curr_ts - prev_ts

            if diff >= 7200: # Missing at least one hour
                gap_start_date = datetime.fromtimestamp(prev_ts)
                gap_end_date = datetime.fromtimestamp(curr_ts)
                duration_hours = diff / 3600
                print(f"   {str(gap_start_date):<25} | {str(gap_end_date):<25} | {duration_hours:.1f} hours")
                gaps_found += 1

        # 2. Gap to NOW
        last_ts = timestamps[-1]
        diff_to_now = now - last_ts
        if diff_to_now > GAP_THRESHOLD: # Allow 1 hour latency
             gap_start_date = datetime.fromtimestamp(last_ts)
             gap_end_date = datetime.fromtimestamp(now)
             duration_hours = diff_to_now / 3600
             print(f"   {str(gap_start_date):<25} | {str(gap_end_date):<25} | {duration_hours:.1f} hours (TO NOW)")
             gaps_found += 1

        if gaps_found == 0:
            print(f"   ✅ PERFECT! No gaps.")
        else:
            print(f"   ⚠️  Found {gaps_found} gaps.")
            MAX_ISSUES_FOUND = True

    conn.close()

if __name__ == "__main__":
    check_rates_gaps_3d()
