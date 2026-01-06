import sqlite3
import pandas as pd
import os

# --- CONFIGURATION ---
CSV_FILE = "aavescan-aave-v3-ethereum-usdc-history.csv"
DB_FILE = "aave_rates.db"

def import_history():
    # 1. Check if files exist
    if not os.path.exists(CSV_FILE):
        print(f"Error: Could not find {CSV_FILE}")
        return

    # 2. Read CSV
    print(f"Reading {CSV_FILE}...")
    try:
        df = pd.read_csv(CSV_FILE)
    except Exception as e:
        print(f"Failed to read CSV: {e}")
        return

    # 3. Clean Data
    # Drop rows where critical data might be missing
    df = df.dropna(subset=['timestamp', 'borrow APR'])
    
    # Ensure timestamp is an integer
    df['timestamp'] = df['timestamp'].astype(int)

    # 4. Connect to Database
    print(f"Connecting to {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Create table if it doesn't exist (just in case)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rates (
            block_number INTEGER,
            timestamp INTEGER,
            apy REAL
        )
    ''')

    # 5. Insert Data
    count = 0
    for _, row in df.iterrows():
        # Map CSV columns to DB schema
        # block_number = 0 (Placeholder for history)
        # timestamp = row['timestamp']
        # apy = row['borrow APR']
        
        timestamp = int(row['timestamp'])
        apy = float(row['borrow APR'])
        
        # Check if this timestamp already exists to avoid duplicates
        cursor.execute("SELECT 1 FROM rates WHERE timestamp = ?", (timestamp,))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO rates VALUES (?, ?, ?)", (0, timestamp, apy))
            count += 1
        else:
            # Optional: Update existing record or skip
            pass

    conn.commit()
    conn.close()

    print("-" * 30)
    print(f"Success! Imported {count} historical records.")
    print("Restart your dashboard to see the 90-day trend.")

if __name__ == "__main__":
    import_history()