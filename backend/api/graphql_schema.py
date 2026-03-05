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
import sqlite3
import time
from typing import Optional, List
from strawberry.types import Info

from db.comprehensive import (
    get_latest_summary,
    get_lp_positions,
    get_all_latest_lp_positions,
    get_block_summary,
    get_last_indexed_block,
    get_events,
    get_bonds_by_owner,
    get_all_bonds,
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
    notional_usd: float = 0
    bond_id: int = 0
    created_block: Optional[int] = None
    created_tx: Optional[str] = None


@strawberry.type
class Snapshot:
    block_number: int
    market: Optional[MarketState] = None
    pool: Optional[PoolState] = None
    brokers: List[BrokerState] = strawberry.field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# Resolvers / Helpers
# ═══════════════════════════════════════════════════════════

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
        lp_rows = get_lp_positions(addr, block_number)
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
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT MAX(timestamp) FROM events WHERE event_name='Swap'")
        max_ts = c.fetchone()[0]
        if not max_ts:
            conn.close()
            return Volume(volume_usd=0, swap_count=0, hours=hours)

        cutoff = max_ts - (hours * 3600)
        c.execute('''
            SELECT COUNT(*),
                   SUM(ABS(CAST(json_extract(data, '$.amount1') AS INTEGER)))
            FROM events
            WHERE event_name='Swap' AND timestamp >= ?
        ''', (cutoff,))
        count, vol_raw = c.fetchone()
        conn.close()

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
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT MAX(timestamp) FROM events WHERE event_name='Swap'")
        max_ts = c.fetchone()[0]
        if not max_ts:
            conn.close()
            return []

        cutoff = max_ts - (hours * 3600)
        bucket_sec = bucket_hours * 3600
        c.execute('''
            SELECT (timestamp / ?) * ? as bucket_ts,
                   COUNT(*),
                   SUM(ABS(CAST(json_extract(data, '$.amount1') AS INTEGER)))
            FROM events
            WHERE event_name='Swap' AND timestamp >= ?
            GROUP BY bucket_ts
            ORDER BY bucket_ts
        ''', (bucket_sec, bucket_sec, cutoff))
        rows = c.fetchall()
        conn.close()

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

        result = MarketInfo(
            collateral=TokenInfo(name=col_name, symbol=col_symbol, address=col_token),
            position_token=TokenInfo(name=pos_name, symbol=pos_symbol, address=pos_token),
            broker_factory=market_config.get("broker_factory", ""),
            infrastructure=Infrastructure(
                broker_router=market_config.get("broker_router", ""),
                broker_executor=market_config.get("broker_executor", ""),
                twamm_hook=market_config.get("twamm_hook", ""),
                bond_factory=market_config.get("bond_factory", ""),
                pool_manager=market_config.get("pool_manager", "0x000000000004444c5dc75cB358380D2e3dE08A90"),
                v4_quoter=market_config.get("v4_quoter", "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203"),
                v4_position_manager=market_config.get("v4_position_manager", "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"),
                v4_position_descriptor=market_config.get("v4_position_descriptor", "0xd1428ba554f4c8450b763a0b2040a4935c63f06c"),
                v4_state_view=market_config.get("v4_state_view", "0x7ffe42c4a5deea5b0fec41c94c136cf115597227"),
                universal_router=market_config.get("universal_router", "0x66a9893cc07d91d95644aedd05d03f95e1dba8af"),
                permit2=market_config.get("permit2", "0x000000000022D473030F116dDEE9F6B43aC78BA3"),
            ),
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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

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
                COALESCE(json_extract(data, '$.order_id'), json_extract(data, '$.orderId')) as order_id
            FROM events WHERE event_name = 'CancelOrder'
        """)
        cancelled_ids = {row[0] for row in c.fetchall() if row[0]}

        conn.close()

        orders = []
        for row in submit_rows:
            d = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
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
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM block_state")
        total_blocks = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM events")
        total_events = c.fetchone()[0]
        conn.close()
        return IndexerStatus(
            total_block_states=total_blocks,
            total_events=total_events,
            last_indexed_block=get_last_indexed_block(),
        )
    except Exception:
        return IndexerStatus()


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
        rows = get_lp_positions(broker_address)
        if rows:
            return [_row_to_lp(r) for r in rows]
        # Fallback: live RPC query for brokers not in tracked_brokers
        try:
            from indexers.comprehensive import ComprehensiveIndexer
            # Build a lightweight indexer just for LP queries
            rpc = os.getenv("RPC_URL", "http://host.docker.internal:8545")
            config_file = os.getenv("CONFIG_FILE", "/config/deployment.json")
            with open(config_file) as f:
                cfg = json.load(f)
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider(rpc))
            posm_addr = cfg.get("v4_position_manager")
            state_view_addr = cfg.get("v4_state_view")
            if not posm_addr:
                return []
            # Use the indexer's POSM ABI and methods
            from indexers.comprehensive import POSM_ABI
            posm = w3.eth.contract(
                address=Web3.to_checksum_address(posm_addr),
                abi=POSM_ABI
            )
            # Scan Transfer events to this broker
            transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()
            broker_padded = '0x' + broker_address.lower().replace('0x', '').zfill(64)
            logs = w3.eth.get_logs({
                'fromBlock': 0,
                'toBlock': 'latest',
                'address': posm.address,
                'topics': [transfer_topic, None, broker_padded],
            })
            candidate_ids = list(set(
                int(log['topics'][3].hex() if hasattr(log['topics'][3], 'hex') else log['topics'][3], 16)
                for log in logs if len(log['topics']) > 3
            ))
            if not candidate_ids:
                return []
            # Get active token ID
            from indexers.comprehensive import PRIME_BROKER_ABI
            broker_contract = w3.eth.contract(
                address=Web3.to_checksum_address(broker_address),
                abi=PRIME_BROKER_ABI + [{"inputs": [], "name": "activeTokenId",
                    "outputs": [{"name": "", "type": "uint256"}],
                    "stateMutability": "view", "type": "function"}]
            )
            try:
                active_token_id = broker_contract.functions.activeTokenId().call()
            except Exception:
                active_token_id = 0
            positions = []
            for token_id in candidate_ids:
                try:
                    owner = posm.functions.ownerOf(token_id).call()
                    if owner.lower() != broker_address.lower():
                        continue
                    liquidity = posm.functions.getPositionLiquidity(token_id).call()
                    if liquidity == 0:
                        continue
                    tick_lower = 0
                    tick_upper = 0
                    try:
                        info = posm.functions.positionInfo(token_id).call()
                        # Decode position info (same as indexer)
                        info_bytes = bytes.fromhex(info.hex() if hasattr(info, 'hex') else str(info).replace('0x', ''))
                        tick_lower_raw = int.from_bytes(info_bytes[25:28], 'big')
                        tick_upper_raw = int.from_bytes(info_bytes[28:31], 'big')
                        tick_lower = tick_lower_raw - 0x1000000 if tick_lower_raw >= 0x800000 else tick_lower_raw
                        tick_upper = tick_upper_raw - 0x1000000 if tick_upper_raw >= 0x800000 else tick_upper_raw
                    except Exception:
                        pass
                    # Entry price from mint block
                    entry_price = None
                    mint_block = None
                    for log in logs:
                        if len(log['topics']) > 3:
                            tid = int(log['topics'][3].hex() if hasattr(log['topics'][3], 'hex') else log['topics'][3], 16)
                            if tid == token_id:
                                mint_block = log['blockNumber']
                                break
                    if mint_block and state_view_addr:
                        try:
                            from indexers.comprehensive import STATE_VIEW_ABI
                            sv = w3.eth.contract(
                                address=Web3.to_checksum_address(state_view_addr),
                                abi=STATE_VIEW_ABI
                            )
                            pool_id = cfg.get("pool_id")
                            if pool_id:
                                pool_id_bytes = bytes.fromhex(pool_id.replace('0x', ''))
                                slot0 = sv.functions.getSlot0(pool_id_bytes).call(block_identifier=mint_block)
                                entry_price = math.pow(1.0001, slot0[1])
                        except Exception:
                            pass
                    positions.append(LPPosition(
                        token_id=token_id,
                        liquidity=str(liquidity),
                        tick_lower=tick_lower,
                        tick_upper=tick_upper,
                        entry_tick=None,
                        entry_price=entry_price,
                        mint_block=mint_block,
                        is_active=(token_id == active_token_id),
                        broker_address=broker_address.lower(),
                    ))
                except Exception:
                    continue
            return positions
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Live LP query failed: {e}")
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

    @strawberry.field(description="Bond positions. Optionally filtered by owner.")
    def bonds(self, owner: Optional[str] = None, status: Optional[str] = None) -> List[Bond]:
        if owner:
            rows = get_bonds_by_owner(owner, status)
        else:
            rows = get_all_bonds(status, 100)
        return [Bond(
            broker_address=r.get("broker_address", ""),
            owner=r.get("owner", ""),
            status=r.get("status", "active"),
            notional_usd=float(r.get("notional_usd", 0)),
            bond_id=int(r.get("broker_address", "0x0")[-4:], 16) % 10000,
            created_block=r.get("created_block"),
            created_tx=r.get("created_tx"),
        ) for r in rows]


schema = strawberry.Schema(query=Query)
