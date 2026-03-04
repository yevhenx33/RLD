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


@app.get("/api/bonds")
async def get_bonds_endpoint(
    owner: Optional[str] = Query(None, description="Filter by owner address"),
    status: str = Query("all", description="Filter: active, closed, all"),
    limit: int = Query(100, le=500, description="Max results"),
):
    """Get indexed bond positions from BondMinted/BondClosed events."""
    try:
        from db.comprehensive import get_bonds_by_owner, get_all_bonds
        if owner:
            bonds = get_bonds_by_owner(owner, status if status != "all" else None)
        else:
            bonds = get_all_bonds(status if status != "all" else None, limit)
        return {"bonds": bonds, "count": len(bonds)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bonds/{broker_address}")
async def get_bond_detail(broker_address: str):
    """Get a single bond's indexed data by its broker address."""
    try:
        from db.comprehensive import get_bond
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
    resolution: str = Query("1H", description="Resolution: 1H, 4H, 1D, 1W"),
    start_time: Optional[int] = Query(None, description="Start timestamp (unix)"),
    end_time: Optional[int] = Query(None, description="End timestamp (unix)"),
    limit: int = Query(500, le=1000, description="Max data points"),
):
    """
    Get price data formatted for charting, aggregated by resolution bucket.
    Returns OHLC-style data points bucketed by the requested resolution.
    """
    try:
        # Resolution → bucket size in seconds
        BUCKET_MAP = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        bucket_sec = BUCKET_MAP.get(resolution.upper(), 3600)

        db_path = os.environ.get("DB_PATH", "data/comprehensive_state.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # ── Bucketed market state (NF, debt, index price) ──────────
        ms_query = '''
            SELECT
                (block_timestamp / ?) * ? as bucket_ts,
                MIN(block_number) as first_block,
                MAX(block_number) as last_block,
                MAX(CAST(index_price AS INTEGER)) as index_high,
                MIN(CAST(index_price AS INTEGER)) as index_low,
                COUNT(*) as sample_count
            FROM block_state
            WHERE 1=1
        '''
        params = [bucket_sec, bucket_sec]

        if start_time:
            ms_query += ' AND block_timestamp >= ?'
            params.append(start_time)
        if end_time:
            ms_query += ' AND block_timestamp <= ?'
            params.append(end_time)

        ms_query += ' GROUP BY bucket_ts ORDER BY bucket_ts DESC LIMIT ?'
        params.append(limit)

        c.execute(ms_query, params)
        ms_bucket_rows = c.fetchall()

        # Fetch open/close values per bucket
        ms_rows = []
        for row in ms_bucket_rows:
            fb, lb = row['first_block'], row['last_block']
            c.execute('SELECT index_price, normalization_factor, total_debt FROM block_state WHERE block_number = ?', (fb,))
            open_r = c.fetchone()
            c.execute('SELECT index_price, normalization_factor, total_debt FROM block_state WHERE block_number = ?', (lb,))
            close_r = c.fetchone()
            ms_rows.append({
                'bucket_ts': row['bucket_ts'],
                'first_block': fb,
                'last_block': lb,
                'index_high': row['index_high'],
                'index_low': row['index_low'],
                'sample_count': row['sample_count'],
                'index_open': int(open_r['index_price'] or 0) if open_r else 0,
                'index_close': int(close_r['index_price'] or 0) if close_r else 0,
                'nf_close': int(close_r['normalization_factor'] or 0) if close_r else 0,
                'debt_close': int(close_r['total_debt'] or 0) if close_r else 0,
            })

        # ── Bucketed pool state (mark price, tick, liquidity) ──────
        # pool_state lacks block_timestamp, so join with block_state
        ps_query = '''
            SELECT
                (bs.block_timestamp / ?) * ? as bucket_ts,
                MIN(ps.block_number) as first_block,
                MAX(ps.block_number) as last_block,
                MAX(ps.mark_price) as mark_high,
                MIN(ps.mark_price) as mark_low
            FROM pool_state ps
            JOIN block_state bs ON bs.block_number = ps.block_number
            WHERE 1=1
        '''
        ps_params = [bucket_sec, bucket_sec]

        if start_time:
            ps_query += ' AND bs.block_timestamp >= ?'
            ps_params.append(start_time)
        if end_time:
            ps_query += ' AND bs.block_timestamp <= ?'
            ps_params.append(end_time)

        ps_query += ' GROUP BY bucket_ts ORDER BY bucket_ts DESC LIMIT ?'
        ps_params.append(limit)

        c.execute(ps_query, ps_params)
        ps_rows = c.fetchall()

        # Fetch open/close values for each bucket using the first/last block numbers
        pool_map = {}
        for row in ps_rows:
            bucket_ts = row['bucket_ts']
            fb, lb = row['first_block'], row['last_block']
            c.execute('SELECT mark_price, tick, liquidity FROM pool_state WHERE block_number = ?', (fb,))
            open_row = c.fetchone()
            c.execute('SELECT mark_price, tick, liquidity FROM pool_state WHERE block_number = ?', (lb,))
            close_row = c.fetchone()
            pool_map[bucket_ts] = {
                'mark_open': open_row['mark_price'] if open_row else None,
                'mark_close': close_row['mark_price'] if close_row else None,
                'mark_high': row['mark_high'],
                'mark_low': row['mark_low'],
                'tick_close': close_row['tick'] if close_row else None,
                'liq_close': close_row['liquidity'] if close_row else None,
            }

        conn.close()

        # ── Merge into chart data ──────────────────────────────────
        chart_data = []
        for row in reversed(ms_rows):  # Reverse to chronological order
            ts = row['bucket_ts']
            point = {
                'timestamp': ts,
                'block_number': row['last_block'],
                'index_price': (row['index_close'] or 0) / 1e18,
                'index_high': (row['index_high'] or 0) / 1e18,
                'index_low': (row['index_low'] or 0) / 1e18,
                'normalization_factor': (row['nf_close'] or 0) / 1e18,
                'total_debt': (row['debt_close'] or 0) / 1e6,
                'samples': row['sample_count'],
            }
            if ts in pool_map:
                ps = pool_map[ts]
                point['mark_price'] = ps.get('mark_close') or 0
                point['mark_high'] = ps.get('mark_high') or 0
                point['mark_low'] = ps.get('mark_low') or 0
                point['tick'] = ps.get('tick_close') or 0
                point['liquidity'] = ps.get('liq_close') or 0

            chart_data.append(point)

        return {
            'data': chart_data,
            'count': len(chart_data),
            'resolution': resolution.upper(),
            'bucket_seconds': bucket_sec,
            'from_block': chart_data[0]['block_number'] if chart_data else None,
            'to_block': chart_data[-1]['block_number'] if chart_data else None,
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
        db_path = os.environ.get("DB_PATH", "data/comprehensive_state.db")
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        # Get time range
        c.execute("SELECT MAX(timestamp) FROM events WHERE event_name='Swap'")
        max_ts = c.fetchone()[0]
        if not max_ts:
            return {"bars": [], "bucket_hours": bucket}

        bucket_sec = bucket * 3600
        cutoff = max_ts - (hours * 3600)

        c.execute('''
            SELECT (timestamp / ?) * ? as bucket_ts,
                   COUNT(*),
                   SUM(ABS(CAST(json_extract(data, '$.amount0') AS INTEGER))),
                   SUM(ABS(CAST(json_extract(data, '$.amount1') AS INTEGER)))
            FROM events
            WHERE event_name='Swap' AND timestamp >= ?
            GROUP BY bucket_ts
            ORDER BY bucket_ts
        ''', (bucket_sec, bucket_sec, cutoff))

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
        conn.close()

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

            # positionInfo layout: poolId(25) | tickLower(3) | tickUpper(3) | hasSubscriber(1)
            tick_lower_raw = int.from_bytes(info[25:28], "big")
            tick_upper_raw = int.from_bytes(info[28:31], "big")
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
    Pool-wide liquidity distribution — production endpoint with caching.

    Reads from lp_positions DB table (populated by indexer).
    Falls back to direct POSM RPC scan if DB is empty.
    Result is cached for ~12s (1 block).
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
            from db.comprehensive import get_all_latest_lp_positions
            rows = get_all_latest_lp_positions()
            positions = [
                {
                    "token_id": r.get("token_id", 0),
                    "liquidity": int(r.get("liquidity", 0)),
                    "tick_lower": r.get("tick_lower", 0),
                    "tick_upper": r.get("tick_upper", 0),
                }
                for r in rows
                if int(r.get("liquidity", 0)) > 0
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
