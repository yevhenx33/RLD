import sqlite3

db_path = "/home/ubuntu/RLD/backend/clean_rates.db"
conn = sqlite3.connect(db_path)

print("Tables in Aave DB:")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
for t in tables:
    print(t[0])
    schema = conn.execute(f"PRAGMA table_info({t[0]});").fetchall()
    print("  ", schema)

conn.close()
