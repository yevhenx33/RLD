import sqlite3
import os
import sys

# Add backend to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import CLEAN_DB_PATH

# Define DB Path
DB_PATH = CLEAN_DB_PATH

def init_db():
    print(f"🚀 Initializing new database: {DB_PATH}")
    
    # Remove existing file if we want a fresh start, 
    # but usually init is safe to run repeatedly if using CREATE TABLE IF NOT EXISTS
    # For this specific task "create a separate DB", we might want to ensure it's fresh?
    # Let's keep data if exists but ensure table structure.
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Single unified table for hourly stats
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hourly_stats (
            timestamp INTEGER PRIMARY KEY, -- Hourly timestamp (e.g. 1677801600, 1677805200)
            eth_price REAL,
            usdc_rate REAL,
            dai_rate REAL,
            usdt_rate REAL,
            sofr_rate REAL
        )
    """)
    
    # Enable WAL Mode for Concurrency
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    
    
    conn.commit()
    conn.close()
    print("✅ Database initialized successfully.")

if __name__ == "__main__":
    init_db()
