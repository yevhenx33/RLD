import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))


import pandas as pd
import sqlite3
import os
from datetime import datetime

# DB Path
DB_PATH = "backend/aave_rates.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sofr_rates (
            timestamp INTEGER PRIMARY KEY,
            apy REAL
        )
    """)
    conn.commit()
    conn.close()

def import_sofr():
    print("Reading SOFR.xlsx...")
    file_path = "SOFR.xlsx"
    if not os.path.exists(file_path):
        file_path = "../SOFR.xlsx" # Try up one level
    
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return

    try:
        df = pd.read_excel(file_path)
        
        # Filter for SOFR rows only
        # Column names based on inspection: 'Effective Date', 'Rate Type', 'Rate (%)'
        df = df[df['Rate Type'] == 'SOFR'].copy()
        
        # Parse Dates
        df['Effective Date'] = pd.to_datetime(df['Effective Date'], format='%m/%d/%Y')
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        print(f"Found {len(df)} SOFR records. Inserting...")
        
        count = 0
        for _, row in df.iterrows():
            ts = int(row['Effective Date'].timestamp())
            rate = row['Rate (%)']
            
            # Upsert
            cursor.execute("""
                INSERT OR REPLACE INTO sofr_rates (timestamp, apy)
                VALUES (?, ?)
            """, (ts, rate))
            count += 1
            
        conn.commit()
        conn.close()
        print(f"✅ Successfully imported {count} SOFR rates.")
        
    except Exception as e:
        print(f"❌ Error importing SOFR: {e}")

if __name__ == "__main__":
    init_db()
    import_sofr()