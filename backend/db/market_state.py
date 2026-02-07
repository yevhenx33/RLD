"""
Market State Database Module.
Separate database for storing live market state data from RLDCore.
Does NOT share with simulations.db or clean_rates.db.
"""
import sqlite3
import logging
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import os

# Separate database for market state - completely independent
MARKET_STATE_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "market_state.db")

def init_market_state_db():
    """Initialize market state database with required tables."""
    conn = sqlite3.connect(MARKET_STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Markets table - stores deployed market metadata
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT UNIQUE NOT NULL,
            tx_hash TEXT,
            broker_factory TEXT,
            position_token TEXT,
            position_token_symbol TEXT,
            collateral_token TEXT,
            underlying_token TEXT,
            underlying_pool TEXT,
            curator TEXT,
            spot_oracle TEXT,
            rate_oracle TEXT,
            liquidation_module TEXT,
            deployment_block INTEGER,
            deployment_timestamp INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Risk parameters table - separate for clarity
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_risk_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT UNIQUE NOT NULL,
            min_col_ratio TEXT,
            maintenance_margin TEXT,
            liquidation_close_factor TEXT,
            funding_period INTEGER,
            debt_cap TEXT,
            broker_verifier TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (market_id) REFERENCES markets(market_id)
        )
    """)
    
    # Market state snapshots - updated periodically
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_state_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            normalization_factor TEXT,
            total_debt TEXT,
            last_update_timestamp INTEGER,
            block_number INTEGER,
            indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (market_id) REFERENCES markets(market_id)
        )
    """)
    
    # Indexer state for this specific indexer
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS state_indexer_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_block INTEGER NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_id ON markets(market_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_market ON market_state_snapshots(market_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_time ON market_state_snapshots(indexed_at DESC)")
    
    conn.commit()
    conn.close()
    logging.info(f"✅ Market State DB initialized at {MARKET_STATE_DB_PATH}")

@contextmanager
def get_market_state_db():
    """Context manager for market state database connections."""
    conn = sqlite3.connect(MARKET_STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# ============================================
# Market CRUD Operations
# ============================================

def upsert_market(market_data: Dict[str, Any]) -> int:
    """Insert or update a market entry."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO markets (
                market_id, tx_hash, broker_factory, position_token,
                position_token_symbol, collateral_token, underlying_token,
                underlying_pool, curator, spot_oracle, rate_oracle,
                liquidation_module, deployment_block, deployment_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_id) DO UPDATE SET
                tx_hash = COALESCE(excluded.tx_hash, markets.tx_hash),
                broker_factory = COALESCE(excluded.broker_factory, markets.broker_factory),
                position_token = COALESCE(excluded.position_token, markets.position_token),
                position_token_symbol = COALESCE(excluded.position_token_symbol, markets.position_token_symbol),
                collateral_token = COALESCE(excluded.collateral_token, markets.collateral_token),
                underlying_token = COALESCE(excluded.underlying_token, markets.underlying_token),
                underlying_pool = COALESCE(excluded.underlying_pool, markets.underlying_pool),
                curator = COALESCE(excluded.curator, markets.curator),
                spot_oracle = COALESCE(excluded.spot_oracle, markets.spot_oracle),
                rate_oracle = COALESCE(excluded.rate_oracle, markets.rate_oracle),
                liquidation_module = COALESCE(excluded.liquidation_module, markets.liquidation_module),
                deployment_block = COALESCE(excluded.deployment_block, markets.deployment_block),
                deployment_timestamp = COALESCE(excluded.deployment_timestamp, markets.deployment_timestamp)
        """, (
            market_data['market_id'],
            market_data.get('tx_hash'),
            market_data.get('broker_factory'),
            market_data.get('position_token'),
            market_data.get('position_token_symbol'),
            market_data.get('collateral_token'),
            market_data.get('underlying_token'),
            market_data.get('underlying_pool'),
            market_data.get('curator'),
            market_data.get('spot_oracle'),
            market_data.get('rate_oracle'),
            market_data.get('liquidation_module'),
            market_data.get('deployment_block'),
            market_data.get('deployment_timestamp')
        ))
        conn.commit()
        return cursor.lastrowid

def upsert_risk_params(market_id: str, params: Dict[str, Any]):
    """Insert or update risk parameters for a market."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO market_risk_params (
                market_id, min_col_ratio, maintenance_margin,
                liquidation_close_factor, funding_period, debt_cap, broker_verifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_id) DO UPDATE SET
                min_col_ratio = excluded.min_col_ratio,
                maintenance_margin = excluded.maintenance_margin,
                liquidation_close_factor = excluded.liquidation_close_factor,
                funding_period = excluded.funding_period,
                debt_cap = excluded.debt_cap,
                broker_verifier = excluded.broker_verifier,
                updated_at = CURRENT_TIMESTAMP
        """, (
            market_id,
            str(params.get('min_col_ratio', 0)),
            str(params.get('maintenance_margin', 0)),
            str(params.get('liquidation_close_factor', 0)),
            params.get('funding_period', 0),
            str(params.get('debt_cap', 0)),
            params.get('broker_verifier')
        ))
        conn.commit()

def insert_state_snapshot(market_id: str, state: Dict[str, Any], block_number: int):
    """Insert a new market state snapshot."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO market_state_snapshots (
                market_id, normalization_factor, total_debt,
                last_update_timestamp, block_number
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            market_id,
            str(state.get('normalization_factor', 0)),
            str(state.get('total_debt', 0)),
            state.get('last_update_timestamp', 0),
            block_number
        ))
        conn.commit()
        return cursor.lastrowid

# ============================================
# Query Operations
# ============================================

def get_all_markets_with_state() -> List[Dict[str, Any]]:
    """Get all markets with their latest state and risk params."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.*,
                rp.min_col_ratio,
                rp.maintenance_margin,
                rp.liquidation_close_factor,
                rp.funding_period,
                rp.debt_cap,
                rp.broker_verifier,
                s.normalization_factor,
                s.total_debt,
                s.last_update_timestamp as state_last_update,
                s.block_number as state_block,
                s.indexed_at as state_indexed_at
            FROM markets m
            LEFT JOIN market_risk_params rp ON m.market_id = rp.market_id
            LEFT JOIN (
                SELECT market_id, normalization_factor, total_debt, 
                       last_update_timestamp, block_number, indexed_at,
                       ROW_NUMBER() OVER (PARTITION BY market_id ORDER BY indexed_at DESC) as rn
                FROM market_state_snapshots
            ) s ON m.market_id = s.market_id AND s.rn = 1
            ORDER BY m.deployment_timestamp DESC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_market_by_id(market_id: str) -> Optional[Dict[str, Any]]:
    """Get a specific market by market_id."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.*,
                rp.min_col_ratio,
                rp.maintenance_margin,
                rp.liquidation_close_factor,
                rp.funding_period,
                rp.debt_cap,
                rp.broker_verifier,
                s.normalization_factor,
                s.total_debt,
                s.last_update_timestamp as state_last_update,
                s.block_number as state_block
            FROM markets m
            LEFT JOIN market_risk_params rp ON m.market_id = rp.market_id
            LEFT JOIN (
                SELECT market_id, normalization_factor, total_debt, 
                       last_update_timestamp, block_number,
                       ROW_NUMBER() OVER (PARTITION BY market_id ORDER BY indexed_at DESC) as rn
                FROM market_state_snapshots
            ) s ON m.market_id = s.market_id AND s.rn = 1
            WHERE m.market_id = ?
        """, (market_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def market_exists_by_id(market_id: str) -> bool:
    """Check if a market exists by market_id."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM markets WHERE market_id = ? LIMIT 1", (market_id,))
        return cursor.fetchone() is not None

def get_state_indexer_last_block() -> int:
    """Get the last block indexed by the state indexer."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_block FROM state_indexer_state WHERE id = 1")
        row = cursor.fetchone()
        return row[0] if row else 0

def update_state_indexer_block(block_number: int):
    """Update the state indexer's last processed block."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO state_indexer_state (id, last_block, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                last_block = excluded.last_block,
                updated_at = CURRENT_TIMESTAMP
        """, (block_number,))
        conn.commit()

def get_all_market_ids() -> List[str]:
    """Get all market IDs for state polling."""
    with get_market_state_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT market_id FROM markets")
        rows = cursor.fetchall()
        return [row['market_id'] for row in rows]
