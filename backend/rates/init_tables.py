import sqlite3
import os
import sys

# Add backend to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import ASSETS, DB_PATH

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

for symbol, data in ASSETS.items():
    if data['type'] != 'onchain':
        continue
        
    print(f"Creating table {data['table']}...")
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {data['table']} (
            block_number INTEGER,
            timestamp INTEGER,
            apy REAL
        )
    ''')

conn.commit()
print("Tables created.")
conn.close()
