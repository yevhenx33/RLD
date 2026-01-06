import sqlite3
import pandas as pd
from datetime import datetime, timedelta

# Configuration
DB_PATH = "aave_rates.db"
GAP_THRESHOLD_MINUTES = 60  # Report gaps larger than this (e.g. 1 hour)

def check_data_integrity():
    print(f"🔍 Connecting to {DB_PATH}...")
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    
    # 1. Load only timestamps (fast)
    print("📉 Loading timestamps...")
    df = pd.read_sql_query("SELECT timestamp, block_number FROM rates ORDER BY timestamp ASC", conn)
    conn.close()

    if df.empty:
        print("❌ Database is empty.")
        return

    # 2. Basic Stats
    total_count = len(df)
    start_ts = df['timestamp'].iloc[0]
    end_ts = df['timestamp'].iloc[-1]
    
    start_date = datetime.fromtimestamp(start_ts)
    end_date = datetime.fromtimestamp(end_ts)
    duration = end_date - start_date
    
    print("-" * 40)
    print(f"📊 DATASET SUMMARY")
    print("-" * 40)
    print(f"✅ Total Data Points: {total_count:,}")
    print(f"📅 Date Range:       {start_date}  ->  {end_date}")
    print(f"⏱️ Total Duration:   {duration.days} days, {duration.seconds//3600} hours")
    
    # Estimated Coverage calculation (assuming ~12s per block)
    # This is a rough heuristic for Ethereum Mainnet
    expected_points = duration.total_seconds() / 12
    coverage_pct = (total_count / expected_points) * 100
    print(f"📉 Density Score:    ~{coverage_pct:.2f}% (100% = full 12s block resolution)")
    
    # 3. Gap Analysis
    print("-" * 40)
    print(f"⚠️ GAPS DETECTED (> {GAP_THRESHOLD_MINUTES} Minutes)")
    print("-" * 40)

    # Calculate difference between consecutive rows
    df['prev_ts'] = df['timestamp'].shift(1)
    df['diff'] = df['timestamp'] - df['prev_ts']
    
    # Filter for gaps larger than threshold (convert minutes to seconds)
    gap_threshold_seconds = GAP_THRESHOLD_MINUTES * 60
    gaps = df[df['diff'] > gap_threshold_seconds]

    if gaps.empty:
        print(f"✅ PERFECT! No gaps larger than {GAP_THRESHOLD_MINUTES} minutes found.")
    else:
        print(f"Found {len(gaps)} gaps.\n")
        print(f"{'Start of Gap':<25} | {'End of Gap':<25} | {'Duration':<15} | {'Missing Blocks (Est)'}")
        print("-" * 95)
        
        for index, row in gaps.iterrows():
            gap_end = datetime.fromtimestamp(row['timestamp'])
            gap_start = datetime.fromtimestamp(row['prev_ts'])
            gap_duration = row['diff']
            
            # Formatting duration nicely
            hours = int(gap_duration // 3600)
            mins = int((gap_duration % 3600) // 60)
            duration_str = f"{hours}h {mins}m"
            
            # Estimated missing blocks
            missing_blocks = int(gap_duration / 12)
            
            print(f"{str(gap_start):<25} | {str(gap_end):<25} | {duration_str:<15} | ~{missing_blocks:,}")

    print("-" * 95)
    print("💡 TIP: Use 'batched_backfill.py' with the specific dates above to fill these holes.")

if __name__ == "__main__":
    check_data_integrity()