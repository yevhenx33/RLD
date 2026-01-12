import sys
import os
import sqlite3
import time
from datetime import datetime, timedelta

# Add backend to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import ASSETS

# DB PATH: aave_rates.db (Raw Block Data)
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "aave_rates.db")

def audit_block_1h():
    print(f"🔍 AUDIT: Block-Level Data (Last 1 Hour)")
    print(f"📁 Database: {DB_PATH}")
    
    if not os.path.exists(DB_PATH):
        print("❌ Database not found!")
        return

    try:
         conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    except sqlite3.OperationalError:
         conn = sqlite3.connect(DB_PATH)
    
    cursor = conn.cursor()

    # Time window: Last 1 Hour
    now = time.time()
    query_start_time = now - 3600
    
    print(f"📅 Range: {datetime.fromtimestamp(query_start_time)} -> {datetime.fromtimestamp(now)}")
    
    # Threshold: User specified 15s tolerance for block level gaps.
    GAP_THRESHOLD = 15 # Seconds

    print("-" * 60)
    
    total_gaps = 0

    for symbol, config in ASSETS.items():
        if config['type'] != 'onchain':
            continue
            
        table = config['table']
        print(f"\n🔵 ASSET: {symbol} (Table: {table})")

        query = f"""
        SELECT timestamp, block_number
        FROM {table}
        WHERE timestamp >= ? 
        ORDER BY timestamp ASC
        """
        
        try:
            cursor.execute(query, (query_start_time,))
            rows = cursor.fetchall()
        except Exception as e:
            print(f"⚠️  Error: {e}")
            continue

        if not rows:
            print("❌ No data found for the last 1 hour.")
            total_gaps += 1
            continue

        timestamps = [r[0] for r in rows]
        blocks = [r[1] for r in rows]
        
        count = len(timestamps)
        print(f"   Records: {count}")
        
        # Check for gaps
        local_gaps = 0
        for i in range(1, count):
            diff = timestamps[i] - timestamps[i-1]
            if diff > GAP_THRESHOLD:
                start = datetime.fromtimestamp(timestamps[i-1])
                end = datetime.fromtimestamp(timestamps[i])
                duration = diff / 3600.0
                print(f"   ⚠️ GAP: {start} -> {end} ({duration:.1f} hours)")
                local_gaps += 1
        
        # Check gap to NOW
        last_ts = timestamps[-1]
        diff_now = now - last_ts
        if diff_now > GAP_THRESHOLD:
             start = datetime.fromtimestamp(last_ts)
             end = datetime.fromtimestamp(now)
             duration = diff_now / 3600.0
             print(f"   ⚠️ GAP (TO NOW): {start} -> {end} ({duration:.1f} hours)")
             local_gaps += 1

        if local_gaps == 0:
            print("   ✅ Continuous")
        else:
            total_gaps += local_gaps

    conn.close()
    
    if total_gaps == 0:
        print("\n✅ PASSED: No gaps > 15s in the last 1 hour.")
    else:
        print(f"\n❌ FAILED: Found {total_gaps} gaps > 15s in the last hour.")
        sys.exit(1)

if __name__ == "__main__":
    audit_block_1h()
