import sqlite3
import time

DB_PATH = "aave_rates.db"

def aggregate_data():
    print("🚀 Starting Data Aggregation (rates -> rates_1h)...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. DROP Existing Table/View
    try:
        cursor.execute("DROP TABLE IF EXISTS rates_1h") # Migration from static table
    except sqlite3.OperationalError:
        pass # Might be a view

    cursor.execute("DROP VIEW IF EXISTS rates_1h")
    
    # 2. CREATE Dynamic View
    print("⏳ Creating Dynamic View 'rates_1h'...")
    query = '''
        CREATE VIEW rates_1h AS
        WITH all_hours AS (
            SELECT (timestamp / 3600) * 3600 as hour_ts FROM rates
            UNION
            SELECT (timestamp / 3600) * 3600 as hour_ts FROM eth_prices
        ),
        hourly_rates AS (
            SELECT 
                (timestamp / 3600) * 3600 as hour_ts,
                AVG(apy) as avg_apy,
                MAX(block_number) as max_block
            FROM rates
            GROUP BY hour_ts
        ),
        hourly_prices AS (
            SELECT 
                (timestamp / 3600) * 3600 as hour_ts,
                AVG(price) as avg_price
            FROM eth_prices
            GROUP BY hour_ts
        )
        SELECT 
            h.hour_ts as timestamp,
            r.avg_apy as apy,
            p.avg_price as eth_price,
            r.max_block as block_number
        FROM all_hours h
        LEFT JOIN hourly_rates r ON h.hour_ts = r.hour_ts
        LEFT JOIN hourly_prices p ON h.hour_ts = p.hour_ts
        ORDER BY h.hour_ts DESC;
    '''
    
    try:
        cursor.execute(query)
        conn.commit()
        print(f"✅ Successfully created VIEW rates_1h.")
    except Exception as e:
        print(f"❌ Aggregation Failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    aggregate_data()
