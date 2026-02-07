
import pytest
from fastapi.testclient import TestClient
import sqlite3
import os
import sys
from unittest.mock import MagicMock, patch

# Add backend to sys.path to allow imports
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from api.main import app

@pytest.fixture(scope="function")
def mock_db():
    # Create in-memory DB
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # --- Schema Setup ---
    # 1. Base Tables
    tables = ["rates", "rates_dai", "rates_usdt", "sofr_rates"]
    for t in tables:
        cursor.execute(f"CREATE TABLE {t} (block_number INTEGER, timestamp INTEGER, apy REAL)")
    
    cursor.execute("CREATE TABLE eth_prices (timestamp INTEGER PRIMARY KEY, price REAL, block_number INTEGER)")

    # 2. Views (Simplified for Test)
    # View for USDC 1H
    cursor.execute("""
        CREATE VIEW rates_1h AS 
        SELECT 
            MAX(timestamp) as timestamp, 
            AVG(apy) as apy, 
            MAX(block_number) as block_number 
        FROM rates 
        GROUP BY CAST(timestamp / 3600 AS INTEGER)
    """)
    # View for USDC 4H
    cursor.execute("""
        CREATE VIEW rates_4h AS 
        SELECT 
            MAX(timestamp) as timestamp, 
            AVG(apy) as apy, 
            MAX(block_number) as block_number 
        FROM rates 
        GROUP BY CAST(timestamp / 14400 AS INTEGER)
    """)
    # View for USDC 1D
    cursor.execute("""
        CREATE VIEW rates_1d AS 
        SELECT 
            MAX(timestamp) as timestamp, 
            AVG(apy) as apy, 
            MAX(block_number) as block_number,
            0 as eth_price -- Mock eth_price for view schema
        FROM rates 
        GROUP BY CAST(timestamp / 86400 AS INTEGER)
    """)

    # --- Seed Data ---
    # Base TS: 2024-01-01 00:00:00 = 1704067200
    base_ts = 1704067200
    
    # Insert 24 hours of data for USDC
    for i in range(24):
        ts = base_ts + (i * 3600)
        apy = 5.0 + (i * 0.1) # 5.0% -> 7.3%
        cursor.execute("INSERT INTO rates VALUES (?, ?, ?)", (1000+i, ts, apy))
        cursor.execute("INSERT INTO eth_prices VALUES (?, ?, ?)", (ts, 2000 + i*10, 1000+i))

    # Insert Data for other assets
    cursor.execute("INSERT INTO rates_dai VALUES (?, ?, ?)", (1000, base_ts, 4.5))
    cursor.execute("INSERT INTO rates_usdt VALUES (?, ?, ?)", (1000, base_ts, 5.5))
    cursor.execute("INSERT INTO sofr_rates VALUES (?, ?, ?)", (1000, base_ts, 5.3))

    conn.commit()
    
    yield conn
    conn.close()

@pytest.fixture(scope="function")
def client(mock_db):
    # Patch the get_db_connection function in api.py to return our mock_db
    def get_mock_db_connection():
        return mock_db

    with patch("api.get_db_connection", side_effect=get_mock_db_connection):
        with TestClient(app, base_url="http://localhost") as c:
            yield c
