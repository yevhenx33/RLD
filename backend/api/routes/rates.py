"""
Rate data API routes.

Serves historical lending rates, ETH prices, and WebSocket live updates.
All data comes from clean_rates.db (hourly aggregated) or aave_rates.db (raw).
"""

import asyncio
import logging
import os
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from api.deps import (
    get_db_connection, get_raw_db_connection,
    get_from_cache, set_cache,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────
# GET /rates
# ─────────────────────────────────────────────────────────────

@router.get("/rates")
def get_rates(
    limit: int = 50000,
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="1H, 4H, 1D, 1W"),
    symbol: str = Query("USDC", description="USDC, DAI, USDT, SOFR, sUSDe"),
):
    date_pattern = r"^\d{4}-\d{2}-\d{2}$"
    if start_date and not re.match(date_pattern, start_date):
        raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
    if end_date and not re.match(date_pattern, end_date):
        raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD")

    try:
        cache_key = f"rates:{symbol}:{resolution}:{limit}:{start_date}:{end_date}"
        cached = get_from_cache(cache_key)
        if cached:
            return cached

        symbol_map = {
            "USDC": "usdc_rate",
            "DAI": "dai_rate",
            "USDT": "usdt_rate",
            "SOFR": "sofr_rate",
            "SUSDE": "susde_yield",
            "sUSDe": "susde_yield",
        }

        target_col = symbol_map.get(symbol.upper()) or symbol_map.get(symbol)
        if not target_col:
            raise HTTPException(status_code=400, detail="Invalid Symbol")

        conn = get_db_connection()
        cursor = conn.cursor()

        buckets = {"5M": 300, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        seconds = buckets.get(resolution, 3600)

        if resolution in ("1H", "5M"):
            select_clause = f"timestamp, {target_col} as apy, eth_price"
            group_clause = ""
        else:
            select_clause = f"""
                MAX(timestamp) as timestamp,
                AVG({target_col}) as apy,
                AVG(eth_price) as eth_price
            """
            group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"

        effective_limit = min(limit, 100000)

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

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return []

        # Convert to list of dicts, sorted ASC
        data = [dict(row) for row in rows]
        data.sort(key=lambda r: r["timestamp"])

        # Forward-fill NaN gaps
        last_apy = None
        for row in data:
            if row.get("apy") is not None:
                last_apy = row["apy"]
            elif last_apy is not None:
                row["apy"] = last_apy

        # Clean NaN/None for JSON
        for row in data:
            for k, v in row.items():
                if isinstance(v, float) and v != v:  # NaN check
                    row[k] = None

        set_cache(cache_key, data)
        return data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ERROR in get_rates: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


# ─────────────────────────────────────────────────────────────
# GET /eth-prices
# ─────────────────────────────────────────────────────────────

@router.get("/eth-prices")
def get_eth_prices(
    limit: int = 50000,
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="RAW, 1H, 4H, 1D"),
):
    try:
        cache_key = f"eth_prices:{resolution}:{limit}:{start_date}:{end_date}"
        cached = get_from_cache(cache_key)
        if cached:
            return cached

        # RAW resolution: read directly from aave_rates.db
        if resolution == "RAW":
            try:
                conn_raw = get_raw_db_connection()
                cursor = conn_raw.cursor()
                query = "SELECT timestamp, price, block_number FROM eth_prices WHERE 1=1"
                params = []
                if start_date:
                    dt = datetime.strptime(start_date, "%Y-%m-%d")
                    query += " AND timestamp >= ?"
                    params.append(int(dt.timestamp()))
                if end_date:
                    dt = datetime.strptime(end_date, "%Y-%m-%d")
                    query += " AND timestamp <= ?"
                    params.append(int(dt.timestamp()) + 86399)
                query += f" ORDER BY timestamp DESC LIMIT {min(limit, 100000)}"
                cursor.execute(query, params)
                rows = cursor.fetchall()
                conn_raw.close()
                if not rows:
                    return []
                data = [dict(row) for row in rows]
                for row in data:
                    for k, v in row.items():
                        if isinstance(v, float) and v != v:
                            row[k] = None
                set_cache(cache_key, data)
                return data
            except Exception as e:
                if "no such table" in str(e):
                    return []
                raise

        conn = get_db_connection()
        cursor = conn.cursor()

        buckets = {"5M": 300, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        seconds = buckets.get(resolution, 3600)

        if resolution in ("1H", "5M"):
            select_clause = "timestamp, eth_price as price"
            group_clause = ""
        else:
            select_clause = """
                MAX(timestamp) as timestamp,
                AVG(eth_price) as price
            """
            group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"

        params = []
        query = f"SELECT {select_clause} FROM hourly_stats WHERE timestamp >= 1677801600"

        if start_date:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(int(dt.timestamp()))

        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            query += " AND timestamp <= ?"
            params.append(int(dt.timestamp()) + 86399)

        query += f" {group_clause} ORDER BY timestamp DESC LIMIT {limit}"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return []

        data = [dict(row) for row in rows]
        for row in data:
            for k, v in row.items():
                if isinstance(v, float) and v != v:
                    row[k] = None

        set_cache(cache_key, data)
        return data

    except HTTPException:
        raise
    except Exception as e:
        if "no such table" in str(e):
            return []
        logger.error(f"ERROR in get_eth_prices: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


# ─────────────────────────────────────────────────────────────
# WebSocket /ws/rates
# ─────────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


@router.websocket("/ws/rates")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def broadcast_rates():
    """Background loop: poll DB every 5s, broadcast to WebSocket clients."""
    last_ts = None
    while True:
        try:
            await asyncio.sleep(5)
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM hourly_stats ORDER BY timestamp DESC LIMIT 1")
            row = cursor.fetchone()
            conn.close()

            if row:
                latest = dict(row)
                if latest.get("timestamp") != last_ts:
                    last_ts = latest.get("timestamp")
                    payload = {
                        "type": "UPDATE",
                        "data": {
                            "timestamp": latest.get("timestamp"),
                            "USDC": latest.get("usdc_rate"),
                            "DAI": latest.get("dai_rate"),
                            "USDT": latest.get("usdt_rate"),
                            "SOFR": latest.get("sofr_rate"),
                            "sUSDe": latest.get("susde_yield"),
                            "ETH": latest.get("eth_price"),
                        }
                    }
                    await manager.broadcast(payload)

        except Exception as e:
            logger.error(f"WS Broadcast Error: {e}")
            await asyncio.sleep(5)


# ─────────────────────────────────────────────────────────────
# GET /download/db/{filename}
# ─────────────────────────────────────────────────────────────

@router.get("/download/db/{filename}")
async def download_database(filename: str, secret: str = Query(...)):
    """Download database files for migration (secret-protected)."""
    expected_secret = os.getenv("MIGRATION_SECRET", "")
    if not expected_secret or secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    allowed_files = ["aave_rates.db", "clean_rates.db", "yields.json"]
    if filename not in allowed_files:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = os.path.join(
        os.getenv("DB_DIR", os.path.join(os.path.dirname(__file__), "..", "data")),
        filename
    )
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on server")

    logger.info(f"📦 Serving file for migration: {file_path}")
    return FileResponse(file_path, filename=filename, media_type="application/octet-stream")
