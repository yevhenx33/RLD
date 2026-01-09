import sqlite3

ASSETS = {
    "DAI": "rates_dai",
    "USDT": "rates_usdt"
}

conn = sqlite3.connect('aave_rates.db')
cursor = conn.cursor()

for symbol, table in ASSETS.items():
    print(f"Creating table {table}...")
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {table} (
            block_number INTEGER,
            timestamp INTEGER,
            apy REAL
        )
    ''')

conn.commit()
print("Tables created.")
conn.close()
