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


@app.get("/api/volume")
async def get_volume(
    hours: int = Query(24, le=168, description="Hours lookback for volume"),
):
    """Get trade volume aggregated from Swap events."""
    try:
        db_path = os.environ.get("DB_PATH", "data/comprehensive_state.db")
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        # Get latest timestamp
        c.execute("SELECT MAX(timestamp) FROM events WHERE event_name='Swap'")
        max_ts = c.fetchone()[0]
        if not max_ts:
            return {"volume_24h": 0, "swap_count": 0, "volume_formatted": "$0"}

        cutoff = max_ts - (hours * 3600)

        c.execute('''
            SELECT COUNT(*),
                   SUM(ABS(CAST(json_extract(data, '$.amount1') AS INTEGER)))
            FROM events
            WHERE event_name='Swap' AND timestamp >= ?
        ''', (cutoff,))
        count, vol_raw = c.fetchone()
        conn.close()

        vol_raw = vol_raw or 0
        vol_usd = vol_raw / 1e6

        # Format nicely
        if vol_usd >= 1e9:
            formatted = f"${vol_usd / 1e9:.2f}B"
        elif vol_usd >= 1e6:
            formatted = f"${vol_usd / 1e6:.2f}M"
        elif vol_usd >= 1e3:
            formatted = f"${vol_usd / 1e3:.0f}K"
        else:
            formatted = f"${vol_usd:,.0f}"

        return {
            "volume_usd": vol_usd,
            "swap_count": count or 0,
            "volume_formatted": formatted,
            "hours": hours,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/market-info")
async def get_market_info(request: Request):
    """Get on-chain market config, token names, and risk parameters."""
    try:
        market_config = getattr(request.app.state, "market_config", None)
        if not market_config:
            raise HTTPException(status_code=503, detail="Market config not available")

        rpc_url = market_config.get("rpc_url", os.environ.get("RPC_URL", "http://host.docker.internal:8545"))
        rld_core = market_config.get("rld_core", "0xaE7b7A1c6C4d859e19301ccAc2C6eD28A4C51288")
        market_id = market_config.get("market_id", "0x660a01c4bdc81dcbc5845841998ef85fac39414b465dc91c77330463bc5b1a92")
        col_token = market_config.get("collateral_token", "0x91c8C745fd156d8624677aa924Cdc1Ef8173C69C")
        pos_token = market_config.get("position_token", "0x699BF0931001f6cc804942C6C998d9E4dC95cB28")

        import urllib.request as urlreq

        def eth_call(to: str, data: str) -> str:
            payload = json.dumps({
                "jsonrpc": "2.0", "method": "eth_call", "id": 1,
                "params": [{"to": to, "data": data}, "latest"]
            }).encode()
            req = urlreq.Request(rpc_url, data=payload,
                                 headers={"Content-Type": "application/json"})
            with urlreq.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())["result"]

        def decode_string(hex_data: str) -> str:
            raw = bytes.fromhex(hex_data.replace("0x", ""))
            offset = int.from_bytes(raw[0:32], "big")
            length = int.from_bytes(raw[offset:offset+32], "big")
            return raw[offset+32:offset+32+length].decode("utf-8")

        def decode_uint(hex_data: str, slot: int) -> int:
            raw = bytes.fromhex(hex_data.replace("0x", ""))
            return int.from_bytes(raw[slot*32:(slot+1)*32], "big")

        # Token name/symbol: name()=0x06fdde03, symbol()=0x95d89b41
        col_name = decode_string(eth_call(col_token, "0x06fdde03"))
        col_symbol = decode_string(eth_call(col_token, "0x95d89b41"))
        pos_name = decode_string(eth_call(pos_token, "0x06fdde03"))
        pos_symbol = decode_string(eth_call(pos_token, "0x95d89b41"))

        # getMarketConfig(bytes32) selector = 0x6a6ae218
        selector = "0x6a6ae218"
        padded_id = market_id.replace("0x", "").zfill(64)
        config_data = eth_call(rld_core, selector + padded_id)

        # MarketConfig: uint64, uint64, uint64, uint32, uint128, bytes32, address
        min_col_ratio = decode_uint(config_data, 0)
        maintenance_margin = decode_uint(config_data, 1)
        liq_close_factor = decode_uint(config_data, 2)
        funding_period = decode_uint(config_data, 3)
        debt_cap = decode_uint(config_data, 4)

        broker_factory = market_config.get("broker_factory", os.environ.get("BROKER_FACTORY"))

        # RLD-deployed infrastructure — from deployment.json (via market_config)
        broker_router = market_config.get("broker_router", os.environ.get("BROKER_ROUTER", ""))
        twamm_hook = market_config.get("twamm_hook", os.environ.get("TWAMM_HOOK", ""))

        # Official Uniswap V4 mainnet addresses (always available on mainnet fork)
        # Hardcoded fallbacks guarantee these are always returned even without config
        pool_manager = market_config.get("pool_manager", "0x000000000004444c5dc75cB358380D2e3dE08A90")
        v4_quoter = market_config.get("v4_quoter", "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203")
        v4_position_manager = market_config.get("v4_position_manager", "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e")
        v4_position_descriptor = market_config.get("v4_position_descriptor", "0xd1428ba554f4c8450b763a0b2040a4935c63f06c")
        v4_state_view = market_config.get("v4_state_view", "0x7ffe42c4a5deea5b0fec41c94c136cf115597227")
        universal_router = market_config.get("universal_router", "0x66a9893cc07d91d95644aedd05d03f95e1dba8af")
        permit2 = market_config.get("permit2", "0x000000000022D473030F116dDEE9F6B43aC78BA3")

        return {
            "collateral": {"name": col_name, "symbol": col_symbol, "address": col_token},
            "position_token": {"name": pos_name, "symbol": pos_symbol, "address": pos_token},
            "broker_factory": broker_factory,
            "infrastructure": {
                # RLD-specific
                "broker_router": broker_router,
                "twamm_hook": twamm_hook,
                "pool_fee": 500,
                "tick_spacing": 5,
                # Uniswap V4 official
                "pool_manager": pool_manager,
                "v4_quoter": v4_quoter,
                "v4_position_manager": v4_position_manager,
                "v4_position_descriptor": v4_position_descriptor,
                "v4_state_view": v4_state_view,
                "universal_router": universal_router,
                "permit2": permit2,
            },
            "risk_params": {
                "min_col_ratio": min_col_ratio / 1e18,
                "min_col_ratio_pct": f"{min_col_ratio / 1e16:.0f}%",
                "maintenance_margin": maintenance_margin / 1e18,
                "maintenance_margin_pct": f"{maintenance_margin / 1e16:.0f}%",
                "liq_close_factor": liq_close_factor / 1e18,
                "liq_close_factor_pct": f"{liq_close_factor / 1e16:.0f}%",
                "funding_period_sec": funding_period,
                "funding_period_days": funding_period / 86400,
                "debt_cap": debt_cap,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
