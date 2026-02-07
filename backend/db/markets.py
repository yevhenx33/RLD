"""
Database module for local blockchain indexer.
Manages SQLite database for storing RLD market deployments.
"""
import sqlite3
import logging
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "simulations.db")

def init_db():
    """Initialize database and create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create markets table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT UNIQUE NOT NULL,
            market_address TEXT,
            position_token TEXT,
            underlying_token TEXT,
            collateral_token TEXT,
            underlying_pool TEXT,
            curator TEXT,
            spot_oracle TEXT,
            rate_oracle TEXT,
            liquidation_module TEXT,
            
            -- Risk params (stored as WAD values)
            min_col_ratio INTEGER,
            maintenance_margin INTEGER,
            liquidation_close_factor INTEGER,
            oracle_period INTEGER,
            pool_fee INTEGER,
            tick_spacing INTEGER,
            
            -- Metadata
            position_token_name TEXT,
            position_token_symbol TEXT,
            deployment_block INTEGER,
            deployment_timestamp INTEGER,
            status TEXT DEFAULT 'active',
            
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_hash ON markets(tx_hash)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_address ON markets(market_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON markets(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_deployment_block ON markets(deployment_block)")
    
    # Create indexer state table to track last processed block
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS indexer_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_block INTEGER NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    logging.info(f"✅ Database initialized at {DB_PATH}")

@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def insert_market(market_data: Dict[str, Any]) -> int:
    """Insert a new market into the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO markets (
                tx_hash, market_address, position_token,
                underlying_token, collateral_token, underlying_pool,
                curator, spot_oracle, rate_oracle, liquidation_module,
                min_col_ratio, maintenance_margin, liquidation_close_factor,
                oracle_period, pool_fee, tick_spacing,
                position_token_name, position_token_symbol,
                deployment_block, deployment_timestamp, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market_data['tx_hash'],
            market_data.get('market_address'),
            market_data.get('position_token'),
            market_data['underlying_token'],
            market_data['collateral_token'],
            market_data['underlying_pool'],
            market_data['curator'],
            market_data['spot_oracle'],
            market_data['rate_oracle'],
            market_data['liquidation_module'],
            market_data['min_col_ratio'],
            market_data['maintenance_margin'],
            market_data['liquidation_close_factor'],
            market_data['oracle_period'],
            market_data['pool_fee'],
            market_data['tick_spacing'],
            market_data['position_token_name'],
            market_data['position_token_symbol'],
            market_data['deployment_block'],
            market_data['deployment_timestamp'],
            market_data.get('status', 'active')
        ))
        conn.commit()
        return cursor.lastrowid

def get_all_markets() -> List[Dict[str, Any]]:
    """Get all markets from database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM markets 
            WHERE status = 'active'
            ORDER BY deployment_block DESC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_market_by_tx_hash(tx_hash: str) -> Optional[Dict[str, Any]]:
    """Get a specific market by transaction hash."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM markets WHERE tx_hash = ?", (tx_hash,))
        row = cursor.fetchone()
        return dict(row) if row else None

def market_exists(tx_hash: str) -> bool:
    """Check if a market already exists in the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM markets WHERE tx_hash = ? LIMIT 1", (tx_hash,))
        return cursor.fetchone() is not None

def get_last_indexed_block() -> int:
    """Get the last block number that was indexed."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_block FROM indexer_state WHERE id = 1")
        row = cursor.fetchone()
        return row[0] if row else 0

def update_last_indexed_block(block_number: int):
    """Update the last indexed block number."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO indexer_state (id, last_block, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                last_block = excluded.last_block,
                updated_at = CURRENT_TIMESTAMP
        """, (block_number,))
        conn.commit()
