import os
import sqlite3
import threading
import atexit
from typing import List, Optional

import clickhouse_connect
import strawberry
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from strawberry.fastapi import GraphQLRouter
from indexer.protocols import (
    AAVE_MARKET,
    MORPHO_MARKET,
    FLUID_MARKET,
    CHAINLINK_PRICES,
    SOFR_RATES,
    MORPHO_ALLOCATION,
    MORPHO_VAULT,
    READY_PROTOCOLS_DEFAULT,
    RAW_TABLE_BY_PROTOCOL,
    RAW_HEAD_QUERY_BY_PROTOCOL,
    PROCESSOR_STATE_ALIASES,
)

MAX_LIMIT = 10000
MAX_READY_LAG_BLOCKS = int(os.getenv("INDEXER_MAX_READY_LAG_BLOCKS", "250000"))
INDEXER_READY_PROTOCOLS = tuple(
    protocol.strip()
    for protocol in os.getenv(
        "INDEXER_READY_PROTOCOLS", ",".join(READY_PROTOCOLS_DEFAULT)
    ).split(",")
    if protocol.strip()
)
MORPHO_ALLOCATION_DB_PATH = os.getenv(
    "MORPHO_ALLOCATION_DB_PATH",
    "/app/morpho_data/morpho_enriched_final.db",
)
CLICKHOUSE_CONNECT_TIMEOUT = int(os.getenv("CLICKHOUSE_CONNECT_TIMEOUT", "5"))
CLICKHOUSE_SEND_RECEIVE_TIMEOUT = int(os.getenv("CLICKHOUSE_SEND_RECEIVE_TIMEOUT", "30"))
CLICKHOUSE_QUERY_RETRIES = int(os.getenv("CLICKHOUSE_QUERY_RETRIES", "1"))
CLICKHOUSE_AUTOGENERATE_SESSION_ID = (
    os.getenv("CLICKHOUSE_AUTOGENERATE_SESSION_ID", "false").strip().lower()
    in ("1", "true", "yes")
)
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_ASYNC_INSERT = (
    os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower()
    in ("1", "true", "yes")
)
CLICKHOUSE_WAIT_FOR_ASYNC_INSERT = (
    os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower()
    in ("1", "true", "yes")
)

_CLICKHOUSE_CLIENT = None
_CLICKHOUSE_LOCK = threading.Lock()
_TABLES_READY = False
API_MARKET_TIMESERIES_AGG_TABLE = "api_market_timeseries_hourly_agg"
API_PROTOCOL_TVL_AGG_TABLE = "api_protocol_tvl_entity_weekly_agg"


def _parse_cors_origins(env_name: str, default_origins: list[str]) -> list[str]:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default_origins
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if not origins:
        return default_origins
    return [origin for origin in origins if origin != "*"] or default_origins


@strawberry.type
class HistoricalRate:
    timestamp: int
    symbol: str
    apy: float
    price: float


@strawberry.type
class MarketSnapshot:
    symbol: str
    protocol: str
    supply_usd: float = strawberry.field(name="supplyUsd")
    borrow_usd: float = strawberry.field(name="borrowUsd")
    supply_apy: float = strawberry.field(name="supplyApy")
    borrow_apy: float = strawberry.field(name="borrowApy")
    utilization: float


@strawberry.type
class LatestRates:
    timestamp: int
    usdc: Optional[float] = None
    dai: Optional[float] = None
    usdt: Optional[float] = None
    sofr: Optional[float] = None
    susde: Optional[float] = None
    eth_price: Optional[float] = strawberry.field(name="ethPrice", default=None)


@strawberry.type
class MarketDetail:
    entity_id: str = strawberry.field(name="entityId")
    symbol: str
    protocol: str
    supply_usd: float = strawberry.field(name="supplyUsd")
    borrow_usd: float = strawberry.field(name="borrowUsd")
    supply_apy: float = strawberry.field(name="supplyApy")
    borrow_apy: float = strawberry.field(name="borrowApy")
    utilization: float
    collateral_symbol: Optional[str] = strawberry.field(name="collateralSymbol", default=None)
    lltv: Optional[float] = None


@strawberry.type
class ProtocolTvlPoint:
    date: str
    aave: float = 0.0
    morpho: float = 0.0
    euler: float = 0.0
    fluid: float = 0.0


@strawberry.type
class MarketTimeseriesPoint:
    timestamp: int
    supply_apy: Optional[float] = strawberry.field(name="supplyApy", default=None)
    borrow_apy: Optional[float] = strawberry.field(name="borrowApy", default=None)
    utilization: Optional[float] = None
    supply_usd: Optional[float] = strawberry.field(name="supplyUsd", default=None)
    borrow_usd: Optional[float] = strawberry.field(name="borrowUsd", default=None)


@strawberry.type
class VaultAllocationDetail:
    name: Optional[str]
    vault_address: str = strawberry.field(name="vaultAddress")
    shares: str


@strawberry.type
class VaultAllocationPoint:
    timestamp: int
    allocations: list[VaultAllocationDetail]


def _new_clickhouse_client():
    settings = {}
    if CLICKHOUSE_ASYNC_INSERT:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = 1 if CLICKHOUSE_WAIT_FOR_ASYNC_INSERT else 0
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        settings=settings,
        connect_timeout=CLICKHOUSE_CONNECT_TIMEOUT,
        send_receive_timeout=CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
        query_retries=CLICKHOUSE_QUERY_RETRIES,
        autogenerate_session_id=CLICKHOUSE_AUTOGENERATE_SESSION_ID,
    )


def _ensure_support_tables(ch) -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS processor_state (
            protocol String,
            last_processed_block UInt64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY protocol
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS collector_state (
            protocol String,
            last_collected_block UInt64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY protocol
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS api_market_latest (
            protocol LowCardinality(String),
            entity_id String,
            symbol LowCardinality(String),
            target_id String,
            timestamp DateTime,
            supply_usd Float64,
            borrow_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            price_usd Float64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (protocol, entity_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS api_market_timeseries_hourly_agg (
            protocol LowCardinality(String),
            entity_id String,
            ts DateTime,
            supply_apy_state AggregateFunction(avg, Float64),
            borrow_apy_state AggregateFunction(avg, Float64),
            utilization_state AggregateFunction(avg, Float64),
            supply_usd_state AggregateFunction(avg, Float64),
            borrow_usd_state AggregateFunction(avg, Float64)
        ) ENGINE = AggregatingMergeTree()
        PARTITION BY toStartOfMonth(ts)
        ORDER BY (entity_id, ts, protocol)
        TTL ts + INTERVAL 18 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS api_protocol_tvl_entity_weekly_agg (
            day DateTime,
            protocol LowCardinality(String),
            entity_id String,
            supply_usd_state AggregateFunction(argMax, Float64, DateTime)
        ) ENGINE = AggregatingMergeTree()
        PARTITION BY toStartOfMonth(day)
        ORDER BY (day, protocol, entity_id)
        TTL day + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_api_market_timeseries_hourly_agg
        TO api_market_timeseries_hourly_agg
        AS
        SELECT
            protocol,
            entity_id,
            toStartOfHour(timestamp) AS ts,
            avgState(toFloat64(supply_apy)) AS supply_apy_state,
            avgState(toFloat64(borrow_apy)) AS borrow_apy_state,
            avgState(toFloat64(utilization)) AS utilization_state,
            avgState(toFloat64(supply_usd)) AS supply_usd_state,
            avgState(toFloat64(borrow_usd)) AS borrow_usd_state
        FROM unified_timeseries
        GROUP BY protocol, entity_id, ts
        """
    )
    ch.command(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_api_protocol_tvl_entity_weekly_agg
        TO api_protocol_tvl_entity_weekly_agg
        AS
        SELECT
            toStartOfWeek(timestamp) AS day,
            splitByChar('_', protocol)[1] AS protocol,
            entity_id,
            argMaxState(toFloat64(supply_usd), timestamp) AS supply_usd_state
        FROM unified_timeseries
        WHERE protocol IN ('AAVE_MARKET', 'MORPHO_MARKET', 'EULER_MARKET', 'FLUID_MARKET')
        GROUP BY day, protocol, entity_id
        """
    )
    # Bootstrap pre-aggregated latest table once on fresh deployments.
    latest_count = _query_int(ch, "SELECT count() FROM api_market_latest")
    if latest_count == 0:
        ch.command(
            """
            INSERT INTO api_market_latest
            (
                protocol, entity_id, symbol, target_id, timestamp,
                supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd
            )
            SELECT
                protocol,
                entity_id,
                argMax(symbol, timestamp) AS symbol,
                argMax(target_id, timestamp) AS target_id,
                max(timestamp) AS timestamp,
                argMax(supply_usd, timestamp) AS supply_usd,
                argMax(borrow_usd, timestamp) AS borrow_usd,
                argMax(supply_apy, timestamp) AS supply_apy,
                argMax(borrow_apy, timestamp) AS borrow_apy,
                argMax(utilization, timestamp) AS utilization,
                argMax(price_usd, timestamp) AS price_usd
            FROM unified_timeseries
            GROUP BY protocol, entity_id
            """
        )
    hourly_count = _query_int(ch, "SELECT count() FROM api_market_timeseries_hourly_agg")
    if hourly_count == 0:
        ch.command(
            """
            INSERT INTO api_market_timeseries_hourly_agg
            SELECT
                protocol,
                entity_id,
                toStartOfHour(timestamp) AS ts,
                avgState(toFloat64(supply_apy)) AS supply_apy_state,
                avgState(toFloat64(borrow_apy)) AS borrow_apy_state,
                avgState(toFloat64(utilization)) AS utilization_state,
                avgState(toFloat64(supply_usd)) AS supply_usd_state,
                avgState(toFloat64(borrow_usd)) AS borrow_usd_state
            FROM unified_timeseries
            GROUP BY protocol, entity_id, ts
            """
        )
    weekly_count = _query_int(ch, "SELECT count() FROM api_protocol_tvl_entity_weekly_agg")
    if weekly_count == 0:
        ch.command(
            """
            INSERT INTO api_protocol_tvl_entity_weekly_agg
            SELECT
                toStartOfWeek(timestamp) AS day,
                splitByChar('_', protocol)[1] AS protocol,
                entity_id,
                argMaxState(toFloat64(supply_usd), timestamp) AS supply_usd_state
            FROM unified_timeseries
            WHERE protocol IN ('AAVE_MARKET', 'MORPHO_MARKET', 'EULER_MARKET', 'FLUID_MARKET')
            GROUP BY day, protocol, entity_id
            """
        )
    _TABLES_READY = True


def close_clickhouse_client() -> None:
    global _CLICKHOUSE_CLIENT
    with _CLICKHOUSE_LOCK:
        if _CLICKHOUSE_CLIENT is None:
            return
        try:
            _CLICKHOUSE_CLIENT.close_connections()
        except Exception:
            pass
        try:
            _CLICKHOUSE_CLIENT.close()
        except Exception:
            pass
        _CLICKHOUSE_CLIENT = None


def get_clickhouse_client():
    global _CLICKHOUSE_CLIENT
    with _CLICKHOUSE_LOCK:
        if _CLICKHOUSE_CLIENT is None:
            _CLICKHOUSE_CLIENT = _new_clickhouse_client()
        _ensure_support_tables(_CLICKHOUSE_CLIENT)
        return _CLICKHOUSE_CLIENT


atexit.register(close_clickhouse_client)


def _query_int(ch, sql: str) -> int:
    value = ch.command(sql)
    if value in (None, "", "None"):
        return 0
    return int(value)


def _collect_processing_lag(ch, protocols: Optional[list[str]] = None) -> dict[str, int]:
    monitored = protocols or list(RAW_TABLE_BY_PROTOCOL.keys())
    lag_by_protocol: dict[str, int] = {}
    for protocol in monitored:
        raw_table = RAW_TABLE_BY_PROTOCOL.get(protocol)
        state_protocols = PROCESSOR_STATE_ALIASES.get(protocol, (protocol,))
        if raw_table is None:
            lag_by_protocol[protocol] = -1
            continue
        state_in = ", ".join(f"'{_escape_sql_string(p)}'" for p in state_protocols)
        try:
            raw_head = _query_int(ch, f"SELECT max(block_number) FROM {raw_table}")
            proc_head = _query_int(
                ch,
                f"SELECT max(last_processed_block) FROM processor_state WHERE protocol IN ({state_in})",
            )
            lag_by_protocol[protocol] = max(0, raw_head - proc_head)
        except Exception:
            lag_by_protocol[protocol] = -1
    return lag_by_protocol


def _collect_collector_lag(ch, protocols: Optional[list[str]] = None) -> dict[str, int]:
    monitored = protocols or list(RAW_TABLE_BY_PROTOCOL.keys())
    lag_by_protocol: dict[str, int] = {}
    for protocol in monitored:
        raw_head_query = RAW_HEAD_QUERY_BY_PROTOCOL.get(protocol)
        if raw_head_query is None:
            lag_by_protocol[protocol] = -1
            continue
        try:
            raw_head = _query_int(ch, raw_head_query)
            collected_head = _query_int(
                ch,
                f"SELECT max(last_collected_block) FROM collector_state WHERE protocol = '{_escape_sql_string(protocol)}'",
            )
            lag_by_protocol[protocol] = max(0, raw_head - collected_head)
        except Exception:
            lag_by_protocol[protocol] = -1
    return lag_by_protocol


def _safe_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def _escape_sql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _normalize_rate_symbol(symbol: str) -> str:
    upper = symbol.upper()
    if upper == "SUSDE":
        return "sUSDe"
    if upper == "ETH":
        return "WETH"
    return upper


def _time_bucket_expr(resolution: str, column: str = "timestamp") -> str:
    mapping = {
        "1H": f"toStartOfHour({column})",
        "4H": f"toStartOfInterval({column}, INTERVAL 4 HOUR)",
        "1D": f"toStartOfDay({column})",
        "1W": f"toStartOfWeek({column})",
    }
    return mapping.get(resolution.upper(), f"toStartOfHour({column})")


def _query_historical_rates(ch, symbols: list[str], resolution: str, limit: int) -> list[HistoricalRate]:
    normalized = [_normalize_rate_symbol(s) for s in symbols]
    time_expr = _time_bucket_expr(resolution)
    queries: list[str] = []

    aave_symbols = [s for s in normalized if s not in ("SOFR", "WETH")]
    if aave_symbols:
        in_aave = ", ".join(f"'{_escape_sql_string(s)}'" for s in sorted(set(aave_symbols)))
        queries.append(
            f"""
            SELECT
                toUnixTimestamp({time_expr}) AS ts,
                symbol,
                avg(borrow_apy) AS apy,
                avg(price_usd) AS price
            FROM aave_timeseries
            WHERE protocol = 'AAVE_MARKET' AND symbol IN ({in_aave})
            GROUP BY ts, symbol
            """
        )

    if "SOFR" in normalized:
        queries.append(
            f"""
            SELECT
                toUnixTimestamp({time_expr}) AS ts,
                'SOFR' AS symbol,
                avg(apy) AS apy,
                0.0 AS price
            FROM raw_sofr_rates
            GROUP BY ts, symbol
            """
        )

    if "WETH" in normalized:
        queries.append(
            f"""
            SELECT
                toUnixTimestamp({time_expr}) AS ts,
                'WETH' AS symbol,
                0.0 AS apy,
                avg(price) AS price
            FROM chainlink_prices
            WHERE feed = 'ETH / USD'
            GROUP BY ts, symbol
            """
        )

    if not queries:
        return []

    sql = " UNION ALL ".join(queries)
    sql = f"SELECT ts, symbol, apy, price FROM ({sql}) ORDER BY ts DESC LIMIT {_safe_limit(limit)}"
    res = ch.query(sql)
    return [
        HistoricalRate(
            timestamp=int(row[0]),
            symbol=str(row[1]),
            apy=float(row[2]),
            price=float(row[3]),
        )
        for row in res.result_rows
    ]


def _query_market_snapshots(ch) -> list[MarketSnapshot]:
    sql = """
    SELECT
        symbol,
        protocol,
        supply_usd,
        borrow_usd,
        supply_apy,
        borrow_apy,
        if(supply_usd > 0, borrow_usd / supply_usd, 0.0) AS utilization
    FROM api_market_latest FINAL
    WHERE supply_usd >= 1000 OR borrow_usd >= 1000 OR protocol LIKE 'AAVE%'
    ORDER BY supply_usd DESC
    """
    res = ch.query(sql)
    return [
        MarketSnapshot(
            symbol=str(row[0]),
            protocol=str(row[1]),
            supply_usd=float(row[2]),
            borrow_usd=float(row[3]),
            supply_apy=float(row[4]),
            borrow_apy=float(row[5]),
            utilization=float(row[6]),
        )
        for row in res.result_rows
    ]


def _query_latest_rates(ch) -> Optional[LatestRates]:
    latest = LatestRates(timestamp=0)
    max_ts = 0

    aave_sql = """
    SELECT symbol, argMax(borrow_apy, timestamp) AS apy, toUnixTimestamp(max(timestamp)) AS ts
    FROM aave_timeseries
    WHERE protocol = 'AAVE_MARKET' AND symbol IN ('USDC', 'DAI', 'USDT', 'sUSDe')
    GROUP BY symbol
    """
    for symbol, apy, ts in ch.query(aave_sql).result_rows:
        if symbol == "USDC":
            latest.usdc = float(apy)
        elif symbol == "DAI":
            latest.dai = float(apy)
        elif symbol == "USDT":
            latest.usdt = float(apy)
        elif symbol == "sUSDe":
            latest.susde = float(apy)
        max_ts = max(max_ts, int(ts or 0))

    sofr_row = ch.query(
        "SELECT argMax(apy, timestamp) AS apy, toUnixTimestamp(max(timestamp)) AS ts FROM raw_sofr_rates"
    ).result_rows
    if sofr_row and sofr_row[0][0] is not None:
        latest.sofr = float(sofr_row[0][0])
        max_ts = max(max_ts, int(sofr_row[0][1] or 0))

    eth_row = ch.query(
        "SELECT argMax(price, timestamp) AS price, toUnixTimestamp(max(timestamp)) AS ts "
        "FROM chainlink_prices WHERE feed = 'ETH / USD'"
    ).result_rows
    if eth_row and eth_row[0][0] is not None:
        latest.eth_price = float(eth_row[0][0])
        max_ts = max(max_ts, int(eth_row[0][1] or 0))

    if (
        latest.usdc is None
        and latest.dai is None
        and latest.usdt is None
        and latest.sofr is None
        and latest.susde is None
        and latest.eth_price is None
    ):
        return None

    latest.timestamp = max_ts
    return latest


def _query_protocol_markets(ch, protocol: str) -> list[MarketDetail]:
    allowed = {
        AAVE_MARKET,
        MORPHO_MARKET,
        MORPHO_VAULT,
        MORPHO_ALLOCATION,
        "EULER_MARKET",
        FLUID_MARKET,
    }
    if protocol not in allowed:
        return []

    escaped_protocol = _escape_sql_string(protocol)
    if protocol.startswith("MORPHO"):
        query = f"""
        SELECT t.entity_id, t.symbol, t.proto, t.supply_usd, t.borrow_usd,
               t.supply_apy, t.borrow_apy, t.utilization,
               COALESCE(p.collateral_symbol, '') AS collateral_symbol,
               COALESCE(p.lltv, 0) AS lltv
        FROM (
            SELECT entity_id,
                   symbol,
                   '{escaped_protocol}' AS proto,
                   supply_usd,
                   borrow_usd,
                   supply_apy,
                   borrow_apy,
                   utilization
            FROM api_market_latest FINAL
            WHERE protocol = '{escaped_protocol}'
        ) AS t
        LEFT JOIN morpho_market_params AS p ON t.entity_id = p.market_id
        WHERE t.supply_usd >= 1000 OR t.borrow_usd >= 1000
        ORDER BY t.supply_usd DESC
        """
    else:
        query = f"""
        SELECT entity_id, symbol, proto, supply_usd, borrow_usd,
               supply_apy, borrow_apy, utilization,
               '' AS collateral_symbol, 0 AS lltv
        FROM (
            SELECT entity_id,
                   symbol,
                   '{escaped_protocol}' AS proto,
                   supply_usd,
                   borrow_usd,
                   supply_apy,
                   borrow_apy,
                   utilization
            FROM api_market_latest FINAL
            WHERE protocol = '{escaped_protocol}'
        )
        WHERE supply_usd >= 1000 OR borrow_usd >= 1000
        ORDER BY supply_usd DESC
        """

    res = ch.query(query)
    return [
        MarketDetail(
            entity_id=str(row[0]),
            symbol=str(row[1]),
            protocol=str(row[2]),
            supply_usd=float(row[3]),
            borrow_usd=float(row[4]),
            supply_apy=float(row[5]),
            borrow_apy=float(row[6]),
            utilization=float(row[7]),
            collateral_symbol=str(row[8]) if row[8] else None,
            lltv=float(row[9]) if row[9] else None,
        )
        for row in res.result_rows
    ]


def _query_protocol_tvl_history(ch) -> list[ProtocolTvlPoint]:
    try:
        res = ch.query(
            f"""
            SELECT day, protocol, sum(argMaxMerge(supply_usd_state)) AS total_supply
            FROM {API_PROTOCOL_TVL_AGG_TABLE}
            GROUP BY day, protocol
            ORDER BY day ASC
            """
        )
    except Exception:
        # Compatibility fallback while weekly pre-agg table backfills.
        res = ch.query(
            """
            SELECT day, protocol, sum(supply_usd) AS total_supply
            FROM (
                SELECT entity_id,
                       splitByChar('_', protocol)[1] AS protocol,
                       toStartOfWeek(timestamp) AS day,
                       argMax(supply_usd, timestamp) AS supply_usd
                FROM unified_timeseries
                WHERE protocol IN ('AAVE_MARKET', 'MORPHO_MARKET', 'EULER_MARKET', 'FLUID_MARKET')
                GROUP BY entity_id, protocol, day
            )
            GROUP BY day, protocol
            ORDER BY day ASC
            """
        )
    pivot: dict[str, dict[str, float]] = {}
    for day, protocol, total_supply in res.result_rows:
        date_key = str(day)
        if date_key not in pivot:
            pivot[date_key] = {}
        pivot[date_key][str(protocol).upper()] = float(total_supply)

    return [
        ProtocolTvlPoint(
            date=date_key,
            aave=values.get("AAVE", 0.0),
            morpho=values.get("MORPHO", 0.0),
            euler=values.get("EULER", 0.0),
            fluid=values.get("FLUID", 0.0),
        )
        for date_key, values in sorted(pivot.items(), key=lambda item: item[0])
    ]


def _query_market_timeseries(ch, entity_id: str, resolution: str, limit: int) -> list[MarketTimeseriesPoint]:
    time_expr = _time_bucket_expr(resolution, "ts")
    sql = f"""
    SELECT
        toUnixTimestamp({time_expr}) AS ts,
        avgMerge(supply_apy_state) AS supply_apy,
        avgMerge(borrow_apy_state) AS borrow_apy,
        avgMerge(utilization_state) AS utilization,
        avgMerge(supply_usd_state) AS supply_usd,
        avgMerge(borrow_usd_state) AS borrow_usd
    FROM {API_MARKET_TIMESERIES_AGG_TABLE}
    WHERE entity_id LIKE %(eid_prefix)s
    GROUP BY ts
    ORDER BY ts DESC
    LIMIT %(lim)s
    """
    try:
        res = ch.query(
            sql,
            parameters={"eid_prefix": f"{entity_id}%", "lim": _safe_limit(limit)},
        )
    except Exception:
        # Compatibility fallback while hourly pre-agg table backfills.
        res = ch.query(
            f"""
            SELECT
                toUnixTimestamp({time_expr}) AS ts,
                avg(supply_apy) AS supply_apy,
                avg(borrow_apy) AS borrow_apy,
                avg(utilization) AS utilization,
                avg(supply_usd) AS supply_usd,
                avg(borrow_usd) AS borrow_usd
            FROM unified_timeseries
            WHERE entity_id LIKE %(eid_prefix)s
            GROUP BY ts
            ORDER BY ts DESC
            LIMIT %(lim)s
            """,
            parameters={"eid_prefix": f"{entity_id}%", "lim": _safe_limit(limit)},
        )
    points = [
        MarketTimeseriesPoint(
            timestamp=int(row[0]),
            supply_apy=float(row[1]) if row[1] is not None else None,
            borrow_apy=float(row[2]) if row[2] is not None else None,
            utilization=float(row[3]) if row[3] is not None else None,
            supply_usd=float(row[4]) if row[4] is not None else None,
            borrow_usd=float(row[5]) if row[5] is not None else None,
        )
        for row in res.result_rows
    ]
    points.reverse()
    return points


def _query_market_vault_allocations(ch, entity_id: str, limit: int) -> list[VaultAllocationPoint]:
    # Preferred path: ClickHouse table produced by Morpho processor.
    try:
        where_expr = f"market_id LIKE '{_escape_sql_string(entity_id)}%'" if len(entity_id) < 60 else f"market_id = '{_escape_sql_string(entity_id)}'"
        rows = ch.query(
            f"""
            SELECT
                toUInt64(intDiv(timestamp, 86400) * 86400) AS day_ts,
                vault_address,
                argMax(supply_shares, timestamp) AS shares
            FROM morpho_vault_allocations
            WHERE {where_expr}
            GROUP BY day_ts, vault_address
            ORDER BY day_ts DESC
            LIMIT {_safe_limit(limit) * 128}
            """
        ).result_rows
        meta_rows = ch.query(
            "SELECT lower(vault_address), name FROM morpho_vault_meta"
        ).result_rows
        meta = {str(addr): str(name) for addr, name in meta_rows}
    except Exception:
        rows = []
        meta = {}

    if rows:
        grouped: dict[int, list[VaultAllocationDetail]] = {}
        for day_ts, vault_address, shares in rows:
            day_ts_i = int(day_ts)
            if day_ts_i not in grouped:
                grouped[day_ts_i] = []
            vault_str = str(vault_address)
            grouped[day_ts_i].append(
                VaultAllocationDetail(
                    name=meta.get(vault_str.lower(), "Unknown Vault"),
                    vault_address=vault_str,
                    shares=str(float(shares or 0.0)),
                )
            )
        for day_ts in grouped:
            grouped[day_ts].sort(key=lambda item: float(item.shares), reverse=True)
        selected_days = sorted(grouped.keys(), reverse=True)[: _safe_limit(limit)]
        points = [
            VaultAllocationPoint(timestamp=day_ts, allocations=grouped[day_ts])
            for day_ts in selected_days
        ]
        points.reverse()
        return points

    # Legacy fallback to sqlite if historical table exists.
    if not os.path.exists(MORPHO_ALLOCATION_DB_PATH):
        return []
    conn = sqlite3.connect(MORPHO_ALLOCATION_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        db_entity_id = entity_id
        where_clause = "market_id LIKE ?" if len(entity_id) < 60 else "market_id = ?"
        if len(entity_id) < 60:
            db_entity_id += "%"
        cur.execute("SELECT lower(vault_address), name FROM vault_meta")
        meta = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute(
            f"""
            SELECT timestamp, vault_address, supply_shares
            FROM vault_allocations
            WHERE {where_clause}
            """,
            (db_entity_id,),
        )
        rows = cur.fetchall()
        daily_latest: dict[tuple[int, str], dict[str, int | float]] = {}
        for row in rows:
            ts = int(row["timestamp"])
            vault_address = str(row["vault_address"])
            shares = float(row["supply_shares"] or 0.0)
            day_ts = (ts // 86400) * 86400
            key = (day_ts, vault_address)
            existing = daily_latest.get(key)
            if existing is None or int(existing["ts"]) < ts:
                daily_latest[key] = {"ts": ts, "shares": shares}
        grouped: dict[int, list[VaultAllocationDetail]] = {}
        for (day_ts, vault_address), data in daily_latest.items():
            if day_ts not in grouped:
                grouped[day_ts] = []
            grouped[day_ts].append(
                VaultAllocationDetail(
                    name=meta.get(vault_address.lower(), "Unknown Vault"),
                    vault_address=vault_address,
                    shares=str(data["shares"]),
                )
            )
        for day_ts in grouped:
            grouped[day_ts].sort(key=lambda item: float(item.shares), reverse=True)
        selected_days = sorted(grouped.keys(), reverse=True)[: _safe_limit(limit)]
        points = [
            VaultAllocationPoint(timestamp=day_ts, allocations=grouped[day_ts])
            for day_ts in selected_days
        ]
        points.reverse()
        return points
    finally:
        conn.close()


@strawberry.type
class Query:
    @strawberry.field(name="historicalRates")
    def historical_rates(
        self, symbols: List[str], resolution: str, limit: int = 17520
    ) -> List[HistoricalRate]:
        ch = get_clickhouse_client()
        return _query_historical_rates(ch, symbols, resolution, limit)

    @strawberry.field(name="marketSnapshots")
    def market_snapshots(self) -> List[MarketSnapshot]:
        ch = get_clickhouse_client()
        return _query_market_snapshots(ch)

    @strawberry.field(name="latestRates")
    def latest_rates(self) -> Optional[LatestRates]:
        ch = get_clickhouse_client()
        return _query_latest_rates(ch)

    @strawberry.field(name="protocolMarkets")
    def protocol_markets(self, protocol: str = "AAVE_MARKET") -> list[MarketDetail]:
        ch = get_clickhouse_client()
        return _query_protocol_markets(ch, protocol)

    @strawberry.field(name="protocolTvlHistory")
    def protocol_tvl_history(self) -> list[ProtocolTvlPoint]:
        ch = get_clickhouse_client()
        return _query_protocol_tvl_history(ch)

    @strawberry.field(name="marketTimeseries")
    def market_timeseries(
        self, entity_id: str, resolution: str = "1H", limit: int = 2000
    ) -> list[MarketTimeseriesPoint]:
        ch = get_clickhouse_client()
        return _query_market_timeseries(ch, entity_id, resolution, limit)

    @strawberry.field(name="marketVaultAllocations")
    def market_vault_allocations(
        self, entity_id: str, limit: int = 365
    ) -> list[VaultAllocationPoint]:
        ch = get_clickhouse_client()
        return _query_market_vault_allocations(ch, entity_id, limit)


schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(schema)
app = FastAPI(title="RLD ClickHouse GraphQL")
_CORS_ORIGINS = _parse_cors_origins(
    "ENVIO_CORS_ORIGINS",
    [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://rld.fi",
        "https://www.rld.fi",
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(graphql_app, prefix="/graphql")
app.include_router(graphql_app, prefix="/envio-graphql")


@app.get("/healthz")
def healthz():
    try:
        ch = get_clickhouse_client()
        ch.command("SELECT 1")
        return {
            "status": "ok",
            "clickhouse": "ok",
            "collectorLag": _collect_collector_lag(ch),
            "processingLag": _collect_processing_lag(ch),
        }
    except Exception as exc:
        close_clickhouse_client()
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "clickhouse": "down", "error": str(exc)},
        )


@app.get("/livez")
def livez():
    # Lightweight liveness check used by Docker healthcheck.
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    try:
        ch = get_clickhouse_client()
        ch.command("SELECT 1")
        collector_lag_by_protocol = _collect_collector_lag(ch, list(INDEXER_READY_PROTOCOLS))
        lag_by_protocol = _collect_processing_lag(ch, list(INDEXER_READY_PROTOCOLS))
        failing_processing = [
            protocol
            for protocol, lag in lag_by_protocol.items()
            if lag >= 0 and lag > MAX_READY_LAG_BLOCKS
        ]
        failing_collector = [
            protocol
            for protocol, lag in collector_lag_by_protocol.items()
            if lag >= 0 and lag > MAX_READY_LAG_BLOCKS
        ]
        failing = sorted(set(failing_processing + failing_collector))
        if failing:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": "lag_exceeded",
                    "maxLagBlocks": MAX_READY_LAG_BLOCKS,
                    "collectorLag": collector_lag_by_protocol,
                    "processingLag": lag_by_protocol,
                    "failingProtocols": failing,
                },
            )
        return {
            "status": "ready",
            "maxLagBlocks": MAX_READY_LAG_BLOCKS,
            "collectorLag": collector_lag_by_protocol,
            "processingLag": lag_by_protocol,
        }
    except Exception as exc:
        close_clickhouse_client()
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "clickhouse_unavailable", "error": str(exc)},
        )


def create_app():
    return app
