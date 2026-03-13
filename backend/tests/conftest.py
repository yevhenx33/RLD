
import pytest
from fastapi.testclient import TestClient
import sqlite3
import os
import sys
from unittest.mock import patch

# Add backend to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from api.main import app


@pytest.fixture(scope="function")
def mock_db():
    """Create in-memory DB with hourly_stats schema + seed data."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # hourly_stats table (matches clean_rates.db schema)
    cursor.execute("""
        CREATE TABLE hourly_stats (
            timestamp INTEGER PRIMARY KEY,
            eth_price REAL,
            usdc_rate REAL,
            dai_rate REAL,
            usdt_rate REAL,
            sofr_rate REAL,
            susde_yield REAL
        )
    """)

    # sync_state table
    cursor.execute("""
        CREATE TABLE sync_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("INSERT INTO sync_state VALUES ('last_block_number', '24633000')")

    # Seed 24h of data
    base_ts = 1704067200  # 2024-01-01 00:00:00
    for i in range(24):
        ts = base_ts + (i * 3600)
        cursor.execute(
            "INSERT INTO hourly_stats VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, 2000 + i * 10, 5.0 + i * 0.1, 4.5, 5.5, 5.3, 15.0 + i * 0.05)
        )

    # Legacy per-asset tables for RAW queries
    cursor.execute("CREATE TABLE rates (block_number INTEGER PRIMARY KEY, timestamp INTEGER, apy REAL)")
    cursor.execute("CREATE TABLE rates_dai (block_number INTEGER PRIMARY KEY, timestamp INTEGER, apy REAL)")
    cursor.execute("CREATE TABLE rates_usdt (block_number INTEGER PRIMARY KEY, timestamp INTEGER, apy REAL)")
    cursor.execute("CREATE TABLE eth_prices (timestamp INTEGER PRIMARY KEY, price REAL, block_number INTEGER)")

    for i in range(24):
        ts = base_ts + (i * 3600)
        cursor.execute("INSERT INTO rates VALUES (?, ?, ?)", (1000 + i, ts, 5.0 + i * 0.1))
        cursor.execute("INSERT INTO eth_prices VALUES (?, ?, ?)", (ts, 2000 + i * 10, 1000 + i))

    cursor.execute("INSERT INTO rates_dai VALUES (?, ?, ?)", (1000, base_ts, 4.5))
    cursor.execute("INSERT INTO rates_usdt VALUES (?, ?, ?)", (1000, base_ts, 5.5))

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(scope="function")
def client(mock_db):
    def get_mock_db():
        return mock_db

    with patch("api.deps.get_db_connection", side_effect=get_mock_db):
        with patch("api.routes.rates.get_db_connection", side_effect=get_mock_db):
            with TestClient(app, base_url="http://localhost") as c:
                yield c
