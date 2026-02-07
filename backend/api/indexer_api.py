#!/usr/bin/env python3
"""
REST API for Comprehensive Indexer Data.
Exposes historical market state, pool state, events, and broker positions.

Run with:
    uvicorn indexer_api:app --host 0.0.0.0 --port 8080 --reload
"""
import os
import sys
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import json

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comprehensive_indexer_db import (
    get_latest_summary,
    get_block_summary,
    get_block_states,
    get_pool_states,
    get_events,
    get_broker_position_history,
    get_last_indexed_block,
    DB_PATH
)

app = FastAPI(
    title="RLD Indexer API",
    description="Historical market state data for RLD protocol",
    version="1.0.0"
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Response Models
class MarketState(BaseModel):
    block_number: int
    block_timestamp: int
    market_id: str
    normalization_factor: int
    total_debt: int
    last_update_timestamp: int
    index_price: int
    # Computed fields
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


class IndexerStatus(BaseModel):
    last_indexed_block: int
    db_path: str
    total_block_states: int
    total_events: int


# API Endpoints

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "rld-indexer-api"}


@app.get("/api/status", response_model=IndexerStatus)
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
        
        return IndexerStatus(
            last_indexed_block=get_last_indexed_block(),
            db_path=DB_PATH,
            total_block_states=total_blocks,
            total_events=total_events
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/latest")
async def get_latest():
    """Get the latest indexed block snapshot."""
    try:
        summary = get_latest_summary()
        if 'error' in summary:
            raise HTTPException(status_code=404, detail=summary['error'])
        
        # Add decimal representations
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


@app.get("/api/history/market", response_model=List[MarketState])
async def get_market_history(
    market_id: Optional[str] = None,
    from_block: Optional[int] = Query(None, description="Start block"),
    to_block: Optional[int] = Query(None, description="End block"),
    limit: int = Query(100, le=1000, description="Max results")
):
    """Get historical market state data."""
    try:
        states = get_block_states(market_id, from_block, to_block, limit)
        
        # Add decimal representations
        for state in states:
            state['nf_decimal'] = state.get('normalization_factor', 0) / 1e18
            state['debt_decimal'] = state.get('total_debt', 0) / 1e6
            state['index_price_decimal'] = state.get('index_price', 0) / 1e18
        
        return states
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history/pool", response_model=List[PoolState])
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


@app.get("/api/events", response_model=List[Event])
async def get_events_list(
    event_name: Optional[str] = Query(None, description="Filter by event name"),
    market_id: Optional[str] = Query(None, description="Filter by market ID"),
    from_block: Optional[int] = Query(None, description="Start block"),
    to_block: Optional[int] = Query(None, description="End block"),
    limit: int = Query(100, le=1000, description="Max results")
):
    """Get historical events."""
    try:
        # Use named parameters to avoid order confusion
        events = get_events(
            from_block=from_block, 
            to_block=to_block, 
            event_name=event_name, 
            market_id=market_id, 
            limit=limit
        )
        # Rename 'data' to 'event_data' for API response model
        for e in events:
            if 'data' in e:
                e['event_data'] = e.pop('data')
            # Rename 'timestamp' to 'block_timestamp' for API response model
            if 'timestamp' in e:
                e['block_timestamp'] = e.pop('timestamp')
        return events
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history/broker", response_model=List[BrokerPosition])
async def get_broker_history(
    broker_address: str = Query(..., description="Broker address"),
    market_id: Optional[str] = Query(None, description="Market ID"),
    from_block: Optional[int] = Query(None, description="Start block"),
    to_block: Optional[int] = Query(None, description="End block"),
    limit: int = Query(100, le=1000, description="Max results")
):
    """Get historical broker positions."""
    try:
        positions = get_broker_position_history(broker_address, market_id, from_block, to_block, limit)
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
        # Get market states for index price
        market_states = get_block_states(None, from_block, to_block, limit)
        
        # Get pool states for mark price
        pool_states = get_pool_states(None, from_block, to_block, limit)
        
        # Build chart data
        chart_data = []
        pool_by_block = {p['block_number']: p for p in pool_states}
        
        for ms in market_states:
            block = ms['block_number']
            data_point = {
                'block_number': block,
                'timestamp': ms.get('block_timestamp', 0),
                'index_price': ms.get('index_price', 0) / 1e18,
                'normalization_factor': ms.get('normalization_factor', 0) / 1e18,
                'total_debt': ms.get('total_debt', 0) / 1e6
            }
            
            # Add pool data if available
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
