import sys
import os
import sqlite3
import time
from datetime import datetime

# Add backend to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
# No specific config needed for clean_rates.db location in config.py, usually explicit
# But we know it's in backend root

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "clean_rates.db")

def audit_hourly_full():
    print(f"🔍 AUDIT: Hourly Data (Since 03.03.2023)")
    print(f"📁 Database: {DB_PATH}")
    
    if not os.path.exists(DB_PATH):
        print("❌ Database not found!")
        return

    try:
         conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    except sqlite3.OperationalError:
         conn = sqlite3.connect(DB_PATH)
    
    cursor = conn.cursor()

    # Start Date: 03.03.2023
    start_dt = datetime(2023, 3, 3)
    start_ts = start_dt.timestamp()
    
    print(f"📅 Start Date: {start_dt}")
    
    # Expected: Hourly data (3600s)
    # Gap warning if > 3600s (allow small drift? maybe 3700s)
    GAP_THRESHOLD = 3700 

    query = """
    SELECT timestamp, eth_price, usdc_rate, dai_rate, usdt_rate
    FROM hourly_stats
    WHERE timestamp >= ?
    ORDER BY timestamp ASC
    """
    
    try:
        cursor.execute(query, (start_ts,))
        rows = cursor.fetchall()
    except Exception as e:
        print(f"⚠️  Error: {e}")
        conn.close()
        return

    if not rows:
        print("❌ No data found since 03.03.2023.")
        conn.close()
        return

    count = len(rows)
    timestamps = [r[0] for r in rows]
    print(f"✅ Found {count} records.")

    # Check for gaps in timestamps
    gaps_found = 0
    
    print("\n--- Inspecting Time Continuity ---")
    for i in range(1, count):
        diff = timestamps[i] - timestamps[i-1]
        # Hourly stats should be exactly 3600 potentially, but let's see.
        # If diff > 3600 implies missing hour?
        # Yes, hourly stats are usually slotted.
        
        if diff > GAP_THRESHOLD:
            start = datetime.fromtimestamp(timestamps[i-1])
            end = datetime.fromtimestamp(timestamps[i])
            duration_hours = (diff / 3600.0) - 1 # approximate missing hours
            if duration_hours >= 1:
                print(f"⚠️ GAP: {start} -> {end} (Missing ~{duration_hours:.1f} hours)")
                gaps_found += 1
    
    # Define Start Date constraints
    dt_2025 = datetime(2025, 1, 1)
    ts_2025 = dt_2025.timestamp()

    # Check completeness of columns (NULLs)
    print("\n--- Inspecting Data Completeness (NULLs) ---")
    print(f"   USDC/ETH Requirement: Since {start_dt.date()}")
    print(f"   DAI/USDT Requirement: Since {dt_2025.date()}")
    
    null_counts = {"eth": 0, "usdc": 0, "dai": 0, "usdt": 0}
    
    # query: SELECT timestamp, eth_price, usdc_rate, dai_rate, usdt_rate
    
    for r in rows:
        ts = r[0]
        
        # ETH & USDC: Check from start (2023)
        if r[1] is None: null_counts["eth"] += 1
        if r[2] is None: null_counts["usdc"] += 1
        
        # DAI & USDT: Check only from 2025
        if ts >= ts_2025:
            if r[3] is None: null_counts["dai"] += 1
            if r[4] is None: null_counts["usdt"] += 1
        
    for k, v in null_counts.items():
        if v > 0:
             print(f"⚠️ {k.upper()}: {v} NULL records")
             gaps_found += 1
        else:
             print(f"✅ {k.upper()}: Complete")
             
    # Check TO NOW logic...

    # Check TO NOW
    now = time.time()
    last_ts = timestamps[-1]
    if (now - last_ts) > GAP_THRESHOLD:
        start = datetime.fromtimestamp(last_ts)
        end = datetime.fromtimestamp(now)
        duration_hours = (now - last_ts) / 3600.0
        print(f"\n⚠️ GAP (TO NOW): {start} -> {end} ({duration_hours:.1f} hours)")
        gaps_found += 1

    conn.close()

    if gaps_found == 0:
        print("\n✅ PASSED: No gaps found.")
    else:
        print(f"\n❌ FAILED: Found issues.")
        sys.exit(1)

if __name__ == "__main__":
    audit_hourly_full()
