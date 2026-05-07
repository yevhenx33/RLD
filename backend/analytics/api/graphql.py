from __future__ import annotations

import os
import threading
import atexit
import math
import logging
import json
import time
import uuid
from collections import defaultdict, deque
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import clickhouse_connect
import strawberry
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from strawberry.fastapi import GraphQLRouter
from analytics.config import apply_env_from_config

apply_env_from_config()

# Imports must follow apply_env_from_config() so env-backed defaults load correctly.
from analytics.protocols import (  # noqa: E402
    AAVE_MARKET,
    CHAINLINK_PRICES,
    FLUID_MARKET,
    METAMORPHO_VAULT,
    MORPHO_MARKET,
    PENDLE_ETHEREUM_PT_YT_PRICES,
    SOFR_RATES,
    READY_PROTOCOLS_DEFAULT,
    RAW_TABLE_BY_PROTOCOL,
    RAW_HEAD_QUERY_BY_PROTOCOL,
    PROCESSOR_STATE_ALIASES,
)
from analytics.state import get_source_status  # noqa: E402
from analytics.tokens import TOKENS, get_usd_price  # noqa: E402
from analytics.aave_accounts import AAVE_DEPLOYMENT_ID, ensure_aave_account_tables  # noqa: E402

logger = logging.getLogger("rld.clickhouse_api")


def _parse_env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_env_csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return tuple(value.strip() for value in raw.split(",") if value.strip())


MAX_LIMIT = 10000
API_DEFAULT_PAGE_SIZE = 100
API_MAX_PAGE_SIZE = 1000
MAX_READY_LAG_BLOCKS = int(os.getenv("INDEXER_MAX_READY_LAG_BLOCKS", "250000"))
MAX_READY_SOFR_BUSINESS_DAYS = int(os.getenv("INDEXER_MAX_READY_SOFR_BUSINESS_DAYS", "3"))
INDEXER_READY_PROTOCOLS = tuple(
    protocol.strip()
    for protocol in os.getenv(
        "INDEXER_READY_PROTOCOLS", ",".join(READY_PROTOCOLS_DEFAULT)
    ).split(",")
    if protocol.strip()
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
INDEXER_VERSION = os.getenv("INDEXER_VERSION", "dev")
GRAPHQL_ENABLE_IDE = _parse_env_bool("GRAPHQL_ENABLE_IDE", False)
GRAPHQL_ENABLE_INTROSPECTION = _parse_env_bool(
    "GRAPHQL_ENABLE_INTROSPECTION",
    GRAPHQL_ENABLE_IDE,
)
GRAPHQL_ALLOW_GET_QUERIES = _parse_env_bool(
    "GRAPHQL_ALLOW_GET_QUERIES",
    GRAPHQL_ENABLE_IDE,
)
GRAPHQL_MAX_BODY_BYTES = int(os.getenv("GRAPHQL_MAX_BODY_BYTES", "262144"))
GRAPHQL_MAX_DEPTH = int(os.getenv("GRAPHQL_MAX_DEPTH", "12"))
GRAPHQL_RATE_LIMIT_PER_MINUTE = int(os.getenv("GRAPHQL_RATE_LIMIT_PER_MINUTE", "120"))
GRAPHQL_REQUIRE_API_KEY = _parse_env_bool("GRAPHQL_REQUIRE_API_KEY", False)
API_KEYS = frozenset(_parse_env_csv("API_KEYS", _parse_env_csv("ANALYTICS_API_KEYS")))
API_ADMIN_TOKENS = frozenset(
    _parse_env_csv(
        "API_ADMIN_TOKENS",
        _parse_env_csv("ANALYTICS_ADMIN_TOKENS", _parse_env_csv("ANALYTICS_ADMIN_TOKEN")),
    )
)
API_PROTECT_ADMIN_ENDPOINTS = _parse_env_bool(
    "API_PROTECT_ADMIN_ENDPOINTS",
    _parse_env_bool("ANALYTICS_PROTECT_ADMIN_ENDPOINTS", bool(API_ADMIN_TOKENS)),
)
API_PUBLIC_READY_PROTOCOLS = tuple(
    protocol.strip()
    for protocol in os.getenv(
        "API_PUBLIC_READY_PROTOCOLS",
        ",".join(
            (
                AAVE_MARKET,
                MORPHO_MARKET,
                METAMORPHO_VAULT,
                FLUID_MARKET,
                PENDLE_ETHEREUM_PT_YT_PRICES,
                CHAINLINK_PRICES,
                SOFR_RATES,
            )
        ),
    ).split(",")
    if protocol.strip()
)
PROCESSING_OPTIONAL_PROTOCOLS = (SOFR_RATES, PENDLE_ETHEREUM_PT_YT_PRICES)
ENVIO_GRAPHQL_ALIAS_SUNSET = os.getenv(
    "ENVIO_GRAPHQL_ALIAS_SUNSET",
    "Wed, 31 Dec 2026 00:00:00 GMT",
)

_CLICKHOUSE_CLIENT = None
_CLICKHOUSE_LOCK = threading.Lock()
_RATE_LIMIT_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LIMIT_LOCK = threading.Lock()
_HTTP_METRICS_LOCK = threading.Lock()
_HTTP_REQUEST_COUNTS: dict[tuple[str, str], int] = defaultdict(int)
_HTTP_REQUEST_LATENCY_SUM: dict[tuple[str, str], float] = defaultdict(float)
_HTTP_REQUEST_LATENCY_MAX: dict[tuple[str, str], float] = defaultdict(float)
API_MARKET_TIMESERIES_AGG_TABLE = "api_market_timeseries_hourly_agg"
API_PROTOCOL_TVL_AGG_TABLE = "api_protocol_tvl_entity_weekly_agg"
AAVE_FLOW_DAILY_AGG_TABLE = "api_aave_market_flow_daily_agg"
API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE = "api_chainlink_price_weekly_agg"
AAVE_SERIES_TABLE = "market_timeseries"
TVL_PROTOCOLS = ("AAVE", "EULER", "FLUID", "MORPHO")
TVL_SYNTHETIC_ENTITY_IDS = {"AAVE_MARKET_SYNTHETIC"}
LENDING_DATA_MARKET_PROTOCOLS = (AAVE_MARKET, MORPHO_MARKET, FLUID_MARKET)
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
class PendleAsset:
    asset_address: str = strawberry.field(name="assetAddress")
    chain_id: int = strawberry.field(name="chainId")
    asset_type: str = strawberry.field(name="assetType")
    symbol: str
    market_address: str = strawberry.field(name="marketAddress")
    expiry: int
    active: bool
    matured: bool


@strawberry.type
class PendleLatestPrice:
    asset_address: str = strawberry.field(name="assetAddress")
    chain_id: int = strawberry.field(name="chainId")
    asset_type: str = strawberry.field(name="assetType")
    symbol: str
    price_usd: float = strawberry.field(name="priceUsd")
    timestamp: int


@strawberry.type
class PendlePricePoint:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@strawberry.type
class PendleMarketPagePayload:
    market_address: str = strawberry.field(name="marketAddress")
    assets: list[PendleAsset]
    latest_prices: list[PendleLatestPrice] = strawberry.field(name="latestPrices")
    freshness: AnalyticsFreshness


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
    collateral_usd: Optional[float] = strawberry.field(name="collateralUsd", default=None)
    lltv: Optional[float] = None
    oracle: Optional[str] = None
    pricing_status: Optional[str] = strawberry.field(name="pricingStatus", default=None)
    loan_asset: Optional[str] = strawberry.field(name="loanAsset", default=None)
    loan_token: Optional[str] = strawberry.field(name="loanToken", default=None)
    loan_decimals: Optional[int] = strawberry.field(name="loanDecimals", default=None)
    collateral_asset: Optional[str] = strawberry.field(name="collateralAsset", default=None)
    collateral_token: Optional[str] = strawberry.field(name="collateralToken", default=None)
    collateral_decimals: Optional[int] = strawberry.field(name="collateralDecimals", default=None)
    loan_price_usd: Optional[float] = strawberry.field(name="loanPriceUsd", default=None)
    collateral_price_usd: Optional[float] = strawberry.field(name="collateralPriceUsd", default=None)
    supply_assets: Optional[str] = strawberry.field(name="supplyAssets", default=None)
    borrow_assets: Optional[str] = strawberry.field(name="borrowAssets", default=None)
    collateral_assets: Optional[str] = strawberry.field(name="collateralAssets", default=None)
    irm: Optional[str] = None
    oracle_support: Optional[str] = strawberry.field(name="oracleSupport", default=None)
    pricing_error: Optional[str] = strawberry.field(name="pricingError", default=None)
    is_active: Optional[bool] = strawberry.field(name="isActive", default=None)
    has_supply: Optional[bool] = strawberry.field(name="hasSupply", default=None)
    has_borrow: Optional[bool] = strawberry.field(name="hasBorrow", default=None)
    has_collateral: Optional[bool] = strawberry.field(name="hasCollateral", default=None)
    last_event_timestamp: Optional[int] = strawberry.field(name="lastEventTimestamp", default=None)
    last_priced_timestamp: Optional[int] = strawberry.field(name="lastPricedTimestamp", default=None)


@strawberry.type
class MorphoMarketEvent:
    timestamp: int
    block_number: int = strawberry.field(name="blockNumber")
    tx_hash: str = strawberry.field(name="txHash")
    log_index: int = strawberry.field(name="logIndex")
    market_id: str = strawberry.field(name="marketId")
    event_name: str = strawberry.field(name="eventName")
    caller: str
    on_behalf: str = strawberry.field(name="onBehalf")
    receiver: str
    assets: str
    shares: str
    collateral_assets: str = strawberry.field(name="collateralAssets")
    repaid_assets: str = strawberry.field(name="repaidAssets")
    seized_assets: str = strawberry.field(name="seizedAssets")
    bad_debt_assets: str = strawberry.field(name="badDebtAssets")
    interest_assets: str = strawberry.field(name="interestAssets")
    fee_shares: str = strawberry.field(name="feeShares")
    fee_wad: str = strawberry.field(name="feeWad")


@strawberry.type
class MorphoMarketPosition:
    market_id: str = strawberry.field(name="marketId")
    user: str
    supply_shares: str = strawberry.field(name="supplyShares")
    borrow_shares: str = strawberry.field(name="borrowShares")
    collateral_assets: str = strawberry.field(name="collateralAssets")
    estimated_supply_assets: str = strawberry.field(name="estimatedSupplyAssets")
    estimated_borrow_assets: str = strawberry.field(name="estimatedBorrowAssets")
    collateral_usd: float = strawberry.field(name="collateralUsd")
    health_factor: Optional[float] = strawberry.field(name="healthFactor", default=None)
    last_event_timestamp: int = strawberry.field(name="lastEventTimestamp")


@strawberry.type
class MetaMorphoVault:
    vault_address: str = strawberry.field(name="vaultAddress")
    name: str
    asset_symbol: str = strawberry.field(name="assetSymbol")
    asset_address: str = strawberry.field(name="assetAddress")
    total_assets: str = strawberry.field(name="totalAssets")
    total_supply: str = strawberry.field(name="totalSupply")
    share_price_usd: float = strawberry.field(name="sharePriceUsd")
    tvl_usd: float = strawberry.field(name="tvlUsd")
    is_canonical_tvl: bool = strawberry.field(name="isCanonicalTvl")
    last_snapshot_timestamp: int = strawberry.field(name="lastSnapshotTimestamp")


@strawberry.type
class MetaMorphoVaultAllocation:
    vault_address: str = strawberry.field(name="vaultAddress")
    market_id: str = strawberry.field(name="marketId")
    cap: str
    supplied_assets: str = strawberry.field(name="suppliedAssets")
    supplied_usd: float = strawberry.field(name="suppliedUsd")
    allocation_share: float = strawberry.field(name="allocationShare")
    timestamp: int


@strawberry.type
class MetaMorphoVaultFlow:
    timestamp: int
    vault_address: str = strawberry.field(name="vaultAddress")
    asset_symbol: str = strawberry.field(name="assetSymbol")
    deposit_assets: str = strawberry.field(name="depositAssets")
    withdraw_assets: str = strawberry.field(name="withdrawAssets")
    deposit_shares: str = strawberry.field(name="depositShares")
    withdraw_shares: str = strawberry.field(name="withdrawShares")
    transfer_shares: str = strawberry.field(name="transferShares")
    deposit_usd: float = strawberry.field(name="depositUsd")
    withdraw_usd: float = strawberry.field(name="withdrawUsd")
    net_flow_usd: float = strawberry.field(name="netFlowUsd")
    event_count: int = strawberry.field(name="eventCount")


@strawberry.type
class FluidContractRegistryItem:
    chain_id: int = strawberry.field(name="chainId")
    product_type: str = strawberry.field(name="productType")
    contract: str
    factory: str
    name: str
    created_block: int = strawberry.field(name="createdBlock")
    active: bool
    resolver: str
    metadata: str


@strawberry.type
class FluidProductSnapshot:
    chain_id: int = strawberry.field(name="chainId")
    product_type: str = strawberry.field(name="productType")
    product_id: str = strawberry.field(name="productId")
    timestamp: int
    block_number: int = strawberry.field(name="blockNumber")
    symbol: str
    underlying: str
    collateral_token: str = strawberry.field(name="collateralToken")
    debt_token: str = strawberry.field(name="debtToken")
    supply_usd: float = strawberry.field(name="supplyUsd")
    borrow_usd: float = strawberry.field(name="borrowUsd")
    collateral_usd: float = strawberry.field(name="collateralUsd")
    liquidity_usd: float = strawberry.field(name="liquidityUsd")
    volume_usd: float = strawberry.field(name="volumeUsd")
    fees_usd: float = strawberry.field(name="feesUsd")
    supply_apy: float = strawberry.field(name="supplyApy")
    borrow_apy: float = strawberry.field(name="borrowApy")
    utilization: float
    ltv: float
    liquidation_threshold: float = strawberry.field(name="liquidationThreshold")
    position_count: int = strawberry.field(name="positionCount")
    is_canonical_tvl: bool = strawberry.field(name="isCanonicalTvl")
    pricing_status: str = strawberry.field(name="pricingStatus")
    oracle_status: str = strawberry.field(name="oracleStatus")
    snapshot_status: str = strawberry.field(name="snapshotStatus")
    provenance: str
    error: str


@strawberry.type
class FluidProductComponent:
    chain_id: int = strawberry.field(name="chainId")
    product_type: str = strawberry.field(name="productType")
    product_id: str = strawberry.field(name="productId")
    timestamp: int
    block_number: int = strawberry.field(name="blockNumber")
    component_type: str = strawberry.field(name="componentType")
    token: str
    symbol: str
    raw_amount: str = strawberry.field(name="rawAmount")
    decimals: int
    price_usd: float = strawberry.field(name="priceUsd")
    amount_usd: float = strawberry.field(name="amountUsd")
    pricing_status: str = strawberry.field(name="pricingStatus")
    oracle_status: str = strawberry.field(name="oracleStatus")
    provenance: str


@strawberry.type
class ProtocolTvlPoint:
    date: str
    aave: float = 0.0
    euler: float = 0.0
    fluid: float = 0.0
    morpho: float = 0.0


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
class AnalyticsFreshness:
    ready: bool
    status: str
    version: str
    generated_at: int = strawberry.field(name="generatedAt")


@strawberry.type
class AnalyticsProtocolReadiness:
    protocol: str
    ready: bool
    collector_lag: int = strawberry.field(name="collectorLag")
    processing_lag: int = strawberry.field(name="processingLag")
    max_lag_blocks: int = strawberry.field(name="maxLagBlocks")
    issues: list[str]


@strawberry.type
class AnalyticsStatusPayload:
    ready: bool
    status: str
    version: str
    generated_at: int = strawberry.field(name="generatedAt")
    ready_protocols: list[str] = strawberry.field(name="readyProtocols")
    public_ready_protocols: list[str] = strawberry.field(name="publicReadyProtocols")
    protocols: list[AnalyticsProtocolReadiness]
    morpho_coverage_json: str = strawberry.field(name="morphoCoverageJson")
    fluid_coverage_json: str = strawberry.field(name="fluidCoverageJson")


@strawberry.type
class ReadinessIssue:
    code: str
    severity: str = "warning"
    message: str = ""


@strawberry.type
class SourceFreshness:
    protocol: str
    collector_lag: int = strawberry.field(name="collectorLag")
    processing_lag: int = strawberry.field(name="processingLag")
    status: str
    issues: list[ReadinessIssue]


@strawberry.type
class PricingCoverage:
    priced: int
    unpriced: int
    unsupported: int
    partial: int = 0


@strawberry.type
class ProtocolCoverage:
    protocol: str
    total: int
    indexed: int
    priced: int
    unpriced: int
    unsupported: int
    partial: int
    status: str


@strawberry.type
class ProtocolStatus:
    protocol: str
    ready: bool
    status: str
    freshness: SourceFreshness
    coverage: ProtocolCoverage


@strawberry.type
class ApiStatusPayload:
    ready: bool
    status: str
    version: str
    generated_at: int = strawberry.field(name="generatedAt")
    protocols: list[ProtocolStatus]


@strawberry.type
class PageInfo:
    has_next_page: bool = strawberry.field(name="hasNextPage")
    end_cursor: Optional[str] = strawberry.field(name="endCursor", default=None)


@strawberry.type
class MarketConnection:
    nodes: list[MarketDetail]
    page_info: PageInfo = strawberry.field(name="pageInfo")
    total_count: int = strawberry.field(name="totalCount")


@strawberry.type
class MarketSeriesConnection:
    nodes: list[MarketTimeseriesPoint]
    page_info: PageInfo = strawberry.field(name="pageInfo")
    total_count: int = strawberry.field(name="totalCount")


@strawberry.type
class AaveAccountFreshness:
    latest_event_block: int = strawberry.field(name="latestEventBlock")
    latest_index_timestamp: int = strawberry.field(name="latestIndexTimestamp")
    latest_price_timestamp: int = strawberry.field(name="latestPriceTimestamp")
    reconstruction_status: str = strawberry.field(name="reconstructionStatus")
    last_rpc_audit_timestamp: int = strawberry.field(name="lastRpcAuditTimestamp")
    audit_precision_status: str = strawberry.field(name="auditPrecisionStatus")


@strawberry.type
class AaveAccountStats:
    deployment_id: str = strawberry.field(name="deploymentId")
    active_accounts: int = strawberry.field(name="activeAccounts")
    debt_accounts: int = strawberry.field(name="debtAccounts")
    collateral_accounts: int = strawberry.field(name="collateralAccounts")
    total_collateral_usd: float = strawberry.field(name="totalCollateralUsd")
    total_debt_usd: float = strawberry.field(name="totalDebtUsd")
    weighted_liquidation_threshold: float = strawberry.field(name="weightedLiquidationThreshold")
    accounts_below_hf_125: int = strawberry.field(name="accountsBelowHf125")
    accounts_below_hf_1: int = strawberry.field(name="accountsBelowHf1")
    freshness: AaveAccountFreshness


@strawberry.type
class AaveAccountPosition:
    reserve: str
    symbol: str
    scaled_supply_raw: str = strawberry.field(name="scaledSupplyRaw")
    scaled_variable_debt_raw: str = strawberry.field(name="scaledVariableDebtRaw")
    supply_usd: float = strawberry.field(name="supplyUsd")
    debt_usd: float = strawberry.field(name="debtUsd")
    collateral_enabled: bool = strawberry.field(name="collateralEnabled")
    liquidation_threshold: float = strawberry.field(name="liquidationThreshold")


@strawberry.type
class AaveAccountProfilePoint:
    timestamp: int
    total_collateral_usd: float = strawberry.field(name="totalCollateralUsd")
    total_debt_usd: float = strawberry.field(name="totalDebtUsd")
    net_worth_usd: float = strawberry.field(name="netWorthUsd")
    weighted_liquidation_threshold: float = strawberry.field(name="weightedLiquidationThreshold")
    health_factor: Optional[float] = strawberry.field(name="healthFactor")
    emode_category: int = strawberry.field(name="emodeCategory")
    position_count: int = strawberry.field(name="positionCount")
    debt_position_count: int = strawberry.field(name="debtPositionCount")
    collateral_position_count: int = strawberry.field(name="collateralPositionCount")
    last_event_block: int = strawberry.field(name="lastEventBlock")


@strawberry.type
class AaveAccount:
    deployment_id: str = strawberry.field(name="deploymentId")
    address: str
    total_collateral_usd: float = strawberry.field(name="totalCollateralUsd")
    total_debt_usd: float = strawberry.field(name="totalDebtUsd")
    weighted_liquidation_threshold: float = strawberry.field(name="weightedLiquidationThreshold")
    health_factor: Optional[float] = strawberry.field(name="healthFactor")
    emode_category: int = strawberry.field(name="emodeCategory")
    positions: list[AaveAccountPosition]
    freshness: AaveAccountFreshness


@strawberry.type
class AaveAccountConnection:
    nodes: list[AaveAccount]
    page_info: PageInfo = strawberry.field(name="pageInfo")
    total_count: int = strawberry.field(name="totalCount")


@strawberry.type
class LendingDataStats:
    total_supply_usd: float = strawberry.field(name="totalSupplyUsd")
    total_borrow_usd: float = strawberry.field(name="totalBorrowUsd")
    average_supply_apy: float = strawberry.field(name="averageSupplyApy")
    average_borrow_apy: float = strawberry.field(name="averageBorrowApy")
    market_count: int = strawberry.field(name="marketCount")


@strawberry.type
class LendingDataChartPoint:
    timestamp: int
    tvl: float
    average_supply_apy: Optional[float] = strawberry.field(name="averageSupplyApy", default=None)
    average_borrow_apy: Optional[float] = strawberry.field(name="averageBorrowApy", default=None)


@strawberry.type
class LendingDataMarketRow:
    entity_id: str = strawberry.field(name="entityId")
    symbol: str
    protocol: str
    supply_usd: float = strawberry.field(name="supplyUsd")
    borrow_usd: float = strawberry.field(name="borrowUsd")
    supply_apy: float = strawberry.field(name="supplyApy")
    borrow_apy: float = strawberry.field(name="borrowApy")
    utilization: float
    net_worth: float = strawberry.field(name="netWorth")


@strawberry.type
class LendingDataPagePayload:
    freshness: AnalyticsFreshness
    stats: LendingDataStats
    chart_data: list[LendingDataChartPoint] = strawberry.field(name="chartData")
    markets: list[LendingDataMarketRow]


@strawberry.type
class ProtocolMarketsStats:
    total_supply_usd: float = strawberry.field(name="totalSupplyUsd")
    total_borrow_usd: float = strawberry.field(name="totalBorrowUsd")
    average_utilization: float = strawberry.field(name="averageUtilization")
    average_supply_apy: float = strawberry.field(name="averageSupplyApy")
    average_borrow_apy: float = strawberry.field(name="averageBorrowApy")
    market_count: int = strawberry.field(name="marketCount")


@strawberry.type
class ProtocolMarketRow:
    entity_id: str = strawberry.field(name="entityId")
    symbol: str
    protocol: str
    supply_usd: float = strawberry.field(name="supplyUsd")
    borrow_usd: float = strawberry.field(name="borrowUsd")
    supply_apy: float = strawberry.field(name="supplyApy")
    borrow_apy: float = strawberry.field(name="borrowApy")
    utilization: float
    collateral_symbol: Optional[str] = strawberry.field(name="collateralSymbol", default=None)
    collateral_usd: Optional[float] = strawberry.field(name="collateralUsd", default=None)
    lltv: Optional[float] = None
    oracle: Optional[str] = None
    pricing_status: Optional[str] = strawberry.field(name="pricingStatus", default=None)
    is_trapped: bool = strawberry.field(name="isTrapped", default=False)


@strawberry.type
class ProtocolMarketsPagePayload:
    freshness: AnalyticsFreshness
    stats: ProtocolMarketsStats
    rows: list[ProtocolMarketRow]


@strawberry.type
class LendingPoolRatePoint:
    timestamp: int
    supply_apy: float = strawberry.field(name="supplyApy")
    borrow_apy: float = strawberry.field(name="borrowApy")
    utilization: float
    supply_usd: float = strawberry.field(name="supplyUsd")
    borrow_usd: float = strawberry.field(name="borrowUsd")


@strawberry.type
class LendingPoolPagePayload:
    freshness: AnalyticsFreshness
    market: Optional[MarketDetail]
    rate_chart: list[LendingPoolRatePoint] = strawberry.field(name="rateChart")
    flow_chart: list[MarketFlowPoint] = strawberry.field(name="flowChart")


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
    ensure_aave_account_tables(ch)
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
        FROM market_timeseries
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
            FROM market_timeseries
            WHERE protocol IN ('AAVE_MARKET', 'EULER_MARKET', 'FLUID_MARKET', 'MORPHO_MARKET')
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
                FROM market_timeseries
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
            FROM market_timeseries
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
                FROM market_timeseries
                WHERE protocol IN ('AAVE_MARKET', 'EULER_MARKET', 'FLUID_MARKET', 'MORPHO_MARKET')
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
        return _CLICKHOUSE_CLIENT


atexit.register(close_clickhouse_client)


def _query_int(ch, sql: str) -> int:
    value = ch.command(sql)
    if value in (None, "", "None"):
        return 0
    return int(value)


def _business_days_since(value: datetime | date | None) -> int:
    if value is None:
        return 9999
    if isinstance(value, datetime):
        start = value.date()
    else:
        start = value
    today = datetime.now(timezone.utc).date()
    if start >= today:
        return 0
    days = 0
    cursor = start + timedelta(days=1)
    while cursor <= today:
        if cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return days


def _collect_processing_lag(ch, protocols: Optional[list[str]] = None) -> dict[str, int]:
    monitored = protocols or list(RAW_TABLE_BY_PROTOCOL.keys())
    lag_by_protocol: dict[str, int] = {}
    for protocol in monitored:
        if protocol == SOFR_RATES:
            lag_by_protocol[protocol] = -1
            continue
        raw_table = RAW_TABLE_BY_PROTOCOL.get(protocol)
        state_protocols = PROCESSOR_STATE_ALIASES.get(protocol, (protocol,))
        if raw_table is None:
            lag_by_protocol[protocol] = -1
            continue
        state_in = ", ".join(f"'{_escape_sql_string(p)}'" for p in state_protocols)
        try:
            status = get_source_status(ch, protocol, "processor")
            if status["last_event_block"] or status["last_processed_block"]:
                raw_head = status["last_event_block"]
                proc_head = status["last_processed_block"]
                lag_by_protocol[protocol] = max(0, raw_head - proc_head)
                continue
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
        if protocol == SOFR_RATES:
            try:
                status = get_source_status(ch, protocol, "collector")
                lag_by_protocol[protocol] = _business_days_since(status["last_data_timestamp"])
            except Exception:
                lag_by_protocol[protocol] = 9999
            continue
        raw_head_query = RAW_HEAD_QUERY_BY_PROTOCOL.get(protocol)
        if raw_head_query is None:
            lag_by_protocol[protocol] = -1
            continue
        try:
            status = get_source_status(ch, protocol, "collector")
            if status["source_head_block"] or status["last_scanned_block"]:
                lag_by_protocol[protocol] = max(
                    0,
                    status["source_head_block"] - status["last_scanned_block"],
                )
                continue
            raw_head = _query_int(ch, raw_head_query)
            collected_head = _query_int(
                ch,
                f"SELECT max(last_collected_block) FROM collector_state WHERE protocol = '{_escape_sql_string(protocol)}'",
            )
            lag_by_protocol[protocol] = max(0, raw_head - collected_head)
        except Exception:
            lag_by_protocol[protocol] = -1
    return lag_by_protocol


def _source_status_snapshot(ch) -> list[dict[str, object]]:
    rows = ch.query(
        """
        SELECT
            source,
            kind,
            last_scanned_block,
            last_event_block,
            last_processed_block,
            source_head_block,
            last_data_timestamp,
            last_success_at,
            last_error
        FROM source_status FINAL
        ORDER BY source, kind
        """
    ).result_rows
    return [
        {
            "source": str(row[0]),
            "kind": str(row[1]),
            "lastScannedBlock": int(row[2] or 0),
            "lastEventBlock": int(row[3] or 0),
            "lastProcessedBlock": int(row[4] or 0),
            "sourceHeadBlock": int(row[5] or 0),
            "lastDataTimestamp": row[6].isoformat() if isinstance(row[6], datetime) else str(row[6]),
            "lastSuccessAt": row[7].isoformat() if isinstance(row[7], datetime) else str(row[7]),
            "lastError": str(row[8] or ""),
        }
        for row in rows
    ]


def _prometheus_metrics(ch) -> str:
    collector_lag = _collect_collector_lag(ch)
    processing_lag = _collect_processing_lag(ch)
    status_rows = _source_status_snapshot(ch)
    lines = [
        "# HELP rld_indexer_collector_lag Source collector lag. Blocks for EVM sources, business days for SOFR.",
        "# TYPE rld_indexer_collector_lag gauge",
    ]
    for source, lag in collector_lag.items():
        lines.append(f'rld_indexer_collector_lag{{source="{source}"}} {lag}')
    lines.extend([
        "# HELP rld_indexer_processing_lag Source processor lag in blocks.",
        "# TYPE rld_indexer_processing_lag gauge",
    ])
    for source, lag in processing_lag.items():
        lines.append(f'rld_indexer_processing_lag{{source="{source}"}} {lag}')
    lines.extend([
        "# HELP rld_indexer_last_success_age_seconds Seconds since source status last success.",
        "# TYPE rld_indexer_last_success_age_seconds gauge",
    ])
    now_ts = datetime.now(timezone.utc).timestamp()
    for row in status_rows:
        source = row["source"]
        kind = row["kind"]
        try:
            success_ts = datetime.fromisoformat(str(row["lastSuccessAt"])).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            success_ts = 0
        lines.append(
            f'rld_indexer_last_success_age_seconds{{source="{source}",kind="{kind}"}} {max(0, now_ts - success_ts):.0f}'
        )
    part_rows = ch.query(
        """
        SELECT table, count() AS parts
        FROM system.parts
        WHERE active AND database = currentDatabase()
        GROUP BY table
        """
    ).result_rows
    lines.extend([
        "# HELP rld_clickhouse_active_parts Active ClickHouse parts by table.",
        "# TYPE rld_clickhouse_active_parts gauge",
    ])
    for table, parts in part_rows:
        lines.append(f'rld_clickhouse_active_parts{{table="{table}"}} {int(parts)}')
    disk_rows = ch.query(
        """
        SELECT name, free_space, total_space
        FROM system.disks
        """
    ).result_rows
    lines.extend([
        "# HELP rld_clickhouse_disk_free_bytes Free ClickHouse disk bytes.",
        "# TYPE rld_clickhouse_disk_free_bytes gauge",
        "# HELP rld_clickhouse_disk_total_bytes Total ClickHouse disk bytes.",
        "# TYPE rld_clickhouse_disk_total_bytes gauge",
    ])
    for name, free_space, total_space in disk_rows:
        lines.append(f'rld_clickhouse_disk_free_bytes{{disk="{name}"}} {int(free_space)}')
        lines.append(f'rld_clickhouse_disk_total_bytes{{disk="{name}"}} {int(total_space)}')
    with _HTTP_METRICS_LOCK:
        http_counts = dict(_HTTP_REQUEST_COUNTS)
        http_latency_sum = dict(_HTTP_REQUEST_LATENCY_SUM)
        http_latency_max = dict(_HTTP_REQUEST_LATENCY_MAX)
    lines.extend([
        "# HELP rld_api_http_requests_total HTTP requests handled by the analytics API.",
        "# TYPE rld_api_http_requests_total counter",
    ])
    for (path, status), count in sorted(http_counts.items()):
        lines.append(
            f'rld_api_http_requests_total{{path="{_metric_label(path)}",status="{_metric_label(status)}"}} {count}'
        )
    lines.extend([
        "# HELP rld_api_http_request_latency_seconds_sum Total HTTP request latency seconds.",
        "# TYPE rld_api_http_request_latency_seconds_sum counter",
        "# HELP rld_api_http_request_latency_seconds_max Max observed HTTP request latency seconds since process start.",
        "# TYPE rld_api_http_request_latency_seconds_max gauge",
    ])
    for (path, status), total in sorted(http_latency_sum.items()):
        label_path = _metric_label(path)
        label_status = _metric_label(status)
        lines.append(
            f'rld_api_http_request_latency_seconds_sum{{path="{label_path}",status="{label_status}"}} {total:.6f}'
        )
        lines.append(
            f'rld_api_http_request_latency_seconds_max{{path="{label_path}",status="{label_status}"}} {http_latency_max.get((path, status), 0.0):.6f}'
        )
    return "\n".join(lines) + "\n"


def _metric_label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _graphql_error(message: str, code: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"errors": [{"message": message, "extensions": {"code": code}}]},
    )


def _extract_bearer_token(value: str) -> str:
    prefix = "bearer "
    if value.lower().startswith(prefix):
        return value[len(prefix) :].strip()
    return value.strip()


def _request_api_key(request: Request) -> str:
    return (
        request.headers.get("x-rld-api-key", "").strip()
        or _extract_bearer_token(request.headers.get("authorization", ""))
    )


def _request_admin_token(request: Request) -> str:
    return (
        request.headers.get("x-rld-admin-token", "").strip()
        or _extract_bearer_token(request.headers.get("authorization", ""))
    )


def _is_valid_api_key(value: str) -> bool:
    return bool(value and value in API_KEYS)


def _is_valid_admin_token(value: str) -> bool:
    return bool(value and value in API_ADMIN_TOKENS)


def _rate_limit_bucket_key(request: Request) -> str:
    key = _request_api_key(request)
    if key:
        return f"key:{key}"
    forwarded = request.headers.get("x-forwarded-for", "")
    client_host = request.client.host if request.client else "unknown"
    ip = forwarded.split(",", 1)[0].strip() or client_host
    return f"ip:{ip}"


def _rate_limit_allowed(bucket_key: str, now: Optional[float] = None) -> bool:
    if GRAPHQL_RATE_LIMIT_PER_MINUTE <= 0:
        return True
    current = time.monotonic() if now is None else now
    window_start = current - 60.0
    with _RATE_LIMIT_LOCK:
        bucket = _RATE_LIMIT_BUCKETS[bucket_key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= GRAPHQL_RATE_LIMIT_PER_MINUTE:
            return False
        bucket.append(current)
    return True


def _is_introspection_query(query: str) -> bool:
    compact = " ".join(str(query or "").split())
    return "__schema" in compact or "__type" in compact


def _query_depth(query: str) -> int:
    depth = 0
    max_depth = 0
    in_string = False
    escape = False
    for char in str(query or ""):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif char == "}":
            depth = max(0, depth - 1)
    return max_depth


async def _parse_graphql_request_body(request: Request) -> tuple[Optional[dict[str, object]], Optional[JSONResponse]]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > GRAPHQL_MAX_BODY_BYTES:
                return None, _graphql_error("GraphQL request body is too large", "REQUEST_TOO_LARGE", 413)
        except ValueError:
            return None, _graphql_error("Invalid Content-Length", "BAD_REQUEST", 400)
    raw_body = await request.body()
    if len(raw_body) > GRAPHQL_MAX_BODY_BYTES:
        return None, _graphql_error("GraphQL request body is too large", "REQUEST_TOO_LARGE", 413)
    if not raw_body:
        return {}, None
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _graphql_error("Invalid GraphQL JSON request", "BAD_REQUEST", 400)
    if not isinstance(payload, dict):
        return None, _graphql_error("Invalid GraphQL JSON request", "BAD_REQUEST", 400)
    return payload, None


def _request_path_family(path: str) -> str:
    if path.startswith("/graphql"):
        return "/graphql"
    if path.startswith("/envio-graphql"):
        return "/envio-graphql"
    if path in {"/status", "/healthz", "/readyz", "/public-readyz", "/metrics", "/livez"}:
        return path
    if path.startswith("/api/v1/"):
        return "/api/v1"
    return "other"


def _record_http_metric(path: str, status_code: int, latency_seconds: float) -> None:
    key = (_request_path_family(path), str(status_code))
    with _HTTP_METRICS_LOCK:
        _HTTP_REQUEST_COUNTS[key] += 1
        _HTTP_REQUEST_LATENCY_SUM[key] += latency_seconds
        _HTTP_REQUEST_LATENCY_MAX[key] = max(_HTTP_REQUEST_LATENCY_MAX[key], latency_seconds)


def _protocol_readiness_items(
    collector_lag_by_protocol: dict[str, int],
    processing_lag_by_protocol: dict[str, int],
    protocols: tuple[str, ...],
) -> list[AnalyticsProtocolReadiness]:
    items: list[AnalyticsProtocolReadiness] = []
    for protocol in protocols:
        collector_lag = int(collector_lag_by_protocol.get(protocol, -1))
        processing_lag = int(processing_lag_by_protocol.get(protocol, -1))
        issues: list[str] = []
        if collector_lag < 0:
            issues.append("collector_lag_unavailable")
        elif protocol == SOFR_RATES and collector_lag > MAX_READY_SOFR_BUSINESS_DAYS:
            issues.append("collector_lag_exceeded")
        elif protocol != SOFR_RATES and collector_lag > MAX_READY_LAG_BLOCKS:
            issues.append("collector_lag_exceeded")
        if protocol not in PROCESSING_OPTIONAL_PROTOCOLS:
            if processing_lag < 0:
                issues.append("processing_lag_unavailable")
            elif processing_lag > MAX_READY_LAG_BLOCKS:
                issues.append("processing_lag_exceeded")
        items.append(
            AnalyticsProtocolReadiness(
                protocol=protocol,
                ready=not issues,
                collector_lag=collector_lag,
                processing_lag=processing_lag,
                max_lag_blocks=(
                    MAX_READY_SOFR_BUSINESS_DAYS if protocol == SOFR_RATES else MAX_READY_LAG_BLOCKS
                ),
                issues=issues,
            )
        )
    return items


def _analytics_status_payload(ch, protocols: tuple[str, ...] = API_PUBLIC_READY_PROTOCOLS) -> AnalyticsStatusPayload:
    collector_lag = _collect_collector_lag(ch, list(protocols))
    processing_lag = _collect_processing_lag(ch, list(protocols))
    readiness = _protocol_readiness_items(collector_lag, processing_lag, protocols)
    ready = all(item.ready for item in readiness)
    return AnalyticsStatusPayload(
        ready=ready,
        status="ready" if ready else "degraded",
        version=INDEXER_VERSION,
        generated_at=int(datetime.now(timezone.utc).timestamp()),
        ready_protocols=list(INDEXER_READY_PROTOCOLS),
        public_ready_protocols=list(protocols),
        protocols=readiness,
        morpho_coverage_json=json.dumps(_morpho_coverage_snapshot(ch), sort_keys=True),
        fluid_coverage_json=json.dumps(_fluid_coverage_snapshot(ch), sort_keys=True),
    )


def _analytics_status_response(ch, protocols: tuple[str, ...] = API_PUBLIC_READY_PROTOCOLS) -> dict[str, object]:
    payload = _analytics_status_payload(ch, protocols)
    return {
        "status": payload.status,
        "ready": payload.ready,
        "version": payload.version,
        "generatedAt": payload.generated_at,
        "readyProtocols": payload.ready_protocols,
        "publicReadyProtocols": payload.public_ready_protocols,
        "protocols": [
            {
                "protocol": item.protocol,
                "ready": item.ready,
                "collectorLag": item.collector_lag,
                "processingLag": item.processing_lag,
                "maxLagBlocks": item.max_lag_blocks,
                "issues": item.issues,
            }
            for item in payload.protocols
        ],
        "morphoCoverage": json.loads(payload.morpho_coverage_json or "{}"),
        "fluidCoverage": json.loads(payload.fluid_coverage_json or "{}"),
    }


def _api_page_size(first: Optional[int]) -> int:
    if first is None:
        return API_DEFAULT_PAGE_SIZE
    return max(1, min(int(first), API_MAX_PAGE_SIZE))


def _decode_cursor(after: Optional[str]) -> int:
    if not after:
        return 0
    try:
        return max(0, int(str(after)))
    except ValueError:
        return 0


def _connection_page(items: list, first: Optional[int], after: Optional[str]) -> tuple[list, PageInfo, int]:
    start = _decode_cursor(after)
    size = _api_page_size(first)
    total = len(items)
    end = min(total, start + size)
    nodes = items[start:end]
    has_next = end < total
    return nodes, PageInfo(has_next_page=has_next, end_cursor=str(end) if has_next else None), total


def _readiness_issue(code: str) -> ReadinessIssue:
    message_by_code = {
        "collector_lag_unavailable": "Collector lag is unavailable.",
        "collector_lag_exceeded": "Collector lag exceeds the configured readiness threshold.",
        "processing_lag_unavailable": "Processor lag is unavailable.",
        "processing_lag_exceeded": "Processor lag exceeds the configured readiness threshold.",
    }
    return ReadinessIssue(code=code, severity="error", message=message_by_code.get(code, code))


def _protocol_coverage(ch, protocol: str) -> ProtocolCoverage:
    protocol = str(protocol or "").strip().upper()
    try:
        if protocol == MORPHO_MARKET:
            coverage = _morpho_coverage_snapshot(ch)
            total = int(coverage.get("totalDiscoveredMarkets", 0) or 0)
            indexed = int(coverage.get("metricMarkets", 0) or 0)
            priced = int(coverage.get("pricedMarkets", 0) or 0)
            unpriced = int(coverage.get("unpricedMarkets", 0) or 0)
            unsupported = int(coverage.get("unsupportedOracleMarkets", 0) or 0)
            partial = max(0, total - priced - unpriced - unsupported)
        elif protocol == FLUID_MARKET:
            coverage = _fluid_coverage_snapshot(ch)
            total = sum(int(value or 0) for value in dict(coverage.get("productContracts", {}) or {}).values())
            indexed = int(coverage.get("productComponentSubjects", 0) or 0)
            priced = int(coverage.get("pricedProductSnapshotRows", 0) or 0)
            unpriced = sum(
                int(row.get("count", 0) or 0)
                for row in coverage.get("productStatus", []) or []
                if row.get("pricingStatus") == "UNPRICED"
            )
            partial = sum(
                int(row.get("count", 0) or 0)
                for row in coverage.get("productStatus", []) or []
                if row.get("snapshotStatus") == "PARTIAL" or row.get("pricingStatus") == "PARTIAL"
            )
            unsupported = len(coverage.get("missingOracles", []) or [])
        elif protocol == PENDLE_ETHEREUM_PT_YT_PRICES:
            total = _query_int(ch, "SELECT count() FROM pendle_eth_assets FINAL")
            indexed = total
            priced = _query_int(ch, "SELECT count() FROM pendle_eth_price_latest FINAL WHERE price_usd > 0")
            unpriced = max(0, total - priced)
            unsupported = 0
            partial = 0
        elif protocol in {AAVE_MARKET, "EULER_MARKET"}:
            total = _query_int(
                ch,
                f"SELECT count() FROM api_market_latest FINAL WHERE protocol = '{_escape_sql_string(protocol)}'",
            )
            indexed = total
            priced = _query_int(
                ch,
                f"""
                SELECT count()
                FROM api_market_latest FINAL
                WHERE protocol = '{_escape_sql_string(protocol)}'
                  AND (supply_usd > 0 OR borrow_usd > 0 OR price_usd > 0)
                """,
            )
            unpriced = max(0, total - priced)
            unsupported = 0
            partial = 0
        elif protocol == METAMORPHO_VAULT:
            total = _query_int(ch, "SELECT count() FROM metamorpho_vault_registry")
            indexed = _query_int(ch, "SELECT uniqExact(vault_address) FROM metamorpho_vault_state FINAL")
            priced = _query_int(ch, "SELECT uniqExact(vault_address) FROM metamorpho_vault_state FINAL WHERE tvl_usd > 0")
            unpriced = max(0, total - priced)
            unsupported = 0
            partial = max(0, total - indexed)
        elif protocol == CHAINLINK_PRICES:
            total = _query_int(ch, "SELECT uniqExact(feed) FROM chainlink_prices")
            indexed = total
            priced = total
            unpriced = 0
            unsupported = 0
            partial = 0
        elif protocol == SOFR_RATES:
            total = _query_int(ch, "SELECT count() FROM raw_sofr_rates")
            indexed = total
            priced = total
            unpriced = 0
            unsupported = 0
            partial = 0
        else:
            total = indexed = priced = unpriced = unsupported = partial = 0
        status = "ready" if total == 0 or priced > 0 or protocol in {CHAINLINK_PRICES, SOFR_RATES} else "degraded"
        return ProtocolCoverage(
            protocol=protocol,
            total=total,
            indexed=indexed,
            priced=priced,
            unpriced=unpriced,
            unsupported=unsupported,
            partial=partial,
            status=status,
        )
    except Exception as exc:
        logger.warning("Protocol coverage unavailable for %s: %s", protocol, exc)
        return ProtocolCoverage(
            protocol=protocol,
            total=0,
            indexed=0,
            priced=0,
            unpriced=0,
            unsupported=0,
            partial=0,
            status="unavailable",
        )


def _api_protocol_statuses(ch) -> list[ProtocolStatus]:
    collector_lag = _collect_collector_lag(ch, list(API_PUBLIC_READY_PROTOCOLS))
    processing_lag = _collect_processing_lag(ch, list(API_PUBLIC_READY_PROTOCOLS))
    readiness = _protocol_readiness_items(collector_lag, processing_lag, API_PUBLIC_READY_PROTOCOLS)
    statuses: list[ProtocolStatus] = []
    for item in readiness:
        issues = [_readiness_issue(code) for code in item.issues]
        freshness = SourceFreshness(
            protocol=item.protocol,
            collector_lag=item.collector_lag,
            processing_lag=item.processing_lag,
            status="ready" if item.ready else "degraded",
            issues=issues,
        )
        coverage = _protocol_coverage(ch, item.protocol)
        statuses.append(
            ProtocolStatus(
                protocol=item.protocol,
                ready=item.ready and coverage.status != "unavailable",
                status="ready" if item.ready and coverage.status != "unavailable" else "degraded",
                freshness=freshness,
                coverage=coverage,
            )
        )
    return statuses


def _api_status_payload(ch) -> ApiStatusPayload:
    statuses = _api_protocol_statuses(ch)
    ready = all(status.ready for status in statuses)
    return ApiStatusPayload(
        ready=ready,
        status="ready" if ready else "degraded",
        version=INDEXER_VERSION,
        generated_at=int(datetime.now(timezone.utc).timestamp()),
        protocols=statuses,
    )


def _filter_markets(markets: list[MarketDetail], filter_text: Optional[str]) -> list[MarketDetail]:
    needle = str(filter_text or "").strip().lower()
    if not needle:
        return markets
    return [
        market
        for market in markets
        if needle in str(market.entity_id or "").lower()
        or needle in str(market.symbol or "").lower()
        or needle in str(market.collateral_symbol or "").lower()
    ]


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
            FROM {AAVE_SERIES_TABLE}
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
        FROM {AAVE_SERIES_TABLE}
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
        FROM {AAVE_SERIES_TABLE}
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
            FROM {AAVE_SERIES_TABLE}
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


def _query_lending_data_market_snapshots(ch) -> list[MarketSnapshot]:
    protocol_names = ", ".join(f"'{_escape_sql_string(protocol)}'" for protocol in LENDING_DATA_MARKET_PROTOCOLS)
    sql = f"""
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
        WHERE protocol IN ({protocol_names})
          AND (
            protocol = '{_escape_sql_string(AAVE_MARKET)}'
            OR supply_usd >= 1000
            OR borrow_usd >= 1000
          )
          AND NOT (
            protocol = '{_escape_sql_string(MORPHO_MARKET)}'
            AND supply_apy > 1.0
          )
        ORDER BY borrow_usd DESC, supply_usd DESC, protocol ASC, entity_id ASC
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


def _finite_non_negative(value: object) -> float:
    try:
        numeric = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(numeric):
        return 0.0
    return max(0.0, numeric)



def _fluid_coverage_snapshot(ch) -> dict[str, object]:
    try:
        reserve_support_rows = ch.query(
            """
            SELECT oracle_support, count()
            FROM fluid_reserve_oracle_support FINAL
            GROUP BY oracle_support
            """
        ).result_rows
        reserve_support = {str(status): int(count) for status, count in reserve_support_rows}
        product_rows = ch.query(
            """
            SELECT product_type, count()
            FROM fluid_contract_registry FINAL
            GROUP BY product_type
            """
        ).result_rows
        product_counts = {str(product): int(count) for product, count in product_rows}
        snapshot_rows = _query_int(ch, "SELECT count() FROM fluid_product_snapshots FINAL")
        priced_product_rows = _query_int(ch, """
            SELECT countIf(pricing_status = 'PRICED')
            FROM (
                SELECT
                    product_type,
                    product_id,
                    argMax(pricing_status, tuple(timestamp, block_number)) AS pricing_status
                FROM fluid_product_snapshots FINAL
                GROUP BY product_type, product_id
            )
            """)
        canonical_rows = _query_int(ch, """
            SELECT countIf(is_canonical_tvl = 1)
            FROM (
                SELECT
                    product_type,
                    product_id,
                    argMax(is_canonical_tvl, tuple(timestamp, block_number)) AS is_canonical_tvl
                FROM fluid_product_snapshots FINAL
                GROUP BY product_type, product_id
            )
            """)
        product_status_rows = ch.query("""
            SELECT product_type, pricing_status, snapshot_status, count()
            FROM (
                SELECT
                    product_type,
                    product_id,
                    argMax(pricing_status, tuple(timestamp, block_number)) AS pricing_status,
                    argMax(snapshot_status, tuple(timestamp, block_number)) AS snapshot_status
                FROM fluid_product_snapshots FINAL
                GROUP BY product_type, product_id
            )
            GROUP BY product_type, pricing_status, snapshot_status
            """).result_rows
        product_status = [
            {"productType": str(product), "pricingStatus": str(pricing), "snapshotStatus": str(snapshot), "count": int(count)}
            for product, pricing, snapshot, count in product_status_rows
        ]
        missing_reason_rows = ch.query("""
            SELECT symbol, reason, count()
            FROM fluid_asset_oracle_support FINAL
            WHERE oracle_support != 'CHAINLINK_SUPPORTED'
              AND asset NOT IN (
                  SELECT subject
                  FROM oracle_snapshots FINAL
                  WHERE source = 'FLUID' AND status = 'OK' AND price_usd > 0
              )
            GROUP BY symbol, reason
            ORDER BY count() DESC, symbol
            LIMIT 50
            """).result_rows
        missing_oracles = [
            {"symbol": str(symbol), "reason": str(reason), "count": int(count)}
            for symbol, reason, count in missing_reason_rows
        ]
        component_rows = _query_int(ch, "SELECT count() FROM fluid_product_components FINAL")
        component_subjects = _query_int(ch, "SELECT uniqExact(product_id) FROM fluid_product_components FINAL")
        shared_oracle_rows = _query_int(ch, "SELECT count() FROM oracle_snapshots FINAL WHERE source = 'FLUID' AND status = 'OK'")
        shared_oracle_subjects = _query_int(ch, "SELECT uniqExact(subject) FROM oracle_snapshots FINAL WHERE source = 'FLUID' AND status = 'OK'")
        raw_head = _query_int(ch, "SELECT max(block_number) FROM fluid_events")
        processed_head = _query_int(ch, "SELECT max(last_processed_block) FROM processor_state WHERE protocol = 'FLUID_MARKET'")
        validation_rows = ch.query(
            """
            SELECT status, checked_count, mismatch_count, max_relative_supply_diff, max_relative_borrow_diff, finished_at
            FROM fluid_rpc_validation_runs FINAL
            ORDER BY finished_at DESC
            LIMIT 1
            """
        ).result_rows
        validation = {}
        if validation_rows:
            row = validation_rows[0]
            finished = row[5]
            if isinstance(finished, datetime):
                finished_ts = int(finished.replace(tzinfo=timezone.utc).timestamp()) if finished.tzinfo is None else int(finished.timestamp())
            else:
                finished_ts = 0
            validation = {
                "status": str(row[0]),
                "checkedCount": int(row[1] or 0),
                "mismatchCount": int(row[2] or 0),
                "maxRelativeSupplyDiff": float(row[3] or 0.0),
                "maxRelativeBorrowDiff": float(row[4] or 0.0),
                "finishedAt": finished_ts,
            }
        return {
            "reserveStates": _query_int(ch, "SELECT count() FROM fluid_reserve_state FINAL"),
            "reserveSupport": reserve_support,
            "productContracts": product_counts,
            "productSnapshotRows": int(snapshot_rows or 0),
            "pricedProductSnapshotRows": int(priced_product_rows or 0),
            "canonicalProductSnapshotRows": int(canonical_rows or 0),
            "productStatus": product_status,
            "productComponentRows": int(component_rows or 0),
            "productComponentSubjects": int(component_subjects or 0),
            "missingOracles": missing_oracles,
            "fluidOracleSnapshotRows": int(shared_oracle_rows or 0),
            "fluidOracleSnapshotSubjects": int(shared_oracle_subjects or 0),
            "rawHead": int(raw_head or 0),
            "processedHead": int(processed_head or 0),
            "lagBlocks": max(0, int(raw_head or 0) - int(processed_head or 0)),
            "tvlPolicy": "Liquidity Layer reserves are canonical; fToken/vault/DEX snapshots are exposure rows unless isCanonicalTvl=true.",
            "rpcValidation": validation,
        }
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)}


def _query_fluid_contracts(product_type: Optional[str] = None, active_only: bool = True, limit: int = 500) -> list[FluidContractRegistryItem]:
    ch = get_clickhouse_client()
    filters = ["chain_id = 1"]
    if product_type:
        filters.append(f"product_type = '{_escape_sql_string(product_type.upper())}'")
    if active_only:
        filters.append("active = 1")
    safe_limit = max(1, min(int(limit or 500), 2000))
    query = f"""
        SELECT chain_id, product_type, contract, factory, name, created_block, active, resolver, metadata
        FROM fluid_contract_registry FINAL
        WHERE {' AND '.join(filters)}
        ORDER BY product_type, name, contract
        LIMIT {safe_limit}
        """
    rows = ch.query(query).result_rows
    return [
        FluidContractRegistryItem(
            chain_id=int(row[0] or 0),
            product_type=str(row[1] or ""),
            contract=str(row[2] or ""),
            factory=str(row[3] or ""),
            name=str(row[4] or ""),
            created_block=int(row[5] or 0),
            active=bool(row[6]),
            resolver=str(row[7] or ""),
            metadata=str(row[8] or ""),
        )
        for row in rows
    ]


def _query_fluid_product_snapshots(product_type: Optional[str] = None, product_id: Optional[str] = None, limit: int = 500) -> list[FluidProductSnapshot]:
    ch = get_clickhouse_client()
    filters = ["chain_id = 1"]
    if product_type:
        filters.append(f"product_type = '{_escape_sql_string(product_type.upper())}'")
    if product_id:
        filters.append(f"product_id = '{_escape_sql_string(product_id.lower())}'")
    safe_limit = max(1, min(int(limit or 500), 5000))
    where_clause = " AND ".join(filters)
    query = f"""
        SELECT chain_id, product_type, product_id, timestamp, block_number, symbol, underlying,
               collateral_token, debt_token, supply_usd, borrow_usd, collateral_usd, liquidity_usd,
               volume_usd, fees_usd, supply_apy, borrow_apy, utilization, ltv,
               liquidation_threshold, position_count, is_canonical_tvl, pricing_status,
               oracle_status, snapshot_status, provenance, error
        FROM (
            SELECT
                chain_id,
                product_type,
                product_id,
                argMax(timestamp, tuple(fps.timestamp, fps.block_number)) AS timestamp,
                argMax(block_number, tuple(fps.timestamp, fps.block_number)) AS block_number,
                argMax(symbol, tuple(fps.timestamp, fps.block_number)) AS symbol,
                argMax(underlying, tuple(fps.timestamp, fps.block_number)) AS underlying,
                argMax(collateral_token, tuple(fps.timestamp, fps.block_number)) AS collateral_token,
                argMax(debt_token, tuple(fps.timestamp, fps.block_number)) AS debt_token,
                argMax(supply_usd, tuple(fps.timestamp, fps.block_number)) AS supply_usd,
                argMax(borrow_usd, tuple(fps.timestamp, fps.block_number)) AS borrow_usd,
                argMax(collateral_usd, tuple(fps.timestamp, fps.block_number)) AS collateral_usd,
                argMax(liquidity_usd, tuple(fps.timestamp, fps.block_number)) AS liquidity_usd,
                argMax(volume_usd, tuple(fps.timestamp, fps.block_number)) AS volume_usd,
                argMax(fees_usd, tuple(fps.timestamp, fps.block_number)) AS fees_usd,
                argMax(supply_apy, tuple(fps.timestamp, fps.block_number)) AS supply_apy,
                argMax(borrow_apy, tuple(fps.timestamp, fps.block_number)) AS borrow_apy,
                argMax(utilization, tuple(fps.timestamp, fps.block_number)) AS utilization,
                argMax(ltv, tuple(fps.timestamp, fps.block_number)) AS ltv,
                argMax(liquidation_threshold, tuple(fps.timestamp, fps.block_number)) AS liquidation_threshold,
                argMax(position_count, tuple(fps.timestamp, fps.block_number)) AS position_count,
                argMax(is_canonical_tvl, tuple(fps.timestamp, fps.block_number)) AS is_canonical_tvl,
                argMax(pricing_status, tuple(fps.timestamp, fps.block_number)) AS pricing_status,
                argMax(oracle_status, tuple(fps.timestamp, fps.block_number)) AS oracle_status,
                argMax(snapshot_status, tuple(fps.timestamp, fps.block_number)) AS snapshot_status,
                argMax(provenance, tuple(fps.timestamp, fps.block_number)) AS provenance,
                argMax(error, tuple(fps.timestamp, fps.block_number)) AS error
            FROM fluid_product_snapshots AS fps FINAL
            WHERE {where_clause}
            GROUP BY chain_id, product_type, product_id
        )
        ORDER BY liquidity_usd DESC, supply_usd DESC, product_type, product_id
        LIMIT {safe_limit}
        """
    rows = ch.query(query).result_rows
    result: list[FluidProductSnapshot] = []
    for row in rows:
        ts = row[3]
        if isinstance(ts, datetime):
            timestamp = int(ts.replace(tzinfo=timezone.utc).timestamp()) if ts.tzinfo is None else int(ts.timestamp())
        else:
            timestamp = 0
        result.append(
            FluidProductSnapshot(
                chain_id=int(row[0] or 0),
                product_type=str(row[1] or ""),
                product_id=str(row[2] or ""),
                timestamp=timestamp,
                block_number=int(row[4] or 0),
                symbol=str(row[5] or ""),
                underlying=str(row[6] or ""),
                collateral_token=str(row[7] or ""),
                debt_token=str(row[8] or ""),
                supply_usd=float(row[9] or 0.0),
                borrow_usd=float(row[10] or 0.0),
                collateral_usd=float(row[11] or 0.0),
                liquidity_usd=float(row[12] or 0.0),
                volume_usd=float(row[13] or 0.0),
                fees_usd=float(row[14] or 0.0),
                supply_apy=float(row[15] or 0.0),
                borrow_apy=float(row[16] or 0.0),
                utilization=float(row[17] or 0.0),
                ltv=float(row[18] or 0.0),
                liquidation_threshold=float(row[19] or 0.0),
                position_count=int(row[20] or 0),
                is_canonical_tvl=bool(row[21]),
                pricing_status=str(row[22] or ""),
                oracle_status=str(row[23] or ""),
                snapshot_status=str(row[24] or ""),
                provenance=str(row[25] or ""),
                error=str(row[26] or ""),
            )
        )
    return result


def _query_fluid_product_components(product_type: Optional[str] = None, product_id: Optional[str] = None, limit: int = 1000) -> list[FluidProductComponent]:
    ch = get_clickhouse_client()
    filters = ["chain_id = 1"]
    if product_type:
        filters.append(f"product_type = '{_escape_sql_string(product_type.upper())}'")
    if product_id:
        filters.append(f"product_id = '{_escape_sql_string(product_id.lower())}'")
    safe_limit = max(1, min(int(limit or 1000), 10000))
    query = f"""
        SELECT chain_id, product_type, product_id, timestamp, block_number, component_type,
               token, symbol, raw_amount, decimals, price_usd, amount_usd, pricing_status,
               oracle_status, provenance
        FROM fluid_product_components FINAL
        WHERE {' AND '.join(filters)}
        ORDER BY timestamp DESC, product_type, product_id, component_type, token
        LIMIT {safe_limit}
        """
    rows = ch.query(query).result_rows
    result: list[FluidProductComponent] = []
    for row in rows:
        ts = row[3]
        timestamp = int(ts.replace(tzinfo=timezone.utc).timestamp()) if isinstance(ts, datetime) and ts.tzinfo is None else (int(ts.timestamp()) if isinstance(ts, datetime) else 0)
        result.append(FluidProductComponent(
            chain_id=int(row[0] or 0), product_type=str(row[1] or ""), product_id=str(row[2] or ""),
            timestamp=timestamp, block_number=int(row[4] or 0), component_type=str(row[5] or ""),
            token=str(row[6] or ""), symbol=str(row[7] or ""), raw_amount=str(row[8] or "0"),
            decimals=int(row[9] or 0), price_usd=float(row[10] or 0.0), amount_usd=float(row[11] or 0.0),
            pricing_status=str(row[12] or ""), oracle_status=str(row[13] or ""), provenance=str(row[14] or ""),
        ))
    return result


def _morpho_coverage_snapshot(ch) -> dict[str, object]:
    try:
        params = ch.query(
            """
            SELECT
                count() AS total,
                min(creation_block) AS first_block,
                max(creation_block) AS last_market_block
            FROM morpho_market_params
            """
        ).result_rows[0]
        support_rows = ch.query(
            """
            SELECT oracle_support, count()
            FROM morpho_market_oracle_support FINAL
            GROUP BY oracle_support
            """
        ).result_rows
        support_counts = {str(status): int(count) for status, count in support_rows}
        display_row = ch.query(
            """
            SELECT
                count() AS display_markets,
                countIf(latest.entity_id != '') AS api_latest_markets,
                countIf(metrics.market_id != '') AS metric_markets,
                countIf(
                    greatest(
                        if(latest.entity_id != '', latest.supply_usd, if(metrics.market_id != '', metrics.supply_usd, 0.0)),
                        if(latest.entity_id != '', latest.borrow_usd, if(metrics.market_id != '', metrics.borrow_usd, 0.0))
                    ) > 0
                ) AS priced_markets,
                countIf(
                    greatest(
                        if(latest.entity_id != '', latest.supply_usd, if(metrics.market_id != '', metrics.supply_usd, 0.0)),
                        if(latest.entity_id != '', latest.borrow_usd, if(metrics.market_id != '', metrics.borrow_usd, 0.0))
                    ) <= 0
                ) AS zero_markets,
                countIf(support.market_id = '') AS missing_support_markets,
                countIf(support.oracle_support = 'ORACLE_SNAPSHOT_SUPPORTED') AS snapshot_required_markets,
                countIf(support.oracle_support = 'ORACLE_SNAPSHOT_SUPPORTED' AND ifNull(snapshots.oracle, '') != '') AS snapshot_resolved_markets,
                countIf(support.oracle_support = 'ORACLE_SNAPSHOT_SUPPORTED' AND ifNull(snapshots.oracle, '') = '') AS missing_snapshot_markets
            FROM morpho_market_params p
            LEFT JOIN (
                SELECT entity_id,
                       argMax(supply_usd, inserted_at) AS supply_usd,
                       argMax(borrow_usd, inserted_at) AS borrow_usd
                FROM api_market_latest FINAL
                WHERE protocol = 'MORPHO_MARKET'
                GROUP BY entity_id
            ) AS latest
              ON latest.entity_id = p.market_id
            LEFT JOIN (
                SELECT market_id,
                       argMax(supply_usd, tuple(timestamp, inserted_at)) AS supply_usd,
                       argMax(borrow_usd, tuple(timestamp, inserted_at)) AS borrow_usd
                FROM morpho_market_metrics FINAL
                GROUP BY market_id
            ) AS metrics
              ON metrics.market_id = p.market_id
            LEFT JOIN (SELECT * FROM morpho_market_oracle_support FINAL) AS support
              ON support.market_id = p.market_id
            LEFT JOIN (
                SELECT lower(oracle) AS oracle
                FROM morpho_oracle_snapshots FINAL
                WHERE status = 'OK'
                GROUP BY oracle
            ) AS snapshots
              ON snapshots.oracle = lower(p.oracle)
            """
        ).result_rows[0]
        oracle_snapshot_supported = _query_int(
            ch,
            """
            SELECT count()
            FROM (SELECT * FROM morpho_market_oracle_support FINAL) AS s
            INNER JOIN morpho_market_params AS p USING market_id
            WHERE p.oracle != '0x0000000000000000000000000000000000000000'
              AND NOT empty(s.loan_price_feeds)
              AND empty(s.collateral_price_feeds)
            """,
        )
        snapshot_rows = _query_int(ch, "SELECT count() FROM morpho_oracle_snapshots FINAL WHERE status = 'OK'")
        snapshot_oracles = _query_int(ch, "SELECT uniqExact(oracle) FROM morpho_oracle_snapshots FINAL WHERE status = 'OK'")
        snapshot_latest = _query_int(ch, "SELECT toUInt64(max(toUnixTimestamp(timestamp))) FROM morpho_oracle_snapshots FINAL WHERE status = 'OK'")
        raw_head = _query_int(ch, "SELECT max(block_number) FROM morpho_events")
        processed_head = _query_int(ch, "SELECT max(last_processed_block) FROM processor_state WHERE protocol = \'MORPHO_MARKET\'")
        oracle_snapshot_supported_count = support_counts.get("ORACLE_SNAPSHOT_SUPPORTED", 0)
        unsupported_count = support_counts.get("UNSUPPORTED_ORACLE", 0)
        if support_counts.get("ORACLE_SNAPSHOT_SUPPORTED", 0) == 0:
            unsupported_count = max(0, unsupported_count - oracle_snapshot_supported_count)
        return {
            "totalDiscoveredMarkets": int(params[0] or 0),
            "chainlinkSupportedMarkets": support_counts.get("CHAINLINK_SUPPORTED", 0),
            "oracleSnapshotSupportedMarkets": oracle_snapshot_supported_count,
            "unsupportedOracleMarkets": unsupported_count,
            "displayMarkets": int(display_row[0] or 0),
            "apiLatestMarkets": int(display_row[1] or 0),
            "metricMarkets": int(display_row[2] or 0),
            "pricedMarkets": int(display_row[3] or 0),
            "zeroMarkets": int(display_row[4] or 0),
            "missingApiLatestMarkets": max(0, int(params[0] or 0) - int(display_row[1] or 0)),
            "missingMetricMarkets": max(0, int(params[0] or 0) - int(display_row[2] or 0)),
            "missingOracleSupportMarkets": int(display_row[5] or 0),
            "unpricedMarkets": support_counts.get("UNPRICED", 0),
            "oracleSnapshotRows": int(snapshot_rows or 0),
            "oracleSnapshotOracles": int(snapshot_oracles or 0),
            "oracleSnapshotLatestTimestamp": int(snapshot_latest or 0),
            "resolverDiagnostics": {
                "oracleSnapshotRequiredMarkets": int(display_row[6] or 0),
                "oracleSnapshotResolvedMarkets": int(display_row[7] or 0),
                "missingSnapshotMarkets": int(display_row[8] or 0),
                "chainlinkSupportedMarkets": support_counts.get("CHAINLINK_SUPPORTED", 0),
                "oracleSnapshotSupportedMarkets": oracle_snapshot_supported_count,
                "unsupportedOracleMarkets": unsupported_count,
                "unpricedMarkets": support_counts.get("UNPRICED", 0),
            },
            "firstMarketBlock": int(params[1] or 0),
            "lastMarketBlock": int(params[2] or 0),
            "rawHead": int(raw_head or 0),
            "processedHead": int(processed_head or 0),
            "lagBlocks": max(0, int(raw_head or 0) - int(processed_head or 0)),
        }
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)}


def _freshness_payload(status: str = "ready", ready: bool = True) -> AnalyticsFreshness:
    return AnalyticsFreshness(
        ready=ready,
        status=status,
        version=INDEXER_VERSION,
        generated_at=int(datetime.now(timezone.utc).timestamp()),
    )


def _build_lending_data_page_payload(
    freshness: AnalyticsFreshness,
    markets: list[MarketSnapshot],
    tvl_history: list[ProtocolTvlPoint],
    apy_history: list[ProtocolApyPoint],
) -> LendingDataPagePayload:
    normalized_markets: list[LendingDataMarketRow] = []
    for row in markets:
        supply_usd = _finite_non_negative(row.supply_usd)
        borrow_usd = _finite_non_negative(row.borrow_usd)
        supply_apy = _finite_non_negative(row.supply_apy)
        borrow_apy = _finite_non_negative(row.borrow_apy)
        if str(row.protocol or "").upper() == MORPHO_MARKET and supply_apy > 1.0:
            continue
        utilization = min(1.0, borrow_usd / supply_usd) if supply_usd > 0 else 0.0
        normalized_markets.append(
            LendingDataMarketRow(
                entity_id=str(row.entity_id or ""),
                symbol=str(row.symbol or "UNKNOWN"),
                protocol=str(row.protocol or AAVE_MARKET),
                supply_usd=supply_usd,
                borrow_usd=borrow_usd,
                supply_apy=supply_apy,
                borrow_apy=borrow_apy,
                utilization=utilization,
                net_worth=max(0.0, supply_usd - borrow_usd),
            )
        )

    totals_supply = sum(row.supply_usd for row in normalized_markets)
    totals_borrow = sum(row.borrow_usd for row in normalized_markets)
    weighted_supply = sum(row.supply_apy * row.supply_usd for row in normalized_markets)
    weighted_borrow = sum(row.borrow_apy * row.borrow_usd for row in normalized_markets)
    stats = LendingDataStats(
        total_supply_usd=totals_supply,
        total_borrow_usd=totals_borrow,
        average_supply_apy=weighted_supply / totals_supply if totals_supply > 0 else 0.0,
        average_borrow_apy=weighted_borrow / totals_borrow if totals_borrow > 0 else 0.0,
        market_count=len(normalized_markets),
    )

    chart_by_ts: dict[int, LendingDataChartPoint] = {}
    for row in tvl_history:
        raw_date = str(row.date or "").strip()
        if not raw_date:
            continue
        normalized_date = raw_date
        if len(raw_date) == 10 and raw_date[4] == "-" and raw_date[7] == "-":
            normalized_date = f"{raw_date}T00:00:00+00:00"
        elif " " in raw_date:
            normalized_date = f"{raw_date.replace(' ', 'T')}+00:00"
        try:
            ts = int(datetime.fromisoformat(normalized_date.replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
        chart_by_ts[ts] = LendingDataChartPoint(
            timestamp=ts,
            tvl=(
                _finite_non_negative(row.aave)
                + _finite_non_negative(row.euler)
                + _finite_non_negative(row.fluid)
                + _finite_non_negative(row.morpho)
            ),
        )

    for row in apy_history:
        ts = int(row.timestamp or 0)
        if ts <= 0:
            continue
        existing = chart_by_ts.get(ts) or LendingDataChartPoint(timestamp=ts, tvl=0.0)
        existing.average_supply_apy = _finite_non_negative(row.average_supply_apy) * 100.0
        existing.average_borrow_apy = _finite_non_negative(row.average_borrow_apy) * 100.0
        chart_by_ts[ts] = existing

    april_2023 = 1680307200
    chart_data = sorted(
        [point for point in chart_by_ts.values() if point.timestamp >= april_2023],
        key=lambda point: point.timestamp,
    )
    normalized_markets.sort(key=lambda row: row.borrow_usd, reverse=True)
    return LendingDataPagePayload(
        freshness=freshness,
        stats=stats,
        chart_data=chart_data,
        markets=normalized_markets,
    )


def _build_protocol_markets_page_payload(
    freshness: AnalyticsFreshness,
    markets: list[MarketDetail],
) -> ProtocolMarketsPagePayload:
    rows: list[ProtocolMarketRow] = []
    for market in markets:
        supply_usd = _finite_non_negative(market.supply_usd)
        borrow_usd = _finite_non_negative(market.borrow_usd)
        supply_apy = _finite_non_negative(market.supply_apy)
        borrow_apy = _finite_non_negative(market.borrow_apy)
        utilization = min(1.0, borrow_usd / supply_usd) if supply_usd > 0 else 0.0
        is_trapped = utilization >= 0.995 and supply_apy > 1.0
        rows.append(
            ProtocolMarketRow(
                entity_id=str(market.entity_id or ""),
                symbol=str(market.symbol or "UNKNOWN"),
                protocol=str(market.protocol or ""),
                supply_usd=supply_usd,
                borrow_usd=borrow_usd,
                supply_apy=supply_apy,
                borrow_apy=borrow_apy,
                utilization=utilization,
                collateral_symbol=market.collateral_symbol,
                collateral_usd=market.collateral_usd,
                lltv=market.lltv,
                oracle=market.oracle,
                pricing_status=market.pricing_status,
                is_trapped=is_trapped,
            )
        )
    rows.sort(key=lambda row: row.supply_usd, reverse=True)

    total_supply = sum(row.supply_usd for row in rows)
    total_borrow = sum(row.borrow_usd for row in rows)
    healthy = [row for row in rows if not row.is_trapped]
    healthy_supply = sum(row.supply_usd for row in healthy)
    healthy_borrow = sum(row.borrow_usd for row in healthy)
    stats = ProtocolMarketsStats(
        total_supply_usd=total_supply,
        total_borrow_usd=total_borrow,
        average_utilization=total_borrow / total_supply if total_supply > 0 else 0.0,
        average_supply_apy=(
            sum(row.supply_apy * row.supply_usd for row in healthy) / healthy_supply
            if healthy_supply > 0
            else 0.0
        ),
        average_borrow_apy=(
            sum(row.borrow_apy * row.borrow_usd for row in healthy) / healthy_borrow
            if healthy_borrow > 0
            else 0.0
        ),
        market_count=len(rows),
    )
    return ProtocolMarketsPagePayload(freshness=freshness, stats=stats, rows=rows)


def _build_lending_pool_page_payload(
    freshness: AnalyticsFreshness,
    markets: list[MarketDetail],
    timeseries: list[MarketTimeseriesPoint],
    flows: list[MarketFlowPoint],
) -> LendingPoolPagePayload:
    market = markets[0] if markets else None
    rate_chart = [
        LendingPoolRatePoint(
            timestamp=int(point.timestamp or 0),
            supply_apy=_finite_non_negative(point.supply_apy) * 100.0,
            borrow_apy=_finite_non_negative(point.borrow_apy) * 100.0,
            utilization=_finite_non_negative(point.utilization) * 100.0,
            supply_usd=_finite_non_negative(point.supply_usd),
            borrow_usd=_finite_non_negative(point.borrow_usd),
        )
        for point in timeseries
        if int(point.timestamp or 0) > 0
    ]
    rate_chart.sort(key=lambda point: point.timestamp)
    return LendingPoolPagePayload(
        freshness=freshness,
        market=market,
        rate_chart=rate_chart,
        flow_chart=flows,
    )


def _query_lending_data_page(ch, display_in: str) -> LendingDataPagePayload:
    return _build_lending_data_page_payload(
        _freshness_payload(),
        _query_lending_data_market_snapshots(ch),
        _query_protocol_tvl_history(ch, display_in),
        _query_protocol_apy_history(ch, AAVE_MARKET, "1W", 5000),
    )


def _query_protocol_markets_page(ch, protocol: str) -> ProtocolMarketsPagePayload:
    return _build_protocol_markets_page_payload(
        _freshness_payload(),
        _query_protocol_markets(ch, protocol),
    )


def _query_lending_pool_page(
    ch,
    protocol: str,
    entity_id: str,
    timeseries_limit: int,
    flow_limit: int,
) -> LendingPoolPagePayload:
    return _build_lending_pool_page_payload(
        _freshness_payload(),
        _query_protocol_markets(ch, protocol, entity_id),
        _query_market_timeseries(ch, entity_id, "1D", timeseries_limit, protocol),
        _query_market_flow_timeseries(ch, entity_id, "1D", flow_limit, protocol),
    )


def _query_latest_rates(ch) -> Optional[LatestRates]:
    latest = LatestRates(timestamp=0)
    max_ts = 0

    aave_sql = f"""
    SELECT symbol, argMax(borrow_apy, timestamp) AS apy, toUnixTimestamp(max(timestamp)) AS ts
    FROM {AAVE_SERIES_TABLE}
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


def _aave_account_freshness(ch, deployment_id: str) -> AaveAccountFreshness:
    ensure_aave_account_tables(ch)
    latest_event = _query_int(
        ch,
        f"SELECT toUInt64(max(block_number)) FROM aave_account_events WHERE deployment_id = '{_escape_sql_string(deployment_id)}'",
    )
    latest_index = _query_int(
        ch,
        "SELECT toUInt64(max(toUnixTimestamp(timestamp))) FROM aave_timeseries",
    )
    latest_price = _query_int(
        ch,
        "SELECT toUInt64(max(toUnixTimestamp(timestamp))) FROM api_market_latest FINAL WHERE protocol = 'AAVE_MARKET'",
    )
    audit = ch.query(
        """
        SELECT toUInt64(max(toUnixTimestamp(finished_at))) AS ts,
               argMax(status, finished_at) AS status
        FROM aave_reconstruction_audit_runs
        WHERE deployment_id = %(deployment_id)s
        """,
        parameters={"deployment_id": deployment_id},
    ).result_rows
    audit_ts = int(audit[0][0] or 0) if audit else 0
    audit_status = str(audit[0][1] or "NOT_RUN") if audit else "NOT_RUN"
    status = "READY" if latest_event and latest_index and latest_price else "NO_ACCOUNT_EVENTS"
    return AaveAccountFreshness(
        latest_event_block=int(latest_event or 0),
        latest_index_timestamp=int(latest_index or 0),
        latest_price_timestamp=int(latest_price or 0),
        reconstruction_status=status,
        last_rpc_audit_timestamp=audit_ts,
        audit_precision_status=audit_status,
    )


def _query_aave_account_position_rows(
    ch,
    deployment_id: str,
    address: Optional[str] = None,
) -> list[dict]:
    ensure_aave_account_tables(ch)
    escaped_deployment = _escape_sql_string(deployment_id)
    user_filter = ""
    parameters: dict[str, object] = {}
    if address:
        parameters["address"] = _normalize_entity_id(address)
        user_filter = "AND user = %(address)s"
    rows = ch.query(
        f"""
        WITH
        positions AS (
            SELECT
                user,
                reserve,
                sumIf(scaled_delta_raw, token_type = 'ATOKEN') AS scaled_supply_raw,
                sumIf(scaled_delta_raw, token_type = 'VARIABLE_DEBT') AS scaled_variable_debt_raw,
                max(block_number) AS last_block
            FROM aave_account_events
            WHERE deployment_id = '{escaped_deployment}'
              {user_filter}
              AND reserve != ''
            GROUP BY user, reserve
            HAVING scaled_supply_raw != 0 OR scaled_variable_debt_raw != 0
        ),
        collateral AS (
            SELECT
                user,
                reserve,
                argMax(collateral_enabled, tuple(block_number, log_index)) AS collateral_enabled
            FROM aave_account_events
            WHERE deployment_id = '{escaped_deployment}'
              {user_filter}
              AND event_name IN ('ReserveUsedAsCollateralEnabled', 'ReserveUsedAsCollateralDisabled')
            GROUP BY user, reserve
        ),
        emode AS (
            SELECT
                user,
                argMax(emode_category, tuple(block_number, log_index)) AS emode_category
            FROM aave_account_events
            WHERE deployment_id = '{escaped_deployment}'
              {user_filter}
              AND event_name = 'UserEModeSet'
            GROUP BY user
        )
        SELECT
            p.user,
            p.reserve,
            ifNull(tokens.symbol, latest.symbol) AS symbol,
            toUInt8(ifNull(tokens.decimals, 18)) AS decimals,
            p.scaled_supply_raw,
            p.scaled_variable_debt_raw,
            toUInt8(ifNull(c.collateral_enabled, 1)) AS collateral_enabled,
            toUInt16(ifNull(e.emode_category, 0)) AS emode_category,
            ifNull(state.liquidity_index, 1e27) AS liquidity_index,
            ifNull(state.variable_borrow_index, 1e27) AS variable_borrow_index,
            ifNull(latest.price_usd, 0.0) AS price_usd,
            ifNull(latest.ltv, 0.0) AS ltv,
            if(
                ifNull(e.emode_category, 0) > 0
                AND ifNull(e.emode_category, 0) = ifNull(latest.e_mode_category, 0)
                AND ifNull(latest.e_mode_liquidation_threshold, 0.0) > 0,
                latest.e_mode_liquidation_threshold,
                ifNull(latest.liquidation_threshold, 0.0)
            ) AS liquidation_threshold,
            p.last_block
        FROM positions AS p
        LEFT JOIN (SELECT * FROM aave_reserve_tokens FINAL) AS tokens
          ON tokens.deployment_id = '{escaped_deployment}' AND tokens.reserve = p.reserve
        LEFT JOIN (
            SELECT entity_id, argMax(liquidity_index, updated_at) AS liquidity_index,
                   argMax(variable_borrow_index, updated_at) AS variable_borrow_index
            FROM aave_scaled_state
            GROUP BY entity_id
        ) AS state
          ON state.entity_id = p.reserve
        LEFT JOIN (
            SELECT entity_id, symbol, price_usd, ltv, liquidation_threshold,
                   e_mode_category, e_mode_liquidation_threshold
            FROM (SELECT * FROM api_market_latest FINAL)
            WHERE protocol = 'AAVE_MARKET'
        ) AS latest
          ON latest.entity_id = p.reserve
        LEFT JOIN collateral AS c
          ON c.user = p.user AND c.reserve = p.reserve
        LEFT JOIN emode AS e
          ON e.user = p.user
        ORDER BY p.user ASC, p.reserve ASC
        """,
        parameters=parameters,
    ).result_rows
    result = []
    for row in rows:
        decimals = int(row[3] or 18)
        scale = 10 ** decimals
        supply_tokens = max(0.0, float(row[4] or 0) * float(row[8] or 0.0) / 1e27 / scale)
        debt_tokens = max(0.0, float(row[5] or 0) * float(row[9] or 0.0) / 1e27 / scale)
        price = float(row[10] or 0.0)
        collateral_enabled = bool(row[6])
        liquidation_threshold = float(row[12] or 0.0)
        supply_usd = supply_tokens * price
        debt_usd = debt_tokens * price
        collateral_usd = supply_usd if collateral_enabled else 0.0
        result.append(
            {
                "user": str(row[0]),
                "reserve": str(row[1]),
                "symbol": str(row[2] or ""),
                "scaled_supply_raw": str(row[4] or "0"),
                "scaled_variable_debt_raw": str(row[5] or "0"),
                "collateral_enabled": collateral_enabled,
                "emode_category": int(row[7] or 0),
                "supply_usd": supply_usd,
                "collateral_usd": collateral_usd,
                "debt_usd": debt_usd,
                "liquidation_threshold": liquidation_threshold,
                "collateral_liquidation_usd": collateral_usd * liquidation_threshold,
                "last_block": int(row[13] or 0),
            }
        )
    return result


def _build_aave_account(
    deployment_id: str,
    user: str,
    rows: list[dict],
    freshness: AaveAccountFreshness,
) -> AaveAccount:
    total_collateral = sum(float(row["collateral_usd"]) for row in rows)
    total_debt = sum(float(row["debt_usd"]) for row in rows)
    liquidation_value = sum(float(row["collateral_liquidation_usd"]) for row in rows)
    weighted_lt = liquidation_value / total_collateral if total_collateral > 0 else 0.0
    hf = liquidation_value / total_debt if total_debt > 1e-9 else None
    emode = max((int(row["emode_category"]) for row in rows), default=0)
    return AaveAccount(
        deployment_id=deployment_id,
        address=user,
        total_collateral_usd=total_collateral,
        total_debt_usd=total_debt,
        weighted_liquidation_threshold=weighted_lt,
        health_factor=hf,
        emode_category=emode,
        positions=[
            AaveAccountPosition(
                reserve=str(row["reserve"]),
                symbol=str(row["symbol"]),
                scaled_supply_raw=str(row["scaled_supply_raw"]),
                scaled_variable_debt_raw=str(row["scaled_variable_debt_raw"]),
                supply_usd=float(row["supply_usd"]),
                debt_usd=float(row["debt_usd"]),
                collateral_enabled=bool(row["collateral_enabled"]),
                liquidation_threshold=float(row["liquidation_threshold"]),
            )
            for row in rows
            if float(row["supply_usd"]) > 0 or float(row["debt_usd"]) > 0
        ],
        freshness=freshness,
    )


def _query_aave_accounts(
    ch,
    deployment_id: str,
    min_debt_usd: float,
    max_health_factor: Optional[float] = None,
    order_by: str = "HEALTH_FACTOR_ASC",
) -> list[AaveAccount]:
    freshness = _aave_account_freshness(ch, deployment_id)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in _query_aave_account_position_rows(ch, deployment_id):
        grouped[str(row["user"])].append(row)
    accounts = [
        _build_aave_account(deployment_id, user, rows, freshness)
        for user, rows in grouped.items()
    ]
    accounts = [
        account
        for account in accounts
        if account.total_debt_usd >= float(min_debt_usd or 0.0)
        and (
            max_health_factor is None
            or (account.health_factor is not None and account.health_factor <= float(max_health_factor))
        )
    ]
    normalized_order = str(order_by or "HEALTH_FACTOR_ASC").upper()
    if normalized_order == "DEBT_DESC":
        accounts.sort(key=lambda item: (-item.total_debt_usd, item.address))
    elif normalized_order == "COLLATERAL_DESC":
        accounts.sort(key=lambda item: (-item.total_collateral_usd, item.address))
    else:
        accounts.sort(
            key=lambda item: (
                item.health_factor if item.health_factor is not None else float("inf"),
                -item.total_debt_usd,
                item.address,
            )
        )
    return accounts


def _query_aave_account(
    ch,
    deployment_id: str,
    address: str,
) -> Optional[AaveAccount]:
    freshness = _aave_account_freshness(ch, deployment_id)
    rows = _query_aave_account_position_rows(ch, deployment_id, address)
    if not rows:
        return None
    user = _normalize_entity_id(address)
    return _build_aave_account(deployment_id, user, rows, freshness)


def _query_aave_account_stats(
    ch,
    deployment_id: str,
    min_debt_usd: float,
    min_collateral_usd: float,
) -> AaveAccountStats:
    accounts = _query_aave_accounts(ch, deployment_id, min_debt_usd=0.0)
    filtered = [
        account
        for account in accounts
        if account.total_debt_usd >= float(min_debt_usd or 0.0)
        and account.total_collateral_usd >= float(min_collateral_usd or 0.0)
    ]
    total_collateral = sum(account.total_collateral_usd for account in filtered)
    total_debt = sum(account.total_debt_usd for account in filtered)
    liquidation_value = sum(
        account.total_collateral_usd * account.weighted_liquidation_threshold
        for account in filtered
    )
    return AaveAccountStats(
        deployment_id=deployment_id,
        active_accounts=len(filtered),
        debt_accounts=sum(1 for account in filtered if account.total_debt_usd > 0),
        collateral_accounts=sum(1 for account in filtered if account.total_collateral_usd > 0),
        total_collateral_usd=total_collateral,
        total_debt_usd=total_debt,
        weighted_liquidation_threshold=(
            liquidation_value / total_collateral if total_collateral > 0 else 0.0
        ),
        accounts_below_hf_125=sum(
            1 for account in filtered if account.health_factor is not None and account.health_factor < 1.25
        ),
        accounts_below_hf_1=sum(
            1 for account in filtered if account.health_factor is not None and account.health_factor < 1.0
        ),
        freshness=_aave_account_freshness(ch, deployment_id),
    )


def _query_aave_account_profile_history(
    ch,
    deployment_id: str,
    address: str,
    start_ts: Optional[int],
    end_ts: Optional[int],
    limit: int,
) -> list[AaveAccountProfilePoint]:
    ensure_aave_account_tables(ch)
    filters = ["deployment_id = %(deployment_id)s", "user = %(user)s"]
    parameters: dict[str, object] = {
        "deployment_id": deployment_id,
        "user": _normalize_entity_id(address),
        "limit": _safe_limit(limit),
    }
    if start_ts is not None:
        filters.append("timestamp >= fromUnixTimestamp(%(start_ts)s)")
        parameters["start_ts"] = int(start_ts)
    if end_ts is not None:
        filters.append("timestamp <= fromUnixTimestamp(%(end_ts)s)")
        parameters["end_ts"] = int(end_ts)
    where_sql = " AND ".join(filters)
    rows = ch.query(
        f"""
        SELECT toUnixTimestamp(timestamp), total_collateral_usd, total_debt_usd,
               net_worth_usd, weighted_liquidation_threshold, health_factor,
               emode_category, position_count, debt_position_count,
               collateral_position_count, last_event_block
        FROM aave_account_profile_timeseries FINAL
        WHERE {where_sql}
        ORDER BY timestamp DESC
        LIMIT %(limit)s
        """,
        parameters=parameters,
    ).result_rows
    points = [
        AaveAccountProfilePoint(
            timestamp=int(row[0] or 0),
            total_collateral_usd=float(row[1] or 0.0),
            total_debt_usd=float(row[2] or 0.0),
            net_worth_usd=float(row[3] or 0.0),
            weighted_liquidation_threshold=float(row[4] or 0.0),
            health_factor=float(row[5]) if row[5] is not None and float(row[5]) > 0 else None,
            emode_category=int(row[6] or 0),
            position_count=int(row[7] or 0),
            debt_position_count=int(row[8] or 0),
            collateral_position_count=int(row[9] or 0),
            last_event_block=int(row[10] or 0),
        )
        for row in rows
    ]
    points.sort(key=lambda item: item.timestamp)
    return points


def _query_protocol_markets(ch, protocol: str, entity_id: Optional[str] = None) -> list[MarketDetail]:
    allowed = {
        AAVE_MARKET,
        "EULER_MARKET",
        FLUID_MARKET,
        MORPHO_MARKET,
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

    if protocol == MORPHO_MARKET:
        morpho_entity_filter = ""
        if normalized_entity_id:
            escaped_entity = _escape_sql_string(normalized_entity_id)
            if normalized_entity_id.startswith("0x"):
                morpho_entity_filter = f" AND p.market_id LIKE '{escaped_entity}%'"
            else:
                morpho_entity_filter = f" AND p.market_id = '{escaped_entity}'"
        query = f"""
        SELECT entity_id, symbol, proto, supply_usd, borrow_usd,
               supply_apy, borrow_apy, utilization,
               collateral_symbol, collateral_usd, lltv, oracle, pricing_status,
               loan_asset, loan_token, loan_decimals, collateral_asset, collateral_token,
               collateral_decimals, loan_price_usd, collateral_price_usd,
               supply_assets, borrow_assets, collateral_assets, irm, oracle_support,
               pricing_error, is_active, has_supply, has_borrow, has_collateral,
               last_event_ts, last_priced_ts
        FROM (
            SELECT p.market_id AS entity_id,
                   if(latest.entity_id != '' AND latest.symbol != '', latest.symbol, p.loan_symbol) AS symbol,
                   '{escaped_protocol}' AS proto,
                   if(latest.entity_id != '', latest.supply_usd, if(metrics.market_id != '', metrics.supply_usd, 0.0)) AS supply_usd,
                   if(latest.entity_id != '', latest.borrow_usd, if(metrics.market_id != '', metrics.borrow_usd, 0.0)) AS borrow_usd,
                   if(latest.entity_id != '', latest.supply_apy, if(metrics.market_id != '', metrics.supply_apy, 0.0)) AS supply_apy,
                   if(latest.entity_id != '', latest.borrow_apy, if(metrics.market_id != '', metrics.borrow_apy, 0.0)) AS borrow_apy,
                   if(latest.entity_id != '', latest.utilization, if(metrics.market_id != '', metrics.utilization, 0.0)) AS utilization,
                   if(metrics.market_id != '' AND metrics.collateral_symbol != '', metrics.collateral_symbol, p.collateral_symbol) AS collateral_symbol,
                   if(metrics.market_id != '', metrics.collateral_usd, 0.0) AS collateral_usd,
                   if(metrics.market_id != '' AND metrics.lltv > 0, metrics.lltv, toFloat64(p.lltv) / 1000000000000000000.0) AS lltv,
                   if(metrics.market_id != '' AND metrics.oracle != '', metrics.oracle, p.oracle) AS oracle,
                   multiIf(metrics.market_id != '', 'PRICED', support.market_id != '', support.oracle_support, 'DISCOVERED') AS pricing_status,
                   p.loan_symbol AS loan_asset,
                   p.loan_token AS loan_token,
                   toUInt64(p.loan_decimals) AS loan_decimals,
                   p.collateral_symbol AS collateral_asset,
                   p.collateral_token AS collateral_token,
                   toUInt64(p.collateral_decimals) AS collateral_decimals,
                   if(metrics.market_id != '', metrics.loan_price_usd, 0.0) AS loan_price_usd,
                   if(metrics.market_id != '', metrics.collateral_price_usd, 0.0) AS collateral_price_usd,
                   if(state.market_id != '', state.total_supply_assets, '0') AS supply_assets,
                   if(state.market_id != '', state.total_borrow_assets, '0') AS borrow_assets,
                   if(state.market_id != '', state.collateral_assets, '0') AS collateral_assets,
                   p.irm AS irm,
                   if(support.market_id != '', support.oracle_support, 'DISCOVERED') AS oracle_support,
                   if(support.market_id != '', support.reason, '') AS pricing_error,
                   if(state.market_id != '', state.last_event_timestamp > toDateTime(0), false) AS is_active,
                   if(state.market_id != '', toUInt256OrZero(state.total_supply_assets) > 0, false) AS has_supply,
                   if(state.market_id != '', toUInt256OrZero(state.total_borrow_assets) > 0, false) AS has_borrow,
                   if(state.market_id != '', toUInt256OrZero(state.collateral_assets) > 0, false) AS has_collateral,
                   if(state.market_id != '', toUnixTimestamp(state.last_event_timestamp), 0) AS last_event_ts,
                   if(metrics.market_id != '', toUnixTimestamp(metrics.metric_timestamp), 0) AS last_priced_ts
            FROM morpho_market_params p
            LEFT JOIN (
                SELECT entity_id, symbol, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization
                FROM api_market_latest FINAL
                WHERE protocol = '{escaped_protocol}'
            ) AS latest
              ON latest.entity_id = p.market_id
            LEFT JOIN (
                SELECT market_id,
                       argMax(timestamp, tuple(timestamp, inserted_at)) AS metric_timestamp,
                       argMax(supply_usd, tuple(timestamp, inserted_at)) AS supply_usd,
                       argMax(borrow_usd, tuple(timestamp, inserted_at)) AS borrow_usd,
                       argMax(supply_apy, tuple(timestamp, inserted_at)) AS supply_apy,
                       argMax(borrow_apy, tuple(timestamp, inserted_at)) AS borrow_apy,
                       argMax(utilization, tuple(timestamp, inserted_at)) AS utilization,
                       argMax(collateral_symbol, tuple(timestamp, inserted_at)) AS collateral_symbol,
                       argMax(collateral_usd, tuple(timestamp, inserted_at)) AS collateral_usd,
                       argMax(lltv, tuple(timestamp, inserted_at)) AS lltv,
                       argMax(oracle, tuple(timestamp, inserted_at)) AS oracle,
                       argMax(oracle_support, tuple(timestamp, inserted_at)) AS oracle_support,
                       argMax(loan_price_usd, tuple(timestamp, inserted_at)) AS loan_price_usd,
                       argMax(collateral_price_usd, tuple(timestamp, inserted_at)) AS collateral_price_usd
                FROM morpho_market_metrics FINAL
                GROUP BY market_id
            ) AS metrics
              ON metrics.market_id = p.market_id
            LEFT JOIN (SELECT * FROM morpho_market_oracle_support FINAL) AS support
              ON support.market_id = p.market_id
            LEFT JOIN (SELECT * FROM morpho_market_state FINAL) AS state
              ON state.market_id = p.market_id
            WHERE 1 = 1
            {morpho_entity_filter}
        )
        ORDER BY supply_usd DESC, borrow_usd DESC, entity_id ASC
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
               '' AS collateral_symbol, 0 AS collateral_usd, 0 AS lltv, '' AS oracle, '' AS oracle_support
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
            collateral_usd=float(row[9]) if row[9] is not None else None,
            lltv=float(row[10]) if row[10] else None,
            oracle=str(row[11]) if row[11] else None,
            pricing_status=str(row[12]) if row[12] else None,
            loan_asset=str(row[13]) if len(row) > 13 and row[13] else None,
            loan_token=str(row[14]) if len(row) > 14 and row[14] else None,
            loan_decimals=int(row[15]) if len(row) > 15 and row[15] is not None else None,
            collateral_asset=str(row[16]) if len(row) > 16 and row[16] else None,
            collateral_token=str(row[17]) if len(row) > 17 and row[17] else None,
            collateral_decimals=int(row[18]) if len(row) > 18 and row[18] is not None else None,
            loan_price_usd=float(row[19]) if len(row) > 19 and row[19] is not None else None,
            collateral_price_usd=float(row[20]) if len(row) > 20 and row[20] is not None else None,
            supply_assets=str(row[21]) if len(row) > 21 and row[21] is not None else None,
            borrow_assets=str(row[22]) if len(row) > 22 and row[22] is not None else None,
            collateral_assets=str(row[23]) if len(row) > 23 and row[23] is not None else None,
            irm=str(row[24]) if len(row) > 24 and row[24] else None,
            oracle_support=str(row[25]) if len(row) > 25 and row[25] else None,
            pricing_error=str(row[26]) if len(row) > 26 and row[26] else None,
            is_active=bool(row[27]) if len(row) > 27 and row[27] is not None else None,
            has_supply=bool(row[28]) if len(row) > 28 and row[28] is not None else None,
            has_borrow=bool(row[29]) if len(row) > 29 and row[29] is not None else None,
            has_collateral=bool(row[30]) if len(row) > 30 and row[30] is not None else None,
            last_event_timestamp=int(row[31]) if len(row) > 31 and row[31] else None,
            last_priced_timestamp=int(row[32]) if len(row) > 32 and row[32] else None,
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
                euler=totals_by_protocol.get("EULER", 0.0),
                fluid=totals_by_protocol.get("FLUID", 0.0),
                morpho=totals_by_protocol.get("MORPHO", 0.0),
            )
        )
        cursor += one_week

    return points


def _query_protocol_tvl_history(ch, display_in: str = "USD") -> list[ProtocolTvlPoint]:
    res = ch.query(
        f"""
        SELECT day, protocol, entity_id, argMaxMerge(supply_usd_state) AS supply_usd
        FROM {API_PROTOCOL_TVL_AGG_TABLE}
        WHERE protocol IN ('AAVE', 'EULER', 'FLUID', 'MORPHO')
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
                euler=0.0,
                fluid=0.0,
                morpho=0.0,
                )
            )
            continue

        converted.append(
            ProtocolTvlPoint(
                date=point.date,
                aave=float(point.aave / divisor),
                euler=float(point.euler / divisor),
                fluid=float(point.fluid / divisor),
                morpho=float(point.morpho / divisor),
            )
        )
    return converted


def _query_protocol_apy_history(
    ch, protocol: str, resolution: str, limit: int
) -> list[ProtocolApyPoint]:
    allowed = {AAVE_MARKET, "EULER_MARKET", FLUID_MARKET, MORPHO_MARKET}
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
                FROM {AAVE_SERIES_TABLE}
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


def _query_market_timeseries(
    ch,
    entity_id: str,
    resolution: str,
    limit: int,
    protocol: Optional[str] = None,
) -> list[MarketTimeseriesPoint]:
    normalized_entity_id = _normalize_entity_id(entity_id)
    if not normalized_entity_id:
        return []

    protocol_filter = ""
    params = {
        "eid_prefix": f"{normalized_entity_id}%",
        "lim": _safe_limit(limit),
    }
    if protocol:
        protocol_filter = "AND protocol = %(protocol)s"
        params["protocol"] = str(protocol)

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
      {protocol_filter}
    GROUP BY ts
    ORDER BY ts DESC
    LIMIT %(lim)s
    """
    res = ch.query(sql, parameters=params)
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


def _query_market_flow_timeseries_from_balance_deltas(
    ch,
    entity_id: str,
    resolution: str,
    limit: int,
    protocol: Optional[str] = None,
) -> list[MarketFlowPoint]:
    safe_limit = _safe_limit(limit)
    # Fetch one extra point so first visible bucket can compute deltas.
    points = _query_market_timeseries(ch, entity_id, resolution, safe_limit + 1, protocol)
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


def _query_market_flow_timeseries(
    ch,
    entity_id: str,
    resolution: str,
    limit: int,
    protocol: Optional[str] = None,
) -> list[MarketFlowPoint]:
    if _is_aave_market_entity(ch, entity_id):
        preaggregated = _query_aave_preaggregated_flow_timeseries(
            ch, entity_id, resolution, limit
        )
        if preaggregated:
            return preaggregated
        return _query_aave_event_flow_timeseries(ch, entity_id, resolution, limit)
    return _query_market_flow_timeseries_from_balance_deltas(ch, entity_id, resolution, limit, protocol)


def _normalize_pendle_asset_type(value: str) -> str:
    normalized = (value or "").strip().upper()
    return normalized if normalized in {"PT", "YT"} else ""


def _normalize_pendle_time_frame(value: str) -> str:
    normalized = (value or "hour").strip().lower()
    return normalized if normalized in {"hour", "day", "week"} else "hour"


def _normalize_pendle_address(value: str) -> str:
    text = (value or "").strip().lower()
    if "-" in text and text.split("-", 1)[0].isdigit():
        text = text.split("-", 1)[1]
    if text and not text.startswith("0x") and len(text) == 40:
        text = f"0x{text}"
    return text


def _timestamp(value) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    if isinstance(value, date):
        return int(datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0
    return 0


def _pendle_type_filter(asset_types: Optional[list[str]]) -> str:
    if not asset_types:
        return ""
    normalized = sorted({kind for kind in (_normalize_pendle_asset_type(v) for v in asset_types) if kind})
    if not normalized:
        return ""
    return " AND asset_type IN (" + ", ".join(f"'{kind}'" for kind in normalized) + ")"


def _query_pendle_eth_assets(
    ch,
    asset_types: Optional[list[str]] = None,
    active_only: bool = False,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[PendleAsset]:
    conditions = ["chain_id = 1"]
    type_filter = _pendle_type_filter(asset_types)
    if type_filter:
        conditions.append(type_filter.replace(" AND ", "", 1))
    if active_only:
        conditions.append("active = 1")
    if search and search.strip():
        needle = _escape_sql_string(search.strip().lower())
        conditions.append(
            f"(positionCaseInsensitive(symbol, '{needle}') > 0 "
            f"OR positionCaseInsensitive(asset_address, '{needle}') > 0 "
            f"OR positionCaseInsensitive(market_address, '{needle}') > 0)"
        )
    sql = f"""
        SELECT asset_address, chain_id, asset_type, symbol, market_address, expiry, active, matured
        FROM pendle_eth_assets FINAL
        WHERE {' AND '.join(conditions)}
        ORDER BY active DESC, expiry ASC, symbol ASC, asset_address ASC
        LIMIT {_safe_limit(limit)} OFFSET {max(0, int(offset or 0))}
    """
    rows = ch.query(sql).result_rows
    return [
        PendleAsset(
            asset_address=str(row[0]),
            chain_id=int(row[1]),
            asset_type=str(row[2]),
            symbol=str(row[3]),
            market_address=str(row[4]),
            expiry=_timestamp(row[5]),
            active=bool(row[6]),
            matured=bool(row[7]),
        )
        for row in rows
    ]


def _query_pendle_eth_latest_prices(
    ch,
    asset_types: Optional[list[str]] = None,
    addresses: Optional[list[str]] = None,
    limit: int = 500,
) -> list[PendleLatestPrice]:
    conditions = ["chain_id = 1"]
    type_filter = _pendle_type_filter(asset_types)
    if type_filter:
        conditions.append(type_filter.replace(" AND ", "", 1))
    if addresses:
        normalized = sorted({_normalize_pendle_address(v) for v in addresses if _normalize_pendle_address(v)})
        if normalized:
            values = ", ".join(f"'{_escape_sql_string(v)}'" for v in normalized)
            conditions.append(f"asset_address IN ({values})")
    sql = f"""
        SELECT asset_address, chain_id, asset_type, symbol, price_usd, source_timestamp
        FROM pendle_eth_price_latest FINAL
        WHERE {' AND '.join(conditions)}
        ORDER BY source_timestamp DESC, symbol ASC, asset_address ASC
        LIMIT {_safe_limit(limit)}
    """
    rows = ch.query(sql).result_rows
    return [
        PendleLatestPrice(
            asset_address=str(row[0]),
            chain_id=int(row[1]),
            asset_type=str(row[2]),
            symbol=str(row[3]),
            price_usd=float(row[4]),
            timestamp=_timestamp(row[5]),
        )
        for row in rows
    ]


def _query_pendle_market_page(ch, search: str) -> PendleMarketPagePayload:
    assets = _query_pendle_eth_assets(ch, None, False, search, 50, 0)
    addresses = [asset.asset_address for asset in assets]
    prices = _query_pendle_eth_latest_prices(ch, None, addresses, API_MAX_PAGE_SIZE) if addresses else []
    market_address = ""
    if assets:
        market_address = assets[0].market_address
    else:
        market_address = _normalize_pendle_address(search) or str(search or "").strip().lower()
    return PendleMarketPagePayload(
        market_address=market_address,
        assets=assets,
        latest_prices=prices,
        freshness=_freshness_payload(),
    )


def _query_morpho_market_events(market_id: Optional[str] = None, event_name: Optional[str] = None, limit: int = 500) -> list[MorphoMarketEvent]:
    ch = get_clickhouse_client()
    filters = []
    if market_id:
        filters.append(f"market_id = '{_escape_sql_string(market_id.lower())}'")
    if event_name:
        filters.append(f"event_name = '{_escape_sql_string(event_name)}'")
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    safe_limit = max(1, min(int(limit or 500), 5000))
    try:
        rows = ch.query(
            f"""
            SELECT timestamp, block_number, tx_hash, log_index, market_id, event_name,
                   caller, on_behalf, receiver, assets, shares, collateral_assets,
                   repaid_assets, seized_assets, bad_debt_assets, interest_assets, fee_shares, fee_wad
            FROM morpho_market_events FINAL
            {where_clause}
            ORDER BY block_number DESC, log_index DESC
            LIMIT {safe_limit}
            """
        ).result_rows
    except Exception:
        return []
    return [
        MorphoMarketEvent(
            timestamp=_timestamp(row[0]),
            block_number=int(row[1] or 0),
            tx_hash=str(row[2] or ""),
            log_index=int(row[3] or 0),
            market_id=str(row[4] or ""),
            event_name=str(row[5] or ""),
            caller=str(row[6] or ""),
            on_behalf=str(row[7] or ""),
            receiver=str(row[8] or ""),
            assets=str(row[9] or "0"),
            shares=str(row[10] or "0"),
            collateral_assets=str(row[11] or "0"),
            repaid_assets=str(row[12] or "0"),
            seized_assets=str(row[13] or "0"),
            bad_debt_assets=str(row[14] or "0"),
            interest_assets=str(row[15] or "0"),
            fee_shares=str(row[16] or "0"),
            fee_wad=str(row[17] or "0"),
        )
        for row in rows
    ]


def _query_morpho_market_positions(market_id: Optional[str] = None, user: Optional[str] = None, limit: int = 500) -> list[MorphoMarketPosition]:
    ch = get_clickhouse_client()
    filters = []
    if market_id:
        filters.append(f"pos.market_id = '{_escape_sql_string(market_id.lower())}'")
    if user:
        filters.append(f"pos.user = '{_escape_sql_string(user.lower())}'")
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    safe_limit = max(1, min(int(limit or 500), 5000))
    try:
        rows = ch.query(
            f"""
            SELECT pos.market_id, pos.user, pos.supply_shares, pos.borrow_shares, pos.collateral_assets,
                   if(state.total_supply_shares != '0',
                      toString(toUInt256OrZero(pos.supply_shares) * toUInt256OrZero(state.total_supply_assets) / toUInt256OrZero(state.total_supply_shares)), '0') AS estimated_supply_assets,
                   if(state.total_borrow_shares != '0',
                      toString(toUInt256OrZero(pos.borrow_shares) * toUInt256OrZero(state.total_borrow_assets) / toUInt256OrZero(state.total_borrow_shares)), '0') AS estimated_borrow_assets,
                   if(metrics.market_id != '' AND toUInt256OrZero(state.collateral_assets) > 0,
                      metrics.collateral_usd * toFloat64(toUInt256OrZero(pos.collateral_assets)) / toFloat64(toUInt256OrZero(state.collateral_assets)), 0.0) AS collateral_usd,
                   if(metrics.market_id != '' AND metrics.borrow_usd > 0,
                      (metrics.collateral_usd * metrics.lltv) / metrics.borrow_usd, NULL) AS health_factor,
                   toUnixTimestamp(pos.last_event_timestamp) AS last_event_ts
            FROM morpho_market_positions pos
            LEFT JOIN morpho_market_state state ON state.market_id = pos.market_id
            LEFT JOIN (
                SELECT market_id,
                       argMax(collateral_usd, tuple(timestamp, inserted_at)) AS collateral_usd,
                       argMax(borrow_usd, tuple(timestamp, inserted_at)) AS borrow_usd,
                       argMax(lltv, tuple(timestamp, inserted_at)) AS lltv
                FROM morpho_market_metrics FINAL
                GROUP BY market_id
            ) AS metrics ON metrics.market_id = pos.market_id
            {where_clause}
            ORDER BY toUInt256OrZero(pos.collateral_assets) DESC, pos.market_id, pos.user
            LIMIT {safe_limit}
            """
        ).result_rows
    except Exception:
        return []
    return [
        MorphoMarketPosition(
            market_id=str(row[0] or ""),
            user=str(row[1] or ""),
            supply_shares=str(row[2] or "0"),
            borrow_shares=str(row[3] or "0"),
            collateral_assets=str(row[4] or "0"),
            estimated_supply_assets=str(row[5] or "0"),
            estimated_borrow_assets=str(row[6] or "0"),
            collateral_usd=float(row[7] or 0.0),
            health_factor=float(row[8]) if row[8] is not None else None,
            last_event_timestamp=int(row[9] or 0),
        )
        for row in rows
    ]


def _query_metamorpho_vaults(vault_address: Optional[str] = None, limit: int = 500) -> list[MetaMorphoVault]:
    ch = get_clickhouse_client()
    vault_filter = f"WHERE registry.vault_address = '{_escape_sql_string(vault_address.lower())}'" if vault_address else ""
    safe_limit = max(1, min(int(limit or 500), 2000))
    try:
        rows = ch.query(
            f"""
            SELECT registry.vault_address, registry.name, registry.asset_symbol, registry.asset_address,
                   if(state.vault_address != '', state.total_assets, '0') AS total_assets,
                   if(state.vault_address != '', state.total_supply, '0') AS total_supply,
                   if(state.vault_address != '', state.share_price_usd, 0.0) AS share_price_usd,
                   if(state.vault_address != '', state.tvl_usd, 0.0) AS tvl_usd,
                   if(state.vault_address != '', state.is_canonical_tvl, 0) AS is_canonical_tvl,
                   if(state.vault_address != '', toUnixTimestamp(state.metric_timestamp), 0) AS last_snapshot_ts
            FROM metamorpho_vault_registry registry
            LEFT JOIN (
                SELECT vault_address,
                       argMax(timestamp, tuple(timestamp, inserted_at)) AS metric_timestamp,
                       argMax(total_assets, tuple(timestamp, inserted_at)) AS total_assets,
                       argMax(total_supply, tuple(timestamp, inserted_at)) AS total_supply,
                       argMax(share_price_usd, tuple(timestamp, inserted_at)) AS share_price_usd,
                       argMax(tvl_usd, tuple(timestamp, inserted_at)) AS tvl_usd,
                       argMax(is_canonical_tvl, tuple(timestamp, inserted_at)) AS is_canonical_tvl
                FROM metamorpho_vault_state FINAL
                GROUP BY vault_address
            ) AS state ON state.vault_address = registry.vault_address
            {vault_filter}
            ORDER BY tvl_usd DESC, registry.name
            LIMIT {safe_limit}
            """
        ).result_rows
    except Exception:
        return []
    return [
        MetaMorphoVault(
            vault_address=str(row[0] or ""),
            name=str(row[1] or ""),
            asset_symbol=str(row[2] or ""),
            asset_address=str(row[3] or ""),
            total_assets=str(row[4] or "0"),
            total_supply=str(row[5] or "0"),
            share_price_usd=float(row[6] or 0.0),
            tvl_usd=float(row[7] or 0.0),
            is_canonical_tvl=bool(row[8]),
            last_snapshot_timestamp=int(row[9] or 0),
        )
        for row in rows
    ]


def _query_metamorpho_vault_allocations(vault_address: Optional[str] = None, market_id: Optional[str] = None, limit: int = 1000) -> list[MetaMorphoVaultAllocation]:
    ch = get_clickhouse_client()
    filters = []
    if vault_address:
        filters.append(f"vault_address = '{_escape_sql_string(vault_address.lower())}'")
    if market_id:
        filters.append(f"market_id = '{_escape_sql_string(market_id.lower())}'")
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    safe_limit = max(1, min(int(limit or 1000), 5000))
    try:
        rows = ch.query(
            f"""
            SELECT vault_address, market_id,
                   argMax(cap, tuple(timestamp, inserted_at)) AS cap,
                   argMax(supplied_assets, tuple(timestamp, inserted_at)) AS supplied_assets,
                   argMax(supplied_usd, tuple(timestamp, inserted_at)) AS supplied_usd,
                   argMax(allocation_share, tuple(timestamp, inserted_at)) AS allocation_share,
                   max(timestamp) AS latest_timestamp
            FROM metamorpho_vault_allocations FINAL
            {where_clause}
            GROUP BY vault_address, market_id
            ORDER BY supplied_usd DESC
            LIMIT {safe_limit}
            """
        ).result_rows
    except Exception:
        return []
    return [
        MetaMorphoVaultAllocation(
            vault_address=str(row[0] or ""),
            market_id=str(row[1] or ""),
            cap=str(row[2] or "0"),
            supplied_assets=str(row[3] or "0"),
            supplied_usd=float(row[4] or 0.0),
            allocation_share=float(row[5] or 0.0),
            timestamp=_timestamp(row[6]),
        )
        for row in rows
    ]



def _query_metamorpho_vault_flows(
    vault_address: Optional[str] = None,
    limit: int = 1000,
) -> list[MetaMorphoVaultFlow]:
    ch = get_clickhouse_client()
    filters = []
    if vault_address:
        filters.append(f"vault_address = '{_escape_sql_string(vault_address.lower())}'")
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    safe_limit = max(1, min(int(limit or 1000), 5000))
    try:
        rows = ch.query(
            f"""
            SELECT timestamp, vault_address, asset_symbol,
                   argMax(deposit_assets, inserted_at) AS deposit_assets,
                   argMax(withdraw_assets, inserted_at) AS withdraw_assets,
                   argMax(deposit_shares, inserted_at) AS deposit_shares,
                   argMax(withdraw_shares, inserted_at) AS withdraw_shares,
                   argMax(transfer_shares, inserted_at) AS transfer_shares,
                   argMax(deposit_usd, inserted_at) AS deposit_usd,
                   argMax(withdraw_usd, inserted_at) AS withdraw_usd,
                   argMax(net_flow_usd, inserted_at) AS net_flow_usd,
                   argMax(event_count, inserted_at) AS event_count
            FROM metamorpho_vault_flows_hourly FINAL
            {where_clause}
            GROUP BY timestamp, vault_address, asset_symbol
            ORDER BY timestamp DESC, abs(net_flow_usd) DESC
            LIMIT {safe_limit}
            """
        ).result_rows
    except Exception:
        return []
    return [
        MetaMorphoVaultFlow(
            timestamp=_timestamp(row[0]),
            vault_address=str(row[1] or ""),
            asset_symbol=str(row[2] or ""),
            deposit_assets=str(row[3] or "0"),
            withdraw_assets=str(row[4] or "0"),
            deposit_shares=str(row[5] or "0"),
            withdraw_shares=str(row[6] or "0"),
            transfer_shares=str(row[7] or "0"),
            deposit_usd=float(row[8] or 0.0),
            withdraw_usd=float(row[9] or 0.0),
            net_flow_usd=float(row[10] or 0.0),
            event_count=int(row[11] or 0),
        )
        for row in rows
    ]


def _query_pendle_eth_price_history(
    ch,
    address: str,
    time_frame: str = "hour",
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    limit: int = 1440,
) -> list[PendlePricePoint]:
    asset_address = _normalize_pendle_address(address)
    if not asset_address:
        return []
    frame = _normalize_pendle_time_frame(time_frame)
    conditions = [
        "chain_id = 1",
        f"asset_address = '{_escape_sql_string(asset_address)}'",
        f"time_frame = '{frame}'",
    ]
    if start_ts is not None:
        conditions.append(f"timestamp >= toDateTime({max(0, int(start_ts))})")
    if end_ts is not None:
        conditions.append(f"timestamp <= toDateTime({max(0, int(end_ts))})")
    sql = f"""
        SELECT timestamp, open, high, low, close, volume
        FROM pendle_eth_price_ohlcv FINAL
        WHERE {' AND '.join(conditions)}
        ORDER BY timestamp DESC
        LIMIT {min(_safe_limit(limit), 1440)}
    """
    rows = list(reversed(ch.query(sql).result_rows))
    return [
        PendlePricePoint(
            timestamp=_timestamp(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in rows
    ]


@strawberry.type
class Query:
    @strawberry.field(name="apiStatus")
    def api_status(self) -> ApiStatusPayload:
        ch = get_clickhouse_client()
        return _api_status_payload(ch)

    @strawberry.field(name="protocols")
    def protocols(self) -> list[ProtocolStatus]:
        ch = get_clickhouse_client()
        return _api_protocol_statuses(ch)

    @strawberry.field(name="protocolCoverage")
    def protocol_coverage(self, protocol: str) -> ProtocolCoverage:
        ch = get_clickhouse_client()
        return _protocol_coverage(ch, protocol)

    @strawberry.field(name="markets")
    def markets(
        self,
        protocol: str,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[str] = None,
    ) -> MarketConnection:
        ch = get_clickhouse_client()
        rows = _filter_markets(_query_protocol_markets(ch, protocol), filter)
        nodes, page_info, total_count = _connection_page(rows, first, after)
        return MarketConnection(nodes=nodes, page_info=page_info, total_count=total_count)

    @strawberry.field(name="market")
    def market(self, protocol: str, market_id: str) -> Optional[MarketDetail]:
        ch = get_clickhouse_client()
        rows = _query_protocol_markets(ch, protocol, market_id)
        return rows[0] if rows else None

    @strawberry.field(name="marketPage")
    def market_page(
        self,
        protocol: str,
        market_id: str,
        quote_asset: str = "USD",
        timeseries_limit: Optional[int] = None,
        flow_limit: Optional[int] = None,
    ) -> LendingPoolPagePayload:
        del quote_asset
        ch = get_clickhouse_client()
        return _query_lending_pool_page(
            ch,
            protocol,
            market_id,
            _api_page_size(timeseries_limit),
            _api_page_size(flow_limit),
        )

    @strawberry.field(name="marketSeries")
    def market_series(
        self,
        protocol: str,
        market_id: str,
        resolution: str = "1D",
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> MarketSeriesConnection:
        ch = get_clickhouse_client()
        rows = _query_market_timeseries(ch, market_id, resolution, API_MAX_PAGE_SIZE, protocol)
        if start_ts is not None:
            rows = [row for row in rows if int(row.timestamp or 0) >= int(start_ts)]
        if end_ts is not None:
            rows = [row for row in rows if int(row.timestamp or 0) <= int(end_ts)]
        nodes, page_info, total_count = _connection_page(rows, first, after)
        return MarketSeriesConnection(nodes=nodes, page_info=page_info, total_count=total_count)

    @strawberry.field(name="analyticsStatus", deprecation_reason="Use apiStatus.")
    def analytics_status(self) -> AnalyticsStatusPayload:
        ch = get_clickhouse_client()
        return _analytics_status_payload(ch)

    @strawberry.field(name="lendingDataPage")
    def lending_data_page(self, display_in: str = "USD") -> LendingDataPagePayload:
        ch = get_clickhouse_client()
        return _query_lending_data_page(ch, display_in)

    @strawberry.field(name="protocolMarketsPage")
    def protocol_markets_page(self, protocol: str = AAVE_MARKET) -> ProtocolMarketsPagePayload:
        ch = get_clickhouse_client()
        return _query_protocol_markets_page(ch, protocol)

    @strawberry.field(name="lendingPoolPage")
    def lending_pool_page(
        self,
        protocol: str = AAVE_MARKET,
        entity_id: str = "",
        timeseries_limit: int = 500,
        flow_limit: int = 500,
    ) -> LendingPoolPagePayload:
        ch = get_clickhouse_client()
        return _query_lending_pool_page(ch, protocol, entity_id, timeseries_limit, flow_limit)

    @strawberry.field(name="historicalRates")
    def historical_rates(
        self, symbols: List[str], resolution: str, limit: int = 17520
    ) -> List[HistoricalRate]:
        ch = get_clickhouse_client()
        return _query_historical_rates(ch, symbols, resolution, limit)

    @strawberry.field(name="pendleEthAssets")
    def pendle_eth_assets(
        self,
        asset_types: Optional[List[str]] = None,
        active_only: bool = False,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PendleAsset]:
        ch = get_clickhouse_client()
        return _query_pendle_eth_assets(ch, asset_types, active_only, search, limit, offset)

    @strawberry.field(name="pendleEthLatestPrices")
    def pendle_eth_latest_prices(
        self,
        asset_types: Optional[List[str]] = None,
        addresses: Optional[List[str]] = None,
        limit: int = 500,
    ) -> List[PendleLatestPrice]:
        ch = get_clickhouse_client()
        return _query_pendle_eth_latest_prices(ch, asset_types, addresses, limit)

    @strawberry.field(name="pendleEthPriceHistory")
    def pendle_eth_price_history(
        self,
        address: str,
        time_frame: str = "hour",
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 1440,
    ) -> List[PendlePricePoint]:
        ch = get_clickhouse_client()
        return _query_pendle_eth_price_history(ch, address, time_frame, start_ts, end_ts, limit)

    @strawberry.field(name="pendleMarketPage")
    def pendle_market_page(self, search: str) -> PendleMarketPagePayload:
        ch = get_clickhouse_client()
        return _query_pendle_market_page(ch, search)

    @strawberry.field(name="marketSnapshots")
    def market_snapshots(self, protocol: Optional[str] = None) -> List[MarketSnapshot]:
        ch = get_clickhouse_client()
        return _query_market_snapshots(ch, protocol)

    @strawberry.field(name="aaveAccountStats")
    def aave_account_stats(
        self,
        deployment_id: str = AAVE_DEPLOYMENT_ID,
        min_debt_usd: float = 0.0,
        min_collateral_usd: float = 0.0,
    ) -> AaveAccountStats:
        ch = get_clickhouse_client()
        return _query_aave_account_stats(ch, deployment_id, min_debt_usd, min_collateral_usd)

    @strawberry.field(name="aaveAccounts")
    def aave_accounts(
        self,
        deployment_id: str = AAVE_DEPLOYMENT_ID,
        first: Optional[int] = None,
        after: Optional[str] = None,
        order_by: str = "HEALTH_FACTOR_ASC",
        min_debt_usd: float = 0.0,
        max_health_factor: Optional[float] = None,
    ) -> AaveAccountConnection:
        ch = get_clickhouse_client()
        rows = _query_aave_accounts(ch, deployment_id, min_debt_usd, max_health_factor, order_by)
        nodes, page_info, total_count = _connection_page(rows, first, after)
        return AaveAccountConnection(nodes=nodes, page_info=page_info, total_count=total_count)

    @strawberry.field(name="aaveAccount")
    def aave_account(
        self,
        address: str,
        deployment_id: str = AAVE_DEPLOYMENT_ID,
    ) -> Optional[AaveAccount]:
        ch = get_clickhouse_client()
        return _query_aave_account(ch, deployment_id, address)

    @strawberry.field(name="aaveAccountProfileHistory")
    def aave_account_profile_history(
        self,
        address: str,
        deployment_id: str = AAVE_DEPLOYMENT_ID,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 1000,
    ) -> list[AaveAccountProfilePoint]:
        ch = get_clickhouse_client()
        return _query_aave_account_profile_history(ch, deployment_id, address, start_ts, end_ts, limit)

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

    @strawberry.field(name="morphoMarketEvents")
    def morpho_market_events(
        self, market_id: Optional[str] = None, event_name: Optional[str] = None, limit: int = 500
    ) -> list[MorphoMarketEvent]:
        return _query_morpho_market_events(market_id, event_name, limit)

    @strawberry.field(name="morphoMarketPositions")
    def morpho_market_positions(
        self, market_id: Optional[str] = None, user: Optional[str] = None, limit: int = 500
    ) -> list[MorphoMarketPosition]:
        return _query_morpho_market_positions(market_id, user, limit)

    @strawberry.field(name="metamorphoVaults")
    def metamorpho_vaults(
        self, vault_address: Optional[str] = None, limit: int = 500
    ) -> list[MetaMorphoVault]:
        return _query_metamorpho_vaults(vault_address, limit)

    @strawberry.field(name="metamorphoVaultAllocations")
    def metamorpho_vault_allocations(
        self, vault_address: Optional[str] = None, market_id: Optional[str] = None, limit: int = 1000
    ) -> list[MetaMorphoVaultAllocation]:
        return _query_metamorpho_vault_allocations(vault_address, market_id, limit)

    @strawberry.field(name="metamorphoVaultFlows")
    def metamorpho_vault_flows(
        self, vault_address: Optional[str] = None, limit: int = 1000
    ) -> list[MetaMorphoVaultFlow]:
        return _query_metamorpho_vault_flows(vault_address, limit)

    @strawberry.field(name="fluidContracts")
    def fluid_contracts(
        self, product_type: Optional[str] = None, active_only: bool = True, limit: int = 500
    ) -> list[FluidContractRegistryItem]:
        return _query_fluid_contracts(product_type, active_only, limit)

    @strawberry.field(name="fluidProductSnapshots")
    def fluid_product_snapshots(
        self, product_type: Optional[str] = None, product_id: Optional[str] = None, limit: int = 500
    ) -> list[FluidProductSnapshot]:
        return _query_fluid_product_snapshots(product_type, product_id, limit)

    @strawberry.field(name="fluidProductComponents")
    def fluid_product_components(
        self, product_type: Optional[str] = None, product_id: Optional[str] = None, limit: int = 1000
    ) -> list[FluidProductComponent]:
        return _query_fluid_product_components(product_type, product_id, limit)

schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(
    schema,
    graphql_ide="graphiql" if GRAPHQL_ENABLE_IDE else None,
    allow_queries_via_get=GRAPHQL_ALLOW_GET_QUERIES,
)
app = FastAPI(title="RLD ClickHouse GraphQL")
_CORS_ORIGINS = _parse_cors_origins(
    "API_CORS_ORIGINS",
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


@app.middleware("http")
async def analytics_api_guard(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "").strip() or str(uuid.uuid4())
    start = time.monotonic()
    response = None
    path = request.url.path
    try:
        if path in {"/status", "/healthz", "/metrics"} and API_PROTECT_ADMIN_ENDPOINTS:
            admin_token = _request_admin_token(request)
            if not _is_valid_admin_token(admin_token):
                response = JSONResponse(
                    status_code=401,
                    content={"status": "unauthorized", "reason": "admin_token_required"},
                )
                return response

        if path.startswith("/graphql") or path.startswith("/envio-graphql"):
            if request.method.upper() == "GET" and not GRAPHQL_ALLOW_GET_QUERIES:
                response = _graphql_error("GraphQL GET queries are disabled", "GET_QUERIES_DISABLED", 405)
                return response

            if GRAPHQL_REQUIRE_API_KEY and not _is_valid_api_key(_request_api_key(request)):
                response = _graphql_error("API key required", "UNAUTHENTICATED", 401)
                return response

            if not _rate_limit_allowed(_rate_limit_bucket_key(request)):
                response = _graphql_error("GraphQL rate limit exceeded", "RATE_LIMITED", 429)
                return response

            if request.method.upper() == "POST":
                payload, error_response = await _parse_graphql_request_body(request)
                if error_response is not None:
                    response = error_response
                    return response
                query = str((payload or {}).get("query") or "")
                if query:
                    if not GRAPHQL_ENABLE_INTROSPECTION and _is_introspection_query(query):
                        response = _graphql_error("GraphQL introspection is disabled", "INTROSPECTION_DISABLED", 400)
                        return response
                    depth = _query_depth(query)
                    if depth > GRAPHQL_MAX_DEPTH:
                        response = _graphql_error("GraphQL query is too deep", "QUERY_TOO_DEEP", 400)
                        return response

        response = await call_next(request)
        return response
    finally:
        if response is not None:
            response.headers["X-Request-ID"] = request_id
            if path.startswith("/envio-graphql"):
                response.headers["Deprecation"] = "true"
                response.headers["Link"] = '</graphql>; rel="successor-version"'
                if ENVIO_GRAPHQL_ALIAS_SUNSET:
                    response.headers["Sunset"] = ENVIO_GRAPHQL_ALIAS_SUNSET
                response.headers["Warning"] = '299 - "/envio-graphql is deprecated; use /graphql"'
            _record_http_metric(path, response.status_code, time.monotonic() - start)
@app.get("/healthz")
def healthz():
    try:
        ch = get_clickhouse_client()
        ch.command("SELECT 1")
        return {
            "status": "ok",
            "clickhouse": "ok",
            "version": INDEXER_VERSION,
            "collectorLag": _collect_collector_lag(ch),
            "processingLag": _collect_processing_lag(ch),
            "sourceStatus": _source_status_snapshot(ch),
            "morphoCoverage": _morpho_coverage_snapshot(ch),
            "fluidCoverage": _fluid_coverage_snapshot(ch),
        }
    except Exception as exc:
        close_clickhouse_client()
        logger.warning("ClickHouse health check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "clickhouse": "down", "reason": "clickhouse_unavailable"},
        )


@app.get("/livez")
def livez():
    # Lightweight liveness check used by Docker healthcheck.
    return {"status": "alive", "version": INDEXER_VERSION}


@app.get("/status")
def status():
    try:
        ch = get_clickhouse_client()
        ch.command("SELECT 1")
        return {
            "status": "ok",
            "version": INDEXER_VERSION,
            "readyProtocols": list(INDEXER_READY_PROTOCOLS),
            "publicReadiness": _analytics_status_response(ch),
            "collectorLag": _collect_collector_lag(ch),
            "processingLag": _collect_processing_lag(ch),
            "sourceStatus": _source_status_snapshot(ch),
            "morphoCoverage": _morpho_coverage_snapshot(ch),
            "fluidCoverage": _fluid_coverage_snapshot(ch),
        }
    except Exception as exc:
        close_clickhouse_client()
        logger.warning("ClickHouse status check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "version": INDEXER_VERSION, "reason": "clickhouse_unavailable"},
        )


@app.get("/metrics")
def metrics():
    try:
        ch = get_clickhouse_client()
        ch.command("SELECT 1")
        return Response(_prometheus_metrics(ch), media_type="text/plain; version=0.0.4")
    except Exception as exc:
        close_clickhouse_client()
        logger.warning("ClickHouse metrics check failed: %s", exc)
        return Response("# metrics unavailable\n", status_code=503, media_type="text/plain")


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
            if protocol != SOFR_RATES and lag >= 0 and lag > MAX_READY_LAG_BLOCKS
        ]
        failing_collector = [
            protocol
            for protocol, lag in collector_lag_by_protocol.items()
            if (
                lag >= 0
                and (
                    (protocol == SOFR_RATES and lag > MAX_READY_SOFR_BUSINESS_DAYS)
                    or (protocol != SOFR_RATES and lag > MAX_READY_LAG_BLOCKS)
                )
        )
    ]
        failing = sorted(set(failing_processing + failing_collector))
        if failing:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": "lag_exceeded",
                    "version": INDEXER_VERSION,
                    "maxLagBlocks": MAX_READY_LAG_BLOCKS,
                    "maxSofrBusinessDays": MAX_READY_SOFR_BUSINESS_DAYS,
                    "collectorLag": collector_lag_by_protocol,
                    "processingLag": lag_by_protocol,
                    "failingProtocols": failing,
                },
            )
        return {
            "status": "ready",
            "version": INDEXER_VERSION,
            "maxLagBlocks": MAX_READY_LAG_BLOCKS,
            "maxSofrBusinessDays": MAX_READY_SOFR_BUSINESS_DAYS,
            "collectorLag": collector_lag_by_protocol,
            "processingLag": lag_by_protocol,
        }
    except Exception as exc:
        close_clickhouse_client()
        logger.warning("ClickHouse readiness check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "clickhouse_unavailable"},
        )


@app.get("/public-readyz")
def public_readyz():
    try:
        ch = get_clickhouse_client()
        ch.command("SELECT 1")
        payload = _analytics_status_response(ch)
        status_code = 200 if payload["ready"] else 503
        return JSONResponse(status_code=status_code, content=payload)
    except Exception as exc:
        close_clickhouse_client()
        logger.warning("Public readiness check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "ready": False,
                "version": INDEXER_VERSION,
                "reason": "clickhouse_unavailable",
            },
        )


@app.get("/api/v1/oracle/usdc-borrow-apy")
def get_usdc_borrow_apy():
    try:
        ch = get_clickhouse_client()
        sql = f"""
        SELECT
            argMax(borrow_apy, timestamp) AS apy,
            max(timestamp) AS updated_at
        FROM {AAVE_SERIES_TABLE}
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
        logger.warning("USDC borrow APY lookup failed: %s", exc)
        return JSONResponse(status_code=500, content={"error": "rate_unavailable"})


def create_app():
    return app
