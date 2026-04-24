import os
import sqlite3
import threading
import atexit
import math
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
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
    MORPHO_ALLOCATION,
    MORPHO_VAULT,
    READY_PROTOCOLS_DEFAULT,
    RAW_TABLE_BY_PROTOCOL,
    RAW_HEAD_QUERY_BY_PROTOCOL,
    PROCESSOR_STATE_ALIASES,
)
from indexer.tokens import TOKENS, get_usd_price

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
AAVE_FLOW_DAILY_AGG_TABLE = "api_aave_market_flow_daily_agg"
API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE = "api_chainlink_price_weekly_agg"
TVL_PROTOCOLS = ("AAVE", "MORPHO", "EULER", "FLUID")
TVL_SYNTHETIC_ENTITY_IDS = {"AAVE_MARKET_SYNTHETIC"}
AAVE_FLOW_EVENT_NAMES = (
    "Supply",
    "Withdraw",
    "Borrow",
    "Repay",
    "LiquidationCall",
    "MintedToTreasury",
)


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
    entity_id: str = strawberry.field(name="entityId")
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
class ProtocolApyPoint:
    timestamp: int
    average_supply_apy: float = strawberry.field(name="averageSupplyApy")
    average_borrow_apy: float = strawberry.field(name="averageBorrowApy")


@strawberry.type
class MarketTimeseriesPoint:
    timestamp: int
    supply_apy: Optional[float] = strawberry.field(name="supplyApy", default=None)
    borrow_apy: Optional[float] = strawberry.field(name="borrowApy", default=None)
    utilization: Optional[float] = None
    supply_usd: Optional[float] = strawberry.field(name="supplyUsd", default=None)
    borrow_usd: Optional[float] = strawberry.field(name="borrowUsd", default=None)


@strawberry.type
class MarketFlowPoint:
    timestamp: int
    supply_inflow_usd: float = strawberry.field(name="supplyInflowUsd")
    supply_outflow_usd: float = strawberry.field(name="supplyOutflowUsd")
    borrow_inflow_usd: float = strawberry.field(name="borrowInflowUsd")
    borrow_outflow_usd: float = strawberry.field(name="borrowOutflowUsd")
    net_supply_flow_usd: float = strawberry.field(name="netSupplyFlowUsd")
    net_borrow_flow_usd: float = strawberry.field(name="netBorrowFlowUsd")
    cumulative_supply_net_inflow_usd: float = strawberry.field(
        name="cumulativeSupplyNetInflowUsd", default=0.0
    )
    cumulative_borrow_net_inflow_usd: float = strawberry.field(
        name="cumulativeBorrowNetInflowUsd", default=0.0
    )


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
        f"""
        CREATE TABLE IF NOT EXISTS {AAVE_FLOW_DAILY_AGG_TABLE} (
            day DateTime,
            entity_id String,
            supply_inflow_raw_state AggregateFunction(sum, UInt256),
            supply_outflow_raw_state AggregateFunction(sum, UInt256),
            borrow_inflow_raw_state AggregateFunction(sum, UInt256),
            borrow_outflow_raw_state AggregateFunction(sum, UInt256)
        ) ENGINE = AggregatingMergeTree()
        PARTITION BY toStartOfMonth(day)
        ORDER BY (entity_id, day)
        TTL day + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE} (
            day DateTime,
            feed LowCardinality(String),
            price_state AggregateFunction(argMax, Float64, DateTime)
        ) ENGINE = AggregatingMergeTree()
        PARTITION BY toStartOfMonth(day)
        ORDER BY (feed, day)
        TTL day + INTERVAL 72 MONTH DELETE
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
        SELECT day, clean_protocol AS protocol, entity_id, supply_usd_state
        FROM (
            SELECT
                toStartOfWeek(timestamp) AS day,
                splitByChar('_', protocol)[1] AS clean_protocol,
                entity_id,
                argMaxState(toFloat64(supply_usd), inserted_at) AS supply_usd_state
            FROM unified_timeseries
            WHERE protocol IN ('AAVE_MARKET', 'MORPHO_MARKET', 'EULER_MARKET', 'FLUID_MARKET')
              AND entity_id NOT IN ('AAVE_MARKET_SYNTHETIC')
            GROUP BY day, clean_protocol, entity_id
        )
        """
    )
    ch.command(
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_{API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}
        TO {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}
        AS
        SELECT
            toStartOfWeek(timestamp) AS day,
            feed,
            argMaxState(toFloat64(price), timestamp) AS price_state
        FROM chainlink_prices
        WHERE feed IN ('BTC / USD', 'ETH / USD')
        GROUP BY day, feed
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
                tupleElement(latest_tuple, 1) AS symbol,
                tupleElement(latest_tuple, 2) AS target_id,
                tupleElement(latest_tuple, 3) AS timestamp,
                tupleElement(latest_tuple, 4) AS supply_usd,
                tupleElement(latest_tuple, 5) AS borrow_usd,
                tupleElement(latest_tuple, 6) AS supply_apy,
                tupleElement(latest_tuple, 7) AS borrow_apy,
                tupleElement(latest_tuple, 8) AS utilization,
                tupleElement(latest_tuple, 9) AS price_usd
            FROM (
                SELECT
                    protocol,
                    entity_id,
                    argMax(
                        tuple(
                            symbol,
                            target_id,
                            timestamp,
                            supply_usd,
                            borrow_usd,
                            supply_apy,
                            borrow_apy,
                            utilization,
                            price_usd
                        ),
                        timestamp
                    ) AS latest_tuple
                FROM unified_timeseries
                WHERE entity_id != 'AAVE_MARKET_SYNTHETIC'
                GROUP BY protocol, entity_id
            )
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
            WHERE entity_id != 'AAVE_MARKET_SYNTHETIC'
            GROUP BY protocol, entity_id, ts
            """
        )
    weekly_count = _query_int(ch, "SELECT count() FROM api_protocol_tvl_entity_weekly_agg")
    if weekly_count == 0:
        ch.command(
            """
            INSERT INTO api_protocol_tvl_entity_weekly_agg
            SELECT day, clean_protocol AS protocol, entity_id, supply_usd_state
            FROM (
                SELECT
                    toStartOfWeek(timestamp) AS day,
                    splitByChar('_', protocol)[1] AS clean_protocol,
                    entity_id,
                    argMaxState(toFloat64(supply_usd), inserted_at) AS supply_usd_state
                FROM unified_timeseries
                WHERE protocol IN ('AAVE_MARKET', 'MORPHO_MARKET', 'EULER_MARKET', 'FLUID_MARKET')
                  AND entity_id != 'AAVE_MARKET_SYNTHETIC'
                GROUP BY day, clean_protocol, entity_id
            )
            """
        )
    weekly_price_count = _query_int(
        ch, f"SELECT count() FROM {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}"
    )
    if weekly_price_count == 0:
        ch.command(
            f"""
            INSERT INTO {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}
            SELECT
                toStartOfWeek(timestamp) AS day,
                feed,
                argMaxState(toFloat64(price), timestamp) AS price_state
            FROM chainlink_prices
            WHERE feed IN ('BTC / USD', 'ETH / USD')
            GROUP BY day, feed
            """
        )
    ch.command(
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_{AAVE_FLOW_DAILY_AGG_TABLE}
        TO {AAVE_FLOW_DAILY_AGG_TABLE}
        AS
        SELECT
            day,
            entity_id,
            sumState(supply_inflow_raw) AS supply_inflow_raw_state,
            sumState(supply_outflow_raw) AS supply_outflow_raw_state,
            sumState(borrow_inflow_raw) AS borrow_inflow_raw_state,
            sumState(borrow_outflow_raw) AS borrow_outflow_raw_state
        FROM (
            SELECT
                toStartOfDay(block_timestamp) AS day,
                lower(concat('0x', substring(ifNull(topic1, ''), 27))) AS entity_id,
                if(
                    event_name = 'Supply',
                    if(length(data) >= 130, reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))), toUInt256(0)),
                    if(event_name = 'MintedToTreasury', if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)), toUInt256(0))
                ) AS supply_inflow_raw,
                if(
                    event_name = 'Withdraw',
                    if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)),
                    if(
                        event_name = 'Repay'
                        AND if(length(data) >= 130, reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))), toUInt256(0)) = toUInt256(1),
                        if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)),
                        toUInt256(0)
                    )
                ) AS supply_outflow_raw,
                if(
                    event_name = 'Borrow',
                    if(length(data) >= 130, reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))), toUInt256(0)),
                    toUInt256(0)
                ) AS borrow_inflow_raw,
                if(
                    event_name = 'Repay',
                    if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)),
                    toUInt256(0)
                ) AS borrow_outflow_raw
            FROM aave_events
            WHERE event_name IN ('Supply', 'Withdraw', 'Borrow', 'Repay', 'MintedToTreasury')

            UNION ALL

            SELECT
                toStartOfDay(block_timestamp) AS day,
                lower(concat('0x', substring(ifNull(topic1, ''), 27))) AS entity_id,
                toUInt256(0) AS supply_inflow_raw,
                if(length(data) >= 130, reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))), toUInt256(0)) AS supply_outflow_raw,
                toUInt256(0) AS borrow_inflow_raw,
                toUInt256(0) AS borrow_outflow_raw
            FROM aave_events
            WHERE event_name = 'LiquidationCall'

            UNION ALL

            SELECT
                toStartOfDay(block_timestamp) AS day,
                lower(concat('0x', substring(ifNull(topic2, ''), 27))) AS entity_id,
                toUInt256(0) AS supply_inflow_raw,
                toUInt256(0) AS supply_outflow_raw,
                toUInt256(0) AS borrow_inflow_raw,
                if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)) AS borrow_outflow_raw
            FROM aave_events
            WHERE event_name = 'LiquidationCall'
        )
        WHERE length(entity_id) = 42
        GROUP BY day, entity_id
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


def _bucket_seconds(resolution: str) -> int:
    mapping = {
        "1H": 3600,
        "4H": 4 * 3600,
        "1D": 24 * 3600,
        "1W": 7 * 24 * 3600,
    }
    return mapping.get(resolution.upper(), 3600)


def _normalize_entity_id(entity_id: str) -> str:
    normalized = str(entity_id or "").strip().lower()
    if not normalized:
        return normalized
    if not normalized.startswith("0x"):
        normalized = f"0x{normalized}"
    return normalized


def _candidate_chainlink_feeds_for_symbol(symbol: str) -> tuple[str, ...]:
    raw = str(symbol or "").strip()
    upper = raw.upper()
    feeds: set[str] = {"ETH / USD", "BTC / USD"}
    if raw:
        feeds.update(
            {
                f"{raw} / USD",
                f"{upper} / USD",
                f"{raw} / ETH",
                f"{upper} / ETH",
                f"{raw} / BTC",
                f"{upper} / BTC",
            }
        )

    # Normalize known feed naming quirks for get_usd_price().
    if upper in {"WSTETH", "STETH"}:
        feeds.update({"STETH / USD", "STETH / ETH"})
    if upper == "WEETH":
        feeds.add("weETH / ETH")
    if upper == "RETH":
        feeds.add("RETH / ETH")
    if upper == "WBTC":
        feeds.add("WBTC / BTC")
    if upper == "CBBTC":
        feeds.add("cbBTC / USD")
    if upper == "LBTC":
        feeds.add("LBTC / BTC")
    if upper == "TBTC":
        feeds.add("TBTC / USD")
    if upper in {"USDE", "SUSDE"}:
        feeds.add("USDe / USD")
    if upper in {"USDS", "SUSDS"}:
        feeds.add("USDS / USD")
    if upper in {"USD0", "USD0PP", "USD0++"}:
        feeds.update({"USD0 / USD", "USD0++ / USD"})
    if upper in {"MKR", "SKY"}:
        feeds.update({"MKR / USD", "SKY / USD"})
    if upper == "LDO":
        feeds.add("LDO / ETH")
    if upper in {"PAXG", "XAUT"}:
        feeds.update({"PAXG / USD", "XAU / USD"})
    if upper == "EURC":
        feeds.add("EURC / USD")
    if upper == "RLUSD":
        feeds.add("RLUSD / USD")
    if upper == "USDG":
        feeds.add("USDG / USD")
    if upper == "CRVUSD":
        feeds.add("CRVUSD / USD")
    return tuple(sorted(feeds))


def _load_chainlink_feed_series(
    ch,
    feeds: tuple[str, ...],
    start_ts: datetime,
    end_ts: datetime,
) -> dict[str, tuple[list[int], list[float]]]:
    if not feeds:
        return {}

    escaped_feeds = ", ".join(f"'{_escape_sql_string(feed)}'" for feed in feeds)
    base_rows = ch.query(
        f"""
        SELECT feed, toUnixTimestamp(timestamp) AS ts, price
        FROM chainlink_prices
        WHERE feed IN ({escaped_feeds})
          AND timestamp >= %(start_ts)s
          AND timestamp <= %(end_ts)s
        ORDER BY feed ASC, ts ASC
        """,
        parameters={"start_ts": start_ts, "end_ts": end_ts},
    ).result_rows

    # Add one boundary point per feed before/after window for true nearest lookup.
    before_rows = ch.query(
        f"""
        SELECT feed, toUnixTimestamp(max(timestamp)) AS ts, argMax(price, timestamp) AS price
        FROM chainlink_prices
        WHERE feed IN ({escaped_feeds}) AND timestamp < %(start_ts)s
        GROUP BY feed
        """,
        parameters={"start_ts": start_ts},
    ).result_rows
    after_rows = ch.query(
        f"""
        SELECT feed, toUnixTimestamp(min(timestamp)) AS ts, argMin(price, timestamp) AS price
        FROM chainlink_prices
        WHERE feed IN ({escaped_feeds}) AND timestamp > %(end_ts)s
        GROUP BY feed
        """,
        parameters={"end_ts": end_ts},
    ).result_rows

    by_feed: dict[str, list[tuple[int, float]]] = {}
    for feed, ts, price in [*base_rows, *before_rows, *after_rows]:
        if feed is None or ts is None or price is None:
            continue
        feed_str = str(feed)
        by_feed.setdefault(feed_str, []).append((int(ts), float(price)))

    prepared: dict[str, tuple[list[int], list[float]]] = {}
    for feed, pairs in by_feed.items():
        unique = sorted(set(pairs), key=lambda item: item[0])
        ts_list = [item[0] for item in unique]
        price_list = [item[1] for item in unique]
        prepared[feed] = (ts_list, price_list)
    return prepared


def _nearest_feed_price(
    prepared_series: dict[str, tuple[list[int], list[float]]],
    feed: str,
    target_ts: int,
) -> Optional[float]:
    series = prepared_series.get(feed)
    if series is None:
        return None
    ts_list, price_list = series
    if not ts_list:
        return None
    idx = bisect_left(ts_list, target_ts)
    if idx <= 0:
        return price_list[0]
    if idx >= len(ts_list):
        return price_list[-1]
    prev_ts = ts_list[idx - 1]
    next_ts = ts_list[idx]
    if target_ts - prev_ts <= next_ts - target_ts:
        return price_list[idx - 1]
    return price_list[idx]


def _is_aave_market_entity(ch, entity_id: str) -> bool:
    normalized = _normalize_entity_id(entity_id)
    if not normalized or not normalized.startswith("0x"):
        return False
    escaped = _escape_sql_string(normalized)
    count = _query_int(
        ch,
        f"""
        SELECT count()
        FROM api_market_latest FINAL
        WHERE protocol = 'AAVE_MARKET'
          AND entity_id LIKE '{escaped}%'
        """,
    )
    return count > 0


def _query_aave_cumulative_baseline_usd(
    ch, normalized_entity_id: str, resolution: str, first_bucket_ts: int, denom: float
) -> tuple[float, float]:
    first_bucket_dt = datetime.utcfromtimestamp(first_bucket_ts).replace(microsecond=0)
    flow_bucket_expr = _time_bucket_expr(resolution, "day")
    price_bucket_expr = _time_bucket_expr(resolution, "timestamp")
    rows = ch.query(
        f"""
        SELECT
            coalesce(sum(((supply_in_raw - supply_out_raw) / %(denom)s) * coalesce(price_usd, 0.0)), 0.0) AS baseline_supply_usd,
            coalesce(sum(((borrow_in_raw - borrow_out_raw) / %(denom)s) * coalesce(price_usd, 0.0)), 0.0) AS baseline_borrow_usd
        FROM (
            SELECT
                {flow_bucket_expr} AS bucket_ts,
                toFloat64(sumMerge(supply_inflow_raw_state)) AS supply_in_raw,
                toFloat64(sumMerge(supply_outflow_raw_state)) AS supply_out_raw,
                toFloat64(sumMerge(borrow_inflow_raw_state)) AS borrow_in_raw,
                toFloat64(sumMerge(borrow_outflow_raw_state)) AS borrow_out_raw
            FROM {AAVE_FLOW_DAILY_AGG_TABLE}
            WHERE entity_id = %(eid)s
              AND day < %(first_bucket_ts)s
            GROUP BY bucket_ts
        ) AS flows
        LEFT JOIN (
            SELECT
                {price_bucket_expr} AS bucket_ts,
                avg(toFloat64(price_usd)) AS price_usd
            FROM aave_timeseries
            WHERE protocol = 'AAVE_MARKET'
              AND entity_id LIKE %(eid_prefix)s
              AND timestamp < %(first_bucket_ts)s
            GROUP BY bucket_ts
        ) AS prices USING bucket_ts
        """,
        parameters={
            "denom": denom,
            "eid": normalized_entity_id,
            "eid_prefix": f"{normalized_entity_id}%",
            "first_bucket_ts": first_bucket_dt,
        },
    ).result_rows
    if not rows:
        return 0.0, 0.0
    return float(rows[0][0] or 0.0), float(rows[0][1] or 0.0)


def _query_aave_preaggregated_flow_timeseries(
    ch, entity_id: str, resolution: str, limit: int
) -> list[MarketFlowPoint]:
    if resolution.upper() not in {"1D", "1W"}:
        return []

    normalized = _normalize_entity_id(entity_id)
    if not normalized.startswith("0x"):
        return []

    token = TOKENS.get(normalized[2:])
    if token is None:
        return []
    _, decimals = token
    denom = float(10 ** decimals)

    safe_limit = _safe_limit(limit)
    bucket_seconds = _bucket_seconds(resolution)
    now_dt = datetime.utcnow().replace(microsecond=0)
    window_start = now_dt - timedelta(seconds=(safe_limit + 2) * bucket_seconds)

    ts_bucket_expr = _time_bucket_expr(resolution, "timestamp")
    price_rows = ch.query(
        f"""
        SELECT
            toUnixTimestamp({ts_bucket_expr}) AS bucket_ts,
            avg(price_usd) AS price_usd
        FROM aave_timeseries
        WHERE protocol = 'AAVE_MARKET'
          AND entity_id LIKE %(eid_prefix)s
          AND timestamp >= %(start_ts)s
          AND timestamp <= %(end_ts)s
        GROUP BY bucket_ts
        ORDER BY bucket_ts ASC
        """,
        parameters={
            "eid_prefix": f"{normalized}%",
            "start_ts": window_start,
            "end_ts": now_dt,
        },
    ).result_rows
    if not price_rows:
        return []

    bucket_expr = _time_bucket_expr(resolution, "day")
    flow_rows = ch.query(
        f"""
        SELECT
            toUnixTimestamp({bucket_expr}) AS bucket_ts,
            sumMerge(supply_inflow_raw_state) AS supply_inflow_raw,
            sumMerge(supply_outflow_raw_state) AS supply_outflow_raw,
            sumMerge(borrow_inflow_raw_state) AS borrow_inflow_raw,
            sumMerge(borrow_outflow_raw_state) AS borrow_outflow_raw
        FROM {AAVE_FLOW_DAILY_AGG_TABLE}
        WHERE entity_id = %(eid)s
          AND day >= %(start_ts)s
          AND day <= %(end_ts)s
        GROUP BY bucket_ts
        ORDER BY bucket_ts ASC
        """,
        parameters={
            "eid": normalized,
            "start_ts": window_start,
            "end_ts": now_dt,
        },
    ).result_rows
    if not flow_rows:
        return []

    all_buckets = [int(row[0]) for row in price_rows]
    selected_buckets = all_buckets[-safe_limit:]
    if not selected_buckets:
        return []
    price_by_bucket = {
        int(row[0]): float(row[1]) if row[1] is not None else 0.0
        for row in price_rows
    }
    raw_by_bucket: dict[int, tuple[float, float, float, float]] = {}
    for row in flow_rows:
        bucket = int(row[0])
        raw_by_bucket[bucket] = (
            float(row[1] or 0),
            float(row[2] or 0),
            float(row[3] or 0),
            float(row[4] or 0),
        )

    cumulative_supply_usd, cumulative_borrow_usd = _query_aave_cumulative_baseline_usd(
        ch, normalized, resolution, selected_buckets[0], denom
    )
    points: list[MarketFlowPoint] = []
    for bucket_ts in selected_buckets:
        price = float(price_by_bucket.get(bucket_ts, 0.0))
        supply_in_raw, supply_out_raw, borrow_in_raw, borrow_out_raw = raw_by_bucket.get(
            bucket_ts, (0.0, 0.0, 0.0, 0.0)
        )
        supply_inflow_usd = (supply_in_raw / denom) * price
        supply_outflow_usd = (supply_out_raw / denom) * price
        borrow_inflow_usd = (borrow_in_raw / denom) * price
        borrow_outflow_usd = (borrow_out_raw / denom) * price
        net_supply_flow_usd = float(supply_inflow_usd - supply_outflow_usd)
        net_borrow_flow_usd = float(borrow_inflow_usd - borrow_outflow_usd)
        cumulative_supply_usd += net_supply_flow_usd
        cumulative_borrow_usd += net_borrow_flow_usd
        points.append(
            MarketFlowPoint(
                timestamp=int(bucket_ts),
                supply_inflow_usd=float(supply_inflow_usd),
                supply_outflow_usd=float(supply_outflow_usd),
                borrow_inflow_usd=float(borrow_inflow_usd),
                borrow_outflow_usd=float(borrow_outflow_usd),
                net_supply_flow_usd=net_supply_flow_usd,
                net_borrow_flow_usd=net_borrow_flow_usd,
                cumulative_supply_net_inflow_usd=float(cumulative_supply_usd),
                cumulative_borrow_net_inflow_usd=float(cumulative_borrow_usd),
            )
        )
    return points


def _query_aave_event_flow_timeseries(ch, entity_id: str, resolution: str, limit: int) -> list[MarketFlowPoint]:
    normalized = _normalize_entity_id(entity_id)
    if not normalized.startswith("0x"):
        return []
    encoded_topic_entity = f"0x{normalized[2:].rjust(64, '0')}"

    token = TOKENS.get(normalized[2:])
    if token is None:
        return []
    symbol, decimals = token
    denom = float(10 ** decimals)

    safe_limit = _safe_limit(limit)
    bucket_seconds = _bucket_seconds(resolution)
    now_dt = datetime.utcnow().replace(microsecond=0)
    window_start = now_dt - timedelta(seconds=(safe_limit + 2) * bucket_seconds)

    ts_bucket_expr = _time_bucket_expr(resolution, "timestamp")
    price_rows = ch.query(
        f"""
        SELECT
            toUnixTimestamp({ts_bucket_expr}) AS bucket_ts,
            avg(price_usd) AS price_usd
        FROM aave_timeseries
        WHERE protocol = 'AAVE_MARKET'
          AND entity_id LIKE %(eid_prefix)s
          AND timestamp >= %(start_ts)s
          AND timestamp <= %(end_ts)s
        GROUP BY bucket_ts
        ORDER BY bucket_ts ASC
        """,
        parameters={
            "eid_prefix": f"{normalized}%",
            "start_ts": window_start,
            "end_ts": now_dt,
        },
    ).result_rows

    if not price_rows:
        return []

    all_buckets = [int(row[0]) for row in price_rows]
    price_by_bucket = {
        int(row[0]): float(row[1]) if row[1] is not None else 0.0
        for row in price_rows
    }
    feed_candidates = _candidate_chainlink_feeds_for_symbol(symbol)
    try:
        chainlink_series = _load_chainlink_feed_series(ch, feed_candidates, window_start, now_dt)
    except Exception:
        chainlink_series = {}
    event_price_cache: dict[int, float] = {}
    block_bounds = ch.query(
        """
        SELECT min(block_number), max(block_number)
        FROM aave_events
        WHERE block_timestamp >= %(start_ts)s
          AND block_timestamp <= %(end_ts)s
        """,
        parameters={"start_ts": window_start, "end_ts": now_dt},
    ).result_rows
    if not block_bounds or block_bounds[0][0] is None or block_bounds[0][1] is None:
        return []
    min_block = int(block_bounds[0][0])
    max_block = int(block_bounds[0][1])

    event_bucket_expr = _time_bucket_expr(resolution, "block_timestamp")
    event_in = ", ".join(f"'{name}'" for name in AAVE_FLOW_EVENT_NAMES)
    event_rows = ch.query(
        f"""
        SELECT
            toUnixTimestamp({event_bucket_expr}) AS bucket_ts,
            toUnixTimestamp(block_timestamp) AS event_ts,
            event_name,
            topic1 = %(encoded_topic_entity)s AS is_collateral_event,
            topic2 = %(encoded_topic_entity)s AS is_debt_event,
            if(
                length(data) >= 66,
                reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))),
                toUInt256(0)
            ) AS amount0_raw,
            if(
                length(data) >= 130,
                reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))),
                toUInt256(0)
            ) AS amount1_raw
        FROM aave_events
        WHERE block_number >= %(min_block)s
          AND block_number <= %(max_block)s
          AND block_timestamp >= %(start_ts)s
          AND block_timestamp <= %(end_ts)s
          AND (
            (
                event_name IN ('Supply', 'Withdraw', 'Borrow', 'Repay', 'MintedToTreasury')
                AND topic1 = %(encoded_topic_entity)s
            )
            OR
            (
                event_name = 'LiquidationCall'
                AND (
                    topic1 = %(encoded_topic_entity)s
                    OR topic2 = %(encoded_topic_entity)s
                )
            )
          )
          AND event_name IN ({event_in})
        ORDER BY bucket_ts ASC, block_number ASC, log_index ASC
        """,
        parameters={
            "min_block": min_block,
            "max_block": max_block,
            "start_ts": window_start,
            "end_ts": now_dt,
            "encoded_topic_entity": encoded_topic_entity,
        },
    ).result_rows

    usd_flows_by_bucket: dict[int, dict[str, float]] = {}
    for bucket_ts, event_ts, event_name, is_collateral_event, is_debt_event, amount0_raw, amount1_raw in event_rows:
        bucket = int(bucket_ts)
        slot = usd_flows_by_bucket.setdefault(
            bucket,
            {
                "supply_inflow_usd": 0.0,
                "supply_outflow_usd": 0.0,
                "borrow_inflow_usd": 0.0,
                "borrow_outflow_usd": 0.0,
            },
        )
        evt_ts_int = int(event_ts)
        price = event_price_cache.get(evt_ts_int)
        if price is None:
            eth_price = _nearest_feed_price(chainlink_series, "ETH / USD", evt_ts_int)
            btc_price = _nearest_feed_price(chainlink_series, "BTC / USD", evt_ts_int)
            extra_prices: dict[str, float] = {}
            for feed in feed_candidates:
                if feed in {"ETH / USD", "BTC / USD"}:
                    continue
                feed_price = _nearest_feed_price(chainlink_series, feed, evt_ts_int)
                if feed_price is not None:
                    extra_prices[feed] = float(feed_price)
            derived_price = get_usd_price(
                symbol,
                eth_price=float(eth_price) if eth_price is not None else 2000.0,
                btc_price=float(btc_price) if btc_price is not None else 70000.0,
                extra_prices=extra_prices,
            )
            if not math.isfinite(derived_price) or derived_price <= 0:
                derived_price = float(price_by_bucket.get(bucket, 0.0))
            price = max(0.0, float(derived_price))
            event_price_cache[evt_ts_int] = price

        evt = str(event_name or "")
        amount0 = float(amount0_raw) / denom
        amount1 = float(amount1_raw) / denom

        if evt == "Supply":
            slot["supply_inflow_usd"] += amount1 * price
        elif evt == "Withdraw":
            slot["supply_outflow_usd"] += amount0 * price
        elif evt == "Borrow":
            slot["borrow_inflow_usd"] += amount1 * price
        elif evt == "Repay":
            repay_usd = amount0 * price
            slot["borrow_outflow_usd"] += repay_usd
            # Repay(useATokens=true) burns aTokens, so supply also flows out.
            if int(amount1_raw) == 1:
                slot["supply_outflow_usd"] += repay_usd
        elif evt == "MintedToTreasury":
            slot["supply_inflow_usd"] += amount0 * price
        elif evt == "LiquidationCall":
            if bool(is_collateral_event):
                slot["supply_outflow_usd"] += amount1 * price
            if bool(is_debt_event):
                slot["borrow_outflow_usd"] += amount0 * price

    cumulative_supply_usd = 0.0
    cumulative_borrow_usd = 0.0
    points: list[MarketFlowPoint] = []
    for bucket_ts in all_buckets[-safe_limit:]:
        slot = usd_flows_by_bucket.get(
            bucket_ts,
            {
                "supply_inflow_usd": 0.0,
                "supply_outflow_usd": 0.0,
                "borrow_inflow_usd": 0.0,
                "borrow_outflow_usd": 0.0,
            },
        )
        supply_inflow_usd = float(slot["supply_inflow_usd"])
        supply_outflow_usd = float(slot["supply_outflow_usd"])
        borrow_inflow_usd = float(slot["borrow_inflow_usd"])
        borrow_outflow_usd = float(slot["borrow_outflow_usd"])
        net_supply_flow_usd = float(supply_inflow_usd - supply_outflow_usd)
        net_borrow_flow_usd = float(borrow_inflow_usd - borrow_outflow_usd)
        cumulative_supply_usd += net_supply_flow_usd
        cumulative_borrow_usd += net_borrow_flow_usd
        points.append(
            MarketFlowPoint(
                timestamp=int(bucket_ts),
                supply_inflow_usd=float(supply_inflow_usd),
                supply_outflow_usd=float(supply_outflow_usd),
                borrow_inflow_usd=float(borrow_inflow_usd),
                borrow_outflow_usd=float(borrow_outflow_usd),
                net_supply_flow_usd=net_supply_flow_usd,
                net_borrow_flow_usd=net_borrow_flow_usd,
                cumulative_supply_net_inflow_usd=float(cumulative_supply_usd),
                cumulative_borrow_net_inflow_usd=float(cumulative_borrow_usd),
            )
        )
    return points


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


def _query_market_snapshots(ch, protocol: Optional[str] = None) -> list[MarketSnapshot]:
    if protocol:
        sql = """
        SELECT
            entity_id,
            symbol,
            protocol,
            supply_usd,
            borrow_usd,
            supply_apy,
            borrow_apy,
            if(supply_usd > 0, borrow_usd / supply_usd, 0.0) AS utilization
        FROM api_market_latest FINAL
        WHERE protocol = %(protocol)s
        ORDER BY supply_usd DESC
        """
        res = ch.query(sql, parameters={"protocol": protocol})
    else:
        sql = """
        SELECT
            entity_id,
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
            entity_id=str(row[0]),
            symbol=str(row[1]),
            protocol=str(row[2]),
            supply_usd=float(row[3]),
            borrow_usd=float(row[4]),
            supply_apy=float(row[5]),
            borrow_apy=float(row[6]),
            utilization=float(row[7]),
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


def _query_protocol_markets(ch, protocol: str, entity_id: Optional[str] = None) -> list[MarketDetail]:
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
    normalized_entity_id = _normalize_entity_id(entity_id or "")
    entity_filter = ""
    if normalized_entity_id:
        escaped_entity = _escape_sql_string(normalized_entity_id)
        if normalized_entity_id.startswith("0x"):
            entity_filter = f" AND entity_id LIKE '{escaped_entity}%'"
        else:
            entity_filter = f" AND entity_id = '{escaped_entity}'"

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
            {entity_filter}
        ) AS t
        LEFT JOIN morpho_market_params AS p ON t.entity_id = p.market_id
        WHERE t.supply_usd >= 1000 OR t.borrow_usd >= 1000
        ORDER BY t.supply_usd DESC
        """
    else:
        value_filter = (
            ""
            if protocol == AAVE_MARKET
            else "WHERE supply_usd >= 1000 OR borrow_usd >= 1000"
        )
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
            {entity_filter}
        )
        {value_filter}
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


def _to_week_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        return None
    for separator in ("T", " "):
        if separator in text:
            text = text.split(separator, 1)[0]
            break
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _normalize_display_unit(display_in: str) -> str:
    unit = str(display_in or "USD").strip().upper()
    if unit not in {"USD", "BTC", "ETH"}:
        return "USD"
    return unit


def _load_weekly_quote_prices(ch) -> dict[date, dict[str, float]]:
    rows = ch.query(
        f"""
        SELECT
            toDate(day) AS day_date,
            feed,
            argMaxMerge(price_state) AS price
        FROM {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}
        WHERE feed IN ('BTC / USD', 'ETH / USD')
        GROUP BY day_date, feed
        ORDER BY day_date ASC
        """
    ).result_rows
    prices_by_week: dict[date, dict[str, float]] = {}
    for raw_day, raw_feed, raw_price in rows:
        week = _to_week_date(raw_day)
        if week is None:
            continue
        feed = str(raw_feed or "")
        price = float(raw_price or 0.0)
        slot = prices_by_week.setdefault(week, {})
        if feed == "BTC / USD":
            slot["BTC"] = price
        elif feed == "ETH / USD":
            slot["ETH"] = price
    return prices_by_week


def _forward_fill_protocol_tvl(rows) -> list[ProtocolTvlPoint]:
    if not rows:
        return []

    updates_by_week: dict[date, list[tuple[str, str, float]]] = {}
    min_week: date | None = None
    max_week: date | None = None

    for raw_week, raw_protocol, raw_entity_id, raw_supply in rows:
        week = _to_week_date(raw_week)
        if week is None:
            continue
        protocol = str(raw_protocol or "").upper()
        if protocol not in TVL_PROTOCOLS:
            continue
        entity_id = str(raw_entity_id or "")
        if entity_id in TVL_SYNTHETIC_ENTITY_IDS:
            continue

        try:
            supply = max(0.0, float(raw_supply or 0.0))
        except (TypeError, ValueError):
            supply = 0.0

        updates_by_week.setdefault(week, []).append((protocol, entity_id, supply))
        min_week = week if min_week is None else min(min_week, week)
        max_week = week if max_week is None else max(max_week, week)

    if min_week is None or max_week is None:
        return []

    points: list[ProtocolTvlPoint] = []
    current_supply_by_entity: dict[tuple[str, str], float] = {}
    totals_by_protocol = {protocol: 0.0 for protocol in TVL_PROTOCOLS}
    cursor = min_week
    one_week = timedelta(days=7)

    while cursor <= max_week:
        for protocol, entity_id, supply in updates_by_week.get(cursor, []):
            key = (protocol, entity_id)
            previous = current_supply_by_entity.get(key)
            if previous is not None:
                totals_by_protocol[protocol] -= previous
            current_supply_by_entity[key] = supply
            totals_by_protocol[protocol] += supply

        points.append(
            ProtocolTvlPoint(
                date=cursor.isoformat(),
                aave=totals_by_protocol.get("AAVE", 0.0),
                morpho=totals_by_protocol.get("MORPHO", 0.0),
                euler=totals_by_protocol.get("EULER", 0.0),
                fluid=totals_by_protocol.get("FLUID", 0.0),
            )
        )
        cursor += one_week

    return points


def _query_protocol_tvl_history(ch, display_in: str = "USD") -> list[ProtocolTvlPoint]:
    res = ch.query(
        f"""
        SELECT day, protocol, entity_id, argMaxMerge(supply_usd_state) AS supply_usd
        FROM {API_PROTOCOL_TVL_AGG_TABLE}
        WHERE protocol IN ('AAVE', 'MORPHO', 'EULER', 'FLUID')
          AND entity_id != 'AAVE_MARKET_SYNTHETIC'
        GROUP BY day, protocol, entity_id
        ORDER BY day ASC, protocol ASC, entity_id ASC
        """
    )
    points = _forward_fill_protocol_tvl(res.result_rows)
    unit = _normalize_display_unit(display_in)
    if unit == "USD" or not points:
        return points

    prices_by_week = _load_weekly_quote_prices(ch)
    weekly_prices: list[float] = []
    for point in points:
        week = _to_week_date(point.date)
        if week is None:
            weekly_prices.append(0.0)
            continue
        price = float((prices_by_week.get(week) or {}).get(unit, 0.0) or 0.0)
        weekly_prices.append(price if price > 0 else 0.0)

    # Forward-fill with previously known price.
    last_seen = 0.0
    for idx, price in enumerate(weekly_prices):
        if price > 0:
            last_seen = price
            continue
        if last_seen > 0:
            weekly_prices[idx] = last_seen

    # Backfill leading gaps using earliest available price so history remains continuous.
    next_seen = 0.0
    for idx in range(len(weekly_prices) - 1, -1, -1):
        price = weekly_prices[idx]
        if price > 0:
            next_seen = price
            continue
        if next_seen > 0:
            weekly_prices[idx] = next_seen

    converted: list[ProtocolTvlPoint] = []
    for point, divisor in zip(points, weekly_prices):
        if divisor <= 0:
            converted.append(
                ProtocolTvlPoint(
                    date=point.date,
                    aave=0.0,
                    morpho=0.0,
                    euler=0.0,
                    fluid=0.0,
                )
            )
            continue

        converted.append(
            ProtocolTvlPoint(
                date=point.date,
                aave=float(point.aave / divisor),
                morpho=float(point.morpho / divisor),
                euler=float(point.euler / divisor),
                fluid=float(point.fluid / divisor),
            )
        )
    return converted


def _query_protocol_apy_history(
    ch, protocol: str, resolution: str, limit: int
) -> list[ProtocolApyPoint]:
    allowed = {AAVE_MARKET, MORPHO_MARKET, "EULER_MARKET", FLUID_MARKET}
    if protocol not in allowed:
        return []

    safe_limit = _safe_limit(limit)
    escaped_protocol = _escape_sql_string(protocol)
    if protocol == AAVE_MARKET:
        # Full history for /data chart should come from canonical raw timeseries,
        # not the hourly API pre-agg table with shorter TTL retention.
        time_expr = _time_bucket_expr(resolution, "timestamp")
        rows = ch.query(
            f"""
            SELECT
                toUnixTimestamp(bucket_ts) AS bucket_ts,
                if(
                    sum(supply_usd) > 0,
                    sum(supply_apy * supply_usd) / sum(supply_usd),
                    avg(supply_apy)
                ) AS average_supply_apy,
                if(
                    sum(borrow_usd) > 0,
                    sum(borrow_apy * borrow_usd) / sum(borrow_usd),
                    avg(borrow_apy)
                ) AS average_borrow_apy
            FROM (
                SELECT
                    entity_id,
                    {time_expr} AS bucket_ts,
                    avg(toFloat64(supply_apy)) AS supply_apy,
                    avg(toFloat64(borrow_apy)) AS borrow_apy,
                    avg(toFloat64(supply_usd)) AS supply_usd,
                    avg(toFloat64(borrow_usd)) AS borrow_usd
                FROM aave_timeseries
                WHERE protocol = '{escaped_protocol}'
                  AND entity_id != 'AAVE_MARKET_SYNTHETIC'
                GROUP BY entity_id, bucket_ts
            )
            GROUP BY bucket_ts
            ORDER BY bucket_ts DESC
            LIMIT {safe_limit}
            """
        ).result_rows
    else:
        time_expr = _time_bucket_expr(resolution, "ts")
        rows = ch.query(
            f"""
            SELECT
                toUnixTimestamp({time_expr}) AS bucket_ts,
                if(
                    sum(supply_usd) > 0,
                    sum(supply_apy * supply_usd) / sum(supply_usd),
                    avg(supply_apy)
                ) AS average_supply_apy,
                if(
                    sum(borrow_usd) > 0,
                    sum(borrow_apy * borrow_usd) / sum(borrow_usd),
                    avg(borrow_apy)
                ) AS average_borrow_apy
            FROM (
                SELECT
                    entity_id,
                    ts,
                    avgMerge(supply_apy_state) AS supply_apy,
                    avgMerge(borrow_apy_state) AS borrow_apy,
                    avgMerge(supply_usd_state) AS supply_usd,
                    avgMerge(borrow_usd_state) AS borrow_usd
                FROM {API_MARKET_TIMESERIES_AGG_TABLE}
                WHERE protocol = '{escaped_protocol}'
                GROUP BY entity_id, ts
            )
            GROUP BY bucket_ts
            ORDER BY bucket_ts DESC
            LIMIT {safe_limit}
            """
        ).result_rows
    points = [
        ProtocolApyPoint(
            timestamp=int(row[0]),
            average_supply_apy=float(row[1]) if row[1] is not None else 0.0,
            average_borrow_apy=float(row[2]) if row[2] is not None else 0.0,
        )
        for row in rows
    ]
    points.reverse()
    return points


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
    res = ch.query(
        sql,
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


def _query_market_flow_timeseries_from_balance_deltas(ch, entity_id: str, resolution: str, limit: int) -> list[MarketFlowPoint]:
    safe_limit = _safe_limit(limit)
    # Fetch one extra point so first visible bucket can compute deltas.
    points = _query_market_timeseries(ch, entity_id, resolution, safe_limit + 1)
    if not points:
        return []

    cumulative_supply_usd = 0.0
    cumulative_borrow_usd = 0.0
    flows: list[MarketFlowPoint] = []
    prev_supply: Optional[float] = None
    prev_borrow: Optional[float] = None

    for point in points:
        supply_usd = float(point.supply_usd or 0.0)
        borrow_usd = float(point.borrow_usd or 0.0)

        if prev_supply is None or prev_borrow is None:
            delta_supply = 0.0
            delta_borrow = 0.0
        else:
            delta_supply = supply_usd - prev_supply
            delta_borrow = borrow_usd - prev_borrow

        cumulative_supply_usd += delta_supply
        cumulative_borrow_usd += delta_borrow
        flows.append(
            MarketFlowPoint(
                timestamp=int(point.timestamp),
                supply_inflow_usd=max(0.0, delta_supply),
                supply_outflow_usd=max(0.0, -delta_supply),
                borrow_inflow_usd=max(0.0, delta_borrow),
                borrow_outflow_usd=max(0.0, -delta_borrow),
                net_supply_flow_usd=delta_supply,
                net_borrow_flow_usd=delta_borrow,
                cumulative_supply_net_inflow_usd=float(cumulative_supply_usd),
                cumulative_borrow_net_inflow_usd=float(cumulative_borrow_usd),
            )
        )
        prev_supply = supply_usd
        prev_borrow = borrow_usd

    if len(flows) > safe_limit:
        flows = flows[-safe_limit:]
    return flows


def _query_market_flow_timeseries(ch, entity_id: str, resolution: str, limit: int) -> list[MarketFlowPoint]:
    if _is_aave_market_entity(ch, entity_id):
        preaggregated = _query_aave_preaggregated_flow_timeseries(
            ch, entity_id, resolution, limit
        )
        if preaggregated:
            return preaggregated
        return _query_aave_event_flow_timeseries(ch, entity_id, resolution, limit)
    return _query_market_flow_timeseries_from_balance_deltas(ch, entity_id, resolution, limit)


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
    def market_snapshots(self, protocol: Optional[str] = None) -> List[MarketSnapshot]:
        ch = get_clickhouse_client()
        return _query_market_snapshots(ch, protocol)

    @strawberry.field(name="latestRates")
    def latest_rates(self) -> Optional[LatestRates]:
        ch = get_clickhouse_client()
        return _query_latest_rates(ch)

    @strawberry.field(name="protocolMarkets")
    def protocol_markets(
        self, protocol: str = "AAVE_MARKET", entity_id: Optional[str] = None
    ) -> list[MarketDetail]:
        ch = get_clickhouse_client()
        return _query_protocol_markets(ch, protocol, entity_id)

    @strawberry.field(name="protocolTvlHistory")
    def protocol_tvl_history(self, display_in: str = "USD") -> list[ProtocolTvlPoint]:
        ch = get_clickhouse_client()
        return _query_protocol_tvl_history(ch, display_in)

    @strawberry.field(name="protocolApyHistory")
    def protocol_apy_history(
        self, protocol: str = AAVE_MARKET, resolution: str = "1W", limit: int = 500
    ) -> list[ProtocolApyPoint]:
        ch = get_clickhouse_client()
        return _query_protocol_apy_history(ch, protocol, resolution, limit)

    @strawberry.field(name="marketTimeseries")
    def market_timeseries(
        self, entity_id: str, resolution: str = "1H", limit: int = 2000
    ) -> list[MarketTimeseriesPoint]:
        ch = get_clickhouse_client()
        return _query_market_timeseries(ch, entity_id, resolution, limit)

    @strawberry.field(name="marketFlowTimeseries")
    def market_flow_timeseries(
        self, entity_id: str, resolution: str = "1H", limit: int = 2000
    ) -> list[MarketFlowPoint]:
        ch = get_clickhouse_client()
        return _query_market_flow_timeseries(ch, entity_id, resolution, limit)

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


@app.get("/api/v1/oracle/usdc-borrow-apy")
def get_usdc_borrow_apy():
    try:
        ch = get_clickhouse_client()
        sql = """
        SELECT
            argMax(borrow_apy, timestamp) AS apy,
            max(timestamp) AS updated_at
        FROM aave_timeseries
        WHERE protocol = 'AAVE_MARKET' AND symbol = 'USDC'
        """
        res = ch.query(sql).result_rows
        if not res or res[0][0] is None:
            return JSONResponse(status_code=404, content={"error": "Rate not found"})
        updated_raw = res[0][1] if len(res[0]) > 1 else None
        updated_ts: int | None = None
        if isinstance(updated_raw, datetime):
            if updated_raw.tzinfo is None:
                updated_raw = updated_raw.replace(tzinfo=timezone.utc)
            updated_ts = int(updated_raw.timestamp())
        elif isinstance(updated_raw, (int, float)):
            updated_ts = int(updated_raw)
        elif isinstance(updated_raw, str) and updated_raw.strip():
            try:
                updated_ts = int(datetime.fromisoformat(updated_raw.replace("Z", "+00:00")).timestamp())
            except ValueError:
                updated_ts = None

        payload = {"symbol": "USDC", "borrow_apy": float(res[0][0])}
        if updated_ts is not None:
            now_ts = int(datetime.now(tz=timezone.utc).timestamp())
            payload["timestamp"] = updated_ts
            payload["age_seconds"] = max(0, now_ts - updated_ts)
        return payload
    except Exception as exc:
        close_clickhouse_client()
        return JSONResponse(status_code=500, content={"error": str(exc)})


def create_app():
    return app
