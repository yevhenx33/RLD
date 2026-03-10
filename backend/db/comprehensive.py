"""
Comprehensive Indexer Database Schema.
Per-block tracking of market state, pool state, events, and broker positions.
"""
import sqlite3
import logging
import json
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import os

# Database path — configurable via env var for Docker, defaults to local dev path
COMPREHENSIVE_DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "data", "comprehensive_state.db")
)
DB_PATH = COMPREHENSIVE_DB_PATH  # Alias for API

logger = logging.getLogger(__name__)


def init_comprehensive_db():
    """Initialize comprehensive indexer database with required tables."""
    conn = sqlite3.connect(COMPREHENSIVE_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Block-level market state snapshots
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS block_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_number INTEGER NOT NULL,
            block_timestamp INTEGER,
            market_id TEXT NOT NULL,
            normalization_factor TEXT,
            total_debt TEXT,
            last_update_timestamp INTEGER,
            index_price TEXT,
            UNIQUE(block_number, market_id)
        )
    """)
    
    # V4 pool state per block
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pool_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_number INTEGER NOT NULL,
            pool_id TEXT NOT NULL,
            token0 TEXT,
            token1 TEXT,
            sqrt_price_x96 TEXT,
            tick INTEGER,
            liquidity TEXT,
            mark_price REAL,
            fee_growth_global0 TEXT,
            fee_growth_global1 TEXT,
            token0_balance TEXT,
            token1_balance TEXT,
            UNIQUE(block_number, pool_id)
        )
    """)
    
    # Events log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_number INTEGER NOT NULL,
            tx_hash TEXT NOT NULL,
            log_index INTEGER,
            event_name TEXT NOT NULL,
            contract_address TEXT,
            market_id TEXT,
            data TEXT,
            timestamp INTEGER
        )
    """)
    
    # Broker positions per block
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS broker_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_number INTEGER NOT NULL,
            broker_address TEXT NOT NULL,
            market_id TEXT NOT NULL,
            collateral TEXT,
            debt TEXT,
            collateral_value TEXT,
            debt_value TEXT,
            health_factor REAL,
            UNIQUE(block_number, broker_address, market_id)
        )
    """)

    # V4 LP positions per block
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lp_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_number INTEGER NOT NULL,
            broker_address TEXT NOT NULL,
            token_id INTEGER NOT NULL,
            liquidity TEXT,
            tick_lower INTEGER,
            tick_upper INTEGER,
            entry_tick INTEGER,
            entry_price REAL,
            mint_block INTEGER,
            is_active BOOLEAN DEFAULT 0,
            UNIQUE(block_number, broker_address, token_id)
        )
    """)
    
    # Transactions table - all contract interactions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_number INTEGER NOT NULL,
            tx_hash TEXT NOT NULL UNIQUE,
            tx_index INTEGER,
            from_address TEXT NOT NULL,
            to_address TEXT,
            value TEXT,
            gas_used INTEGER,
            gas_price TEXT,
            input_data TEXT,
            method_id TEXT,
            method_name TEXT,
            decoded_args TEXT,
            timestamp INTEGER,
            status INTEGER DEFAULT 1
        )
    """)
    
    # Indexer state
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS indexer_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_indexed_block INTEGER NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes for efficient queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_block_state_block ON block_state(block_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_block_state_market ON block_state(market_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pool_state_block ON pool_state(block_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_block ON events(block_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_name ON events(event_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_broker_pos_block ON broker_positions(block_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_broker_pos_addr ON broker_positions(broker_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lp_pos_block ON lp_positions(block_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lp_pos_broker ON lp_positions(broker_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lp_pos_token ON lp_positions(token_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_block ON transactions(block_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_from ON transactions(from_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_to ON transactions(to_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_method ON transactions(method_id)")

    # Bond positions (BondMinted / BondClosed lifecycle)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bonds (
            broker_address TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            bond_factory TEXT,
            notional TEXT,
            hedge TEXT,
            duration INTEGER,
            created_block INTEGER,
            created_timestamp INTEGER,
            created_tx TEXT,
            closed_block INTEGER,
            closed_timestamp INTEGER,
            closed_tx TEXT,
            collateral_returned TEXT,
            position_returned TEXT,
            status TEXT DEFAULT 'active'
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bonds_owner ON bonds(owner)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bonds_status ON bonds(status)")
    
    # 5-Minute OHLC candles (pre-aggregated from block_state + pool_state)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_candles_5m (
            ts INTEGER PRIMARY KEY,      -- 5-minute bucket start (Unix)
            index_open  REAL,
            index_high  REAL,
            index_low   REAL,
            index_close REAL,
            mark_open   REAL,
            mark_high   REAL,
            mark_low    REAL,
            mark_close  REAL,
            nf_close    REAL,
            debt_close  REAL,
            sample_count INTEGER DEFAULT 0
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_candles_5m_ts ON price_candles_5m(ts)")

    # Migrations: add columns to existing tables (safe to re-run)
    for col in ["token0_balance TEXT", "token1_balance TEXT"]:
        try:
            cursor.execute(f"ALTER TABLE pool_state ADD COLUMN {col}")
        except Exception:
            pass  # column already exists

    conn.commit()
    conn.close()
    logger.info(f"✅ Comprehensive DB initialized at {COMPREHENSIVE_DB_PATH}")


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(COMPREHENSIVE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ============================================
# Block State Operations
# ============================================

def insert_block_state(block_number: int, block_timestamp: int, market_id: str, 
                       state: Dict[str, Any]):
    """Insert a block-level market state snapshot."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO block_state (
                block_number, block_timestamp, market_id,
                normalization_factor, total_debt, last_update_timestamp, index_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            block_number,
            block_timestamp,
            market_id,
            str(state.get('normalization_factor', 0)),
            str(state.get('total_debt', 0)),
            state.get('last_update_timestamp', 0),
            str(state.get('index_price', 0))
        ))
        conn.commit()
        return cursor.lastrowid


def get_block_state(block_number: int, market_id: str) -> Optional[Dict]:
    """Get market state at a specific block."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM block_state 
            WHERE block_number = ? AND market_id = ?
        """, (block_number, market_id))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_latest_block_state(market_id: str) -> Optional[Dict]:
    """Get the latest state snapshot for a market."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM block_state 
            WHERE market_id = ?
            ORDER BY block_number DESC LIMIT 1
        """, (market_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


# ============================================
# Pool State Operations
# ============================================

def insert_pool_state(block_number: int, pool_id: str, state: Dict[str, Any]):
    """Insert V4 pool state snapshot."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO pool_state (
                block_number, pool_id, token0, token1,
                sqrt_price_x96, tick, liquidity, mark_price,
                fee_growth_global0, fee_growth_global1,
                token0_balance, token1_balance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            block_number,
            pool_id,
            state.get('token0'),
            state.get('token1'),
            str(state.get('sqrt_price_x96', 0)),
            state.get('tick', 0),
            str(state.get('liquidity', 0)),
            state.get('mark_price', 0.0),
            str(state.get('fee_growth_global0', 0)),
            str(state.get('fee_growth_global1', 0)),
            str(state.get('token0_balance', 0)),
            str(state.get('token1_balance', 0))
        ))
        conn.commit()
        return cursor.lastrowid


def get_pool_state(block_number: int, pool_id: str) -> Optional[Dict]:
    """Get pool state at a specific block."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM pool_state 
            WHERE block_number = ? AND pool_id = ?
        """, (block_number, pool_id))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_latest_pool_state(pool_id: str) -> Optional[Dict]:
    """Get the latest pool state."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM pool_state 
            WHERE pool_id = ?
            ORDER BY block_number DESC LIMIT 1
        """, (pool_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


# ============================================
# Events Operations
# ============================================

def insert_event(block_number: int, tx_hash: str, log_index: int,
                 event_name: str, contract_address: str, market_id: str,
                 data: Dict, timestamp: int):
    """Insert an event into the log."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO events (
                block_number, tx_hash, log_index, event_name,
                contract_address, market_id, data, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            block_number,
            tx_hash,
            log_index,
            event_name,
            contract_address,
            market_id,
            json.dumps(data),
            timestamp
        ))
        conn.commit()
        return cursor.lastrowid


def get_events(from_block: int = None, to_block: int = None, 
               event_name: str = None, market_id: str = None, 
               limit: int = 100) -> List[Dict]:
    """Query events with filters."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM events WHERE 1=1"
        params = []
        
        if from_block:
            query += " AND block_number >= ?"
            params.append(from_block)
        if to_block:
            query += " AND block_number <= ?"
            params.append(to_block)
        if event_name:
            query += " AND event_name = ?"
            params.append(event_name)
        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)
        
        query += " ORDER BY block_number DESC, log_index ASC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if d.get('data'):
                d['data'] = json.loads(d['data'])
            results.append(d)
        return results


# ============================================
# Transaction Operations
# ============================================

def insert_transaction(block_number: int, tx_hash: str, tx_index: int,
                       from_address: str, to_address: str, value: str,
                       gas_used: int, gas_price: str, input_data: str,
                       method_id: str, method_name: str, decoded_args: Dict,
                       timestamp: int, status: int = 1):
    """Insert a transaction record."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO transactions (
                block_number, tx_hash, tx_index, from_address, to_address,
                value, gas_used, gas_price, input_data, method_id, method_name,
                decoded_args, timestamp, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            block_number, tx_hash, tx_index, from_address, to_address,
            value, gas_used, gas_price, input_data, method_id, method_name,
            json.dumps(decoded_args) if decoded_args else None,
            timestamp, status
        ))
        conn.commit()
        return cursor.lastrowid


def get_transactions(from_block: int = None, to_block: int = None,
                     from_address: str = None, to_address: str = None,
                     method_id: str = None, limit: int = 100) -> List[Dict]:
    """Query transactions with filters."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM transactions WHERE 1=1"
        params = []
        
        if from_block:
            query += " AND block_number >= ?"
            params.append(from_block)
        if to_block:
            query += " AND block_number <= ?"
            params.append(to_block)
        if from_address:
            query += " AND from_address = ?"
            params.append(from_address.lower())
        if to_address:
            query += " AND to_address = ?"
            params.append(to_address.lower())
        if method_id:
            query += " AND method_id = ?"
            params.append(method_id)
        
        query += " ORDER BY block_number DESC, tx_index ASC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if d.get('decoded_args'):
                d['decoded_args'] = json.loads(d['decoded_args'])
            results.append(d)
        return results


# ============================================
# Broker Position Operations  
# ============================================

def insert_broker_position(block_number: int, broker_address: str, 
                          market_id: str, position: Dict[str, Any]):
    """Insert broker position snapshot."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO broker_positions (
                block_number, broker_address, market_id,
                collateral, debt, collateral_value, debt_value, health_factor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            block_number,
            broker_address,
            market_id,
            str(position.get('collateral', 0)),
            str(position.get('debt', 0)),
            str(position.get('collateral_value', 0)),
            str(position.get('debt_value', 0)),
            position.get('health_factor', 0.0)
        ))
        conn.commit()
        return cursor.lastrowid


def get_broker_position(block_number: int, broker_address: str, 
                        market_id: str) -> Optional[Dict]:
    """Get broker position at a specific block."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM broker_positions 
            WHERE block_number = ? AND broker_address = ? AND market_id = ?
        """, (block_number, broker_address, market_id))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_broker_history(broker_address: str, market_id: str = None, 
                       limit: int = 100) -> List[Dict]:
    """Get position history for a broker."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM broker_positions WHERE broker_address = ?"
        params = [broker_address]
        
        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)
        
        query += " ORDER BY block_number DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


# ============================================
# LP Position Operations
# ============================================

def insert_lp_position(block_number: int, broker_address: str, position: Dict[str, Any]):
    """Insert an LP position snapshot."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO lp_positions (
                block_number, broker_address, token_id,
                liquidity, tick_lower, tick_upper,
                entry_tick, entry_price, mint_block, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            block_number,
            broker_address,
            position.get('token_id', 0),
            str(position.get('liquidity', 0)),
            position.get('tick_lower', 0),
            position.get('tick_upper', 0),
            position.get('entry_tick'),
            position.get('entry_price'),
            position.get('mint_block'),
            1 if position.get('is_active') else 0,
        ))
        conn.commit()
        return cursor.lastrowid


def get_lp_positions(broker_address: str, block_number: int = None) -> List[Dict]:
    """Get LP positions for a broker. If block_number is None, returns latest."""
    with get_db() as conn:
        cursor = conn.cursor()
        if block_number:
            cursor.execute("""
                SELECT * FROM lp_positions
                WHERE broker_address = ? AND block_number = ?
                ORDER BY token_id ASC
            """, (broker_address, block_number))
        else:
            # Get the latest block for this broker
            cursor.execute("""
                SELECT * FROM lp_positions
                WHERE broker_address = ? AND block_number = (
                    SELECT MAX(block_number) FROM lp_positions WHERE broker_address = ?
                )
                ORDER BY is_active DESC, token_id ASC
            """, (broker_address, broker_address))
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            d['liquidity'] = int(d.get('liquidity') or 0)
            results.append(d)
        return results


def get_all_latest_lp_positions() -> List[Dict]:
    """Get latest LP positions across all brokers."""
    with get_db() as conn:
        cursor = conn.cursor()
        # Get max block per broker, then join
        cursor.execute("""
            SELECT lp.* FROM lp_positions lp
            INNER JOIN (
                SELECT broker_address, MAX(block_number) as max_block
                FROM lp_positions GROUP BY broker_address
            ) latest ON lp.broker_address = latest.broker_address
                    AND lp.block_number = latest.max_block
            WHERE lp.liquidity != '0'
            ORDER BY lp.is_active DESC, lp.token_id ASC
        """)
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            d['liquidity'] = int(d.get('liquidity') or 0)
            results.append(d)
        return results


# ============================================
# Bond Position Operations
# ============================================

def insert_bond(broker_address: str, owner: str, bond_factory: str,
                notional: str, hedge: str, duration: int,
                created_block: int, created_timestamp: int, created_tx: str):
    """Insert a new bond from a BondMinted event."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO bonds (
                broker_address, owner, bond_factory, notional, hedge, duration,
                created_block, created_timestamp, created_tx, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """, (
            broker_address.lower(), owner.lower(), bond_factory.lower(),
            str(notional), str(hedge), duration,
            created_block, created_timestamp, created_tx
        ))
        conn.commit()
        return cursor.lastrowid


def update_bond_closed(broker_address: str, closed_block: int,
                       closed_timestamp: int, closed_tx: str,
                       collateral_returned: str = '0',
                       position_returned: str = '0'):
    """Mark a bond as closed from a BondClosed event."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bonds SET
                status = 'closed',
                closed_block = ?,
                closed_timestamp = ?,
                closed_tx = ?,
                collateral_returned = ?,
                position_returned = ?
            WHERE broker_address = ?
        """, (
            closed_block, closed_timestamp, closed_tx,
            str(collateral_returned), str(position_returned),
            broker_address.lower()
        ))
        conn.commit()


def get_bonds_by_owner(owner: str, status: str = None) -> List[Dict]:
    """Get all bonds for an owner, optionally filtered by status."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM bonds WHERE owner = ?"
        params = [owner.lower()]
        if status and status != 'all':
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_block DESC"
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def get_bond(broker_address: str) -> Optional[Dict]:
    """Get a single bond by its broker address."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bonds WHERE broker_address = ?",
                       (broker_address.lower(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_bonds(status: str = None, limit: int = 100) -> List[Dict]:
    """Get all bonds, optionally filtered by status."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM bonds WHERE 1=1"
        params = []
        if status and status != 'all':
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_block DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


# ============================================
# Indexer State Operations
# ============================================

def get_last_indexed_block() -> int:
    """Get the last indexed block number."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_indexed_block FROM indexer_state WHERE id = 1")
        row = cursor.fetchone()
        return row[0] if row else 0


def update_last_indexed_block(block_number: int):
    """Update the last indexed block."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO indexer_state (id, last_indexed_block, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                last_indexed_block = excluded.last_indexed_block,
                updated_at = CURRENT_TIMESTAMP
        """, (block_number,))
        conn.commit()


# ============================================
# Utility Functions
# ============================================

def get_block_summary(block_number: int) -> Dict:
    """Get a complete summary of state at a specific block."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get market states
        cursor.execute("SELECT * FROM block_state WHERE block_number = ?", (block_number,))
        market_states = [dict(row) for row in cursor.fetchall()]
        
        # Get pool states
        cursor.execute("SELECT * FROM pool_state WHERE block_number = ?", (block_number,))
        pool_states = [dict(row) for row in cursor.fetchall()]
        
        # Get events
        cursor.execute("SELECT * FROM events WHERE block_number = ?", (block_number,))
        events = []
        for row in cursor.fetchall():
            d = dict(row)
            if d.get('data'):
                d['data'] = json.loads(d['data'])
            events.append(d)
        
        # Get broker positions
        cursor.execute("SELECT * FROM broker_positions WHERE block_number = ?", (block_number,))
        broker_positions = [dict(row) for row in cursor.fetchall()]
        
        return {
            'block_number': block_number,
            'market_states': market_states,
            'pool_states': pool_states,
            'events': events,
            'broker_positions': broker_positions
        }


def get_latest_summary() -> Dict:
    """Get summary of the latest indexed block."""
    last_block = get_last_indexed_block()
    if last_block == 0:
        return {'error': 'No blocks indexed yet'}
    return get_block_summary(last_block)


# ============================================
# Paginated Query Functions (for API)
# ============================================

def get_block_states(market_id: str = None, from_block: int = None, 
                     to_block: int = None, limit: int = 100) -> List[Dict]:
    """Get historical block states with optional filters."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM block_state WHERE 1=1"
        params = []
        
        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)
        if from_block:
            query += " AND block_number >= ?"
            params.append(from_block)
        if to_block:
            query += " AND block_number <= ?"
            params.append(to_block)
        
        query += " ORDER BY block_number DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            # Convert string fields to integers
            d['normalization_factor'] = int(d.get('normalization_factor') or 0)
            d['total_debt'] = int(d.get('total_debt') or 0)
            d['index_price'] = int(d.get('index_price') or 0)
            results.append(d)
        return results


def get_pool_states(pool_id: str = None, from_block: int = None,
                    to_block: int = None, limit: int = 100) -> List[Dict]:
    """Get historical pool states with optional filters."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM pool_state WHERE 1=1"
        params = []
        
        if pool_id:
            query += " AND pool_id = ?"
            params.append(pool_id)
        if from_block:
            query += " AND block_number >= ?"
            params.append(from_block)
        if to_block:
            query += " AND block_number <= ?"
            params.append(to_block)
        
        query += " ORDER BY block_number DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            # Convert string fields to integers
            d['sqrt_price_x96'] = int(d.get('sqrt_price_x96') or 0)
            d['liquidity'] = int(d.get('liquidity') or 0)
            d['fee_growth_global0'] = int(d.get('fee_growth_global0') or 0)
            d['fee_growth_global1'] = int(d.get('fee_growth_global1') or 0)
            results.append(d)
        return results


def get_broker_position_history(broker_address: str, market_id: str = None,
                                from_block: int = None, to_block: int = None,
                                limit: int = 100) -> List[Dict]:
    """Get historical broker positions."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM broker_positions WHERE broker_address = ?"
        params = [broker_address]
        
        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)
        if from_block:
            query += " AND block_number >= ?"
            params.append(from_block)
        if to_block:
            query += " AND block_number <= ?"
            params.append(to_block)
        
        query += " ORDER BY block_number DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            # Convert string fields to integers
            d['collateral'] = int(d.get('collateral') or 0)
            d['debt'] = int(d.get('debt') or 0)
            d['debt_principal'] = int(d.get('debt_principal') or 0) if d.get('debt_principal') else 0
            d['collateral_value'] = int(d.get('collateral_value') or 0)
            d['debt_value'] = int(d.get('debt_value') or 0)
            results.append(d)
        return results


# ============================================
# 5-Minute Candle Builder
# ============================================

FIVE_MIN = 300  # seconds per bucket


def build_5m_candles(since_ts: int = 0) -> int:
    """Aggregate raw block_state + pool_state into price_candles_5m using SQL.
    
    Performs grouping directly in SQLite to avoid loading the entire 
    history into Python memory.

    Returns the number of candles written.
    """
    with get_db() as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Enforce a small lookback so the most recent (possibly incomplete)
        # candle always gets refreshed with new blocks.
        lookback_ts = max(0, since_ts - FIVE_MIN * 2)

        # We construct the 5-minute buckets using SQL. SQLite doesn't have 
        # standard FIRST/LAST window functions in all versions, so we use
        # correlated subqueries for the FIRST and LAST values of the bucket.
        # This is significantly faster and uses less memory than Python grouping.

        c.execute("""
            WITH BucketedBlocks AS (
                SELECT
                    bs.block_number,
                    bs.block_timestamp,
                    (bs.block_timestamp / ?) * ? AS bucket_ts,
                    CAST(bs.index_price AS REAL) / 1e18 AS index_price,
                    CAST(bs.normalization_factor AS REAL) / 1e18 AS nf,
                    CAST(bs.total_debt AS REAL) / 1e6 AS debt,
                    ps.mark_price
                FROM block_state bs
                LEFT JOIN pool_state ps ON ps.block_number = bs.block_number
                WHERE bs.block_timestamp >= ?
            ),
            BucketBoundaries AS (
                SELECT 
                    bucket_ts,
                    MIN(block_timestamp) as first_ts,
                    MAX(block_timestamp) as last_ts,
                    COUNT(*) as sample_count,
                    MAX(index_price) as index_high,
                    MIN(index_price) as index_low,
                    MAX(mark_price) as mark_high,
                    MIN(mark_price) as mark_low
                FROM BucketedBlocks
                GROUP BY bucket_ts
            )
            SELECT 
                bb.bucket_ts AS ts,
                bb.index_high,
                bb.index_low,
                bb.mark_high,
                bb.mark_low,
                bb.sample_count,
                (SELECT index_price FROM BucketedBlocks b WHERE b.bucket_ts = bb.bucket_ts AND b.block_timestamp = bb.first_ts LIMIT 1) AS index_open,
                (SELECT index_price FROM BucketedBlocks b WHERE b.bucket_ts = bb.bucket_ts AND b.block_timestamp = bb.last_ts LIMIT 1) AS index_close,
                (SELECT mark_price FROM BucketedBlocks b WHERE b.bucket_ts = bb.bucket_ts AND b.block_timestamp = bb.first_ts LIMIT 1) AS mark_open,
                (SELECT mark_price FROM BucketedBlocks b WHERE b.bucket_ts = bb.bucket_ts AND b.block_timestamp = bb.last_ts LIMIT 1) AS mark_close,
                (SELECT nf FROM BucketedBlocks b WHERE b.bucket_ts = bb.bucket_ts AND b.block_timestamp = bb.last_ts LIMIT 1) AS nf_close,
                (SELECT debt FROM BucketedBlocks b WHERE b.bucket_ts = bb.bucket_ts AND b.block_timestamp = bb.last_ts LIMIT 1) AS debt_close
            FROM BucketBoundaries bb
        """, (FIVE_MIN, FIVE_MIN, lookback_ts))
        
        rows = c.fetchall()
        if not rows:
            return 0

        upserted = 0
        for r in rows:
            c.execute("""
                INSERT INTO price_candles_5m (
                    ts,
                    index_open, index_high, index_low, index_close,
                    mark_open,  mark_high,  mark_low,  mark_close,
                    nf_close, debt_close,
                    sample_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ts) DO UPDATE SET
                    index_open   = excluded.index_open,
                    index_high   = excluded.index_high,
                    index_low    = excluded.index_low,
                    index_close  = excluded.index_close,
                    mark_open    = excluded.mark_open,
                    mark_high    = excluded.mark_high,
                    mark_low     = excluded.mark_low,
                    mark_close   = excluded.mark_close,
                    nf_close     = excluded.nf_close,
                    debt_close   = excluded.debt_close,
                    sample_count = excluded.sample_count
            """, (
                r["ts"],
                r["index_open"], r["index_high"], r["index_low"], r["index_close"],
                r["mark_open"], r["mark_high"], r["mark_low"], r["mark_close"],
                r["nf_close"], r["debt_close"],
                r["sample_count"],
            ))
            upserted += 1

        conn.commit()
        return upserted
