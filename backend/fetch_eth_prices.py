import requests
import sqlite3
import time
import subprocess
import os
from config import DB_NAME, ETH_PRICE_GRAPH_URL

DB_PATH = os.path.join(os.path.dirname(__file__), DB_NAME)
# Graph API Endpoint
GRAPH_URL = ETH_PRICE_GRAPH_URL
POOL_ADDRESS = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640" # ETH/USDC 0.05%

def get_last_timestamp():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT MAX(timestamp) FROM eth_prices")
        result = cursor.fetchone()
        return result[0] if result and result[0] else 0
    except Exception as e:
        print(f"⚠️ Error getting last timestamp: {e}")
        return 0
    finally:
        conn.close()

def fetch_prices(start_ts, end_ts=None):
    """
    Fetches prices starting from start_ts. 
    If end_ts is provided, fetches only up to that timestamp.
    """
    
    # Construct "where" clause based on end_ts
    time_filter = f"periodStartUnix_gt: {start_ts}"
    if end_ts:
        time_filter += f", periodStartUnix_lt: {end_ts}"
        print(f"📡 Fetching GAP from {start_ts} to {end_ts}...")
    else:
        print(f"📡 Fetching data after timestamp: {start_ts}...")
        
    query_template = """
    {
      poolHourDatas(
        orderBy: periodStartUnix
        orderDirection: asc
        where: {
            pool: "%s", 
            %s
        }
        first: 1000
      ) {
        periodStartUnix
        token0Price
      }
    }
    """
    
    all_prices = []
    current_ts = start_ts
    
    while True:
        # Re-construct query with dynamic current_ts for pagination
        # We need to respect the original end_ts constraint in every page
        current_time_filter = f"periodStartUnix_gt: {current_ts}"
        if end_ts:
             current_time_filter += f", periodStartUnix_lt: {end_ts}"

        query = query_template % (POOL_ADDRESS, current_time_filter)
        
        try:
            response = requests.post(GRAPH_URL, json={'query': query})
            response.raise_for_status()
            data = response.json()
            
            if 'errors' in data:
                print(f"❌ GraphQL Errors: {data['errors']}")
                break
                
            items = data.get('data', {}).get('poolHourDatas', [])
            
            if not items:
                # If searching for a gap and found nothing, we are done with this gap
                if end_ts: 
                    print("   Gap fill complete (no more items).")
                else:
                    print("✅ No more data found.")
                break
                
            all_prices.extend(items)
            print(f"   Received {len(items)} records.")
            
            # Update timestamp for pagination
            last_item_ts = int(items[-1]['periodStartUnix'])
            if last_item_ts <= current_ts:
                break # Avoid infinite loop if timestamp doesn't advance
            current_ts = last_item_ts
            
            # Additional safety check for end_ts (though GraphQL filter should handle it)
            if end_ts and current_ts >= end_ts:
                break
            
            # Sleep to be nice to API
            time.sleep(0.5)
            
        except Exception as e:
            print(f"❌ API Request Failed: {e}")
            break
            
    return all_prices

def save_prices(prices):
    if not prices:
        return
        
    print(f"💾 Saving {len(prices)} records to database...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    count = 0
    for p in prices:
        try:
            ts = int(p['periodStartUnix'])
            # User R script: token0Price ~ 1300 -> This is ETH price in USD
            price = float(p['token0Price'])
            
            cursor.execute(
                "INSERT OR REPLACE INTO eth_prices (timestamp, price) VALUES (?, ?)",
                (ts, price)
            )
            count += 1
        except Exception as e:
            print(f"⚠️ Error parsing record {p}: {e}")
            
    conn.commit()
    conn.close()
    print(f"✅ Saved {count} records.")

def fill_gaps():
    print("🔍 Checking for dataset gaps...")
    conn = sqlite3.connect(DB_PATH)
    
    # Identify gaps > 1 hour (3600s)
    # Self-join approach often cleaner in code than complicated SQL if dataset isn't huge
    # But SQL is faster.
    df_query = """
    SELECT timestamp, next_ts, diff FROM (
        SELECT 
            timestamp, 
            LEAD(timestamp) OVER (ORDER BY timestamp) as next_ts,
            LEAD(timestamp) OVER (ORDER BY timestamp) - timestamp as diff
        FROM eth_prices
        WHERE timestamp >= 1677801600
    ) WHERE diff > 3600
    """
    
    try:
        cursor = conn.cursor()
        cursor.execute(df_query)
        gaps = cursor.fetchall()
        
        if not gaps:
            print("✅ No internal gaps found.")
            return

        print(f"⚠️ Found {len(gaps)} gaps. Attempting to fill...")
        
        total_filled = 0
        for g in gaps:
            start = g[0]
            end = g[1]
            diff = g[2]
            
            # Only fill if meaningful gap (e.g. > 1 week implies missing data, but user said "offline" so likely few hours/days)
            # We'll fill all hourly gaps.
            hours_missing = diff // 3600
            print(f"   Gap: {getattr(time, 'ctime', lambda x: x)(start)} -> {getattr(time, 'ctime', lambda x: x)(end)} (~{hours_missing} hours)")
            
            gap_prices = fetch_prices(start, end)
            if gap_prices:
                save_prices(gap_prices)
                total_filled += len(gap_prices)
                
        print(f"🎉 Filled {total_filled} missing records.")
        
    except Exception as e:
        print(f"⚠️ Gap check failed: {e}")
    finally:
        conn.close()

def main():
    print("🚀 Starting ETH Price Sync...")
    
    # 1. Check for Genesis Data (User requested cut to March 3, 2023)
    GENESIS_TIMESTAMP = 1677801600 # March 3, 2023
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS eth_prices (timestamp INTEGER PRIMARY KEY, price REAL)")
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM eth_prices")
    min_ts, max_ts = cursor.fetchone()
    conn.close()
    
    print(f"📅 DB Range: {min_ts} -> {max_ts}")
    
    start_fetch_ts = GENESIS_TIMESTAMP
    
    # If we have data starting way later than genesis, we need to backfill from genesis
    if min_ts is None or min_ts > GENESIS_TIMESTAMP + 86400:
         print("⚠️ Missing early data. Forcing fetch from Target Start (Mar 2023).")
         start_fetch_ts = GENESIS_TIMESTAMP
    elif max_ts:
         print("✅ History start looks okay.")
         # Run Gap Fill before resuming head sync
         fill_gaps()
         print("Resume from last known timestamp.")
         start_fetch_ts = max_ts
    
    # 2. Fetch new data (Forward Sync)
    new_prices = fetch_prices(start_fetch_ts)
    
    if new_prices:
        # 3. Save to DB
        save_prices(new_prices)
        
        # 4. Trigger Aggregation
        print("🔄 Triggering Data Aggregation...")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(["python3", "aggregate_data.py"], cwd=script_dir)
    else:
        print("🎉 Database is up to date.")

if __name__ == "__main__":
    main()
