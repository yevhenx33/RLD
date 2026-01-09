import requests
import sqlite3
import time
import subprocess
import os

DB_PATH = "aave_rates.db"
# Graph API Endpoint from user's R script
GRAPH_URL = "https://gateway.thegraph.com/api/b838305c2d118eb10501790526a71bb3/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
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

def fetch_prices(start_ts):
    query_template = """
    {
      poolHourDatas(
        orderBy: periodStartUnix
        orderDirection: asc
        where: {
            pool: "%s", 
            periodStartUnix_gt: %d
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
        print(f"📡 Fetching data after timestamp: {current_ts}...")
        query = query_template % (POOL_ADDRESS, current_ts)
        
        try:
            response = requests.post(GRAPH_URL, json={'query': query})
            response.raise_for_status()
            data = response.json()
            
            if 'errors' in data:
                print(f"❌ GraphQL Errors: {data['errors']}")
                break
                
            items = data.get('data', {}).get('poolHourDatas', [])
            
            if not items:
                print("✅ No more data found.")
                break
                
            all_prices.extend(items)
            print(f"   Received {len(items)} records.")
            
            # Update timestamp for pagination
            last_item_ts = int(items[-1]['periodStartUnix'])
            if last_item_ts <= current_ts:
                break # Avoid infinite loop if timestamp doesn't advance
            current_ts = last_item_ts
            
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

def main():
    print("🚀 Starting ETH Price Sync...")
    
    # 1. Check for Genesis Data (User requested cut to March 3, 2023)
    GENESIS_TIMESTAMP = 1677801600 # March 3, 2023
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM eth_prices")
    min_ts, max_ts = cursor.fetchone()
    conn.close()
    
    print(f"📅 DB Range: {min_ts} -> {max_ts}")
    
    start_fetch_ts = GENESIS_TIMESTAMP
    
    # If we have data starting way later than genesis, we need to backfill from genesis
    # But if we want to fill gaps, we might need a more complex logic or just brute force forward.
    # Given requests are cheap (1000 items per request), let's just start from Genesis if min_ts is significantly later.
    if min_ts is None or min_ts > GENESIS_TIMESTAMP + 86400:
         print("⚠️ Missing early data. Forcing fetch from Target Start (Mar 2023).")
         start_fetch_ts = GENESIS_TIMESTAMP
    elif max_ts:
         print("✅ History start looks okay. Resuming from last known timestamp.")
         start_fetch_ts = max_ts
    
    # 2. Fetch new data
    new_prices = fetch_prices(start_fetch_ts)
    
    if new_prices:
        # 3. Save to DB
        save_prices(new_prices)
        
        # 4. Trigger Aggregation
        print("🔄 Triggering Data Aggregation...")
        subprocess.run(["python3", "aggregate_data.py"])
    else:
        print("🎉 Database is up to date.")

if __name__ == "__main__":
    main()
