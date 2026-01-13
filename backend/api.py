from fastapi import FastAPI, HTTPException, Query, Request, Security, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
import sqlite3
import pandas as pd
from datetime import datetime
import time
import os
from collections import defaultdict
import logging
import re
from cachetools import TTLCache

# --- Logging Config ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- Security: API Key ---
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key_header: str = Security(api_key_header)):
    expected_key = os.getenv("API_KEY")
    if expected_key:
        if api_key_header != expected_key:
             raise HTTPException(status_code=403, detail="Invalid or Missing API Key")
    return api_key_header

app = FastAPI(dependencies=[Depends(get_api_key)])

# --- Security: Rate Limiter ---
# Limit: 20 requests per 10 seconds per IP
RATE_LIMIT_WINDOW = 10 
RATE_LIMIT_MAX_REQUESTS = 20
request_history = defaultdict(list)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    now = time.time()
    
    # Filter out requests older than window
    request_history[client_ip] = [t for t in request_history[client_ip] if now - t < RATE_LIMIT_WINDOW]
    
    # Check limit
    if len(request_history[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return JSONResponse(status_code=429, content={"error": "Too Many Requests. Please slow down."})
    
    # Record request
    request_history[client_ip].append(now)
    
    # Prevent memory leak (simple cleanup)
    if len(request_history) > 5000:
        request_history.clear()

    response = await call_next(request)
    return response

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

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
        "https://rate-dashboard.onrender.com",
        "https://www.rate-dashboard.onrender.com",
        "https://rld.fi",
        "https://www.rld.fi"
    ],
    allow_methods=["*"], # Allow all methods (specifically OPTIONS for preflight)
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    last_block = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sync_state WHERE key='last_block_number'")
        row = cursor.fetchone()
        if row:
            last_block = int(row['value'])
        conn.close()
    except Exception as e:
        logging.error(f"Health check db error: {e}")
        
    return {"status": "ok", "message": "Rate Dashboard API is running", "last_indexed_block": last_block}

# Switch to Clean DB
DB_NAME = "clean_rates.db"
DB_DIR = os.getenv("DB_DIR", os.path.dirname(__file__))
DB_PATH = os.path.join(DB_DIR, DB_NAME)

def get_db_connection():
    # Use URI for Read-Only mode validation
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn

# 3. Secure In-Memory Cache (TTL: 20s, Max: 1000 items)
CACHE_STORE = TTLCache(maxsize=1000, ttl=20)

def get_from_cache(key):
    return CACHE_STORE.get(key)

def set_cache(key, val):
    CACHE_STORE[key] = val

@app.get("/rates")
def get_rates(
    limit: int = 50000, 
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="1H, 4H, 1D, 1W"),
    symbol: str = Query("USDC", description="USDC, DAI, USDT, SOFR")
):
    # Regex Validation for Dates (YYYY-MM-DD)
    date_pattern = r"^\d{4}-\d{2}-\d{2}$"
    if start_date and not re.match(date_pattern, start_date):
        raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
    if end_date and not re.match(date_pattern, end_date):
        raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD")

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
            raise HTTPException(status_code=400, detail="Invalid Symbol")

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
        logging.error(f"ERROR in get_rates: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/eth-prices")
def get_eth_prices(
    limit: int = 50000,
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="1H, 4H, 1D")
):
    try:
        # Cache Check
        cache_key = f"eth_prices:{resolution}:{limit}:{start_date}:{end_date}"
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

        # ETH Prices usually displayed ASC for charts, but we apply limit to the LATEST
        # So effective query needed is: Get latest N, then sort ASC.
        # But simpler: Order DESC, Limit, then Sort Python side or Subquery.
        # Given existing logic, let's just apply LIMIT to the query string which uses ASC?
        # NO. ASC LIMIT N gets Oldest N. 
        # We need: SELECT * FROM (...) ORDER BY timestamp ASC.
        # For chart endpoint, preserving ASC default is good. 
        # But if LIMIT is small (e.g. 48), user usually implies "Latest 48".
        # Let's change ORDER to DESC for the query fetch, then sort ASC in Pandas if needed.
        query += f" {group_clause} ORDER BY timestamp DESC LIMIT {limit}"
        
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
        logging.error(f"ERROR in get_eth_prices: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")