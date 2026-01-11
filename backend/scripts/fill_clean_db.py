import sqlite3
import pandas as pd
import os
import time

# Paths
SOURCE_DB_NAME = "aave_rates.db"
TARGET_DB_NAME = "clean_rates.db"

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
SOURCE_DB_PATH = os.path.join(BASE_DIR, SOURCE_DB_NAME)
TARGET_DB_PATH = os.path.join(BASE_DIR, TARGET_DB_NAME)

# Config
START_TIMESTAMP = 1677801600 # March 3, 2023

def fill_clean_db():
    print(f"🚀 Starting Data Population...")
    print(f"   Source: {SOURCE_DB_PATH}")
    print(f"   Target: {TARGET_DB_PATH}")
    print(f"   Start Date: {time.ctime(START_TIMESTAMP)}")
    
    conn_source = sqlite3.connect(f'file:{SOURCE_DB_PATH}?mode=ro', uri=True)
    
    # 1. Fetch Data
    def fetch_data(table_name, value_col, alias):
        print(f"   Fetching {alias} from {table_name}...")
        query = f"SELECT timestamp, {value_col} FROM {table_name} WHERE timestamp >= {START_TIMESTAMP} ORDER BY timestamp ASC"
        df = pd.read_sql_query(query, conn_source)
        
        if df.empty:
            print(f"   ⚠️ Warning: {alias} has no data!")
            return pd.DataFrame()
            
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
        df = df.set_index('datetime')
        df = df.sort_index()
        
        # Resample to Hourly, taking the LAST value of the hour
        # 'h' is deprecated in some versions, 'h' -> 'H'? '1h' is standard.
        # closed='left', label='left' is usually default for hourly frequency (00:00 covers 00:00-01:00)
        # But if we want "Last price OF the hour", e.g. price at 10:59 becomes the price for 10:00 bucket?
        # Or usually price for "11:00" bucket?
        # If I have candles: 10:00 candle represents 10:00 -> 11:00. Close price is at 10:59.
        # So resample('1h').last() does exactly this.
        
        hourly = df[value_col].resample('1h').last()
        return hourly.rename(alias)

    # Assets to fetch
    eth = fetch_data('eth_prices', 'price', 'eth_price')
    usdc = fetch_data('rates', 'apy', 'usdc_rate')
    dai = fetch_data('rates_dai', 'apy', 'dai_rate')
    usdt = fetch_data('rates_usdt', 'apy', 'usdt_rate')
    sofr = fetch_data('sofr_rates', 'apy', 'sofr_rate')
    
    conn_source.close()
    
    # 2. Merge
    print("   Merging datasets...")
    # Concatenate all series along columns (axis=1) matches indices
    merged_df = pd.concat([eth, usdc, dai, usdt, sofr], axis=1)
    
    # Drop rows where ALL columns are NaN? 
    # Or keep all hours?
    # User said "merge all our dataseries".
    # Resample creates a regular index.
    # So we will have a continuous hourly timeline from Start to End of data.
    
    # 3. Format for DB
    # Restore timestamp column
    merged_df['timestamp'] = merged_df.index.astype(int) // 10**9
    
    # 4. Save to Target DB
    print(f"   Saving {len(merged_df)} rows to {TARGET_DB_PATH}...")
    conn_target = sqlite3.connect(TARGET_DB_PATH)
    
    # Use to_sql
    # We want to insert into 'hourly_stats'
    # Match column names
    # DataFrame columns: eth_price, usdc_rate, dai_rate, usdt_rate, sofr_rate, timestamp
    # Table columns: timestamp, eth_price, usdc_rate, dai_rate, usdt_rate, sofr_rate
    
    try:
        merged_df.to_sql('hourly_stats', conn_target, if_exists='append', index=False)
        print("✅ Data population complete.")
        
        # Validation
        cursor = conn_target.cursor()
        cursor.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM hourly_stats")
        row = cursor.fetchone()
        print(f"   Stats: {row[0]} rows. Range: {row[1]} -> {row[2]}")
        
    except Exception as e:
        print(f"❌ Error saving to DB: {e}")
        
    conn_target.close()

if __name__ == "__main__":
    fill_clean_db()
