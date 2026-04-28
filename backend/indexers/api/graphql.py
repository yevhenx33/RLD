"""
api/graphql.py — Strawberry GraphQL API serving indexer data.

Single process — reads from asyncpg pool. All resolvers: pure SELECT.
Served at: /graphql  (Strawberry + FastAPI)
Health at: /healthz
"""
import os
import json
import math
import ipaddress
import logging
from typing import Optional, List, Dict, Any
import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON
from fastapi import FastAPI, Header, Query as FastAPIQuery, Request
from fastapi.responses import JSONResponse
import asyncpg
import db

async def get_pool() -> asyncpg.Pool:
    return await db.get_pool(
        dsn=os.environ.get("DATABASE_URL"),
        min_size=2,
        max_size=10,
    )


# ── Types ──────────────────────────────────────────────────────────────────

@strawberry.type
class Market:
    market_id: str
    broker_factory: str
    mock_oracle: str
    twamm_hook: str
    wausdc: str
    wrlp: str
    pool_id: str
    pool_fee: int
    tick_spacing: int
    min_col_ratio: str
    maintenance_margin: str
    debt_cap: str
    normalization_factor: Optional[str]
    total_debt_raw: Optional[str]
    bad_debt: Optional[str]


@strawberry.type
class Broker:
    address: str
    market_id: str
    owner: str
    created_block: int
    active_token_id: Optional[str]
    wausdc_balance: Optional[str]
    wrlp_balance: Optional[str]
    debt_principal: Optional[str]
    is_frozen: Optional[bool]
    is_liquidated: Optional[bool]


@strawberry.type
class PoolSnapshot:
    market_id: str
    block_number: int
    block_timestamp: int
    mark_price: Optional[float]
    index_price: Optional[float]
    tick: Optional[int]
    sqrt_price_x96: Optional[str]
    liquidity: Optional[str]
    normalization_factor: Optional[str]
    total_debt: Optional[float]
    token0_balance: Optional[str]
    token1_balance: Optional[str]
    fee_growth_global0: Optional[str]
    fee_growth_global1: Optional[str]


@strawberry.type
class Candle:
    market_id: str
    resolution: str
    bucket: int
    mark_open: float
    mark_high: float
    mark_low: float
    mark_close: float
    index_open: float
    index_high: float
    index_low: float
    index_close: float
    volume_usd: float
    swap_count: int


@strawberry.type
class LpPosition:
    token_id: str
    pool_id: Optional[str]
    owner: str
    liquidity: str
    tick_lower: Optional[int]
    tick_upper: Optional[int]
    mint_block: int
    is_active: bool
    is_burned: bool


@strawberry.type
class TwammOrder:
    order_id: str
    pool_id: Optional[str]
    owner: str
    expiration: int
    zero_for_one: bool
    amount_in: str
    sell_rate: Optional[str]
    status: str
    is_registered: bool
    block_number: int
    start_epoch: Optional[int]
    nonce: Optional[int]
    tx_hash: str

    @strawberry.field
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"


@strawberry.type
class IndexerStatus:
    market_id: str
    last_indexed_block: int
    last_indexed_at: Optional[str]
    total_events: int


@strawberry.type
class Event:
    event_name: str
    block_timestamp: int
    block_number: int
    data: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _market(r) -> Market:
    return Market(
        market_id=r["market_id"], broker_factory=r["broker_factory"],
        mock_oracle=r["mock_oracle"], twamm_hook=r["twamm_hook"],
        wausdc=r["wausdc"], wrlp=r["wrlp"], pool_id=r["pool_id"],
        pool_fee=r["pool_fee"], tick_spacing=r["tick_spacing"],
        min_col_ratio=str(r["min_col_ratio"]),
        maintenance_margin=str(r["maintenance_margin"]),
        debt_cap=str(r["debt_cap"]),
        normalization_factor=str(r["normalization_factor"]) if r["normalization_factor"] is not None else None,
        total_debt_raw=str(r["total_debt_raw"]) if r["total_debt_raw"] is not None else None,
        bad_debt=str(r["bad_debt"]) if r["bad_debt"] is not None else None,
    )


def _candle(r) -> Candle:
    return Candle(
        market_id=r["market_id"], resolution=r["resolution"],
        bucket=r["bucket"],
        mark_open=float(r["mark_open"]), mark_high=float(r["mark_high"]),
        mark_low=float(r["mark_low"]), mark_close=float(r["mark_close"]),
        index_open=float(r["index_open"]), index_high=float(r["index_high"]),
        index_low=float(r["index_low"]), index_close=float(r["index_close"]),
        volume_usd=float(r["volume_usd"]), swap_count=r["swap_count"],
    )


def _lp(r) -> LpPosition:
    return LpPosition(
        token_id=str(r["token_id"]), pool_id=r.get("pool_id"),
        owner=r["owner"],
        liquidity=str(r["liquidity"]),
        tick_lower=r["tick_lower"], tick_upper=r["tick_upper"],
        mint_block=r["mint_block"],
        is_active=r["is_active"], is_burned=r["is_burned"],
    )


def _decode_event_data(raw_data: Any) -> Any:
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        try:
            return json.loads(raw_data)
        except json.JSONDecodeError:
            return raw_data
    return raw_data


def _event_topic_address(event_data: Any, topic_index: int) -> str:
    decoded = _decode_event_data(event_data)
    if not isinstance(decoded, dict):
        return ""
    topics = decoded.get("topics") or []
    if len(topics) <= topic_index:
        return ""
    raw = str(topics[topic_index])
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) < 40:
        return ""
    return f"0x{raw[-40:]}".lower()


def _event_uints(event_data: Any) -> list[int]:
    decoded = _decode_event_data(event_data)
    if not isinstance(decoded, dict):
        return []
    raw = str(decoded.get("raw") or "")
    if raw.startswith("0x"):
        raw = raw[2:]
    if not raw:
        return []
    values = []
    for idx in range(0, len(raw), 64):
        chunk = raw[idx:idx + 64]
        if len(chunk) == 64:
            try:
                values.append(int(chunk, 16))
            except ValueError:
                values.append(0)
    return values


def _format_usd_compact(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def _deployment_market_entry(market_id: Optional[str] = None) -> dict[str, Any]:
    try:
        import bootstrap

        cfg = bootstrap.load_deployment_json()
    except Exception:
        return {}

    if market_id and isinstance(cfg.get("markets"), dict):
        for entry in cfg["markets"].values():
            if isinstance(entry, dict) and entry.get("market_id") == market_id:
                return entry
    return cfg


def _deployment_index_price_fallback(market_id: Optional[str] = None) -> Optional[float]:
    """Fallback index price from deployment.json oracle_index_price_wad."""
    try:
        raw = _deployment_market_entry(market_id).get("oracle_index_price_wad")
        if raw in (None, "", "0", 0):
            return None
        return int(raw) / 1e18
    except Exception:
        return None


def _deployment_mark_price_fallback(market_id: Optional[str] = None) -> Optional[float]:
    """Fallback mark/spot price from deployment.json pool_spot_price_wad."""
    try:
        raw = _deployment_market_entry(market_id).get("pool_spot_price_wad")
        if raw in (None, "", "0", 0):
            return None
        return int(raw) / 1e18
    except Exception:
        return None


def _as_price_or_none(value: Any) -> Optional[float]:
    """Parse numeric price and treat non-positive values as missing."""
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _hydrate_snapshot_prices(snapshot: Any, mark_price: Optional[float], index_price: Optional[float]) -> None:
    """
    Backfill market/pool price fields inside snapshot payloads.

    Keeps both camelCase and snake_case keys for frontend/back-compat routes.
    """
    if not isinstance(snapshot, dict):
        return

    market = snapshot.get("market")
    if isinstance(market, dict):
        resolved_index = _as_price_or_none(market.get("indexPrice", market.get("index_price")))
        if resolved_index is None:
            resolved_index = index_price
        if resolved_index is not None:
            market["indexPrice"] = resolved_index
            market["index_price"] = resolved_index

        resolved_mark = _as_price_or_none(market.get("markPrice", market.get("mark_price")))
        if resolved_mark is None:
            resolved_mark = mark_price
        if resolved_mark is not None:
            market["markPrice"] = resolved_mark
            market["mark_price"] = resolved_mark

    pool_obj = snapshot.get("pool")
    if isinstance(pool_obj, dict):
        resolved_mark = _as_price_or_none(pool_obj.get("markPrice", pool_obj.get("mark_price")))
        if resolved_mark is None:
            resolved_mark = mark_price
        if resolved_mark is not None:
            pool_obj["markPrice"] = resolved_mark
            pool_obj["mark_price"] = resolved_mark

        resolved_index = _as_price_or_none(pool_obj.get("indexPrice", pool_obj.get("index_price")))
        if resolved_index is None:
            resolved_index = index_price
        if resolved_index is not None:
            pool_obj["indexPrice"] = resolved_index
            pool_obj["index_price"] = resolved_index

    market_obj = snapshot.get("market")
    pool_obj = snapshot.get("pool")
    derived = snapshot.get("derived")
    if isinstance(market_obj, dict) and isinstance(pool_obj, dict) and isinstance(derived, dict):
        mark = _as_price_or_none(pool_obj.get("markPrice", pool_obj.get("mark_price")))
        index = _as_price_or_none(pool_obj.get("indexPrice", pool_obj.get("index_price")))
        nf = _as_price_or_none(market_obj.get("normalizationFactor", market_obj.get("normalization_factor"))) or 1.0
        if mark is not None and index is not None and index > 0:
            normalized_mark = mark / nf if nf > 0 else mark
            peg_dev = (normalized_mark - index) / index * 100
            funding_rate = (normalized_mark - index) / index
            funding_period = 2_592_000
            try:
                funding_period = int(snapshot.get("riskParams", {}).get("funding_period_sec") or funding_period)
            except (TypeError, ValueError):
                pass
            derived["pegDeviationPct"] = round(peg_dev, 4)
            derived["fundingRateAnnPct"] = round(funding_rate * ((365 * 86400) / funding_period) * 100, 4)


def _record_get(row: asyncpg.Record, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


MARKET_CONFIG_SELECT = """
    SELECT market_id, broker_factory, mock_oracle, twamm_hook,
           ghost_router, twap_engine, twap_engine_lens,
           wausdc, wausdc_symbol, wrlp, wrlp_symbol,
           pool_id, pool_fee, tick_spacing,
           min_col_ratio, maintenance_margin, debt_cap,
           swap_router, bond_factory, basis_trade_factory, broker_executor,
           funding_period_sec, v4_quoter, broker_router,
           v4_position_manager, v4_state_view, pool_manager
    FROM markets
"""


def _deployment_market_id(deploy_cfg: dict[str, Any], market: str | None) -> str | None:
    if not market:
        return deploy_cfg.get("market_id")
    if market.startswith("0x"):
        return market
    markets = deploy_cfg.get("markets")
    if isinstance(markets, dict) and isinstance(markets.get(market), dict):
        return markets[market].get("market_id")
    return market


async def _fetch_market_row(
    conn: asyncpg.Connection,
    market: str | None = None,
) -> asyncpg.Record | None:
    import bootstrap

    deploy_cfg: dict[str, Any] = {}
    try:
        deploy_cfg = bootstrap.load_deployment_json()
    except (FileNotFoundError, ValueError):
        deploy_cfg = {}

    requested_market_id = _deployment_market_id(deploy_cfg, market)
    if requested_market_id:
        row = await conn.fetchrow(
            MARKET_CONFIG_SELECT + " WHERE market_id=$1",
            requested_market_id,
        )
        if row:
            return row

    return await conn.fetchrow(
        MARKET_CONFIG_SELECT + " ORDER BY deploy_timestamp DESC, market_id LIMIT 1"
    )


def _overlay_deployment_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    import bootstrap

    try:
        deploy_cfg = bootstrap.load_deployment_json()
    except (FileNotFoundError, ValueError):
        return payload

    market_entry: dict[str, Any] = {}
    markets = deploy_cfg.get("markets")
    if isinstance(markets, dict):
        for entry in markets.values():
            if isinstance(entry, dict) and entry.get("market_id") == payload.get("market_id"):
                market_entry = entry
                break

    for key in (
        "token0", "token1", "zero_for_one_long", "funding_model",
        "settlement_module", "decay_rate_wad", "collateral_symbol",
        "position_symbol", "type", "cds_coverage_factory",
    ):
        if key in market_entry and not payload.get(key):
            payload[key] = market_entry[key]

    for key in (
        "rpc_url", "rld_core", "pool_manager",
        "swap_router", "bond_factory", "basis_trade_factory",
        "broker_executor", "broker_router", "v4_quoter", "v4_position_manager",
        "ghost_router", "twap_engine", "twap_engine_lens", "cds_coverage_factory",
    ):
        if key in deploy_cfg and not payload.get(key):
            payload[key] = deploy_cfg[key]

    if isinstance(markets, dict):
        payload["markets"] = markets

    return payload


def _market_info_payload(row: asyncpg.Record) -> Dict[str, Any]:
    collateral_symbol = _record_get(row, "wausdc_symbol", "waUSDC")
    position_symbol = _record_get(row, "wrlp_symbol", "wRLP")
    payload = {
        "marketId": row["market_id"],
        "brokerFactory": row["broker_factory"],
        "mockOracle": row["mock_oracle"],
        "twammHook": row["twamm_hook"],
        "ghostRouter": _record_get(row, "ghost_router", ""),
        "twapEngine": _record_get(row, "twap_engine", ""),
        "twapEngineLens": _record_get(row, "twap_engine_lens", ""),
        "wausdc": row["wausdc"],
        "wausdcSymbol": collateral_symbol,
        "wrlp": row["wrlp"],
        "wrlpSymbol": position_symbol,
        "poolId": row["pool_id"],
        "poolFee": row["pool_fee"],
        "tickSpacing": row["tick_spacing"],
        "minColRatio": str(row["min_col_ratio"]),
        "maintenanceMargin": str(row["maintenance_margin"]),
        "debtCap": str(row["debt_cap"]),
        "swapRouter": row["swap_router"],
        "bondFactory": row["bond_factory"],
        "basisTradeFactory": row["basis_trade_factory"],
        "brokerExecutor": row["broker_executor"],
        "fundingPeriodSec": row["funding_period_sec"],
        "v4Quoter": _record_get(row, "v4_quoter", ""),
        "brokerRouter": _record_get(row, "broker_router", ""),
        "v4PositionManager": _record_get(row, "v4_position_manager", ""),
        "v4StateView": _record_get(row, "v4_state_view", ""),
        "poolManager": _record_get(row, "pool_manager", ""),
    }

    # Legacy field aliases used by older UI code paths.
    payload.update({
        "market_id": payload["marketId"],
        "broker_factory": payload["brokerFactory"],
        "mock_oracle": payload["mockOracle"],
        "twamm_hook": payload["twammHook"],
        "ghost_router": payload["ghostRouter"],
        "twap_engine": payload["twapEngine"],
        "twap_engine_lens": payload["twapEngineLens"],
        "pool_id": payload["poolId"],
        "pool_fee": payload["poolFee"],
        "tick_spacing": payload["tickSpacing"],
        "min_col_ratio": payload["minColRatio"],
        "maintenance_margin": payload["maintenanceMargin"],
        "debt_cap": payload["debtCap"],
        "swap_router": payload["swapRouter"],
        "bond_factory": payload["bondFactory"],
        "basis_trade_factory": payload["basisTradeFactory"],
        "broker_executor": payload["brokerExecutor"],
        "funding_period_sec": payload["fundingPeriodSec"],
        "v4_quoter": payload["v4Quoter"],
        "broker_router": payload["brokerRouter"],
        "v4_position_manager": payload["v4PositionManager"],
        "v4_state_view": payload["v4StateView"],
        "pool_manager": payload["poolManager"],
    })

    payload = _overlay_deployment_config(payload)

    payload["collateral"] = {
        "name": collateral_symbol,
        "symbol": collateral_symbol,
        "address": payload["wausdc"],
    }
    payload["position_token"] = {
        "name": position_symbol,
        "symbol": position_symbol,
        "address": payload["wrlp"],
    }
    payload["infrastructure"] = {
        "brokerRouter": payload["brokerRouter"],
        "brokerExecutor": payload["brokerExecutor"],
        "twammHook": payload["twammHook"],
        "twamm_hook": payload["twammHook"],
        "ghostRouter": payload["ghostRouter"],
        "ghost_router": payload["ghostRouter"],
        "twapEngine": payload["twapEngine"],
        "twap_engine": payload["twapEngine"],
        "twapEngineLens": payload["twapEngineLens"],
        "twap_engine_lens": payload["twapEngineLens"],
        "bondFactory": payload["bondFactory"],
        "basisTradeFactory": payload["basisTradeFactory"],
        "poolFee": payload["poolFee"],
        "tickSpacing": payload["tickSpacing"],
        "poolManager": payload["poolManager"],
        "v4Quoter": payload["v4Quoter"],
        "v4PositionManager": payload["v4PositionManager"],
        "v4StateView": payload["v4StateView"],
        "cdsCoverageFactory": payload.get("cds_coverage_factory", ""),
        "cds_coverage_factory": payload.get("cds_coverage_factory", ""),
    }
    payload["risk_params"] = {
        "min_col_ratio": payload["minColRatio"],
        "maintenance_margin": payload["maintenanceMargin"],
        "funding_period_sec": payload["fundingPeriodSec"],
        "debt_cap": payload["debtCap"],
    }
    return payload


# ── Query ──────────────────────────────────────────────────────────────────

@strawberry.type
class Query:

    @strawberry.field
    async def markets(self) -> List[Market]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM markets ORDER BY deploy_timestamp DESC")
        return [_market(r) for r in rows]

    @strawberry.field
    async def brokers(self, market_id: str) -> List[Broker]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM brokers WHERE market_id=$1 ORDER BY created_block", market_id
            )
        return [Broker(
            address=r["address"], market_id=r["market_id"],
            owner=r["owner"], created_block=r["created_block"],
            active_token_id=r["active_lp_token_id"],
            wausdc_balance=str(r["wausdc_balance"]) if r["wausdc_balance"] is not None else None,
            wrlp_balance=str(r["wrlp_balance"]) if r["wrlp_balance"] is not None else None,
            debt_principal=str(r["debt_principal"]) if r["debt_principal"] is not None else None,
            is_frozen=r.get("is_frozen", False),
            is_liquidated=r["is_liquidated"],
        ) for r in rows]

    @strawberry.field
    async def pool_snapshot(self, market_id: str) -> Optional[PoolSnapshot]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                  b.market_id, b.block_number, b.block_timestamp,
                  b.sqrt_price_x96, b.liquidity,
                  COALESCE(b.mark_price, (SELECT mark_price FROM block_states
                   WHERE market_id=$1 AND mark_price IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS mark_price,
                  COALESCE(b.tick, (SELECT tick FROM block_states
                   WHERE market_id=$1 AND tick IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS tick,
                  COALESCE(b.normalization_factor, (SELECT normalization_factor FROM block_states
                   WHERE market_id=$1 AND normalization_factor IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS normalization_factor,
                  COALESCE(b.total_debt, (SELECT total_debt FROM block_states
                   WHERE market_id=$1 AND total_debt IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS total_debt,
                  COALESCE(b.index_price, (SELECT index_price FROM block_states
                   WHERE market_id=$1 AND index_price IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS index_price,
                  COALESCE(b.token0_balance, (SELECT token0_balance FROM block_states
                   WHERE market_id=$1 AND token0_balance IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS token0_balance,
                  COALESCE(b.token1_balance, (SELECT token1_balance FROM block_states
                   WHERE market_id=$1 AND token1_balance IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS token1_balance,
                  COALESCE(b.fee_growth_global0, (SELECT fee_growth_global0 FROM block_states
                   WHERE market_id=$1 AND fee_growth_global0 IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS fee_growth_global0,
                  COALESCE(b.fee_growth_global1, (SELECT fee_growth_global1 FROM block_states
                   WHERE market_id=$1 AND fee_growth_global1 IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS fee_growth_global1
                FROM block_states b
                WHERE b.market_id=$1
                ORDER BY b.block_number DESC
                LIMIT 1
            """, market_id)
        if not row:
            return None
        return PoolSnapshot(
            market_id=row["market_id"],
            block_number=row["block_number"],
            block_timestamp=row["block_timestamp"],
            mark_price=float(row["mark_price"]) if row["mark_price"] is not None else None,
            index_price=float(row["index_price"]) if row["index_price"] is not None else None,
            tick=row["tick"],
            sqrt_price_x96=str(row["sqrt_price_x96"]) if row["sqrt_price_x96"] is not None else None,
            liquidity=str(row["liquidity"]) if row["liquidity"] is not None else None,
            normalization_factor=str(row["normalization_factor"]) if row["normalization_factor"] is not None else None,
            total_debt=float(row["total_debt"]) if row["total_debt"] is not None else None,
            token0_balance=str(row["token0_balance"]) if row["token0_balance"] is not None else None,
            token1_balance=str(row["token1_balance"]) if row["token1_balance"] is not None else None,
            fee_growth_global0=str(row["fee_growth_global0"]) if row["fee_growth_global0"] is not None else None,
            fee_growth_global1=str(row["fee_growth_global1"]) if row["fee_growth_global1"] is not None else None,
        )

    @strawberry.field
    async def candles(
        self,
        market_id: str,
        resolution: str,
        from_bucket: Optional[int] = None,
        to_bucket: Optional[int] = None,
        limit: Optional[int] = 500,
    ) -> List[Candle]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if from_bucket is not None and to_bucket is not None:
                rows = await conn.fetch("""
                    SELECT * FROM candles
                    WHERE market_id=$1 AND resolution=$2
                      AND bucket BETWEEN $3 AND $4
                    ORDER BY bucket ASC
                    LIMIT $5
                """, market_id, resolution, from_bucket, to_bucket, limit or 500)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM candles
                    WHERE market_id=$1 AND resolution=$2
                    ORDER BY bucket DESC
                    LIMIT $3
                """, market_id, resolution, limit or 500)
                # Sort back to ascending for the chart
                rows = sorted(rows, key=lambda x: x["bucket"])
        return [_candle(r) for r in rows]

    @strawberry.field
    async def lp_positions(
        self,
        owner: Optional[str] = None,
        active_only: bool = False,
    ) -> List[LpPosition]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            q = "SELECT * FROM lp_positions WHERE 1=1"
            args: list = []
            if owner:
                args.append(owner.lower())
                q += f" AND owner=${len(args)}"
            if active_only:
                q += " AND is_active=TRUE AND is_burned=FALSE"
            q += " ORDER BY mint_block DESC"
            rows = await conn.fetch(q, *args)
        return [_lp(r) for r in rows]

    @strawberry.field
    async def twamm_orders(
        self, owner: str | None = None, active_only: bool = True
    ) -> List[TwammOrder]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            q = "SELECT * FROM twamm_orders WHERE 1=1"
            args: list = []
            if owner:
                args.append(owner.lower())
                q += f" AND owner=${len(args)}"
            if active_only:
                q += " AND status='active'"
            q += " ORDER BY block_number DESC"
            rows = await conn.fetch(q, *args)
        return [TwammOrder(
            order_id=r["order_id"], pool_id=r.get("pool_id"),
            owner=r["owner"], expiration=r["expiration"],
            zero_for_one=r["zero_for_one"],
            amount_in=str(r["amount_in"]),
            sell_rate=r.get("sell_rate"),
            status=r["status"],
            is_registered=r["is_registered"],
            block_number=r["block_number"],
            start_epoch=r.get("start_epoch"),
            nonce=r.get("nonce"),
            tx_hash=r["tx_hash"],
        ) for r in rows]

    @strawberry.field
    async def indexer_status(self) -> List[IndexerStatus]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM indexer_state ORDER BY market_id")
        return [IndexerStatus(
            market_id=r["market_id"],
            last_indexed_block=r["last_indexed_block"],
            last_indexed_at=r["last_indexed_at"].isoformat() if r["last_indexed_at"] else None,
            total_events=r["total_events"],
        ) for r in rows]

    @strawberry.field
    async def events(self, limit: int = 100) -> List[Event]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT event_name, block_timestamp, block_number, data 
                FROM events 
                ORDER BY block_number DESC, log_index DESC
                LIMIT $1
            """, limit)
        return [Event(
            event_name=r["event_name"],
            block_timestamp=r["block_timestamp"],
            block_number=r["block_number"],
            data=r["data"]
        ) for r in rows]

    @strawberry.field
    async def broker_operations(self, owner: str, limit: int = 50) -> Optional[JSON]:
        """Trade operations for a broker, queried by owner address.
        
        Reads BrokerRouter events (LongExecuted, LongClosed, etc.) from the
        events table. Broker address is in topics[1] (indexed param).
        Returns decoded operations with human-readable amounts.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Find broker by owner
            broker = await conn.fetchrow(
                "SELECT address FROM brokers WHERE owner = $1",
                owner.lower()
            )
            if not broker:
                return []

            broker_addr = broker["address"].lower()
            # Pad to 32-byte topic format: 0x000...address
            broker_topic = "0x" + broker_addr[2:].zfill(64)

            rows = await conn.fetch("""
                SELECT event_name, block_timestamp, block_number, tx_hash, data
                FROM events
                WHERE event_name IN (
                    'LongExecuted', 'LongClosed',
                    'ShortExecuted', 'ShortClosed', 'Deposited'
                )
                AND data::jsonb->'topics'->>1 = $1
                ORDER BY block_number DESC, log_index DESC
                LIMIT $2
            """, broker_topic, limit)

        ops = []
        OP_META = {
            "LongExecuted":  "OPEN_LONG",
            "LongClosed":    "CLOSE_LONG",
            "ShortExecuted": "OPEN_SHORT",
            "ShortClosed":   "CLOSE_SHORT",
            "Deposited":     "DEPOSIT",
        }
        for r in rows:
            raw_hex = r["data"].get("raw", "") if isinstance(r["data"], dict) else ""
            raw_hex = json.loads(r["data"]).get("raw", "") if isinstance(r["data"], str) else raw_hex
            # Decode (uint256, uint256) from data
            amount1 = 0
            amount2 = 0
            if raw_hex and len(raw_hex) >= 130:  # 0x + 64 + 64
                try:
                    amount1 = int(raw_hex[2:66], 16)
                    amount2 = int(raw_hex[66:130], 16)
                except ValueError:
                    pass
            ops.append({
                "type": OP_META.get(r["event_name"], r["event_name"]),
                "amount1": amount1 / 1e6,
                "amount2": amount2 / 1e6,
                "blockNumber": r["block_number"],
                "timestamp": r["block_timestamp"],
                "txHash": r["tx_hash"],
            })
        return ops

    # ── NEW: Precomputed data resolvers ─────────────────────────────

    @strawberry.field
    async def snapshot(self, market: Optional[str] = None) -> Optional[JSON]:
        """Returns the precomputed global snapshot JSON. Zero computation."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await _fetch_market_row(conn, market)
            if not row:
                return None
            market_id = row["market_id"]
            val = await conn.fetchval("SELECT snapshot FROM markets WHERE market_id=$1", market_id)
            latest_prices = await conn.fetchrow("""
                SELECT
                    (SELECT mark_price FROM block_states
                     WHERE market_id=$1 AND mark_price IS NOT NULL
                     ORDER BY block_number DESC
                     LIMIT 1) AS mark_price,
                    (SELECT index_price FROM block_states
                     WHERE market_id=$1 AND index_price IS NOT NULL
                     ORDER BY block_number DESC
                     LIMIT 1) AS index_price
            """, market_id)
        if not val:
            return None
        snap = json.loads(val) if isinstance(val, str) else val
        if not isinstance(snap, dict):
            return snap

        mark_price = _as_price_or_none(latest_prices["mark_price"]) if latest_prices else None
        if mark_price is None:
            mark_price = _deployment_mark_price_fallback(market_id)
        index_price = _as_price_or_none(latest_prices["index_price"]) if latest_prices else None
        if index_price is None:
            index_price = _deployment_index_price_fallback(market_id)

        _hydrate_snapshot_prices(snap, mark_price, index_price)

        return snap

    @strawberry.field
    async def liquidity_distribution(self, market: Optional[str] = None) -> Optional[JSON]:
        """Returns pre-built liquidity bin distribution. Zero computation."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await _fetch_market_row(conn, market)
            if not row:
                return None
            val = await conn.fetchval("SELECT liquidity_bins FROM markets WHERE market_id=$1", row["market_id"])
        if not val:
            return None
        return json.loads(val) if isinstance(val, str) else val

    @strawberry.field
    async def bonds(self, owner: str) -> Optional[JSON]:
        """Returns all bonds owned by the given address."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT broker_address, market_id, owner, notional, hedge,
                       duration, mint_block, mint_tx, status, close_block, close_tx,
                       factory_address, entry_rate
                FROM bonds WHERE owner = $1
                ORDER BY mint_block DESC
            """, owner.lower())
        if not rows:
            return []
        result = []
        for r in rows:
            d = dict(r)
            # Convert Decimal → float for JSON serialization
            for k in ("notional", "hedge", "entry_rate"):
                if d.get(k) is not None:
                    d[k] = float(d[k])
            result.append(d)
        return result

    @strawberry.field
    async def coverage_positions(self, owner: str, market: Optional[str] = None) -> Optional[JSON]:
        """Returns CDS fixed-coverage positions opened through CDSCoverageFactory."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            market_row = await _fetch_market_row(conn, market)
            market_id = market_row["market_id"] if market_row else None
            args: list[Any] = []
            where = "event_name IN ('CoverageOpened', 'CoverageClosed')"
            if market_id:
                args.append(market_id)
                where += f" AND market_id=${len(args)}"
            rows = await conn.fetch(
                f"""
                SELECT market_id, event_name, block_number, block_timestamp,
                       tx_hash, contract_address, data
                FROM events
                WHERE {where}
                ORDER BY block_number ASC, log_index ASC
                """,
                *args,
            )

        owner_lc = owner.lower()
        positions: dict[str, dict[str, Any]] = {}
        for row in rows:
            user_addr = _event_topic_address(row["data"], 1)
            broker_addr = _event_topic_address(row["data"], 2)
            if user_addr != owner_lc or not broker_addr:
                continue

            values = _event_uints(row["data"])
            if row["event_name"] == "CoverageOpened":
                coverage = values[0] if len(values) > 0 else 0
                initial_cost = values[1] if len(values) > 1 else 0
                premium_budget = values[2] if len(values) > 2 else 0
                initial_tokens = values[3] if len(values) > 3 else 0
                duration = values[4] if len(values) > 4 else 0
                positions[broker_addr] = {
                    "brokerAddress": broker_addr,
                    "marketId": row["market_id"],
                    "owner": user_addr,
                    "coverage": coverage / 1e6,
                    "initialCost": initial_cost / 1e6,
                    "premiumBudget": premium_budget / 1e6,
                    "initialPositionTokens": initial_tokens / 1e6,
                    "duration": int(duration),
                    "openedBlock": row["block_number"],
                    "openedAt": row["block_timestamp"],
                    "openedTx": row["tx_hash"],
                    "status": "active",
                }
            elif row["event_name"] == "CoverageClosed" and broker_addr in positions:
                positions[broker_addr]["status"] = "closed"
                positions[broker_addr]["closedBlock"] = row["block_number"]
                positions[broker_addr]["closedAt"] = row["block_timestamp"]
                positions[broker_addr]["closedTx"] = row["tx_hash"]
                positions[broker_addr]["collateralReturned"] = (values[0] if len(values) > 0 else 0) / 1e6
                positions[broker_addr]["positionReturned"] = (values[1] if len(values) > 1 else 0) / 1e6

        return list(reversed(list(positions.values())))

    @strawberry.field
    async def broker_profile(self, owner: str) -> Optional[JSON]:
        """On-demand broker profile with LP position values and fees.
        
        Raw data stored in DB. Values/fees computed at query time
        using 5N multiplications per LP position. <1ms even with 50 positions.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Find broker by owner
            broker = await conn.fetchrow(
                "SELECT * FROM brokers WHERE owner = $1",
                owner.lower()
            )
            if not broker:
                return None

            market_id = broker["market_id"]

            # Get latest block state for current prices
            latest = await conn.fetchrow("""
                SELECT mark_price, tick, fee_growth_global0, fee_growth_global1,
                       index_price
                FROM block_states
                WHERE market_id = $1
                ORDER BY block_number DESC LIMIT 1
            """, market_id)
            if not latest:
                return None

            mark_price = float(latest["mark_price"] or 0)
            current_tick = int(latest["tick"] or 0)
            fg0 = int(latest["fee_growth_global0"] or "0")
            fg1 = int(latest["fee_growth_global1"] or "0")

            # Get LP positions (now keyed by owner, not broker_address)
            positions = await conn.fetch("""
                SELECT * FROM lp_positions
                WHERE owner = $1 AND is_burned = FALSE
                ORDER BY mint_block DESC
            """, broker["address"])

            lp_data = []
            Q128 = 2**128
            for pos in positions:
                tick_lower = pos["tick_lower"]
                tick_upper = pos["tick_upper"]
                liquidity = int(pos["liquidity"])

                if tick_lower is not None and tick_upper is not None and liquidity > 0:
                    # Compute token amounts from liquidity + current tick
                    amt0, amt1 = _liquidity_to_amounts(
                        liquidity, tick_lower, tick_upper, current_tick
                    )
                    amt0_human = amt0 / 1e6
                    amt1_human = amt1 / 1e6
                    value_usd = amt0_human * mark_price + amt1_human
                    in_range = tick_lower <= current_tick < tick_upper
                else:
                    amt0_human = amt1_human = value_usd = 0
                    in_range = False

                # Fee earnings (simplified: using global fee growth as upper bound)
                fg_inside0 = fg0
                fg_inside1 = fg1
                fees0 = liquidity * fg_inside0 / Q128 / 1e6 if liquidity > 0 else 0
                fees1 = liquidity * fg_inside1 / Q128 / 1e6 if liquidity > 0 else 0

                lp_data.append({
                    "tokenId": str(pos["token_id"]),
                    "tickLower": tick_lower,
                    "tickUpper": tick_upper,
                    "liquidity": str(liquidity),
                    "isActive": pos["is_active"],
                    "amount0": round(amt0_human, 4),
                    "amount1": round(amt1_human, 4),
                    "valueUsd": round(value_usd, 2),
                    "feesEarned0": round(fees0, 4),
                    "feesEarned1": round(fees1, 4),
                    "feesUsd": round(fees0 * mark_price + fees1, 2),
                    "inRange": in_range,
                    "poolId": pos.get("pool_id"),
                })

            # Get TWAMM orders owned by this broker
            twamm_orders = await conn.fetch("""
                SELECT * FROM twamm_orders
                WHERE owner = $1
                ORDER BY block_number DESC
            """, broker["address"])

            twamm_data = [
                {
                    "orderId": o["order_id"],
                    "poolId": o.get("pool_id"),
                    "owner": o["owner"],
                    "amountIn": o["amount_in"],
                    "expiration": o["expiration"],
                    "startEpoch": o.get("start_epoch"),
                    "sellRate": o.get("sell_rate"),
                    "zeroForOne": o["zero_for_one"],
                    "nonce": o.get("nonce"),
                    "status": o["status"],
                    "isRegistered": o["is_registered"],
                    "buyTokensOut": o.get("buy_tokens_out", "0"),
                    "sellTokensRefund": o.get("sell_tokens_refund", "0"),
                    "blockNumber": o["block_number"],
                    "txHash": o["tx_hash"],
                }
                for o in twamm_orders
            ]

            # Get operators for this broker
            operators = await conn.fetch(
                "SELECT operator FROM broker_operators WHERE broker_address = $1",
                broker["address"]
            )
            operator_list = [op["operator"] for op in operators]

            # All values returned as raw strings — frontend handles decimal conversion
            return {
                "address": broker["address"],
                "owner": broker["owner"],
                "wausdcBalance": broker["wausdc_balance"] or "0",
                "wrlpBalance": broker["wrlp_balance"] or "0",
                "debtPrincipal": broker["debt_principal"] or "0",
                "activeLpTokenId": broker["active_lp_token_id"] or "0",
                "activeTwammOrderId": broker["active_twamm_order_id"] or "",
                "isFrozen": broker["is_frozen"] or False,
                "isLiquidated": broker["is_liquidated"] or False,
                "operators": operator_list,
                "lpPositions": lp_data,
                "twammOrders": twamm_data,
                "activeTokenId": int(broker["active_lp_token_id"] or 0),
            }

    @strawberry.field
    async def market_info(self, market: Optional[str] = None) -> Optional[JSON]:
        """Static market configuration. Fetched once, cached forever."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await _fetch_market_row(conn, market)
        if not row:
            return None
        return _market_info_payload(row)


# ── Uniswap V4 math for on-demand position value resolution ────────────────

def _liquidity_to_amounts(
    liquidity: int, tick_lower: int, tick_upper: int, current_tick: int
) -> tuple:
    """Convert LP position's liquidity to token amounts at current tick."""
    sa = math.sqrt(1.0001 ** tick_lower)
    sb = math.sqrt(1.0001 ** tick_upper)
    sp = math.sqrt(1.0001 ** current_tick)

    if current_tick < tick_lower:
        amount0 = liquidity * (1/sa - 1/sb)
        amount1 = 0
    elif current_tick >= tick_upper:
        amount0 = 0
        amount1 = liquidity * (sb - sa)
    else:
        amount0 = liquidity * (1/sp - 1/sb)
        amount1 = liquidity * (sp - sa)

    return amount0, amount1


def _parse_cors_origins(env_name: str, default_origins: list[str]) -> list[str]:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default_origins
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if not origins:
        return default_origins
    # We intentionally disallow wildcard here to avoid permissive production CORS.
    return [origin for origin in origins if origin != "*"] or default_origins


api_log = logging.getLogger("indexer.api")


def _env_truthy(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _admin_allowed_clients(env_name: str = "INDEXER_ADMIN_ALLOWED_CLIENTS") -> list[str]:
    raw = os.getenv(env_name, "127.0.0.1,::1,localhost")
    return [entry.strip().lower() for entry in raw.split(",") if entry.strip()]


def _is_admin_allowed_client(host: str | None, allowed_clients: list[str]) -> bool:
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized.startswith("::ffff:"):
        normalized = normalized[7:]
    if normalized in allowed_clients:
        return True
    try:
        client_ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if client_ip.is_loopback and any(entry in {"127.0.0.1", "::1", "localhost"} for entry in allowed_clients):
        return True
    for entry in allowed_clients:
        try:
            if "/" in entry:
                if client_ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif client_ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def _error_response(
    status_code: int,
    detail: str,
    *,
    exc: Exception | None = None,
    expose_errors: bool = False,
) -> JSONResponse:
    if exc is not None:
        api_log.error(detail, exc_info=exc)
    payload: dict[str, Any] = {"status": "error", "detail": detail}
    if exc is not None and expose_errors:
        payload["error"] = str(exc)
    return JSONResponse(payload, status_code=status_code)


def _admin_forbidden_response(
    request: Request,
    x_admin_token: Optional[str],
    admin_token: str,
    allow_unsafe_reset: bool,
    admin_internal_only: bool,
    admin_allowed_clients: list[str],
    log: logging.Logger,
) -> JSONResponse | None:
    client_host = request.client.host if request.client else None
    if admin_internal_only and not _is_admin_allowed_client(client_host, admin_allowed_clients):
        log.warning("Rejected admin call from non-internal client host=%s", client_host)
        return JSONResponse(
            {"status": "forbidden", "detail": "admin endpoint is internal-only"},
            status_code=403,
        )
    if not admin_token and not allow_unsafe_reset:
        log.warning("Rejected admin call because INDEXER_ADMIN_TOKEN is not configured")
        return JSONResponse(
            {"status": "forbidden", "detail": "admin token is not configured"},
            status_code=403,
        )
    if admin_token and x_admin_token != admin_token:
        log.warning("Rejected admin call with invalid token")
        return JSONResponse(
            {"status": "forbidden", "detail": "missing or invalid X-Admin-Token"},
            status_code=403,
        )
    return None


# ── App factory ────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    schema = strawberry.Schema(query=Query)
    graphql_app = GraphQLRouter(schema)

    app = FastAPI(title="RLD Indexer GraphQL API", version="1.0.0")

    cors_origins = _parse_cors_origins(
        "INDEXER_CORS_ORIGINS",
        [
            "http://localhost:3000",
            "http://localhost:5173",
            "https://rld.fi",
            "https://www.rld.fi",
        ],
    )
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(graphql_app, prefix="/graphql")
    admin_token = os.getenv("INDEXER_ADMIN_TOKEN", "").strip()
    allow_unsafe_reset = os.getenv("INDEXER_ALLOW_UNSAFE_ADMIN_RESET", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    expose_internal_errors = _env_truthy("INDEXER_EXPOSE_INTERNAL_ERRORS", False)
    admin_internal_only = _env_truthy("INDEXER_ADMIN_INTERNAL_ONLY", True)
    admin_allowed_clients = _admin_allowed_clients()

    @app.get("/healthz")
    async def healthz():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return {"status": "ok"}
        except Exception as e:
            return _error_response(
                503,
                "database unavailable",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.post("/admin/reset")
    async def admin_reset(
        request: Request,
        x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    ):
        """Deployer calls this after writing deployment.json to wipe stale data."""
        import bootstrap
        log = logging.getLogger("admin.reset")
        try:
            denied = _admin_forbidden_response(
                request,
                x_admin_token,
                admin_token,
                allow_unsafe_reset,
                admin_internal_only,
                admin_allowed_clients,
                log,
            )
            if denied is not None:
                return denied
            pool = await get_pool()
            log.info("POST /admin/reset — truncating all indexed data")
            await bootstrap.reset(pool)  # truncates + re-seeds from deployment.json
            cfg = bootstrap.load_deployment_json()
            market_id = cfg.get("market_id", "unknown")
            markets = cfg.get("markets") if isinstance(cfg.get("markets"), dict) else {}
            market_ids = [
                entry.get("market_id")
                for entry in markets.values()
                if isinstance(entry, dict) and entry.get("market_id")
            ]
            log.info("Reset complete — market_id=%s markets=%s", market_id, market_ids or [market_id])
            return {"status": "ok", "market_id": market_id, "markets": market_ids or [market_id]}
        except Exception as e:
            log.error("Reset failed: %s", e, exc_info=True)
            return _error_response(
                500,
                "reset failed",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.post("/admin/sync-config")
    async def admin_sync_config(
        request: Request,
        x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    ):
        """Non-destructively upsert markets from deployment.json."""
        import bootstrap
        log = logging.getLogger("admin.sync_config")
        try:
            denied = _admin_forbidden_response(
                request,
                x_admin_token,
                admin_token,
                allow_unsafe_reset,
                admin_internal_only,
                admin_allowed_clients,
                log,
            )
            if denied is not None:
                return denied

            pool = await get_pool()
            cfg = await bootstrap.sync_config(pool)
            markets = cfg.get("markets") if isinstance(cfg.get("markets"), dict) else {}
            market_ids = [
                entry.get("market_id")
                for entry in markets.values()
                if isinstance(entry, dict) and entry.get("market_id")
            ]
            if not market_ids and cfg.get("market_id"):
                market_ids = [cfg["market_id"]]
            log.info("Sync config complete markets=%s", market_ids)
            return {"status": "ok", "markets": market_ids, "destructive": False}
        except Exception as e:
            log.error("Sync config failed: %s", e, exc_info=True)
            return _error_response(
                500,
                "sync config failed",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.post("/admin/rewind-market")
    async def admin_rewind_market(
        request: Request,
        market_id: str = FastAPIQuery(...),
        block: Optional[int] = FastAPIQuery(default=None),
        x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    ):
        """Non-destructively move one market cursor backward for replay."""
        from state import update_source_status

        log = logging.getLogger("admin.rewind_market")
        try:
            denied = _admin_forbidden_response(
                request,
                x_admin_token,
                admin_token,
                allow_unsafe_reset,
                admin_internal_only,
                admin_allowed_clients,
                log,
            )
            if denied is not None:
                return denied

            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT market_id, market_type, deploy_block FROM markets WHERE market_id=$1",
                    market_id,
                )
                if not row:
                    return JSONResponse(
                        {"status": "not_found", "detail": "unknown market_id"},
                        status_code=404,
                    )
                target_block = int(block if block is not None else row["deploy_block"])
                if target_block < int(row["deploy_block"] or 0):
                    target_block = int(row["deploy_block"] or 0)
                await conn.execute(
                    """
                    INSERT INTO indexer_state (market_id, last_indexed_block, total_events)
                    VALUES ($1, $2, 0)
                    ON CONFLICT (market_id) DO UPDATE SET
                      last_indexed_block = EXCLUDED.last_indexed_block,
                      last_indexed_at = NOW()
                    """,
                    market_id,
                    target_block,
                )
                await update_source_status(
                    conn,
                    f"sim-indexer:{market_id}",
                    "rewind",
                    market_id=market_id,
                    market_type=row["market_type"],
                    last_scanned_block=target_block,
                    last_processed_block=target_block,
                    source_head_block=target_block,
                )

            log.info("Rewound market %s cursor to block %d", market_id, target_block)
            return {
                "status": "ok",
                "market_id": market_id,
                "last_indexed_block": target_block,
                "destructive": False,
            }
        except Exception as e:
            log.error("Rewind market failed: %s", e, exc_info=True)
            return _error_response(
                500,
                "rewind market failed",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.get("/config")
    async def get_config(market: Optional[str] = FastAPIQuery(default=None)):
        """Daemons poll this to get deployment config. 503 until deployer has run."""
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await _fetch_market_row(conn, market)
            if not row:
                return JSONResponse(
                    {"status": "waiting", "detail": "No market deployed yet"},
                    status_code=503
                )
            cfg = dict(row)
            return _overlay_deployment_config(cfg)
        except Exception as e:
            return _error_response(
                503,
                "config unavailable",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    # Compatibility routes retained for older UI paths.
    @app.get("/api/market-info")
    async def api_market_info(market: Optional[str] = FastAPIQuery(default=None)):
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await _fetch_market_row(conn, market)
            if not row:
                return JSONResponse(
                    {"status": "waiting", "detail": "No market deployed yet"},
                    status_code=503,
                )
            return _market_info_payload(row)
        except Exception as e:
            return _error_response(
                503,
                "market info unavailable",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.get("/api/status")
    async def api_status():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT
                        COALESCE(MAX(last_indexed_block), 0) AS last_indexed_block,
                        COALESCE(SUM(total_events), 0) AS total_events,
                        (SELECT COUNT(*) FROM block_states) AS total_block_states,
                        (SELECT mark_price FROM block_states
                         WHERE mark_price IS NOT NULL
                         ORDER BY block_number DESC
                         LIMIT 1) AS mark_price,
                        (SELECT index_price FROM block_states
                         WHERE index_price IS NOT NULL
                         ORDER BY block_number DESC
                         LIMIT 1) AS index_price
                    FROM indexer_state
                """)
            mark_price = float(row["mark_price"]) if row["mark_price"] is not None else None
            index_price = float(row["index_price"]) if row["index_price"] is not None else None
            if index_price is None:
                index_price = _deployment_index_price_fallback()
            return {
                "status": "ok",
                "last_indexed_block": int(row["last_indexed_block"] or 0),
                "total_events": int(row["total_events"] or 0),
                "total_block_states": int(row["total_block_states"] or 0),
                "mark_price": mark_price,
                "index_price": index_price,
            }
        except Exception as e:
            return _error_response(
                503,
                "status unavailable",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.get("/api/events")
    async def api_events(limit: int = FastAPIQuery(default=100, ge=1, le=500)):
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT event_name, block_timestamp, block_number, tx_hash, data
                    FROM events
                    ORDER BY block_number DESC, log_index DESC
                    LIMIT $1
                """, limit)
            return {
                "events": [
                    {
                        "event_name": r["event_name"],
                        "eventName": r["event_name"],
                        "block_timestamp": r["block_timestamp"],
                        "blockTimestamp": r["block_timestamp"],
                        "block_number": r["block_number"],
                        "blockNumber": r["block_number"],
                        "tx_hash": r["tx_hash"],
                        "txHash": r["tx_hash"],
                        "data": _decode_event_data(r["data"]),
                    }
                    for r in rows
                ]
            }
        except Exception as e:
            return _error_response(
                503,
                "events unavailable",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.get("/api/volume")
    async def api_volume():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT
                        COALESCE(SUM(volume_usd), 0) AS volume_usd,
                        COALESCE(SUM(swap_count), 0) AS swap_count
                    FROM candles
                    WHERE resolution IN ('1h', '1H')
                      AND bucket >= (EXTRACT(EPOCH FROM NOW())::bigint - 86400)
                """)
            volume_usd = float(row["volume_usd"] or 0)
            swap_count = int(row["swap_count"] or 0)
            return {
                "volume_usd": volume_usd,
                "swap_count": swap_count,
                "volume_formatted": _format_usd_compact(volume_usd),
            }
        except Exception as e:
            return _error_response(
                503,
                "volume unavailable",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.get("/api/latest")
    async def api_latest():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                snapshot_raw = await conn.fetchval("SELECT snapshot FROM markets LIMIT 1")
                last_indexed_block = await conn.fetchval(
                    "SELECT COALESCE(MAX(last_indexed_block), 0) FROM indexer_state"
                )
                latest_prices = await conn.fetchrow("""
                    SELECT
                        (SELECT mark_price FROM block_states
                         WHERE mark_price IS NOT NULL
                         ORDER BY block_number DESC
                         LIMIT 1) AS mark_price,
                        (SELECT index_price FROM block_states
                         WHERE index_price IS NOT NULL
                         ORDER BY block_number DESC
                         LIMIT 1) AS index_price
                """)

            snapshot = _decode_event_data(snapshot_raw)
            mark_price = None
            if latest_prices and latest_prices["mark_price"] is not None:
                mark_price = float(latest_prices["mark_price"])
            if mark_price is None:
                mark_price = _deployment_mark_price_fallback()

            index_price = None
            if latest_prices and latest_prices["index_price"] is not None:
                index_price = float(latest_prices["index_price"])
            if index_price is None:
                index_price = _deployment_index_price_fallback()

            _hydrate_snapshot_prices(snapshot, mark_price, index_price)

            response = {
                "block_number": int(last_indexed_block or 0),
                "snapshot": snapshot,
                "mark_price": mark_price,
                "index_price": index_price,
            }
            if isinstance(snapshot, dict):
                response["market"] = snapshot.get("market")
                response["pool"] = snapshot.get("pool")
                response["brokers"] = snapshot.get("brokers", [])
            return response
        except Exception as e:
            return _error_response(
                503,
                "latest snapshot unavailable",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    @app.get("/api/price-history")
    async def api_price_history(
        limit: int = FastAPIQuery(default=1000, ge=1, le=5000)
    ):
        """
        Price history from event-indexed block_states.

        Used by dashboard simulation tab when candle aggregation is sparse/empty.
        """
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT block_number, block_timestamp, mark_price, index_price
                    FROM block_states
                    WHERE block_timestamp > 0
                      AND (mark_price IS NOT NULL OR index_price IS NOT NULL)
                    ORDER BY block_number DESC
                    LIMIT $1
                """, limit)

            # Keep chronological order for chart rendering.
            ordered = list(reversed(rows))
            fallback_index = _deployment_index_price_fallback()
            last_index = fallback_index
            points: List[Dict[str, Any]] = []
            for r in ordered:
                mark = _as_price_or_none(r["mark_price"])
                idx = _as_price_or_none(r["index_price"])
                if idx is not None:
                    last_index = idx
                elif last_index is not None:
                    idx = last_index

                points.append({
                    "block_number": int(r["block_number"]),
                    "block_timestamp": int(r["block_timestamp"]),
                    "mark_price": mark,
                    "index_price": idx,
                })

            return {
                "points": points,
                "count": len(points),
            }
        except Exception as e:
            return _error_response(
                503,
                "price history unavailable",
                exc=e,
                expose_errors=expose_internal_errors,
            )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.graphql:app", host="0.0.0.0", port=8080, reload=False)
