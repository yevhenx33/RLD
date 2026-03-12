"""
GraphQL Schema for RLD Indexer.

Provides a single-query interface for the frontend to fetch ALL data in one request:
- Market state, pool state, broker positions, LP positions
- Volume (computed from Swap events)
- Recent events
- Indexer status
- Market info (token metadata, infrastructure, risk params)
- TWAMM orders (from event logs in DB)
- Bonds (with optional enrichment)

This replaces 7+ separate REST endpoints with 1 GraphQL call.
"""
import strawberry
import os
import json
import math
import psycopg2.extras
import time
import urllib.request
import urllib.parse
from typing import Optional, List
from strawberry.types import Info

from db.event_driven import (
    get_latest_summary,
    get_lp_positions,
    get_all_latest_lp_positions,
    get_block_summary,
    get_last_indexed_block,
    get_events,
    get_bonds_by_owner,
    get_all_bonds,
    get_conn,
    get_broker_lp_positions,
)


def _safe_float(v, default=0.0):
    """Sanitize float: replace inf/nan with default."""
    try:
        f = float(v)
        return default if (math.isinf(f) or math.isnan(f)) else f
    except (TypeError, ValueError):
        return default


DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "data", "comprehensive_state.db"),
)


# ═══════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════

@strawberry.type
class LPPosition:
    token_id: int
    liquidity: str
    tick_lower: int
    tick_upper: int
    entry_tick: Optional[int] = None
    entry_price: Optional[float] = None
    mint_block: Optional[int] = None
    is_active: bool = False
    broker_address: Optional[str] = None


@strawberry.type
class BrokerState:
    address: str
    collateral: str
    debt: str
    collateral_value: str
    debt_value: str
    health_factor: float
    lp_positions: List[LPPosition]


@strawberry.type
class MarketState:
    block_number: int
    block_timestamp: int = 0
    market_id: str = ""
    normalization_factor: str = "0"
    total_debt: str = "0"
    last_update_timestamp: int = 0
    index_price: str = "0"


@strawberry.type
class PoolState:
    pool_id: str
    tick: int
    mark_price: float
    liquidity: str
    sqrt_price_x96: str
    token0_balance: Optional[str] = "0"
    token1_balance: Optional[str] = "0"
    fee_growth_global0: Optional[str] = None
    fee_growth_global1: Optional[str] = None


@strawberry.type
class SwapEvent:
    id: int
    block_number: int
    tx_hash: str
    event_name: str
    timestamp: int = 0
    data: Optional[str] = None  # JSON string


@strawberry.type
class Volume:
    volume_usd: float
    swap_count: int
    hours: int = 24


@strawberry.type
class VolumeBar:
    timestamp: int
    volume_usd: float
    swap_count: int


@strawberry.type
class TokenInfo:
    name: str
    symbol: str
    address: str


@strawberry.type
class Infrastructure:
    broker_router: str = ""
    broker_executor: str = ""
    twamm_hook: str = ""
    bond_factory: str = ""
    basis_trade_factory: str = ""
    pool_fee: int = 500
    tick_spacing: int = 5
    pool_manager: str = ""
    v4_quoter: str = ""
    v4_position_manager: str = ""
    v4_position_descriptor: str = ""
    v4_state_view: str = ""
    universal_router: str = ""
    permit2: str = ""


@strawberry.type
class ExternalContracts:
    usdc: str = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    ausdc: str = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
    aave_pool: str = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
    susde: str = "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"
    usdc_whale: str = "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"


@strawberry.type
class RiskParams:
    min_col_ratio: float = 0
    maintenance_margin: float = 0
    liq_close_factor: float = 0
    funding_period_sec: int = 0
    debt_cap: int = 0


@strawberry.type
class MarketInfo:
    collateral: Optional[TokenInfo] = None
    position_token: Optional[TokenInfo] = None
    broker_factory: str = ""
    infrastructure: Optional[Infrastructure] = None
    external_contracts: Optional[ExternalContracts] = None
    risk_params: Optional[RiskParams] = None


@strawberry.type
class IndexerStatus:
    total_block_states: int = 0
    total_events: int = 0
    last_indexed_block: int = 0


@strawberry.type
class TwammOrder:
    order_id: str
    owner: str
    amount_in: str
    sell_rate: str
    expiration: int
    start_epoch: int = 0
    zero_for_one: bool = False
    block_number: int = 0
    tx_hash: str = ""
    is_cancelled: bool = False


@strawberry.type
class Bond:
    broker_address: str
    owner: str
    status: str
    bond_id: int = 0
    created_block: Optional[int] = None
    created_tx: Optional[str] = None
    # ── Enriched fields (server-side RPC, cached per-call) ──
    notional_usd: float = 0.0
    debt_usd: float = 0.0
    free_collateral: float = 0.0
    elapsed_days: int = 0
    maturity_days: int = 0
    remaining_days: int = 0
    maturity_date: str = "—"
    bond_factory: str = ""
    order_id: str = ""
    frozen: bool = False
    has_active_order: bool = False
    is_matured: bool = False


@strawberry.type
class Candle:
    """OHLC price candle from pre-aggregated candle tables."""
    timestamp: int
    index_open: float
    index_high: float
    index_low: float
    index_close: float
    mark_open: float
    mark_high: float
    mark_low: float
    mark_close: float
    volume: float = 0.0
    swap_count: int = 0


@strawberry.type
class LiquidityBin:
    """Aggregated liquidity distribution bin (Uniswap V4 tick math)."""
    price: float
    price_from: float
    price_to: float
    liquidity: float
    amount0: float
    amount1: float


@strawberry.type
class AccountBalances:
    """Server-side batched token balances for a user account."""
    collateral_balance: float  # waUSDC (6 dec)
    position_balance: float   # wRLP  (6 dec)
    usdc_balance: float       # raw USDC (6 dec)


@strawberry.type
class Snapshot:
    block_number: int
    market: Optional[MarketState] = None
    pool: Optional[PoolState] = None
    brokers: List[BrokerState] = strawberry.field(default_factory=list)


@strawberry.type
class RatePoint:
    timestamp: int
    apy: float
    eth_price: Optional[float] = None
    total_debt: Optional[float] = None  # AAVE debt token totalSupply (USD)


@strawberry.type
class EthPricePoint:
    timestamp: int
    price: float


@strawberry.type
class RatesSeries:
    symbol: str
    data: List[RatePoint]


# ═══════════════════════════════════════════════════════════
# Resolvers / Helpers
# ═══════════════════════════════════════════════════════════

RATES_API_BASE = os.getenv("RATES_API_URL", "http://rates-indexer:8080")

def _fetch_rates(symbol: str, resolution: str = "1H", limit: int = 50000,
                 start_date: str = None, end_date: str = None) -> List[RatePoint]:
    """Fetch rates from the rates API (port 8081)."""
    try:
        params = {"symbol": symbol, "resolution": resolution, "limit": str(limit)}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        qs = urllib.parse.urlencode(params)
        url = f"{RATES_API_BASE}/rates?{qs}"
        req = urllib.request.Request(url, headers={"X-API-Key": os.getenv("API_KEY", "")})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return [
            RatePoint(
                timestamp=int(d.get("timestamp", 0)),
                apy=float(d.get("apy", 0)),
                eth_price=float(d["eth_price"]) if d.get("eth_price") else None,
                total_debt=float(d["total_debt"]) if d.get("total_debt") is not None else None,
            )
            for d in data
        ]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to fetch rates for {symbol}: {e}")
        return []


def _fetch_eth_prices(resolution: str = "1D", limit: int = 50000,
                      start_date: str = None, end_date: str = None) -> List[EthPricePoint]:
    """Fetch ETH prices from the rates API (port 8081)."""
    try:
        params = {"resolution": resolution, "limit": str(limit)}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        qs = urllib.parse.urlencode(params)
        url = f"{RATES_API_BASE}/eth-prices?{qs}"
        req = urllib.request.Request(url, headers={"X-API-Key": os.getenv("API_KEY", "")})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return [
            EthPricePoint(
                timestamp=int(d.get("timestamp", 0)),
                price=float(d.get("price", 0)),
            )
            for d in data
        ]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to fetch ETH prices: {e}")
        return []

def _row_to_lp(row: dict) -> LPPosition:
    return LPPosition(
        token_id=row.get('token_id', 0),
        liquidity=str(row.get('liquidity', 0)),
        tick_lower=row.get('tick_lower', 0),
        tick_upper=row.get('tick_upper', 0),
        entry_tick=row.get('entry_tick'),
        entry_price=row.get('entry_price'),
        mint_block=row.get('mint_block'),
        is_active=bool(row.get('is_active', 0)),
        broker_address=row.get('broker_address'),
    )


def _build_snapshot(summary: dict) -> Snapshot:
    block_number = summary.get('block_number', 0)

    # Market
    market = None
    ms_list = summary.get('market_states', [])
    if ms_list:
        ms = ms_list[0]
        market = MarketState(
            block_number=ms.get('block_number', block_number),
            block_timestamp=ms.get('block_timestamp', 0),
            market_id=ms.get('market_id', ''),
            normalization_factor=str(ms.get('normalization_factor', 0)),
            total_debt=str(ms.get('total_debt', 0)),
            last_update_timestamp=ms.get('last_update_timestamp', 0),
            index_price=str(ms.get('index_price', 0)),
        )

    # Pool
    pool = None
    ps_list = summary.get('pool_states', [])
    if ps_list:
        ps = ps_list[0]
        pool = PoolState(
            pool_id=ps.get('pool_id', ''),
            tick=ps.get('tick', 0),
            mark_price=ps.get('mark_price', 0.0),
            liquidity=str(ps.get('liquidity', 0)),
            sqrt_price_x96=str(ps.get('sqrt_price_x96', 0)),
            token0_balance=str(ps.get('token0_balance', 0)),
            token1_balance=str(ps.get('token1_balance', 0)),
            fee_growth_global0=str(ps.get('fee_growth_global0')) if ps.get('fee_growth_global0') else None,
            fee_growth_global1=str(ps.get('fee_growth_global1')) if ps.get('fee_growth_global1') else None,
        )

    # Brokers with embedded LP positions
    brokers = []
    bp_list = summary.get('broker_positions', [])
    for bp in bp_list:
        addr = bp.get('broker_address', '')
        lp_rows = get_broker_lp_positions(addr)
        lps = [_row_to_lp(r) for r in lp_rows]
        brokers.append(BrokerState(
            address=addr,
            collateral=str(bp.get('collateral', 0)),
            debt=str(bp.get('debt', 0)),
            collateral_value=str(bp.get('collateral_value', 0)),
            debt_value=str(bp.get('debt_value', 0)),
            health_factor=_safe_float(bp.get('health_factor', 0.0)),
            lp_positions=lps,
        ))

    return Snapshot(
        block_number=block_number,
        market=market,
        pool=pool,
        brokers=brokers,
    )


# ── Volume computation from DB ─────────────────────────────

def _compute_volume(hours: int = 24) -> Volume:
    """Compute trade volume from Swap events in DB."""
    try:
        with get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT MAX(timestamp) FROM events WHERE event_name='Swap'")
            max_ts = c.fetchone()[0]
            if not max_ts:
                return Volume(volume_usd=0, swap_count=0, hours=hours)

            cutoff = max_ts - (hours * 3600)
            c.execute("""
                SELECT COUNT(*),
                       SUM(ABS(CAST(data->>'amount1' AS BIGINT)))
                FROM events
                WHERE event_name='Swap' AND timestamp >= %s
            """, (cutoff,))
            count, vol_raw = c.fetchone()

            return Volume(
                volume_usd=(vol_raw or 0) / 1e6,
                swap_count=count or 0,
                hours=hours,
            )
    except Exception:
        return Volume(volume_usd=0, swap_count=0, hours=hours)


def _compute_volume_history(hours: int = 168, bucket_hours: int = 1) -> List[VolumeBar]:
    """Compute volume history bars."""
    try:
        with get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT MAX(timestamp) FROM events WHERE event_name='Swap'")
            max_ts = c.fetchone()[0]
            if not max_ts:
                return []

            cutoff = max_ts - (hours * 3600)
            bucket_sec = bucket_hours * 3600
            c.execute("""
                SELECT (timestamp / %s) * %s as bucket_ts,
                       COUNT(*),
                       SUM(ABS(CAST(data->>'amount1' AS BIGINT)))
                FROM events
                WHERE event_name='Swap' AND timestamp >= %s
                GROUP BY bucket_ts
                ORDER BY bucket_ts
            """, (bucket_sec, bucket_sec, cutoff))
            rows = c.fetchall()

            return [VolumeBar(
                timestamp=row[0],
                swap_count=row[1],
                volume_usd=(row[2] or 0) / 1e6,
            ) for row in rows]
    except Exception:
        return []


# ── Market info (cached, from app state + RPC) ─────────────

_market_info_cache = {"data": None, "ts": 0}


def _get_market_info() -> Optional[MarketInfo]:
    """Get market info from cache or build from config."""
    global _market_info_cache
    now = time.monotonic()

    # Cache for 60s — this data changes extremely rarely (only on redeploy)
    if _market_info_cache["data"] and (now - _market_info_cache["ts"]) < 60:
        return _market_info_cache["data"]

    try:
        # Read from the REST endpoint's cached response file
        # or build from market_config environment
        from api.indexer_api import app
        market_config = getattr(app.state, "market_config", None)
        if not market_config:
            return None

        # Re-read infrastructure addresses from deployment.json to avoid stale env cache
        _deploy_json_path = os.environ.get("CONFIG_FILE", "/config/deployment.json")
        if not os.path.exists(_deploy_json_path):
            # Try the default dev path
            _deploy_json_path = os.path.join(os.path.dirname(__file__), "../../docker/deployment.json")
        if os.path.exists(_deploy_json_path):
            try:
                with open(_deploy_json_path) as _f:
                    _deploy = json.load(_f)
                _infra_keys = (
                    "broker_router", "broker_executor", "bond_factory", "basis_trade_factory",
                    "v4_quoter", "broker_factory", "swap_router", "pool_manager",
                    "v4_position_manager", "v4_position_descriptor", "v4_state_view",
                    "universal_router", "permit2",
                )
                for _k in _infra_keys:
                    if _k in _deploy:
                        market_config[_k] = _deploy[_k]
                # Also load external_contracts if present
                if "external_contracts" in _deploy:
                    market_config["external_contracts"] = _deploy["external_contracts"]
            except Exception:
                pass  # Fall through to cached values

        import urllib.request as urlreq

        rpc_url = market_config.get("rpc_url", os.environ.get("RPC_URL", "http://localhost:8545"))
        rld_core = market_config.get("rld_core", "")
        market_id = market_config.get("market_id", "")
        col_token = market_config.get("collateral_token", "")
        pos_token = market_config.get("position_token", "")

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

        col_name = decode_string(eth_call(col_token, "0x06fdde03"))
        col_symbol = decode_string(eth_call(col_token, "0x95d89b41"))
        pos_name = decode_string(eth_call(pos_token, "0x06fdde03"))
        pos_symbol = decode_string(eth_call(pos_token, "0x95d89b41"))

        selector = "0x6a6ae218"
        padded_id = market_id.replace("0x", "").zfill(64)
        config_data = eth_call(rld_core, selector + padded_id)

        # Build external_contracts from deployment.json (with sane defaults)
        ext = market_config.get("external_contracts", {})
        external = ExternalContracts(
            usdc=ext.get("usdc", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
            ausdc=ext.get("ausdc", "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"),
            aave_pool=ext.get("aave_pool", "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"),
            susde=ext.get("susde", "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"),
            usdc_whale=ext.get("usdc_whale", "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"),
        )

        result = MarketInfo(
            collateral=TokenInfo(name=col_name, symbol=col_symbol, address=col_token),
            position_token=TokenInfo(name=pos_name, symbol=pos_symbol, address=pos_token),
            broker_factory=market_config.get("broker_factory", ""),
            infrastructure=Infrastructure(
                broker_router=market_config.get("broker_router", ""),
                broker_executor=market_config.get("broker_executor", ""),
                twamm_hook=market_config.get("twamm_hook", ""),
                bond_factory=market_config.get("bond_factory", ""),
                basis_trade_factory=market_config.get("basis_trade_factory", ""),
                pool_manager=market_config.get("pool_manager", "0x000000000004444c5dc75cB358380D2e3dE08A90"),
                v4_quoter=market_config.get("v4_quoter", "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203"),
                v4_position_manager=market_config.get("v4_position_manager", "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"),
                v4_position_descriptor=market_config.get("v4_position_descriptor", "0xd1428ba554f4c8450b763a0b2040a4935c63f06c"),
                v4_state_view=market_config.get("v4_state_view", "0x7ffe42c4a5deea5b0fec41c94c136cf115597227"),
                universal_router=market_config.get("universal_router", "0x66a9893cc07d91d95644aedd05d03f95e1dba8af"),
                permit2=market_config.get("permit2", "0x000000000022D473030F116dDEE9F6B43aC78BA3"),
            ),
            external_contracts=external,
            risk_params=RiskParams(
                min_col_ratio=decode_uint(config_data, 0) / 1e18,
                maintenance_margin=decode_uint(config_data, 1) / 1e18,
                liq_close_factor=decode_uint(config_data, 2) / 1e18,
                funding_period_sec=decode_uint(config_data, 3),
                debt_cap=decode_uint(config_data, 4),
            ),
        )
        _market_info_cache = {"data": result, "ts": now}
        return result
    except Exception as e:
        print(f"[GraphQL] market_info error: {e}")
        return None


# ── TWAMM orders from events DB ────────────────────────────

def _get_twamm_orders(owner: Optional[str] = None) -> List[TwammOrder]:
    """Get TWAMM orders from SubmitOrder/CancelOrder events in DB."""
    try:
        with get_conn() as conn:
            c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Find all SubmitOrder events
            c.execute("""
                SELECT * FROM events
                WHERE event_name = 'SubmitOrder'
                ORDER BY block_number DESC
            """)
            submit_rows = c.fetchall()

            # Find all CancelOrder events
            c.execute("""
                SELECT DISTINCT
                    COALESCE(data->>'order_id', data->>'orderId') as order_id
                FROM events WHERE event_name = 'CancelOrder'
            """)
            cancelled_ids = {row['order_id'] for row in c.fetchall() if row.get('order_id')}

        orders = []
        for row in submit_rows:
            d = json.loads(row["data"]) if isinstance(row["data"], str) else (row["data"] or {})
            order_owner = d.get("owner", "")
            if owner and order_owner.lower() != owner.lower():
                continue

            order_id = d.get("order_id", "") or d.get("orderId", "")
            orders.append(TwammOrder(
                order_id=order_id,
                owner=order_owner,
                amount_in=str(d.get("amount_in", 0) or d.get("amountIn", 0)),
                sell_rate=str(d.get("sell_rate", 0) or d.get("sellRate", 0)),
                expiration=int(d.get("expiration", 0)),
                start_epoch=int(d.get("start_epoch", 0) or d.get("startEpoch", 0)),
                zero_for_one=bool(d.get("zero_for_one", False) if "zero_for_one" in d else d.get("zeroForOne", False)),
                block_number=row["block_number"],
                tx_hash=row["tx_hash"],
                is_cancelled=order_id in cancelled_ids,
            ))

        return orders
    except Exception as e:
        print(f"[GraphQL] twamm_orders error: {e}")
        return []


# ── Indexer status ─────────────────────────────────────────

def _get_status() -> IndexerStatus:
    try:
        with get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM market_state")
            total_blocks = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM events")
            total_events = c.fetchone()[0]
        return IndexerStatus(
            total_block_states=total_blocks,
            total_events=total_events,
            last_indexed_block=get_last_indexed_block(),
        )
    except Exception:
        return IndexerStatus()


# ── Bond enrichment (ports batched RPC logic from indexer_api) ─────────────────

def _enrich_bonds(rows: list, rpc_url: str) -> list:
    """
    Enrich bond DB rows with live on-chain data (batched JSON-RPC).
    Returns list of Bond strawberry objects with all 20 fields populated.
    """
    import urllib.request as urlreq
    import math
    from datetime import datetime, timezone

    def _eth_batch(batch):
        payload = json.dumps(batch).encode()
        req = urlreq.Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
        with urlreq.urlopen(req, timeout=10) as resp:
            return {r["id"]: r.get("result", "0x") for r in json.loads(resp.read())}

    def pad_addr(addr):
        return addr.lower().replace("0x", "").zfill(64)

    result_bonds = []
    active = [r for r in rows if r.get("status") == "active"]
    closed = [r for r in rows if r.get("status") != "active"]

    # Closed bonds — no RPC needed
    for r in closed:
        notional = int(r.get("notional") or 0) / 1e6
        result_bonds.append(Bond(
            broker_address=r.get("broker_address", ""),
            owner=r.get("owner", ""),
            status=r.get("status", "closed"),
            bond_id=int(r.get("broker_address", "0x")[-4:], 16) % 10000,
            created_block=r.get("created_block"),
            created_tx=r.get("created_tx"),
            notional_usd=notional, debt_usd=0, free_collateral=0,
            elapsed_days=0, maturity_days=0, remaining_days=0,
            maturity_date="—", bond_factory=r.get("bond_factory", ""),
            order_id="0x" + "0" * 64,
            frozen=False, has_active_order=False, is_matured=True,
        ))

    if not active:
        return result_bonds

    # Phase 1 batch: core, marketId, collateral, frozen, twammOrder
    SEL = {
        "core": "0xf8f9da28", "market_id": "0x1dce43f9",
        "collateral": "0xb2016bd4", "frozen": "0x054f7d9c",
        "twamm_order": "0xe4d7907d",
        "get_position": "0x713f9507", "get_market_state": "0x544e4c74",
        "balance_of": "0x70a08231",
    }
    batch1 = []
    for i, b in enumerate(active):
        addr = b["broker_address"]
        bi = i * 5
        for j, sel in enumerate([SEL["core"], SEL["market_id"], SEL["collateral"], SEL["frozen"], SEL["twamm_order"]]):
            batch1.append({"jsonrpc": "2.0", "id": bi + j, "method": "eth_call", "params": [{"to": addr, "data": sel}, "latest"]})
    block_id = len(active) * 5
    batch1.append({"jsonrpc": "2.0", "id": block_id, "method": "eth_getBlockByNumber", "params": ["latest", False]})

    try:
        r1 = _eth_batch(batch1)
    except Exception:
        # RPC failed — return unenriched
        for r in active:
            result_bonds.append(Bond(
                broker_address=r.get("broker_address", ""), owner=r.get("owner", ""),
                status=r.get("status", "active"),
                bond_id=int(r.get("broker_address", "0x")[-4:], 16) % 10000,
                created_block=r.get("created_block"), created_tx=r.get("created_tx"),
            ))
        return result_bonds

    block_data = r1.get(block_id, {})
    block_ts = int(block_data.get("timestamp", "0x0"), 16) if isinstance(block_data, dict) else 0

    # Parse phase-1 results
    broker_meta = {}
    batch2 = []
    for i, b in enumerate(active):
        bi = i * 5
        core = "0x" + str(r1.get(bi, "0x" + "0" * 64))[-40:]
        mid = r1.get(bi + 1, "0x" + "0" * 64)
        col = "0x" + str(r1.get(bi + 2, "0x" + "0" * 64))[-40:]
        frozen_hex = r1.get(bi + 3, "0x0")
        frozen = int(frozen_hex, 16) != 0 if frozen_hex else False
        twamm_raw = r1.get(bi + 4, "")
        exp = 0; oid = "0x" + "0" * 64
        if twamm_raw and len(twamm_raw) > 2:
            rb = bytes.fromhex(twamm_raw.replace("0x", ""))
            if len(rb) >= 288:
                exp = int.from_bytes(rb[192:224], "big")
                oid = "0x" + rb[256:288].hex()
        broker_meta[b["broker_address"]] = {"core": core, "mid": mid, "col": col, "frozen": frozen, "exp": exp, "oid": oid}

        # Phase 2 batch calls
        pos_data = SEL["get_position"] + mid.replace("0x", "").zfill(64) + pad_addr(b["broker_address"])
        ms_data  = SEL["get_market_state"] + mid.replace("0x", "").zfill(64)
        bal_data = SEL["balance_of"] + pad_addr(b["broker_address"])
        for j, (to, data) in enumerate([(core, pos_data), (core, ms_data), (col, bal_data)]):
            batch2.append({"jsonrpc": "2.0", "id": i * 3 + j, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]})

    try:
        r2 = _eth_batch(batch2)
    except Exception:
        r2 = {}

    # Assemble enriched bonds
    for i, b in enumerate(active):
        meta = broker_meta[b["broker_address"]]
        pos_raw = r2.get(i * 3, "0x" + "0" * 64)
        ms_raw  = r2.get(i * 3 + 1, "0x" + "0" * 64)
        bal_raw = r2.get(i * 3 + 2, "0x" + "0" * 64)

        debt_principal = int(pos_raw[-32:], 16) if pos_raw and len(pos_raw) > 2 else 0
        nf = int(ms_raw[2:66], 16) if ms_raw and len(ms_raw) > 2 else int(1e18)
        col_balance = int(bal_raw, 16) if bal_raw and len(bal_raw) > 2 else 0

        true_debt = (debt_principal * nf) // (10 ** 18)
        debt_usd  = true_debt / 1e6
        free_col  = col_balance / 1e6
        notional  = int(b.get("notional") or 0) / 1e6

        has_order = meta["oid"] != "0x" + "0" * 64
        rem_sec   = max(0, meta["exp"] - block_ts)
        rem_days  = max(0, math.ceil(rem_sec / 86400))
        is_matured = has_order and rem_sec <= 0

        # Duration
        dur_hours = int(b.get("duration_hours", 0) or 0)
        mat_days  = math.ceil(dur_hours / 24) if dur_hours > 0 else rem_days

        # Elapsed
        created_ts = int(b.get("created_ts") or 0)
        elapsed_sec = max(0, block_ts - created_ts) if created_ts else 0
        el_days = elapsed_sec // 86400

        mat_date = "—"
        if meta["exp"] > 0:
            try:
                mat_date = datetime.fromtimestamp(meta["exp"], tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                pass

        result_bonds.append(Bond(
            broker_address=b.get("broker_address", ""),
            owner=b.get("owner", ""),
            status=b.get("status", "active"),
            bond_id=int(b.get("broker_address", "0x")[-4:], 16) % 10000,
            created_block=b.get("created_block"),
            created_tx=b.get("created_tx"),
            notional_usd=notional, debt_usd=debt_usd, free_collateral=free_col,
            elapsed_days=el_days, maturity_days=mat_days,
            remaining_days=rem_days, maturity_date=mat_date,
            bond_factory=b.get("bond_factory", ""),
            order_id=meta["oid"], frozen=meta["frozen"],
            has_active_order=has_order, is_matured=is_matured,
        ))

    return result_bonds


# ── Chart resolver (reads pre-aggregated candle tables) ────────────────────

_CANDLE_TABLE = {
    "5M":  "price_candles_5m",
    "15M": "price_candles_15m",
    "1H":  "price_candles_1h",
    "4H":  "price_candles_4h",
    "1D":  "price_candles_1d",
    "1W":  "price_candles_1d",
}

def _get_chart(
    resolution: str = "1H",
    limit: int = 1000,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
) -> List["Candle"]:
    table = _CANDLE_TABLE.get(resolution.upper(), "price_candles_1h")
    try:
        with get_conn() as conn:
            c = conn.cursor()
            q = f"SELECT * FROM {table} WHERE 1=1"
            params = []
            if start_time:
                q += " AND ts >= %s"; params.append(start_time)
            if end_time:
                q += " AND ts <= %s"; params.append(end_time)
            q += " ORDER BY ts DESC LIMIT %s"; params.append(limit)
            c.execute(q, params)
            rows = c.fetchall()
        return [
            Candle(
                timestamp=row[0],
                index_open=_safe_float(row[6]),  index_high=_safe_float(row[7]),
                index_low=_safe_float(row[8]),   index_close=_safe_float(row[9]),
                mark_open=_safe_float(row[2]),   mark_high=_safe_float(row[3]),
                mark_low=_safe_float(row[4]),    mark_close=_safe_float(row[5]),
                volume=_safe_float(row[10]),     swap_count=int(row[11] or 0),
            )
            for row in reversed(rows)
        ]
    except Exception as e:
        print(f"[GraphQL] chart error: {e}")
        return []


# ── LiquidityBins resolver ─────────────────────────────────────────────────

def _get_liquidity_bins(num_bins: int = 60) -> List["LiquidityBin"]:
    """Compute LP distribution bins from lp_positions + current pool price."""
    try:
        with get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT tick_lower, tick_upper, liquidity FROM lp_positions WHERE liquidity > 0")
            positions = c.fetchall()
            c.execute("SELECT mark_price FROM pool_state LIMIT 1")
            row = c.fetchone()
            if not row:
                return []
            current_price = float(row[0])

        if not positions or not current_price:
            return []

        min_p = current_price * 0.5
        max_p = current_price * 2.0
        bin_w = (max_p - min_p) / num_bins
        bins = []
        for i in range(num_bins):
            p_from = min_p + i * bin_w
            p_to   = min_p + (i + 1) * bin_w
            liq = 0.0
            for (tl, tu, liq_raw) in positions:
                pL = 1.0001 ** tl
                pH = 1.0001 ** tu
                if pH > p_from and pL < p_to:
                    liq += float(liq_raw)
            sa, sb = p_from ** 0.5, p_to ** 0.5
            sp = max(sa, min(current_price ** 0.5, sb))
            a0 = liq * (1 / sp - 1 / sb) / 1e6 if sp < sb else 0
            a1 = liq * (sp - sa) / 1e6 if sp > sa else 0
            bins.append(LiquidityBin(
                price=(p_from + p_to) / 2,
                price_from=p_from, price_to=p_to,
                liquidity=liq,
                amount0=max(0, a0), amount1=max(0, a1),
            ))
        return bins
    except Exception as e:
        print(f"[GraphQL] liquidity_bins error: {e}")
        return []


# ── AccountBalances resolver (batched RPC for 3 tokens) ──────────────────

def _get_account_balances(owner: str, rpc_url: str) -> Optional["AccountBalances"]:
    """Fetch collateral, position token, and raw USDC balances server-side."""
    import urllib.request as urlreq
    try:
        from api.indexer_api import app
        mc = getattr(app.state, "market_config", {}) or {}
    except Exception:
        mc = {}

    col_addr  = mc.get("collateral_token", "")
    pos_addr  = mc.get("position_token", "")
    ext = mc.get("external_contracts", {})
    usdc_addr = ext.get("usdc", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")

    if not col_addr or not pos_addr:
        return None

    def pad(addr):
        return addr.lower().replace("0x", "").zfill(64)

    SEL_BAL = "0x70a08231"
    data = pad(owner)
    batch = [
        {"jsonrpc": "2.0", "id": 0, "method": "eth_call", "params": [{"to": col_addr,  "data": SEL_BAL + data}, "latest"]},
        {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": pos_addr,  "data": SEL_BAL + data}, "latest"]},
        {"jsonrpc": "2.0", "id": 2, "method": "eth_call", "params": [{"to": usdc_addr, "data": SEL_BAL + data}, "latest"]},
    ]
    payload = json.dumps(batch).encode()
    req = urlreq.Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
    with urlreq.urlopen(req, timeout=5) as resp:
        res = {r["id"]: r.get("result", "0x") for r in json.loads(resp.read())}

    def parse(hex_val):
        return int(hex_val, 16) / 1e6 if hex_val and hex_val != "0x" else 0.0

    return AccountBalances(
        collateral_balance=parse(res.get(0, "0x")),
        position_balance=parse(res.get(1, "0x")),
        usdc_balance=parse(res.get(2, "0x")),
    )


# ═══════════════════════════════════════════════════════════
# Query Root
# ═══════════════════════════════════════════════════════════

@strawberry.type
class Query:
    @strawberry.field(description="Latest indexed block snapshot with all market/pool/broker data.")
    def latest(self) -> Snapshot:
        summary = get_latest_summary()
        if 'error' in summary:
            return Snapshot(block_number=0)
        return _build_snapshot(summary)

    @strawberry.field(description="Snapshot at a specific block.")
    def block(self, block_number: int) -> Snapshot:
        summary = get_block_summary(block_number)
        return _build_snapshot(summary)

    @strawberry.field(description="All LP positions for a specific broker (latest block).")
    def lp_positions(self, broker_address: str) -> List[LPPosition]:
        rows = get_broker_lp_positions(broker_address)
        if rows:
            return [_row_to_lp(r) for r in rows]
        # Fallback: event-driven indexer tracks LP positions from events.
        # If no positions found in DB, return empty (no RPC scan needed).
        return []

    @strawberry.field(description="All LP positions across all brokers (latest block).")
    def all_lp_positions(self) -> List[LPPosition]:
        rows = get_all_latest_lp_positions()
        return [_row_to_lp(r) for r in rows]

    @strawberry.field(description="24h trade volume computed from Swap events.")
    def volume(self, hours: int = 24) -> Volume:
        return _compute_volume(hours)

    @strawberry.field(description="Volume history bars for charting.")
    def volume_history(self, hours: int = 168, bucket_hours: int = 1) -> List[VolumeBar]:
        return _compute_volume_history(hours, bucket_hours)

    @strawberry.field(description="Recent events (Swap, etc).")
    def events(self, limit: int = 20, event_name: Optional[str] = None) -> List[SwapEvent]:
        rows = get_events(event_name=event_name, limit=limit)
        return [SwapEvent(
            id=r.get("id", 0),
            block_number=r.get("block_number", 0),
            tx_hash=r.get("tx_hash", ""),
            event_name=r.get("event_name", ""),
            timestamp=r.get("timestamp", 0),
            data=json.dumps(r.get("data")) if r.get("data") else None,
        ) for r in rows]

    @strawberry.field(description="Market info: tokens, infrastructure, risk params. Cached 60s.")
    def market_info(self) -> Optional[MarketInfo]:
        return _get_market_info()

    @strawberry.field(description="Indexer status: block counts, last indexed block.")
    def status(self) -> IndexerStatus:
        return _get_status()

    @strawberry.field(description="TWAMM orders from event logs. Replaces browser RPC scanning.")
    def twamm_orders(self, owner: Optional[str] = None) -> List[TwammOrder]:
        return _get_twamm_orders(owner)

    @strawberry.field(description="Bond positions with full enrichment (server-side batched RPC). Filter by owner/status.")
    def bonds(
        self,
        owner: Optional[str] = None,
        status: Optional[str] = None,
        enrich: bool = True,
    ) -> List[Bond]:
        if owner:
            rows = get_bonds_by_owner(owner, status)
        else:
            rows = get_all_bonds(status, 100)

        if not enrich:
            # Fast path: return basic fields only (no RPC)
            return [Bond(
                broker_address=r.get("broker_address", ""),
                owner=r.get("owner", ""),
                status=r.get("status", "active"),
                bond_id=int(r.get("broker_address", "0x0")[-4:], 16) % 10000,
                created_block=r.get("created_block"),
                created_tx=r.get("created_tx"),
                notional_usd=float(r.get("notional_usd", 0)),
            ) for r in rows]

        # Full enrichment path: batched on-chain RPC
        try:
            from api.indexer_api import app
            mc = getattr(app.state, "market_config", {}) or {}
        except Exception:
            mc = {}
        rpc_url = mc.get("rpc_url", os.environ.get("RPC_URL", "http://localhost:8545"))
        return _enrich_bonds(list(rows), rpc_url)

    @strawberry.field(description="OHLC price candles from pre-aggregated tables. resolution: 5M|15M|1H|4H|1D|1W")
    def chart(
        self,
        resolution: str = "1H",
        limit: int = 1000,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        return _get_chart(resolution, limit, start_time, end_time)

    @strawberry.field(description="LP liquidity distribution bins (Uniswap V4 tick math). Used by pool liquidity chart.")
    def liquidity_bins(self, num_bins: int = 60) -> List[LiquidityBin]:
        return _get_liquidity_bins(num_bins)

    @strawberry.field(description="Server-side batched token balances for a wallet. Returns collateral, position token, and USDC.")
    def balances(self, owner: str) -> Optional[AccountBalances]:
        try:
            from api.indexer_api import app
            mc = getattr(app.state, "market_config", {}) or {}
        except Exception:
            mc = {}
        rpc_url = mc.get("rpc_url", os.environ.get("RPC_URL", "http://localhost:8545"))
        try:
            return _get_account_balances(owner, rpc_url)
        except Exception as e:
            print(f"[GraphQL] balances error: {e}")
            return None

    @strawberry.field(description="Lending rates for one or more symbols. Proxies to rates API.")
    def rates(
        self,
        symbols: List[str],
        resolution: str = "1H",
        limit: int = 50000,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[RatesSeries]:
        results = []
        for symbol in symbols:
            data = _fetch_rates(symbol, resolution, limit, start_date, end_date)
            results.append(RatesSeries(symbol=symbol.upper(), data=data))
        return results

    @strawberry.field(description="ETH price history. Proxies to rates API.")
    def eth_prices(
        self,
        resolution: str = "1D",
        limit: int = 50000,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[EthPricePoint]:
        return _fetch_eth_prices(resolution, limit, start_date, end_date)


schema = strawberry.Schema(query=Query)
