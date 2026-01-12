from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import sqlite3
import pandas as pd
from datetime import datetime
import time
import os

app = FastAPI()

# 1. Security & Compression
app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=["localhost", "127.0.0.1", "0.0.0.0", "testserver", "rate-dashboard.onrender.com"]
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://rate-dashboard.netlify.app",
        "*"
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Rate Dashboard API is running"}

# Switch to Clean DB
DB_NAME = "clean_rates.db"
DB_DIR = os.getenv("DB_DIR", os.path.dirname(__file__))
DB_PATH = os.path.join(DB_DIR, DB_NAME)

def get_db_connection():
    # Use URI for Read-Only mode validation
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn

# 3. Simple In-Memory Cache (TTL: 20s)
CACHE_STORE = {}
CACHE_TTL = 20 

def get_from_cache(key):
    if key in CACHE_STORE:
        val, timestamp = CACHE_STORE[key]
        if time.time() - timestamp < CACHE_TTL:
            return val
    return None

def set_cache(key, val):
    CACHE_STORE[key] = (val, time.time())

@app.get("/rates")
def get_rates(
    limit: int = 50000, 
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="1H, 4H, 1D, 1W"),
    symbol: str = Query("USDC", description="USDC, DAI, USDT, SOFR")
):
    try:
        # Cache Check
        cache_key = f"rates:{symbol}:{resolution}:{limit}:{start_date}:{end_date}"
        cached = get_from_cache(cache_key)
        if cached:
            return cached

        # Map Symbol to Column
        symbol_map = {
            "USDC": "usdc_rate",
            "DAI": "dai_rate",
            "USDT": "usdt_rate",
            "SOFR": "sofr_rate"
        }
        
        target_col = symbol_map.get(symbol.upper())
        if not target_col:
            raise HTTPException(status_code=400, detail="Invalid Symbol. Supported: USDC, DAI, USDT, SOFR")

        conn = get_db_connection()
        
        # Aggregation Logic
        # Database is already 1H resolution (hourly_stats)
        buckets = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        seconds = buckets.get(resolution, 3600)
        
        # Select Clause
        if resolution == "1H":
             select_clause = f"timestamp, {target_col} as apy, eth_price"
             group_clause = ""
        else:
             # Downsampling using AVG
             select_clause = f"""
                MAX(timestamp) as timestamp, 
                AVG({target_col}) as apy, 
                AVG(eth_price) as eth_price
             """
             group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"

        # Limit Safety
        effective_limit = min(limit, 100000)

        # Build Query
        # Enforce Genesis Date: March 3, 2023 (1677801600)
        query = f"SELECT {select_clause} FROM hourly_stats WHERE timestamp >= 1677801600"
        params = []

        if start_date:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(int(dt.timestamp()))
        
        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            query += " AND timestamp <= ?"
            params.append(int(dt.timestamp()) + 86399)

        query += f" {group_clause} ORDER BY timestamp DESC LIMIT {effective_limit}"
        
        # Execute
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return []

        df = df.sort_values("timestamp", ascending=True)
        
        # Fill Gaps (Forward Fill)
        df['apy'] = df['apy'].ffill()
        
        # Convert to Dictionary (JSON)
        data = df.to_dict(orient="records")
        
        # Clean NaNs for JSON
        for row in data:
            for k, v in row.items():
                if isinstance(v, float) and v != v: # check for NaN
                    row[k] = None
        
        set_cache(cache_key, data)
        return data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/eth-prices")
def get_eth_prices(
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="1H, 4H, 1D")
):
    try:
        # Cache Check
        cache_key = f"eth_prices:{resolution}:{start_date}:{end_date}"
        cached = get_from_cache(cache_key)
        if cached:
            return cached

        conn = get_db_connection()
        
        buckets = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        seconds = buckets.get(resolution, 3600)
        
        if resolution == "1H":
            select_clause = "timestamp, eth_price as price"
            group_clause = ""
        else:
            select_clause = f"""
                MAX(timestamp) as timestamp, 
                AVG(eth_price) as price
            """
            group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"
            
        params = []
        # Enforce Genesis Date: March 3, 2023 (1677801600)
        query = f"SELECT {select_clause} FROM hourly_stats WHERE timestamp >= 1677801600"

        if start_date:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(int(dt.timestamp()))
        
        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            query += " AND timestamp <= ?"
            params.append(int(dt.timestamp()) + 86399)

        query += f" {group_clause} ORDER BY timestamp ASC"
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return []

        data = df.to_dict(orient="records")
        for row in data:
            for k, v in row.items():
                if isinstance(v, float) and v != v:
                    row[k] = None
        
        set_cache(cache_key, data)
        return data
        
    except Exception as e:
        if "no such table" in str(e):
            return []
        raise HTTPException(status_code=500, detail=str(e))