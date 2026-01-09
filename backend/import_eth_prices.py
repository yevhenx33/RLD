import sqlite3
import pandas as pd
import os
from datetime import datetime

# Config
CSV_FILE = "../data_gemini.csv"
DB_FILE = "aave_rates.db"

def import_prices():
    print(f"🚀 Starting ETH Price Import...")
    
    if not os.path.exists(CSV_FILE):
        print(f"❌ Error: {CSV_FILE} not found.")
        return

    # 1. Read CSV
    try:
        df = pd.read_csv(CSV_FILE)
        print(f"📄 Read {len(df)} rows from CSV.")
    except Exception as e:
        print(f"❌ Error reading CSV: {e}")
        return

    # 2. Process Data
    # Expected columns: "Date", "token0PriceEU"
    if 'Date' not in df.columns or 'token0PriceEU' not in df.columns:
        print("❌ Error: CSV missing 'Date' or 'token0PriceEU' columns.")
        print(f"   Found: {df.columns.tolist()}")
        return

    # Convert Date to Timestamp
    # Format: 2024-01-10 10:00:00
    try:
        df['dt'] = pd.to_datetime(df['Date'], format='mixed')
        # Convert to unix timestamp (int)
        df['timestamp'] = df['dt'].astype('int64') // 10**9
    except Exception as e:
        print(f"❌ Error parsing dates: {e}")
        return

    # Prepare data for insertion
    # We want [timestamp, price]
    db_data = df[['timestamp', 'token0PriceEU']].dropna().values.tolist()
    
    print(f"   Prepared {len(db_data)} valid records.")

    # 3. Database Insertion
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Create table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS eth_prices (
            timestamp INTEGER PRIMARY KEY,
            price REAL
        )
    ''')
    
    # Upsert data (Replace if exists)
    try:
        cursor.executemany(
            "INSERT OR REPLACE INTO eth_prices (timestamp, price) VALUES (?, ?)",
            db_data
        )
        conn.commit()
        print(f"✅ Successfully inserted/updated {len(db_data)} price records.")
    except Exception as e:
        print(f"❌ Database Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    import_prices()
