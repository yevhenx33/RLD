import sqlite3
import os
import sys
import pandas as pd
from datetime import datetime

# Add backend to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import ASSETS, DB_NAME

# Paths
RAW_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "aave_rates.db")
CLEAN_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "clean_rates.db")

def sync_clean_db():
    print("🔄 SYNC: Validating Clean DB against Raw Data...")
    
    if not os.path.exists(RAW_DB_PATH):
        print("❌ Raw Database not found!")
        return

    # Connect to both
    conn_raw = sqlite3.connect(f'file:{RAW_DB_PATH}?mode=ro', uri=True)
    conn_clean = sqlite3.connect(CLEAN_DB_PATH)
    cursor_clean = conn_clean.cursor()

    # Ensure clean table exists (idempotent)
    cursor_clean.execute("""
        CREATE TABLE IF NOT EXISTS hourly_stats (
            timestamp INTEGER PRIMARY KEY,
            eth_price REAL,
            usdc_rate REAL,
            dai_rate REAL,
            usdt_rate REAL,
            sofr_rate REAL
        )
    """)
    conn_clean.commit()

    # 1. Sync ETH Prices
    print("   Running sync for ETH prices (>= March 3, 2023)...")
    # 1677801600 = March 3, 2023
    df_eth = pd.read_sql_query("SELECT timestamp, price as eth_price FROM eth_prices WHERE timestamp >= 1677801600", conn_raw)
    if not df_eth.empty:
        # Round timestamp to hour
        df_eth['hour_ts'] = (df_eth['timestamp'] // 3600) * 3600
        # Group by hour and take average
        hourly_eth = df_eth.groupby('hour_ts')['eth_price'].mean().reset_index()
        
        # Upsert
        count = 0
        for _, row in hourly_eth.iterrows():
            ts = int(row['hour_ts'])
            val = float(row['eth_price'])
            cursor_clean.execute("""
                INSERT INTO hourly_stats (timestamp, eth_price) VALUES (?, ?)
                ON CONFLICT(timestamp) DO UPDATE SET eth_price=excluded.eth_price
            """, (ts, val))
            count += 1
        print(f"   Synced {count} ETH hourly records.")

    # 2. Sync Assets
    symbol_map = {
        "USDC": "usdc_rate",
        "DAI": "dai_rate",
        "USDT": "usdt_rate"
    }

    for symbol, config in ASSETS.items():
        if config['type'] != 'onchain': continue
        col_name = symbol_map.get(symbol)
        if not col_name: continue

        table = config['table']
        print(f"   Running sync for {symbol}...")
        
        try:
            df = pd.read_sql_query(f"SELECT timestamp, apy FROM {table}", conn_raw)
            if df.empty: continue
            
            df['hour_ts'] = (df['timestamp'] // 3600) * 3600
            hourly_apy = df.groupby('hour_ts')['apy'].mean().reset_index()

            count = 0
            for _, row in hourly_apy.iterrows():
                ts = int(row['hour_ts'])
                val = float(row['apy'])
                cursor_clean.execute(f"""
                    INSERT INTO hourly_stats (timestamp, {col_name}) VALUES (?, ?)
                    ON CONFLICT(timestamp) DO UPDATE SET {col_name}=excluded.{col_name}
                """, (ts, val))
                count += 1
            print(f"   Synced {count} {symbol} hourly records.")
            
        except Exception as e:
            print(f"   ⚠️ Error syncing {symbol}: {e}")

    conn_clean.commit()
    conn_raw.close()
    conn_clean.close()
    print("✅ Sync Complete.")

if __name__ == "__main__":
    sync_clean_db()
