"""Morpho Blue Indexer — Database Schema & Helpers."""
import sqlite3, os
from contextlib import contextmanager
from morpho.config import DB_PATH, DB_DIR

def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        conn.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_params (
    market_id TEXT PRIMARY KEY, loan_token TEXT NOT NULL,
    loan_symbol TEXT, loan_decimals INTEGER,
    collateral_token TEXT, collateral_symbol TEXT, collateral_decimals INTEGER,
    oracle TEXT, irm TEXT, lltv REAL,
    created_block INTEGER, created_timestamp INTEGER, discovered_at INTEGER
);
CREATE TABLE IF NOT EXISTS vault_meta (
    vault_address TEXT PRIMARY KEY, name TEXT, symbol TEXT,
    asset_address TEXT, asset_symbol TEXT, discovered_at INTEGER
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    timestamp INTEGER NOT NULL, block_number INTEGER NOT NULL,
    market_id TEXT NOT NULL,
    total_supply_assets TEXT, total_borrow_assets TEXT,
    total_supply_shares TEXT, total_borrow_shares TEXT,
    last_update INTEGER, fee INTEGER,
    utilization REAL, borrow_apy REAL, supply_apy REAL,
    oracle_price TEXT, rate_at_target TEXT,
    PRIMARY KEY (market_id, timestamp)
);
CREATE TABLE IF NOT EXISTS vault_snapshots (
    timestamp INTEGER NOT NULL, block_number INTEGER NOT NULL,
    vault_address TEXT NOT NULL,
    total_assets TEXT, total_supply TEXT,
    share_price REAL, total_assets_usd REAL,
    PRIMARY KEY (vault_address, timestamp)
);
CREATE TABLE IF NOT EXISTS vault_allocations (
    timestamp INTEGER NOT NULL, vault_address TEXT NOT NULL,
    market_id TEXT NOT NULL,
    supply_shares TEXT, supply_assets TEXT,
    supply_usd REAL, share_pct REAL,
    PRIMARY KEY (vault_address, market_id, timestamp)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    block_number INTEGER NOT NULL, tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL, timestamp INTEGER,
    event_type TEXT NOT NULL, market_id TEXT, data_json TEXT,
    UNIQUE(tx_hash, log_index)
);
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_ms_ts ON market_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_ms_market ON market_snapshots(market_id);
CREATE INDEX IF NOT EXISTS idx_vs_ts ON vault_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_va_ts ON vault_allocations(timestamp);
CREATE INDEX IF NOT EXISTS idx_va_vault ON vault_allocations(vault_address);
CREATE INDEX IF NOT EXISTS idx_va_market ON vault_allocations(market_id);
CREATE INDEX IF NOT EXISTS idx_ev_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_ev_block ON events(block_number);
"""

def get_sync_value(key):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def set_sync_value(key, value):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO sync_state (key,value) VALUES (?,?)", (key, value))

def get_tracked_markets():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM market_params").fetchall()]

def get_tracked_vaults():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM vault_meta").fetchall()]
