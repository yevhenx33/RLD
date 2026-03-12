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
import psycopg2.extras
import json
import math
from fastapi.responses import JSONResponse

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.event_driven import (
    get_last_indexed_block,
    get_events,
    get_conn,
    get_all_markets,
    get_market,
    get_all_brokers,
    get_broker,
    get_pool,
    get_lp_distribution,
    get_broker_lp_positions,
    get_active_bonds,
    get_candles,
    get_latest_summary,
)



def _sanitize_floats(obj):
    """Recursively replace inf/nan floats with None."""
    if isinstance(obj, float):
        return None if (math.isinf(obj) or math.isnan(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_floats(v) for v in obj]
    return obj


class SafeJSONResponse(JSONResponse):
    """JSONResponse that handles inf/nan floats."""
    def render(self, content):
        return super().render(_sanitize_floats(content))


app = FastAPI(
    title="RLD Market Indexer API",
    description="Historical market state data for any RLD market",
    version="2.0.0",
    default_response_class=SafeJSONResponse,
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://rld.fi",
        "https://www.rld.fi",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── ETag / Cache-Control Middleware ────────────────────────────
# Returns 304 Not Modified when the latest indexed block hasn't changed.
# Cuts bandwidth to zero for rapid polling when chain is idle.
from starlette.responses import Response as StarletteResponse

@app.middleware("http")
async def etag_cache_middleware(request, call_next):
    response = await call_next(request)
    path = request.url.path
    # Only apply to API data endpoints (not GraphQL, not static)
    if path.startswith("/api/") and request.method == "GET":
        try:
            block = get_last_indexed_block()
            etag = f'"block-{block}"'
            client_etag = request.headers.get("if-none-match", "")
            if client_etag == etag:
                return StarletteResponse(status_code=304, headers={
                    "ETag": etag,
                    "Cache-Control": "public, max-age=1",
                })
            response.headers["ETag"] = etag
            response.headers["Cache-Control"] = "public, max-age=1"
        except Exception:
            pass  # Don't fail the request if caching breaks
    return response


# Mount GraphQL endpoint
try:
    from strawberry.fastapi import GraphQLRouter
    from api.graphql_schema import schema as gql_schema
    graphql_app = GraphQLRouter(gql_schema)
    app.include_router(graphql_app, prefix="/graphql")
except ImportError:
    pass  # strawberry not installed — GraphQL disabled


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


# In-memory health cache — updated by background task every 5s
# This ensures /health never blocks the event loop or holds db connections
_HEALTH_CACHE: dict = {
    "status": "starting",
    "mode": "event-driven",
    "last_indexed_block": 0,
    "chain_head": None,
    "lag_blocks": None,
    "total_events": 0,
    "total_markets": 0,
    "total_brokers": 0,
    "detected_block_time_s": None,
    "poll_interval_s": None,
}

async def _refresh_health_cache():
    """Background coroutine — refreshes _HEALTH_CACHE every 5s without blocking."""
    while True:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # Run blocking DB calls in a thread pool so the event loop stays free
            def _fetch():
                try:
                    lb = get_last_indexed_block()
                    with get_conn() as conn:
                        c = conn.cursor()
                        c.execute("SELECT COUNT(*) FROM events")
                        te = c.fetchone()[0]
                        c.execute("SELECT COUNT(*) FROM market_meta")
                        tm = c.fetchone()[0]
                        c.execute("SELECT COUNT(*) FROM broker_state")
                        tb = c.fetchone()[0]
                    return lb, te, tm, tb
                except Exception:
                    return None, None, None, None

            lb, te, tm, tb = await loop.run_in_executor(None, _fetch)
            if lb is not None:
                _HEALTH_CACHE["last_indexed_block"] = lb
                _HEALTH_CACHE["total_events"] = te
                _HEALTH_CACHE["total_markets"] = tm
                _HEALTH_CACHE["total_brokers"] = tb
                _HEALTH_CACHE["status"] = "healthy"
        except Exception:
            pass
        import asyncio
        await asyncio.sleep(5)


@app.get("/health")
async def health(request: Request):
    """Instant health check — reads from in-memory cache only (zero blocking)."""
    # Merge app.state extras (block_time, poll_interval set by entrypoint)
    bt = getattr(request.app.state, "detected_block_time", None)
    pi = getattr(request.app.state, "poll_interval_s", None)
    return {
        **_HEALTH_CACHE,
        "market_id": os.environ.get("MARKET_ID", "unknown"),
        "detected_block_time_s": bt,
        "poll_interval_s": pi,
    }




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
        with get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM events")
            total_events = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM market_meta")
            total_markets = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM broker_state")
            total_brokers = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM lp_position_state WHERE liquidity > 0")
            active_lp = cursor.fetchone()[0]

        return {
            "last_indexed_block": get_last_indexed_block(),
            "total_events": total_events,
            "total_markets": total_markets,
            "total_brokers": total_brokers,
            "active_lp_positions": active_lp,
            "mode": "event-driven",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# Event-Driven Projection Endpoints
# ═══════════════════════════════════════════════════════════

@app.get("/api/markets")
async def list_markets():
    """Get all markets with current state."""
    try:
        return get_all_markets()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/markets/{market_id}")
async def get_market_endpoint(market_id: str):
    """Get a single market with current state."""
    result = get_market(market_id)
    if not result:
        raise HTTPException(status_code=404, detail="Market not found")
    return result


@app.get("/api/brokers")
async def list_brokers(market_id: Optional[str] = Query(None)):
    """Get all tracked brokers and their current state."""
    try:
        return get_all_brokers(market_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/broker/{broker_address}")
async def get_broker_endpoint(broker_address: str):
    """Get current state for a single broker."""
    result = get_broker(broker_address)
    if not result:
        raise HTTPException(status_code=404, detail="Broker not found")
    return result


@app.get("/api/pool/{pool_id}")
async def get_pool_endpoint(pool_id: str):
    """Get current pool state (price, tick, liquidity)."""
    result = get_pool(pool_id)
    if not result:
        raise HTTPException(status_code=404, detail="Pool not found")
    return result


@app.get("/api/lp-distribution/{pool_id}")
async def get_lp_distribution_endpoint(pool_id: str):
    """
    Get liquidity distribution across tick ranges for a pool.
    Served entirely from local DB — zero RPC calls.
    Suitable for charting LP depth/distribution.
    """
    try:
        data = get_lp_distribution(pool_id)
        return {"pool_id": pool_id, "ticks": data, "count": len(data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/lp-positions/{broker_address}")
async def get_broker_lp_endpoint(broker_address: str):
    """Get all active LP positions for a broker. Zero RPC calls."""
    try:
        positions = get_broker_lp_positions(broker_address)
        return {"broker_address": broker_address, "positions": positions, "count": len(positions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/candles/{pool_id}")
async def get_candles_endpoint(
    pool_id: str,
    resolution: str = Query("5m", description="Resolution: 5m, 15m, 1h, 4h, 1d"),
    from_ts: Optional[int] = Query(None, description="Start unix timestamp"),
    to_ts: Optional[int] = Query(None, description="End unix timestamp"),
    limit: int = Query(500, le=2000),
):
    """
    Get OHLCV candles for a pool.
    resolution=5m  → price_candles_5m  (written live by indexer on each Swap)
    resolution=15m → price_candles_15m (pre-aggregated by CandleAggregator)
    resolution=1h  → price_candles_1h
    resolution=4h  → price_candles_4h
    resolution=1d  → price_candles_1d
    """
    try:
        candles = get_candles(pool_id, from_ts, to_ts, limit, resolution=resolution)
        return {"pool_id": pool_id, "resolution": resolution, "candles": candles, "count": len(candles)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/api/events")
async def get_events_list(
    event_name: Optional[str] = Query(None),
    market_id: Optional[str] = Query(None),
    from_block: Optional[int] = Query(None),
    to_block: Optional[int] = Query(None),
    limit: int = Query(100, le=1000),
):
    """Get historical events from the audit log."""
    try:
        return get_events(
            from_block=from_block, to_block=to_block,
            event_name=event_name, market_id=market_id, limit=limit
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


# ═══════════════════════════════════════════════════════════
# Bond Enrichment — server-side RPC for live on-chain state
# ═══════════════════════════════════════════════════════════

import time as _time

# TTL cache: rapid UI interactions reuse cached enrichment (2s TTL)
_bond_enrich_cache = {"data": None, "ts": 0, "key": None}


def _enrich_bonds_cached(bonds: list, rpc_url: str, market_config: dict, owner: str = None) -> list:
    """Cached wrapper for bond enrichment. Returns cached data if < 2s old."""
    global _bond_enrich_cache
    cache_key = f"{owner}:{len(bonds)}"
    now = _time.monotonic()
    if (
        _bond_enrich_cache["data"] is not None
        and _bond_enrich_cache["key"] == cache_key
        and (now - _bond_enrich_cache["ts"]) < 2.0
    ):
        return _bond_enrich_cache["data"]
    result = _enrich_bonds_with_rpc(bonds, rpc_url, market_config)
    _bond_enrich_cache = {"data": result, "ts": now, "key": cache_key}
    return result


def _enrich_bonds_with_rpc(bonds: list, rpc_url: str, market_config: dict) -> list:
    """
    Enrich bond records with live on-chain data via batched JSON-RPC.
    Fetches: debt principal, NF, TWAMM order, collateral balance, frozen flag.
    All in 1-2 batch requests (vs 20+ sequential browser calls).
    """
    if not bonds:
        return bonds

    import urllib.request as urlreq
    import math

    active_bonds = [b for b in bonds if b.get("status") == "active"]
    if not active_bonds:
        # Closed bonds — just compute fields from DB data, no RPC needed
        for b in bonds:
            notional = int(b.get("notional") or 0) / 1e6
            b["notional_usd"] = notional
            b["debt_usd"] = 0
            b["free_collateral"] = 0
            b["remaining_days"] = 0
            b["is_matured"] = True
            b["frozen"] = False
            b["has_active_order"] = False
            b["bond_id"] = int(b["broker_address"][-4:], 16) % 10000
        return bonds

    # ── Build batch JSON-RPC request ──────────────────────────
    # Function selectors (4 bytes):
    SEL_CORE = "0xf8f9da28"              # CORE() -> address
    SEL_MARKET_ID = "0x1dce43f9"         # marketId() -> bytes32
    SEL_COLLATERAL = "0xb2016bd4"        # collateralToken() -> address
    SEL_FROZEN = "0x054f7d9c"            # frozen() -> bool
    SEL_TWAMM_ORDER = "0xe4d7907d"       # activeTwammOrder() -> (key, orderKey, orderId)
    SEL_GET_POSITION = "0x713f9507"      # getPosition(bytes32, address)
    SEL_GET_MARKET_STATE = "0x544e4c74"  # getMarketState(bytes32)
    SEL_BALANCE_OF = "0x70a08231"        # balanceOf(address)

    def pad_addr(addr):
        return addr.lower().replace("0x", "").zfill(64)

    batch = []
    # Phase 1: Get broker metadata (core, marketId, collateral, frozen, twamm)
    for i, b in enumerate(active_bonds):
        addr = b["broker_address"]
        base_id = i * 5
        batch.append({"jsonrpc": "2.0", "id": base_id,     "method": "eth_call", "params": [{"to": addr, "data": SEL_CORE}, "latest"]})
        batch.append({"jsonrpc": "2.0", "id": base_id + 1, "method": "eth_call", "params": [{"to": addr, "data": SEL_MARKET_ID}, "latest"]})
        batch.append({"jsonrpc": "2.0", "id": base_id + 2, "method": "eth_call", "params": [{"to": addr, "data": SEL_COLLATERAL}, "latest"]})
        batch.append({"jsonrpc": "2.0", "id": base_id + 3, "method": "eth_call", "params": [{"to": addr, "data": SEL_FROZEN}, "latest"]})
        batch.append({"jsonrpc": "2.0", "id": base_id + 4, "method": "eth_call", "params": [{"to": addr, "data": SEL_TWAMM_ORDER}, "latest"]})

    # Add a block number call at the end
    block_id = len(active_bonds) * 5
    batch.append({"jsonrpc": "2.0", "id": block_id, "method": "eth_getBlockByNumber", "params": ["latest", False]})

    # Send batch 1
    payload = json.dumps(batch).encode()
    req = urlreq.Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
    with urlreq.urlopen(req, timeout=10) as resp:
        results1 = json.loads(resp.read())

    # Index results by id
    r1_map = {r["id"]: r.get("result", "0x") for r in results1}

    # Extract block timestamp
    block_data = r1_map.get(block_id, {})
    block_timestamp = int(block_data.get("timestamp", "0x0"), 16) if isinstance(block_data, dict) else 0

    # Phase 2: Build position + balance batch using core/marketId/collateral
    batch2 = []
    broker_meta = {}
    for i, b in enumerate(active_bonds):
        base_id = i * 5
        core_addr = "0x" + r1_map.get(base_id, "0x" + "0" * 64)[-40:]
        market_id = r1_map.get(base_id + 1, "0x" + "0" * 64)
        col_addr = "0x" + r1_map.get(base_id + 2, "0x" + "0" * 64)[-40:]
        frozen_raw = r1_map.get(base_id + 3, "0x0")
        frozen = int(frozen_raw, 16) != 0 if frozen_raw else False

        # Parse TWAMM order
        twamm_raw = r1_map.get(base_id + 4, "0x" + "0" * 64)
        order_expiration = 0
        order_id = "0x" + "0" * 64
        if twamm_raw and len(twamm_raw) > 2:
            raw_bytes = bytes.fromhex(twamm_raw.replace("0x", ""))
            # activeTwammOrder returns: key(5 slots), orderKey(3 slots), orderId(1 slot)
            # orderKey[1] = expiration (slot index 6 = byte offset 192)
            # orderId = slot index 8 = byte offset 256
            if len(raw_bytes) >= 288:
                order_expiration = int.from_bytes(raw_bytes[192:224], "big")
                order_id = "0x" + raw_bytes[256:288].hex()

        broker_meta[b["broker_address"]] = {
            "core": core_addr,
            "market_id": market_id,
            "col_addr": col_addr,
            "frozen": frozen,
            "order_expiration": order_expiration,
            "order_id": order_id,
        }

        # getPosition(marketId, brokerAddr)
        pos_data = SEL_GET_POSITION + market_id.replace("0x", "").zfill(64) + pad_addr(b["broker_address"])
        batch2.append({"jsonrpc": "2.0", "id": i * 3, "method": "eth_call", "params": [{"to": core_addr, "data": pos_data}, "latest"]})

        # getMarketState(marketId)
        ms_data = SEL_GET_MARKET_STATE + market_id.replace("0x", "").zfill(64)
        batch2.append({"jsonrpc": "2.0", "id": i * 3 + 1, "method": "eth_call", "params": [{"to": core_addr, "data": ms_data}, "latest"]})

        # balanceOf(brokerAddr) on collateral token
        bal_data = SEL_BALANCE_OF + pad_addr(b["broker_address"])
        batch2.append({"jsonrpc": "2.0", "id": i * 3 + 2, "method": "eth_call", "params": [{"to": col_addr, "data": bal_data}, "latest"]})

    # Send batch 2
    if batch2:
        payload2 = json.dumps(batch2).encode()
        req2 = urlreq.Request(rpc_url, data=payload2, headers={"Content-Type": "application/json"})
        with urlreq.urlopen(req2, timeout=10) as resp2:
            results2 = json.loads(resp2.read())
        r2_map = {r["id"]: r.get("result", "0x") for r in results2}
    else:
        r2_map = {}

    # ── Assemble enriched bond data ───────────────────────────
    for i, b in enumerate(active_bonds):
        meta = broker_meta[b["broker_address"]]

        # Parse position (debtPrincipal = first uint128 of tuple)
        pos_raw = r2_map.get(i * 3, "0x" + "0" * 64)
        debt_principal = int(pos_raw[-32:], 16) if pos_raw and len(pos_raw) > 2 else 0

        # Parse market state (normalizationFactor = first uint128)
        ms_raw = r2_map.get(i * 3 + 1, "0x" + "0" * 64)
        nf = int(ms_raw[2:66], 16) if ms_raw and len(ms_raw) > 2 else int(1e18)

        # Parse balance
        bal_raw = r2_map.get(i * 3 + 2, "0x" + "0" * 64)
        col_balance = int(bal_raw, 16) if bal_raw and len(bal_raw) > 2 else 0

        # Compute values
        true_debt = (debt_principal * nf) // (10 ** 18)
        debt_usd = true_debt / 1e6
        free_collateral = col_balance / 1e6
        notional = int(b.get("notional") or 0) / 1e6

        # TWAMM timing
        has_active_order = meta["order_id"] != "0x" + "0" * 64
        remaining_sec = max(0, meta["order_expiration"] - block_timestamp)
        remaining_days = max(0, math.ceil(remaining_sec / 86400))
        is_matured = has_active_order and remaining_sec <= 0

        # Elapsed time
        created_ts = b.get("created_timestamp") or 0
        elapsed_sec = max(0, block_timestamp - created_ts) if created_ts else 0
        elapsed_days = elapsed_sec // 86400

        # Accrued interest estimate
        duration_hours = (b.get("duration") or 0) / 3600
        maturity_days = math.ceil(duration_hours / 24) if duration_hours else remaining_days

        # Maturity date
        maturity_date = "—"
        if has_active_order and meta["order_expiration"] > 0:
            from datetime import datetime, timezone
            maturity_date = datetime.fromtimestamp(meta["order_expiration"], tz=timezone.utc).strftime("%Y-%m-%d")

        b["notional_usd"] = notional
        b["debt_usd"] = debt_usd
        b["free_collateral"] = free_collateral
        b["remaining_days"] = remaining_days
        b["elapsed_days"] = elapsed_days
        b["maturity_days"] = maturity_days
        b["is_matured"] = is_matured
        b["frozen"] = meta["frozen"]
        b["has_active_order"] = has_active_order
        b["order_id"] = meta["order_id"]
        b["maturity_date"] = maturity_date
        b["bond_id"] = int(b["broker_address"][-4:], 16) % 10000
        b["block_timestamp"] = block_timestamp

    # Also compute fields for non-active (closed) bonds
    for b in bonds:
        if b.get("status") != "active":
            notional = int(b.get("notional") or 0) / 1e6
            b["notional_usd"] = notional
            b["debt_usd"] = 0
            b["free_collateral"] = 0
            b["remaining_days"] = 0
            b["elapsed_days"] = 0
            b["maturity_days"] = 0
            b["is_matured"] = True
            b["frozen"] = False
            b["has_active_order"] = False
            b["order_id"] = "0x" + "0" * 64
            b["maturity_date"] = "—"
            b["bond_id"] = int(b["broker_address"][-4:], 16) % 10000
            b["block_timestamp"] = 0
            # Collateral returned from close event
            col_returned = int(b.get("collateral_returned") or 0) / 1e6
            b["collateral_returned_usd"] = col_returned

    return bonds


@app.get("/api/bonds")
async def get_bonds_endpoint(
    request: Request,
    owner: Optional[str] = Query(None, description="Filter by owner address"),
    status: str = Query("all", description="Filter: active, closed, all"),
    enrich: bool = Query(False, description="Enrich with live on-chain data (debt, TWAMM, collateral)"),
    limit: int = Query(100, le=500, description="Max results"),
):
    """
    Get indexed bond positions from BondMinted/BondClosed events.
    With enrich=true, returns live on-chain state (debt, TWAMM order, frozen)
    via server-side batched RPC — eliminates 20+ sequential frontend calls.
    """
    try:
        from db.event_driven import get_bonds_by_owner, get_all_bonds
        if owner:
            bonds = get_bonds_by_owner(owner, status if status != "all" else None)
        else:
            bonds = get_all_bonds(status if status != "all" else None, limit)

        if enrich and bonds:
            market_config = getattr(request.app.state, "market_config", None) or {}
            rpc_url = market_config.get("rpc_url", os.environ.get("RPC_URL", "http://localhost:8545"))
            bonds = _enrich_bonds_cached(bonds, rpc_url, market_config, owner=owner)

        return {"bonds": bonds, "count": len(bonds)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bonds/{broker_address}")
async def get_bond_detail(broker_address: str):
    """Get a single bond's indexed data by its broker address."""
    try:
        from db.event_driven import get_bond
        bond = get_bond(broker_address)
        if not bond:
            raise HTTPException(status_code=404, detail="Bond not found")
        return bond
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chart/price")
async def get_price_chart(
    resolution: str = Query("1H", description="Resolution: 5M, 1H, 4H, 1D, 1W"),
    start_time: Optional[int] = Query(None, description="Start timestamp (unix)"),
    end_time: Optional[int] = Query(None, description="End timestamp (unix)"),
    limit: int = Query(500, le=1000, description="Max data points"),
):
    """
    Get price data formatted for charting, aggregated by resolution bucket.
    5M resolution is served from the pre-aggregated price_candles_5m table.
    Other resolutions use live aggregation from block_state + pool_state.
    """
    try:
        res = resolution.upper()
        BUCKET_MAP = {"5M": 300, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        bucket_sec = BUCKET_MAP.get(res, 3600)

        with get_conn() as conn:
            c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # ── Fast path: 5M → read from pre-aggregated price_candles_5m ──
            if res == "5M":
                q = "SELECT * FROM price_candles_5m WHERE 1=1"
                params = []
                if start_time:
                    q += " AND ts >= %s"
                    params.append(start_time)
                if end_time:
                    q += " AND ts <= %s"
                    params.append(end_time)
                q += " ORDER BY ts DESC LIMIT %s"
                params.append(limit)

                try:
                    c.execute(q, params)
                    candle_rows = c.fetchall()
                except Exception:
                    # Table not yet created — fall through to live aggregation
                    candle_rows = []

                chart_data = []
                for row in reversed(candle_rows):
                    chart_data.append({
                        'timestamp':             row['ts'],
                        'block_number':          None,
                        'index_price':           row['index_close'] or 0,
                        'index_open':            row['index_open']  or 0,
                        'index_high':            row['index_high']  or 0,
                        'index_low':             row['index_low']   or 0,
                        'mark_price':            row['mark_close']  or 0,
                        'mark_open':             row['mark_open']   or 0,
                        'mark_high':             row['mark_high']   or 0,
                        'mark_low':              row['mark_low']    or 0,
                        'normalization_factor':  row['nf_close']    or 0,
                        'total_debt':            row['debt_close']  or 0,
                        'samples':               row['sample_count'] or 0,
                    })

                return {
                    'data':           chart_data,
                    'count':          len(chart_data),
                    'resolution':     res,
                    'bucket_seconds': bucket_sec,
                    'from_block':     None,
                    'to_block':       None,
                }

            # ── Standard path: read from pre-aggregated candle table ──
            TABLE_MAP = {
                "5M":  "price_candles_5m",
                "15M": "price_candles_15m",
                "1H":  "price_candles_1h",
                "4H":  "price_candles_4h",
                "1D":  "price_candles_1d",
                "1W":  "price_candles_1d",  # resample daily for weekly
            }
            table = TABLE_MAP.get(res, "price_candles_1h")

            q = f"SELECT * FROM {table} WHERE 1=1"
            params = []
            if start_time:
                q += " AND ts >= %s"
                params.append(start_time)
            if end_time:
                q += " AND ts <= %s"
                params.append(end_time)
            q += " ORDER BY ts DESC LIMIT %s"
            params.append(limit)

            try:
                c.execute(q, params)
                candle_rows = c.fetchall()
            except Exception:
                candle_rows = []

            chart_data = []
            for row in reversed(candle_rows):
                chart_data.append({
                    'timestamp':            row['ts'],
                    'block_number':         None,
                    'index_price':          row.get('index_close') or 0,
                    'index_open':           row.get('index_open')  or 0,
                    'index_high':           row.get('index_high')  or 0,
                    'index_low':            row.get('index_low')   or 0,
                    'mark_price':           row.get('mark_close')  or 0,
                    'mark_open':            row.get('mark_open')   or 0,
                    'mark_high':            row.get('mark_high')   or 0,
                    'mark_low':             row.get('mark_low')    or 0,
                    'normalization_factor': 0,   # not stored in candle tables
                    'total_debt':           0,   # not stored in candle tables
                    'tick':                 None,
                    'liquidity':            None,
                    'samples':              row.get('swap_count') or 0,
                })

            return {
                'data':           chart_data,
                'count':          len(chart_data),
                'resolution':     res,
                'bucket_seconds': bucket_sec,
                'from_block':     None,
                'to_block':       None,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/volume")
async def get_volume(
    hours: int = Query(24, le=168, description="Hours lookback for volume"),
):
    """Get trade volume aggregated from Swap events."""
    try:
        with get_conn() as conn:
            c = conn.cursor()

            # Get latest timestamp
            c.execute("SELECT MAX(timestamp) FROM events WHERE event_name='Swap'")
            max_ts = c.fetchone()[0]
            if not max_ts:
                return {"volume_24h": 0, "swap_count": 0, "volume_formatted": "$0"}

            cutoff = max_ts - (hours * 3600)

            c.execute("""
                SELECT COUNT(*),
                       SUM(ABS(CAST(data->>'amount1' AS BIGINT)))
                FROM events
                WHERE event_name='Swap' AND timestamp >= %s
            """, (cutoff,))
            count, vol_raw = c.fetchone()

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


@app.get("/api/volume-history")
async def get_volume_history(
    hours: int = Query(168, ge=1, le=720, description="Hours lookback"),
    bucket: int = Query(1, ge=1, le=24, description="Bucket size in hours"),
):
    """
    Hourly (or multi-hour) volume bars from Swap events.
    Returns array of {timestamp, volume_usd, swap_count} buckets.
    """
    try:
        with get_conn() as conn:
            c = conn.cursor()

            # Get time range
            c.execute("SELECT MAX(timestamp) FROM events WHERE event_name='Swap'")
            max_ts = c.fetchone()[0]
            if not max_ts:
                return {"bars": [], "bucket_hours": bucket}

            bucket_sec = bucket * 3600
            cutoff = max_ts - (hours * 3600)

            c.execute("""
                SELECT (timestamp / %s) * %s as bucket_ts,
                       COUNT(*),
                       SUM(ABS(CAST(data->>'amount0' AS BIGINT))),
                       SUM(ABS(CAST(data->>'amount1' AS BIGINT)))
                FROM events
                WHERE event_name='Swap' AND timestamp >= %s
                GROUP BY bucket_ts
                ORDER BY bucket_ts
            """, (bucket_sec, bucket_sec, cutoff))

            bars = []
            for row in c.fetchall():
                ts, count, vol0_raw, vol1_raw = row
                # Use the larger of the two amounts (both are 6-decimal tokens)
                vol_usd = max(vol0_raw or 0, vol1_raw or 0) / 1e6
                bars.append({
                    "timestamp": ts,
                    "volume_usd": round(vol_usd, 2),
                    "swap_count": count,
                })

        return {"bars": bars, "bucket_hours": bucket, "total_bars": len(bars)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════════════════════
# Pool-Wide Liquidity Distribution (cached, production-grade)
# ═══════════════════════════════════════════════════════════

import math
import time as _time
import threading

_liq_cache = {"bins": None, "ts": 0, "lock": threading.Lock()}
_LIQ_CACHE_TTL = 12  # seconds (≈ 1 block)


def _tick_to_price(tick: int) -> float:
    return math.pow(1.0001, tick)


def _liquidity_to_amounts(liquidity: int, tick_lower: int, tick_upper: int, current_tick: int):
    """Convert concentrated liquidity to token amounts (Uni V3/V4 math)."""
    sa = math.sqrt(_tick_to_price(tick_lower))
    sb = math.sqrt(_tick_to_price(tick_upper))
    sp = math.sqrt(_tick_to_price(current_tick))
    sp = max(sa, min(sp, sb))  # clamp

    amount0 = liquidity * (1.0 / sp - 1.0 / sb) if sp < sb else 0
    amount1 = liquidity * (sp - sa) if sp > sa else 0
    return max(0, amount0), max(0, amount1)


def _build_distribution(positions, current_price, current_tick, num_bins=60, token0_decimals=6, token1_decimals=6):
    """Aggregate positions into price bins with token amounts."""
    if not positions or not current_price or current_price <= 0:
        return []

    # ±100% price range: half to double the current price
    min_price = current_price * 0.5
    max_price = current_price * 2.0
    bin_width = (max_price - min_price) / num_bins

    bins = []
    for i in range(num_bins):
        price_from = min_price + i * bin_width
        price_to = min_price + (i + 1) * bin_width
        total_liq = 0

        for p in positions:
            tl = min(p["tick_lower"], p["tick_upper"])
            tu = max(p["tick_lower"], p["tick_upper"])
            p_low = _tick_to_price(tl)
            p_high = _tick_to_price(tu)
            if p_high > price_from and p_low < price_to:
                total_liq += int(p["liquidity"])

        # Convert aggregated liquidity to token amounts for this bin
        bin_tick_lo = int(math.log(price_from) / math.log(1.0001)) if price_from > 0 else -887272
        bin_tick_hi = int(math.log(price_to) / math.log(1.0001)) if price_to > 0 else 887272
        a0, a1 = _liquidity_to_amounts(total_liq, bin_tick_lo, bin_tick_hi, current_tick)

        # Convert from raw units to human-readable using token decimals
        a0_human = a0 / (10 ** token0_decimals)
        a1_human = a1 / (10 ** token1_decimals)

        bins.append({
            "price": round((price_from + price_to) / 2, 6),
            "priceFrom": round(price_from, 6),
            "priceTo": round(price_to, 6),
            "liquidity": str(total_liq),
            "amount0": round(a0_human, 2),
            "amount1": round(a1_human, 2),
        })
    return bins


def _rpc_scan_positions(request):
    """Fallback: scan POSM directly via RPC when DB is empty.
    Filters positions by PoolId to only include the RLD pool.
    """
    from web3 import Web3

    market_config = getattr(request.app.state, "market_config", None)
    rpc_url = (market_config or {}).get("rpc_url", os.environ.get("RPC_URL", "http://localhost:8545"))
    posm_addr = (market_config or {}).get("v4_position_manager", os.environ.get("V4_POSITION_MANAGER", ""))
    if not posm_addr:
        return []

    w3 = Web3(Web3.HTTPProvider(rpc_url))

    # Compute expected PoolId from deployment config
    # PoolId = keccak256(abi.encode(currency0, currency1, fee, tickSpacing, hooks))
    token0 = (market_config or {}).get("token0", "")
    token1 = (market_config or {}).get("token1", "")
    hook = (market_config or {}).get("twamm_hook", "")
    expected_pid_25 = None
    if token0 and token1 and hook:
        try:
            t0 = bytes.fromhex(Web3.to_checksum_address(token0)[2:])
            t1 = bytes.fromhex(Web3.to_checksum_address(token1)[2:])
            hk = bytes.fromhex(Web3.to_checksum_address(hook)[2:])
            # Solidity abi.encode: each field padded to 32 bytes
            encoded = b'\x00' * 12 + t0
            encoded += b'\x00' * 12 + t1
            encoded += (500).to_bytes(32, 'big')        # uint24 fee
            encoded += (5).to_bytes(32, 'big')           # int24 tickSpacing (positive)
            encoded += b'\x00' * 12 + hk
            pool_id = w3.keccak(encoded)
            expected_pid_25 = pool_id[:25]  # positionInfo stores first 25 bytes
        except Exception:
            pass

    POSM_ABI_JSON = [
        {"type": "function", "name": "nextTokenId", "inputs": [], "outputs": [{"type": "uint256"}], "stateMutability": "view"},
        {"type": "function", "name": "getPositionLiquidity", "inputs": [{"type": "uint256", "name": "tokenId"}], "outputs": [{"type": "uint128"}], "stateMutability": "view"},
        {"type": "function", "name": "positionInfo", "inputs": [{"type": "uint256", "name": "tokenId"}], "outputs": [{"type": "bytes32"}], "stateMutability": "view"},
    ]
    posm = w3.eth.contract(address=Web3.to_checksum_address(posm_addr), abi=POSM_ABI_JSON)
    next_id = posm.functions.nextTokenId().call()

    positions = []
    for token_id in range(1, next_id):
        try:
            info = posm.functions.positionInfo(token_id).call()

            # Filter by PoolId (first 25 bytes of positionInfo)
            if expected_pid_25 and info[:25] != expected_pid_25:
                continue

            liq = posm.functions.getPositionLiquidity(token_id).call()
            if liq == 0:
                continue

            # positionInfo layout from LSB: hasSubscriber(8) | tickLower(24) | tickUpper(24) | poolId(200)
            val = int.from_bytes(info, 'big')
            tick_lower_raw = (val >> 8) & 0xFFFFFF
            tick_upper_raw = (val >> 32) & 0xFFFFFF
            tick_lower = tick_lower_raw if tick_lower_raw < 0x800000 else tick_lower_raw - 0x1000000
            tick_upper = tick_upper_raw if tick_upper_raw < 0x800000 else tick_upper_raw - 0x1000000
            positions.append({
                "token_id": token_id,
                "liquidity": liq,
                "tick_lower": tick_lower,
                "tick_upper": tick_upper,
            })
        except Exception:
            continue
    return positions


@app.get("/api/liquidity-distribution")
async def get_liquidity_distribution(
    request: Request,
    num_bins: int = Query(60, ge=10, le=200),
):
    """
    Pool-wide liquidity distribution - production endpoint with caching.

    Reads from lp_positions DB table (populated by indexer).
    Falls back to direct POSM RPC scan if DB is empty.
    Result is cached for about 12 seconds (1 block).
    """
    now = _time.time()

    # Check cache
    with _liq_cache["lock"]:
        if _liq_cache["bins"] is not None and (now - _liq_cache["ts"]) < _LIQ_CACHE_TTL:
            return {
                "bins": _liq_cache["bins"]["bins"],
                "currentPrice": _liq_cache["bins"]["currentPrice"],
                "totalPositions": _liq_cache["bins"]["totalPositions"],
                "cached": True,
                "cacheAge": round(now - _liq_cache["ts"], 1),
            }

    try:
        # Get current price + tick from latest pool state
        current_price = None
        current_tick = 0
        try:
            summary = get_latest_summary()
            ps_list = summary.get("pool_states", [])
            if ps_list:
                current_price = ps_list[0].get("mark_price", None)
                current_tick = ps_list[0].get("tick", 0)
        except Exception:
            pass

        # Primary: read from DB
        positions = []
        try:
            from db.event_driven import get_lp_positions
            rows = get_lp_positions(active_only=True)
            positions = [
                {
                    "token_id": r.get("token_id", 0),
                    "liquidity": int(r.get("current_liquidity", 0)),
                    "tick_lower": r.get("tick_lower", 0),
                    "tick_upper": r.get("tick_upper", 0),
                }
                for r in rows
                if int(r.get("current_liquidity", 0)) > 0
            ]
        except Exception:
            pass

        # Fallback: RPC scan if DB is empty
        if not positions:
            positions = _rpc_scan_positions(request)

        if not positions:
            return {"bins": [], "currentPrice": current_price, "totalPositions": 0, "cached": False, "cacheAge": 0}

        # Compute current_tick from price if not available
        if current_tick == 0 and current_price and current_price > 0:
            current_tick = int(math.log(current_price) / math.log(1.0001))

        bins = _build_distribution(positions, current_price, current_tick, num_bins)

        # Update cache
        result = {
            "bins": bins,
            "currentPrice": current_price,
            "totalPositions": len(positions),
        }
        with _liq_cache["lock"]:
            _liq_cache["bins"] = result
            _liq_cache["ts"] = now

        return {**result, "cached": False, "cacheAge": 0}

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
        broker_executor = market_config.get("broker_executor", os.environ.get("BROKER_EXECUTOR", ""))
        bond_factory = market_config.get("bond_factory", os.environ.get("BOND_FACTORY", ""))
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

        # Canonical mainnet contracts (from deployment.json or hardcoded defaults)
        ext = market_config.get("external_contracts", {})

        return {
            "collateral": {"name": col_name, "symbol": col_symbol, "address": col_token},
            "position_token": {"name": pos_name, "symbol": pos_symbol, "address": pos_token},
            "broker_factory": broker_factory,
            "infrastructure": {
                # RLD-specific
                "broker_router": broker_router,
                "broker_executor": broker_executor,
                "twamm_hook": twamm_hook,
                "bond_factory": bond_factory,
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
            "external_contracts": {
                "usdc": ext.get("usdc", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
                "ausdc": ext.get("ausdc", "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"),
                "aave_pool": ext.get("aave_pool", "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"),
                "susde": ext.get("susde", "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"),
                "usdc_whale": ext.get("usdc_whale", "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"),
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


# ═══════════════════════════════════════════════════════════
# sUSDe Yield (serves from indexed DB, with API fallback)
# ═══════════════════════════════════════════════════════════

_susde_cache = {"data": None, "ts": 0, "lock": threading.Lock()}
_SUSDE_CACHE_TTL = 60  # seconds


@app.get("/api/yields/susde")
async def get_susde_yield():
    """Serve sUSDe yield from the rates-indexer DB.
    Falls back to live Ethena API if DB is unavailable."""
    now = _time.time()

    # Check cache first
    with _susde_cache["lock"]:
        if _susde_cache["data"] is not None and (now - _susde_cache["ts"]) < _SUSDE_CACHE_TTL:
            return {**_susde_cache["data"], "cached": True, "cacheAge": round(now - _susde_cache["ts"], 1)}

    # Try reading from rates DB (production path)
    try:
        import sqlite3 as _sqlite3
        # The rates-indexer writes to this DB
        rates_db_paths = [
            "/var/lib/data/clean_rates.db",   # Docker production
            "/data/clean_rates.db",            # Alternative Docker path
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "clean_rates.db"),
        ]
        db_path = None
        for p in rates_db_paths:
            if os.path.exists(p):
                db_path = p
                break

        if db_path:
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            # Get the latest susde_yield from hourly_stats
            cur.execute("SELECT susde_yield FROM hourly_stats WHERE susde_yield IS NOT NULL ORDER BY timestamp DESC LIMIT 1")
            row = cur.fetchone()
            conn.close()

            if row and row[0] is not None:
                result = {
                    "stakingYield": row[0],
                    "source": "indexed",
                }
                with _susde_cache["lock"]:
                    _susde_cache["data"] = result
                    _susde_cache["ts"] = now
                return {**result, "cached": False, "cacheAge": 0}
    except Exception:
        pass  # Fall through to API fallback

    # Fallback: fetch live from Ethena API
    try:
        import urllib.request as urlreq
        ETHENA_YIELD_URL = "https://ethena.fi/api/yields/protocol-and-staking-yield"
        req = urlreq.Request(ETHENA_YIELD_URL, headers={"User-Agent": "RLD-Indexer/1.0"})
        with urlreq.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())

        result = {
            "stakingYield": raw.get("stakingYield", {}).get("value"),
            "protocolYield": raw.get("protocolYield", {}).get("value"),
            "avg30d": raw.get("avg30dSusdeYield", {}).get("value"),
            "avg90d": raw.get("avg90dSusdeYield", {}).get("value"),
            "lastUpdated": raw.get("stakingYield", {}).get("lastUpdated"),
            "source": "ethena_api",
        }

        with _susde_cache["lock"]:
            _susde_cache["data"] = result
            _susde_cache["ts"] = now

        return {**result, "cached": False, "cacheAge": 0}
    except Exception as e:
        # Return stale cache if available
        with _susde_cache["lock"]:
            if _susde_cache["data"] is not None:
                return {**_susde_cache["data"], "cached": True, "stale": True, "cacheAge": round(now - _susde_cache["ts"], 1)}
        raise HTTPException(status_code=502, detail=f"sUSDe yield unavailable: {e}")

@app.post("/api/admin/reload-config")
async def admin_reload_config(request: Request):
    """Hot-reload deployment.json into the running indexer.
    Updates market_config and env vars without restart."""
    try:
        config_file = getattr(request.app.state, "config_file", None)
        if not config_file or not os.path.exists(config_file):
            raise HTTPException(status_code=404, detail="Config file not found")

        # Import reload helper from entrypoint
        import importlib
        entrypoint = importlib.import_module("entrypoint" if "entrypoint" in sys.modules else "entrypoint")
        new_config = entrypoint.reload_config_into_app(request.app, config_file)

        return {
            "status": "ok",
            "message": f"Config reloaded from {config_file}",
            "bond_factory": new_config.get("bond_factory", ""),
            "broker_factory": new_config.get("broker_factory", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
