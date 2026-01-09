from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import pandas as pd
from datetime import datetime

app = FastAPI()

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

@app.get("/rates")
def get_rates(
    limit: int = 50000, 
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="RAW, 1H, 4H, 1D, 1W")
):
    try:
        conn = get_db_connection()
        
        # --- 1. SMART LIMITS ---
        # Browser Safety: 
        # RAW data is heavy (1 row = 1 block). Limit to 30k (approx 4-5 days).
        # Aggregated data is light (1 row = 1 hour). We can allow 100k rows.
        if resolution == "RAW":
            # --- RAW DATA FROM rates TABLE ---
            effective_limit = min(limit, 30000)
            select_clause = "*"
            table_name = "rates"
            group_clause = ""
            order_clause = "timestamp DESC"
        else:
            # --- AGGREGATED DATA FROM rates_1h TABLE ---
            # rates_1h is already 1H resolution.
            # If requesting 4H or 1D, we aggregate further from rates_1h.
            effective_limit = 100000
            table_name = "rates_1h"
            
            buckets = {"4H": 14400, "1D": 86400, "1W": 604800}
            seconds = buckets.get(resolution)

            if seconds:
                # Downsample (e.g. 1H -> 4H)
                select_clause = f"""
                    MAX(timestamp) as timestamp, 
                    AVG(apy) as apy, 
                    AVG(eth_price) as eth_price,
                    MAX(block_number) as block_number
                """
                group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"
            else:
                # 1H (matches table resolution) - No grouping needed
                select_clause = "*"
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