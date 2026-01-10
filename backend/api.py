from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import sqlite3
import pandas as pd
from datetime import datetime
import time
from functools import wraps

app = FastAPI()

# 1. Security & Compression
app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=["localhost", "127.0.0.1", "0.0.0.0"]
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "aave_rates.db"

def get_db_connection():
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn

# 3. Simple In-Memory Cache (TTL: 20s)
# Reduces DB load for identical frequent requests
CACHE_STORE = {}
CACHE_TTL = 20 

def ttl_cache(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Create unique key from kwargs (FastAPI passes params as kwargs)
        # We sort keys to ensure consistency
        key = f"{func.__name__}:{sorted(kwargs.items())}"
        now = time.time()
        
        # Cleanup old cache (naive) - every 100 requests? 
        # For simplicity, we just check access.
        
        if key in CACHE_STORE:
            val, timestamp = CACHE_STORE[key]
            if now - timestamp < CACHE_TTL:
                return val
            
        result = func(*args, **kwargs)
        CACHE_STORE[key] = (result, now)
        return result
    return wrapper

@app.get("/rates")
@ttl_cache
def get_rates(
    limit: int = 50000, 
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="RAW, 1H, 4H, 1D, 1W"),
    symbol: str = Query("USDC", description="USDC, DAI, USDT")
):
    try:
        conn = get_db_connection()
        
        # Determine Base Table based on Symbol
        symbol = symbol.upper()
        if symbol == "USDC":
            base_table = "rates"
            # Support Views for USDC
            use_views = True
        elif symbol == "DAI":
            base_table = "rates_dai"
            use_views = False # No views yet
        elif symbol == "USDT":
            base_table = "rates_usdt"
            use_views = False # No views yet
        elif symbol == "SOFR":
            base_table = "sofr_rates"
            use_views = False
        else:
            raise HTTPException(status_code=400, detail="Invalid Symbol. Supported: USDC, DAI, USDT, SOFR")

        # --- 1. SMART LIMITS & VIEW ROUTING ---
        # Browser Safety: 
        # RAW data is heavy (1 row = 1 block). Limit to 30k (approx 4-5 days).
        # Aggregated data is light (1 row = 1 hour). We can allow 100k rows.
        
        # Valid Resolutions for Views: 1H, 4H, 1D, 1W, ALL
        if resolution == "RAW" or not use_views:
            effective_limit = min(limit, 30000)
            select_clause = "*"
            table_name = base_table
            group_clause = ""
            order_clause = "timestamp DESC"
        elif resolution == "4H" and use_views:
            effective_limit = 100000
            select_clause = "*"
            table_name = "rates_4h" 
            group_clause = ""
            order_clause = "timestamp DESC"
        elif (resolution == "1D" or resolution == "1W" or resolution == "ALL") and use_views:
            effective_limit = 100000
            table_name = "rates_1d"
            
            if resolution == "1W":
                seconds = 604800
                select_clause = f"""
                    MAX(timestamp) as timestamp, 
                    AVG(apy) as apy, 
                    AVG(eth_price) as eth_price,
                    MAX(block_number) as block_number
                """
                group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"
            else:
                select_clause = "*"
                group_clause = ""
            
            order_clause = "timestamp DESC"
        else:
            # Fallback for View-supported assets OR non-view assets requesting High Res
            if use_views:
                effective_limit = 100000
                select_clause = "*"
                table_name = "rates_1h"
            else:
                # Non-view asset (DAI/USDT) requesting Aggregated resolution
                # For now, return Raw data but mapped to base table
                effective_limit = 30000
                select_clause = "*"
                table_name = base_table

            group_clause = ""
            order_clause = "timestamp DESC"

        # --- 3. BUILD QUERY ---
        query = f"SELECT {select_clause} FROM {table_name} WHERE 1=1"
        params = []

        if start_date:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(int(dt.timestamp()))
        
        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            query += " AND timestamp <= ?"
            params.append(int(dt.timestamp()) + 86399)

        query += f" {group_clause} ORDER BY {order_clause} LIMIT {effective_limit}"
        
        # --- 4. EXECUTE ---
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return []

        df = df.sort_values("timestamp", ascending=True)
        
        # --- 5. FILL GAPS (Forward Fill) ---
        # Interest rates are stateful. If no event happened in an hour, the rate is same as previous.
        # This fixes "gaps" where we have Price data but no Rate data.
        df['apy'] = df['apy'].ffill()
        df['apy'] = df['apy'].ffill()
        if 'block_number' in df.columns:
            df['block_number'] = df['block_number'].ffill()

        # Fix for JSON serialization of NaN (caused by missing eth_price or start of gap)
        data = df.to_dict(orient="records")
        for row in data:
            for k, v in row.items():
                if isinstance(v, float) and v != v: # check for NaN
                    row[k] = None
        return data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/eth-prices")
@ttl_cache
def get_eth_prices(
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="1H, 4H, 1D")
):
    try:
        conn = get_db_connection()
        
        # Aggregation Logic
        # Source data is likely 1H already, but we support downsampling
        buckets = {"1H": 3600, "4H": 14400, "1D": 86400}
        seconds = buckets.get(resolution, 3600)
        
        select_clause = f"""
            MAX(timestamp) as timestamp, 
            AVG(price) as price
        """
        group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"
        order_clause = "timestamp ASC" 

        query = f"SELECT {select_clause} FROM eth_prices WHERE 1=1"
        params = []

        if start_date:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(int(dt.timestamp()))
        
        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            query += " AND timestamp <= ?"
            params.append(int(dt.timestamp()) + 86399)

        query += f" {group_clause} ORDER BY {order_clause}"
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return []

        # Fix for JSON serialization of NaN
        data = df.to_dict(orient="records")
        for row in data:
            for k, v in row.items():
                if isinstance(v, float) and v != v: # check for NaN
                    row[k] = None
        return data
        
    except Exception as e:
        # If table doesn't exist yet, return empty list instead of 500
        if "no such table" in str(e):
            return []
        raise HTTPException(status_code=500, detail=str(e))