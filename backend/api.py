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
            effective_limit = min(limit, 30000) 
        else:
            effective_limit = 100000 # Enough for 10 years of hourly data

        # --- 2. AGGREGATION LOGIC ---
        if resolution == "RAW":
            select_clause = "*"
            group_clause = ""
            order_clause = "timestamp DESC"
        else:
            buckets = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
            seconds = buckets.get(resolution, 3600)
            
            # Group by bucket, avg(apy)
            select_clause = f"""
                MAX(timestamp) as timestamp, 
                AVG(apy) as apy, 
                MAX(block_number) as block_number
            """
            group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"
            order_clause = "timestamp DESC"

        # --- 3. BUILD QUERY ---
        query = f"SELECT {select_clause} FROM rates WHERE 1=1"
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
        return df.to_dict(orient="records")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))