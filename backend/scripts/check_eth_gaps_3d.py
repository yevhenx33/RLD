import sys
import os
import sqlite3
import time
from datetime import datetime

# Add backend to path to import config
# DB PATH directly since config imports missing dotenv
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "aave_rates.db")

def check_eth_gaps_3d():
    print(f"🔍 Connecting to {DB_PATH}...")
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    cursor = conn.cursor()

    # Calculate timestamp for 3 days ago
    now = time.time()
    three_days_ago = now - (3 * 24 * 60 * 60)
    
    print(f"Checking for gaps since {datetime.fromtimestamp(three_days_ago)} (Timestamp: {int(three_days_ago)})")

    query = """
    SELECT timestamp 
    FROM eth_prices 
    WHERE timestamp >= ? 
    ORDER BY timestamp ASC
    """
    
    try:
        cursor.execute(query, (three_days_ago,))
        timestamps = [row[0] for row in cursor.fetchall()]
    except Exception as e:
        print(f"⚠️ Error querying database: {e}")
        conn.close()
        return

    if not timestamps:
        print("❌ No data found for the last 3 days.")
        conn.close()
        return

    total_count = len(timestamps)
    print(f"✅ Found {total_count} data points in the last 3 days.")

    # Check for gaps > 3600 seconds (1 hour)
    GAP_THRESHOLD = 3600
    gaps_found = 0

    print("-" * 60)
    print(f"{'Start Time':<25} | {'End Time':<25} | {'Duration'}")
    print("-" * 60)

    for i in range(1, len(timestamps)):
        prev_ts = timestamps[i-1]
        curr_ts = timestamps[i]
        diff = curr_ts - prev_ts

        if diff > GAP_THRESHOLD:
            # Check if it's just a strictly expected 3600s gap or slightly more?
            # Since data is hourly, diff should be 3600. If diff is 7200, one hour is missing.
            # So strictly speaking, diff > 3600 implies a missing point if we expect exactly hourly.
            # But sometimes timestamps might involve few seconds drift?
            # Creating hourly data usually snaps to hour start, so 3600 is precise.
            # Let's say GAP > 3660 to allow 1 min drift just in case, or strict 3600?
            # fetch_eth_prices using GraphQL `poolHourDatas` which usually are snapped to hour (00:00, 01:00).
            # So 7200 is missing one, 3600 is fine.
            # We can print if diff >= 7200 (meaning at least one whole hour skipped).
            
            if diff >= 7200:
                gap_start_date = datetime.fromtimestamp(prev_ts)
                gap_end_date = datetime.fromtimestamp(curr_ts)
                duration_hours = diff / 3600
                print(f"{str(gap_start_date):<25} | {str(gap_end_date):<25} | {duration_hours:.1f} hours")
                gaps_found += 1

    # Check gap to NOW
    last_ts = timestamps[-1]
    diff_to_now = now - last_ts
    if diff_to_now > GAP_THRESHOLD:
         gap_start_date = datetime.fromtimestamp(last_ts)
         gap_end_date = datetime.fromtimestamp(now)
         duration_hours = diff_to_now / 3600
         print(f"{str(gap_start_date):<25} | {str(gap_end_date):<25} | {duration_hours:.1f} hours (TO NOW)")
         gaps_found += 1

    if gaps_found == 0:
        print(f"✅ PERFECT! No gaps found in the last 3 days.")
    else:
        print(f"⚠️  Found {gaps_found} gaps.")

    conn.close()

if __name__ == "__main__":
    check_eth_gaps_3d()
