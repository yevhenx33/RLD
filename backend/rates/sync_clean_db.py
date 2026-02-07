import sqlite3
import os
import sys
import time

# Add backend to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import ASSETS, DB_PATH, CLEAN_DB_PATH

RAW_DB_PATH = DB_PATH

# Column mapping for assets
SYMBOL_MAP = {
    "USDC": "usdc_rate",
    "DAI": "dai_rate",
    "USDT": "usdt_rate"
}


def _ensure_tables(cursor):
    """Create tables if they don't exist (idempotent)."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hourly_stats (
            timestamp INTEGER PRIMARY KEY,
            eth_price REAL,
            usdc_rate REAL,
            dai_rate REAL,
            usdt_rate REAL,
            sofr_rate REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)


def _get_sync_state(cursor, key, default="0"):
    """Read a value from sync_state."""
    cursor.execute("SELECT value FROM sync_state WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default


def _set_sync_state(cursor, key, value):
    """Write a value to sync_state."""
    cursor.execute(
        "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
        (key, str(value))
    )


def _sync_eth_prices_incremental(conn_raw, cursor_clean, since_ts):
    """Sync only ETH prices newer than since_ts."""
    # Re-aggregate the current hour bucket too (partial hour edge case)
    hour_floor = (since_ts // 3600) * 3600

    cursor_raw = conn_raw.cursor()
    cursor_raw.execute(
        "SELECT timestamp, price FROM eth_prices WHERE timestamp >= ?",
        (hour_floor,)
    )
    rows = cursor_raw.fetchall()

    if not rows:
        return 0

    # Group by hour and average
    hourly = {}
    for ts, price in rows:
        hour_ts = (ts // 3600) * 3600
        if hour_ts not in hourly:
            hourly[hour_ts] = []
        hourly[hour_ts].append(price)

    count = 0
    for hour_ts, prices in hourly.items():
        avg_price = sum(prices) / len(prices)
        cursor_clean.execute("""
            INSERT INTO hourly_stats (timestamp, eth_price) VALUES (?, ?)
            ON CONFLICT(timestamp) DO UPDATE SET eth_price=excluded.eth_price
        """, (hour_ts, avg_price))
        count += 1

    return count


def _sync_asset_incremental(conn_raw, cursor_clean, table, col_name, since_ts):
    """Sync only asset rates newer than since_ts."""
    hour_floor = (since_ts // 3600) * 3600

    cursor_raw = conn_raw.cursor()
    cursor_raw.execute(
        f"SELECT timestamp, apy FROM {table} WHERE timestamp >= ?",
        (hour_floor,)
    )
    rows = cursor_raw.fetchall()

    if not rows:
        return 0

    # Group by hour and average
    hourly = {}
    for ts, apy in rows:
        hour_ts = (ts // 3600) * 3600
        if hour_ts not in hourly:
            hourly[hour_ts] = []
        hourly[hour_ts].append(apy)

    count = 0
    for hour_ts, apys in hourly.items():
        avg_apy = sum(apys) / len(apys)
        cursor_clean.execute(f"""
            INSERT INTO hourly_stats (timestamp, {col_name}) VALUES (?, ?)
            ON CONFLICT(timestamp) DO UPDATE SET {col_name}=excluded.{col_name}
        """, (hour_ts, avg_apy))
        count += 1

    return count


def sync_clean_db(force_full=False):
    """
    Sync raw rate data to hourly aggregated clean DB.
    
    Incremental by default: only processes data since last sync.
    Set force_full=True for initial bootstrap or recovery.
    """
    start = time.time()

    if not os.path.exists(RAW_DB_PATH):
        print("❌ Raw Database not found!")
        return

    conn_raw = sqlite3.connect(f'file:{RAW_DB_PATH}?mode=ro', uri=True)
    conn_clean = sqlite3.connect(CLEAN_DB_PATH)
    cursor_clean = conn_clean.cursor()

    _ensure_tables(cursor_clean)
    conn_clean.commit()

    # Determine sync mode
    last_synced_ts = int(_get_sync_state(cursor_clean, 'last_synced_timestamp', '0'))
    is_incremental = last_synced_ts > 0 and not force_full

    if is_incremental:
        print(f"🔄 INCREMENTAL SYNC (since ts={last_synced_ts})...")
    else:
        print("🔄 FULL SYNC...")
        last_synced_ts = 1677801600  # March 3, 2023 (genesis)

    # 1. Sync ETH Prices
    eth_count = _sync_eth_prices_incremental(conn_raw, cursor_clean, last_synced_ts)
    if eth_count:
        print(f"   ETH prices: {eth_count} hourly records")

    # 2. Sync Assets
    for symbol, config in ASSETS.items():
        if config['type'] != 'onchain':
            continue
        col_name = SYMBOL_MAP.get(symbol)
        if not col_name:
            continue
        count = _sync_asset_incremental(conn_raw, cursor_clean, config['table'], col_name, last_synced_ts)
        if count:
            print(f"   {symbol}: {count} hourly records")

    # 3. Update sync timestamps
    now_ts = int(time.time())
    _set_sync_state(cursor_clean, 'last_synced_timestamp', now_ts)

    # 4. Update last_block_number from raw DB
    try:
        cursor_raw = conn_raw.cursor()
        cursor_raw.execute("SELECT MAX(block_number) FROM rates")
        latest_block = cursor_raw.fetchone()[0]
        if latest_block:
            _set_sync_state(cursor_clean, 'last_block_number', latest_block)
    except Exception as e:
        print(f"   ⚠️ Error reading latest block: {e}")

    conn_clean.commit()
    conn_raw.close()
    conn_clean.close()

    elapsed = time.time() - start
    print(f"✅ Sync complete ({elapsed:.2f}s)")


if __name__ == "__main__":
    # CLI: pass --full to force full resync
    force = "--full" in sys.argv
    sync_clean_db(force_full=force)
