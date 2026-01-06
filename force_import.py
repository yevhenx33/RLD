import sqlite3
import pandas as pd
import os

CSV_FILE = "aavescan-aave-v3-ethereum-usdc-history.csv"
DB_FILE = "aave_rates.db"

def force_import():
    if not os.path.exists(CSV_FILE):
        print(f"❌ Error: File '{CSV_FILE}' not found.")
        return

    print(f"Reading {CSV_FILE}...")
    try:
        df = pd.read_csv(CSV_FILE)
    except Exception as e:
        print(f"❌ Critical Error reading CSV: {e}")
        return

    # --- DATA CLEANING ---
    initial_count = len(df)
    
    # 1. Force 'timestamp' to numeric, turning errors into NaN (Not a Number)
    df['timestamp'] = pd.to_numeric(df['timestamp'], errors='coerce')
    
    # 2. Drop rows where timestamp or APR is missing (NaN)
    df = df.dropna(subset=['timestamp', 'borrow APR'])
    
    # 3. Convert timestamp to integer
    df['timestamp'] = df['timestamp'].astype(int)
    
    print(f"🧹 Cleaned data: Kept {len(df)} valid rows (dropped {initial_count - len(df)} bad rows).")

    # --- DATABASE INSERTION ---
    print(f"Connecting to {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Ensure table exists
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rates (
            block_number INTEGER,
            timestamp INTEGER,
            apy REAL
        )
    ''')
    
    count = 0
    for _, row in df.iterrows():
        ts = int(row['timestamp'])
        apy = float(row['borrow APR'])
        
        # Check if timestamp already exists to prevent duplicates
        cursor.execute("SELECT 1 FROM rates WHERE timestamp = ?", (ts,))
        if cursor.fetchone() is None:
            # Insert with block_number = 0 for historical data
            cursor.execute("INSERT INTO rates VALUES (?, ?, ?)", (0, ts, apy))
            count += 1

    conn.commit()
    
    # --- VERIFICATION ---
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM rates")
    result = cursor.fetchone()
    conn.close()
    
    print("-" * 30)
    if result and result[2] > 0:
        min_ts, max_ts, total = result
        print(f"✅ Successfully added {count} new records.")
        print(f"📊 Total DB Records: {total}")
        try:
            start_date = pd.to_datetime(min_ts, unit='s').strftime('%Y-%m-%d')
            end_date = pd.to_datetime(max_ts, unit='s').strftime('%Y-%m-%d')
            print(f"📅 Data Range: {start_date} to {end_date}")
        except:
            pass
    else:
        print("⚠️ Database is still empty. Something went wrong.")

if __name__ == "__main__":
    force_import()