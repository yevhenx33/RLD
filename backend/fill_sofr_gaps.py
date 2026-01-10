
import sqlite3
import pandas as pd
from datetime import datetime, timedelta

DB_PATH = "backend/aave_rates.db"

def fill_gaps():
    print("Connecting to DB...")
    conn = sqlite3.connect(DB_PATH)
    
    # Read existing data
    df = pd.read_sql_query("SELECT * FROM sofr_rates ORDER BY timestamp ASC", conn)
    
    if df.empty:
        print("No SOFR data found. Run import first.")
        return

    # Convert to datetime
    df['date'] = pd.to_datetime(df['timestamp'], unit='s')
    df = df.set_index('date')
    
    # Create full Date Range from start to end (Daily)
    start_date = df.index.min()
    end_date = df.index.max()
    print(f"Timeframe: {start_date} to {end_date}")
    
    all_days = pd.date_range(start=start_date, end=end_date, freq='D')
    
    # Reindex to full range
    df_reindexed = df.reindex(all_days)
    
    # Forward Fill APY
    df_reindexed['apy'] = df_reindexed['apy'].ffill()
    
    # Identify Gaps (rows that were added)
    # We can check which timestamps were missing, or just simple Upsert all
    # To be efficient, let's filter for rows not in original
    
    print("Writing back to DB...")
    cursor = conn.cursor()
    
    count = 0
    for date, row in df_reindexed.iterrows():
        ts = int(date.timestamp())
        apy = row['apy']
        
        # INSERT OR IGNORE would be safer if we trust existing, 
        # but INSERT OR REPLACE ensures we really fill it. 
        # Since we use ffill, existing values are preserved (mapped to themselves)
        # Use INSERT OR IGNORE to only fill missing
        cursor.execute("INSERT OR IGNORE INTO sofr_rates (timestamp, apy) VALUES (?, ?)", (ts, apy))
        if cursor.rowcount > 0:
            count += 1
            
    conn.commit()
    conn.close()
    print(f"✅ Filled {count} missing days (weekends/holidays).")

if __name__ == "__main__":
    fill_gaps()
