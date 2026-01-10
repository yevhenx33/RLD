import sqlite3
from config import ASSETS, DB_NAME

conn = sqlite3.connect(DB_NAME)
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
