
import sqlite3
import time

DB_PATH = "aave_rates.db"

def setup_view():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS rates_1h") # Dangerous! But I have backup scripts.
    c.execute("DROP VIEW IF EXISTS rates_1h")
    
    # Create VIEW
    c.execute('''
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
            h.hour_ts as timestamp, -- Rename to timestamp to match expected schema
            r.avg_apy as apy,       -- Rename to apy
            p.avg_price as eth_price,
            r.max_block as block_number
        FROM all_hours h
        LEFT JOIN hourly_rates r ON h.hour_ts = r.hour_ts
        LEFT JOIN hourly_prices p ON h.hour_ts = p.hour_ts
        ORDER BY h.hour_ts DESC;
    ''')
    conn.commit()
    conn.close()
    print("VIEW rates_1h created.")

def benchmark():
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    c = conn.cursor()
    start = time.time()
    c.execute("SELECT * FROM rates_1h LIMIT 10") # Trigger aggregation
    rows = c.fetchall()
    end = time.time()
    print(f"Query Time (Full View): {(end-start)*1000:.2f}ms")
    print(f"Rows sample: {len(rows)}")
    conn.close()

if __name__ == "__main__":
    setup_view()
    benchmark()
