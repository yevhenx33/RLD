import sqlite3
import pandas as pd
import subprocess

print("--- MORPHO SQLITE SCHEMA ---")
db_path = "/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db"
try:
    conn = sqlite3.connect(db_path)
    res = conn.execute("PRAGMA table_info(hourly_stats);").fetchall()
    if res:
        print("hourly_stats:", res)
    res_markets = conn.execute("PRAGMA table_info(market_snapshots);").fetchall()
    if res_markets:
        print("market_snapshots:", res_markets)
    conn.close()
except Exception as e:
    print(e)
    
print("\n--- AAVE POSTGRES SCHEMA ---")
try:
    schema = subprocess.check_output(
        ["docker", "exec", "rld_timescale", "psql", "-U", "postgres", "-d", "rld_data", "-t", "-A", "-c", 
         "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'aave_hourly_state';"], 
        encoding='utf-8'
    )
    print(schema)
except Exception as e:
    print(e)
