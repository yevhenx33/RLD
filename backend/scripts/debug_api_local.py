import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))


import sqlite3
import pandas as pd
from datetime import datetime

DB_PATH = "aave_rates.db"

def test_resolution(res, days):
    print(f"\n--- Testing Resolution: {res} ({days} days) ---")
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    
    # Simulate API Logic
    end_ts = int(datetime.now().timestamp())
    start_ts = end_ts - (days * 86400)
    
    if res == "RAW":
        table = "rates"
        query = "SELECT * FROM rates WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 30000"
    elif res == "1H":
        table = "rates_1h"
        query = "SELECT * FROM rates_1h WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 100000"
    else:
        print("Skipping other resolutions")
        return

    try:
        df = pd.read_sql_query(query, conn, params=(start_ts, end_ts))
        print(f"Query: {query}")
        print(f"Rows returned: {len(df)}")
        if not df.empty:
            print("First row:", df.iloc[0].to_dict())
            print("Columns:", df.columns.tolist())
            
            # Check for NaN in critical columns
            if res == "1H":
                print("Null APY count:", df['apy'].isnull().sum())
                print("Null Price count:", df['eth_price'].isnull().sum())
                
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    test_resolution("1H", 30)
    test_resolution("RAW", 1)