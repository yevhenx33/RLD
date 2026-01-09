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
    
    # 2. CREATE Dynamic Views (1H, 4H, 1D)
    
    # helper to generate view query
    def create_view_query(view_name, seconds):
        return f'''
        CREATE VIEW {view_name} AS
        WITH all_periods AS (
            SELECT (timestamp / {seconds}) * {seconds} as period_ts FROM rates
            UNION
            SELECT (timestamp / {seconds}) * {seconds} as period_ts FROM eth_prices
        ),
        period_rates AS (
            SELECT 
                (timestamp / {seconds}) * {seconds} as period_ts,
                AVG(apy) as avg_apy,
                MAX(block_number) as max_block
            FROM rates
            GROUP BY period_ts
        ),
        period_prices AS (
            SELECT 
                (timestamp / {seconds}) * {seconds} as period_ts,
                AVG(price) as avg_price
            FROM eth_prices
            GROUP BY period_ts
        )
        SELECT 
            h.period_ts as timestamp,
            r.avg_apy as apy,
            p.avg_price as eth_price,
            r.max_block as block_number
        FROM all_periods h
        LEFT JOIN period_rates r ON h.period_ts = r.period_ts
        LEFT JOIN period_prices p ON h.period_ts = p.period_ts
        ORDER BY h.period_ts DESC;
        '''

    print("⏳ Creating Dynamic View 'rates_1h'...")
    cursor.execute(create_view_query("rates_1h", 3600))
    
    print("⏳ Creating Dynamic View 'rates_4h'...")
    try: cursor.execute("DROP VIEW IF EXISTS rates_4h")
    except: pass
    cursor.execute(create_view_query("rates_4h", 14400))

    print("⏳ Creating Dynamic View 'rates_1d'...")
    try: cursor.execute("DROP VIEW IF EXISTS rates_1d")
    except: pass
    cursor.execute(create_view_query("rates_1d", 86400))
    
    print(f"✅ Successfully created/refreshed Views: rates_1h, rates_4h, rates_1d.")
    
if __name__ == "__main__":
    aggregate_data()
