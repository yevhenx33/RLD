#!/usr/bin/env python3
"""
REST API for Comprehensive Indexer Data.
Exposes historical market state, pool state, events, and broker positions.

Run standalone:
    uvicorn api.indexer_api:app --host 0.0.0.0 --port 8080 --reload

Or via entrypoint.py (container mode).
"""
import os
import sys
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import json

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.comprehensive import (
    get_latest_summary,
    get_block_summary,
    get_block_states,
    get_pool_states,
    get_events,
    get_broker_history,
    get_last_indexed_block,
    DB_PATH
)

app = FastAPI(
    title="RLD Market Indexer API",
    description="Historical market state data for any RLD market",
    version="2.0.0"
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════
# Response Models
# ═══════════════════════════════════════════════════════════

class MarketState(BaseModel):
    block_number: int
    block_timestamp: int
    market_id: str
    normalization_factor: int
    total_debt: int
    last_update_timestamp: int
    index_price: int
    nf_decimal: Optional[float] = None
    debt_decimal: Optional[float] = None
    index_price_decimal: Optional[float] = None


class PoolState(BaseModel):
    block_number: int
    pool_id: str
    token0: str
    token1: str
    sqrt_price_x96: int
    tick: int
    liquidity: int
    mark_price: float
    fee_growth_global0: int
    fee_growth_global1: int


class Event(BaseModel):
    id: int
    block_number: int
    tx_hash: str
    log_index: int
    event_name: str
    contract_address: str
    market_id: str
    event_data: dict
    block_timestamp: int


class BrokerPosition(BaseModel):
    block_number: int
    broker_address: str
    market_id: str
    collateral: int
    debt: int
    debt_principal: int
    collateral_value: int
    debt_value: int
    health_factor: float


# ═══════════════════════════════════════════════════════════
# Health & Config (container endpoints)
# ═══════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "rld-market-indexer"}


@app.get("/health")
async def health(request: Request):
    """Detailed health check with indexer lag."""
    try:
        last_block = get_last_indexed_block()

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM block_state")
        total_blocks = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM events")
        total_events = c.fetchone()[0]
        conn.close()

        # Get chain head if web3 is available
        chain_head = None
        lag = None
        try:
            from web3 import Web3
            rpc = os.environ.get("RPC_URL", "http://localhost:8545")
            w3 = Web3(Web3.HTTPProvider(rpc))
            chain_head = w3.eth.block_number
            if last_block and chain_head:
                lag = chain_head - last_block
        except:
            pass

        # Market config from entrypoint discovery
        market_id = os.environ.get("MARKET_ID", "unknown")

        return {
            "status": "healthy",
            "market_id": market_id,
            "last_indexed_block": last_block,
            "chain_head": chain_head,
            "lag_blocks": lag,
            "total_blocks_indexed": total_blocks,
            "total_events": total_events,
            "db_path": DB_PATH,
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.get("/config")
async def config(request: Request):
    """Return discovered market configuration."""
    # Set by entrypoint.py after discovery
    market_config = getattr(request.app.state, "market_config", None)
    if market_config:
        # Don't expose RPC URL (may contain API keys)
        safe = {k: v for k, v in market_config.items() if k != "rpc_url"}
        safe["rpc_url"] = "***"
        return safe
    return {"error": "Config not available (not running via entrypoint)"}


# ═══════════════════════════════════════════════════════════
# Core Data Endpoints
# ═══════════════════════════════════════════════════════════

@app.get("/api/status")
async def get_status():
    """Get indexer status and stats."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM block_state")
        total_blocks = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM events")
        total_events = cursor.fetchone()[0]

        conn.close()

        return {
            "last_indexed_block": get_last_indexed_block(),
            "db_path": DB_PATH,
            "total_block_states": total_blocks,
            "total_events": total_events,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/latest")
async def get_latest():
    """Get the latest indexed block snapshot."""
    try:
        summary = get_latest_summary()
        if 'error' in summary:
            raise HTTPException(status_code=404, detail=summary['error'])

        if 'market_state' in summary:
            ms = summary['market_state']
            ms['nf_decimal'] = ms.get('normalization_factor', 0) / 1e18
            ms['debt_decimal'] = ms.get('total_debt', 0) / 1e6
            ms['index_price_decimal'] = ms.get('index_price', 0) / 1e18

        return summary
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/block/{block_number}")
async def get_block(block_number: int):
    """Get snapshot for a specific block."""
    try:
        summary = get_block_summary(block_number)
        if not summary.get('block_number'):
            raise HTTPException(status_code=404, detail=f"Block {block_number} not found")
        return summary
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history/market")
async def get_market_history(
    market_id: Optional[str] = None,
    from_block: Optional[int] = Query(None, description="Start block"),
    to_block: Optional[int] = Query(None, description="End block"),
    limit: int = Query(100, le=1000, description="Max results")
):
    """Get historical market state data."""
    try:
        states = get_block_states(market_id, from_block, to_block, limit)
        for state in states:
            state['nf_decimal'] = state.get('normalization_factor', 0) / 1e18
            state['debt_decimal'] = state.get('total_debt', 0) / 1e6
            state['index_price_decimal'] = state.get('index_price', 0) / 1e18
        return states
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history/pool")
async def get_pool_history(
    pool_id: Optional[str] = None,
    from_block: Optional[int] = Query(None, description="Start block"),
    to_block: Optional[int] = Query(None, description="End block"),
    limit: int = Query(100, le=1000, description="Max results")
):
    """Get historical pool state data."""
    try:
        states = get_pool_states(pool_id, from_block, to_block, limit)
        return states
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
async def get_events_list(
    event_name: Optional[str] = Query(None, description="Filter by event name"),
    market_id: Optional[str] = Query(None, description="Filter by market ID"),
    from_block: Optional[int] = Query(None, description="Start block"),
    to_block: Optional[int] = Query(None, description="End block"),
    limit: int = Query(100, le=1000, description="Max results")
):
    """Get historical events."""
    try:
        events = get_events(
            from_block=from_block,
            to_block=to_block,
            event_name=event_name,
            market_id=market_id,
            limit=limit
        )
        for e in events:
            if 'data' in e:
                e['event_data'] = e.pop('data')
            if 'timestamp' in e:
                e['block_timestamp'] = e.pop('timestamp')
        return events
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history/broker")
async def get_broker_history_endpoint(
    broker_address: str = Query(..., description="Broker address"),
    market_id: Optional[str] = Query(None, description="Market ID"),
    from_block: Optional[int] = Query(None, description="Start block"),
    to_block: Optional[int] = Query(None, description="End block"),
    limit: int = Query(100, le=1000, description="Max results")
):
    """Get historical broker positions."""
    try:
        positions = get_broker_history(broker_address, market_id, limit)
        return positions
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chart/price")
async def get_price_chart(
    from_block: Optional[int] = Query(None, description="Start block"),
    to_block: Optional[int] = Query(None, description="End block"),
    limit: int = Query(500, le=2000, description="Max data points")
):
    """Get price data formatted for charting (index vs mark price)."""
    try:
        market_states = get_block_states(None, from_block, to_block, limit)
        pool_states = get_pool_states(None, from_block, to_block, limit)

        pool_by_block = {p['block_number']: p for p in pool_states}
        chart_data = []

        for ms in market_states:
            block = ms['block_number']
            data_point = {
                'block_number': block,
                'timestamp': ms.get('block_timestamp', 0),
                'index_price': ms.get('index_price', 0) / 1e18,
                'normalization_factor': ms.get('normalization_factor', 0) / 1e18,
                'total_debt': ms.get('total_debt', 0) / 1e6
            }
            if block in pool_by_block:
                ps = pool_by_block[block]
                data_point['mark_price'] = ps.get('mark_price', 0)
                data_point['tick'] = ps.get('tick', 0)
                data_point['liquidity'] = ps.get('liquidity', 0)
            chart_data.append(data_point)

        return {
            'data': chart_data,
            'count': len(chart_data),
            'from_block': chart_data[0]['block_number'] if chart_data else None,
            'to_block': chart_data[-1]['block_number'] if chart_data else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
