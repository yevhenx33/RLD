"""
GraphQL schema for the Rates API.

Provides a single-query interface to fetch all rate data at once,
eliminating sequential REST calls from the frontend.

Usage:
    POST /graphql
    {
      rates(symbol: "USDC", limit: 100) { timestamp apy ethPrice }
      ethPrices(limit: 24) { timestamp price }
      latestRates { timestamp usdc dai usdt sofr susde ethPrice }
    }
"""

import logging
from typing import Optional

import clickhouse_connect
import strawberry
from strawberry.fastapi import GraphQLRouter

from api.deps import get_db_connection, get_from_cache, set_cache

logger = logging.getLogger(__name__)

MAX_LIMIT = 10000


# ─── Types ──────────────────────────────────────────────────

@strawberry.type
class RatePoint:
    timestamp: int
    apy: Optional[float] = None
    eth_price: Optional[float] = strawberry.field(name="ethPrice", default=None)


@strawberry.type
class EthPricePoint:
    timestamp: int
    price: Optional[float] = None


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
class MarketSnapshot:
    symbol: str
    protocol: str
    supply_usd: Optional[float] = None
    borrow_usd: Optional[float] = None
    supply_apy: Optional[float] = None
    borrow_apy: Optional[float] = None
    utilization: Optional[float] = None


@strawberry.type
class MarketDetail:
    """Individual market (per entity_id) with latest data."""
    entity_id: str
    symbol: str
    protocol: str
    supply_usd: float = 0.0
    borrow_usd: float = 0.0
    supply_apy: float = 0.0
    borrow_apy: float = 0.0
    utilization: float = 0.0
    collateral_symbol: Optional[str] = None
    lltv: Optional[float] = None


@strawberry.type
class ProtocolTvlPoint:
    """Weekly TVL data point per protocol."""
    date: str
    aave: float = 0.0
    morpho: float = 0.0
    euler: float = 0.0
    fluid: float = 0.0


@strawberry.type
class MarketTimeseriesPoint:
    """Hourly data point for an individual market."""
    timestamp: int
    supply_apy: Optional[float] = None
    borrow_apy: Optional[float] = None
    utilization: Optional[float] = None
    supply_usd: Optional[float] = None
    borrow_usd: Optional[float] = None


@strawberry.type
class VaultAllocationDetail:
    vault_address: str
    name: Optional[str]
    symbol: Optional[str]
    shares: str

@strawberry.type
class VaultAllocationPoint:
    timestamp: int
    allocations: list[VaultAllocationDetail]


# ─── Resolvers ──────────────────────────────────────────────

SYMBOL_MAP = {
    "USDC": "usdc_rate",
    "DAI": "dai_rate",
    "USDT": "usdt_rate",
    "SOFR": "sofr_rate",
    "sUSDe": "susde_yield",
    "SUSDE": "susde_yield",
}


def _query_rates(
    symbol: str = "USDC",
    limit: int = 500,
    resolution: str = "1H",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[RatePoint]:
    """Resolve rate data from clean_rates.db."""
    limit = min(limit, MAX_LIMIT)
    col = SYMBOL_MAP.get(symbol.upper()) or SYMBOL_MAP.get(symbol)
    if not col:
        return []

    cache_key = f"gql:rates:{symbol}:{resolution}:{limit}:{start_date}:{end_date}"
    cached = get_from_cache(cache_key)
    if cached is not None:
        return cached

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        buckets = {"5M": 300, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        seconds = buckets.get(resolution, 3600)

        if resolution in ("1H", "5M"):
            select = f"timestamp, {col} as apy, eth_price"
            group = ""
        else:
            select = f"MAX(timestamp) as timestamp, AVG({col}) as apy, AVG(eth_price) as eth_price"
            group = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"

        query = f"SELECT {select} FROM hourly_stats WHERE timestamp >= 1677801600"
        params = []

        if start_date:
            from datetime import datetime
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(int(dt.timestamp()))
        if end_date:
            from datetime import datetime
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            query += " AND timestamp <= ?"
            params.append(int(dt.timestamp()) + 86399)

        query += f" {group} ORDER BY timestamp DESC LIMIT {limit}"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        results = [
            RatePoint(
                timestamp=r["timestamp"],
                apy=r["apy"] if r["apy"] == r["apy"] else None,  # NaN check
                eth_price=r["eth_price"] if r["eth_price"] and r["eth_price"] == r["eth_price"] else None,
            )
            for r in rows
        ]
        results.sort(key=lambda r: r.timestamp)

        set_cache(cache_key, results)
        return results

    except Exception as e:
        logger.error(f"GraphQL rates error: {e}")
        return []


def _query_eth_prices(
    limit: int = 500,
    resolution: str = "1H",
) -> list[EthPricePoint]:
    """Resolve ETH price data."""
    limit = min(limit, MAX_LIMIT)

    cache_key = f"gql:eth:{resolution}:{limit}"
    cached = get_from_cache(cache_key)
    if cached is not None:
        return cached

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        buckets = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        seconds = buckets.get(resolution, 3600)

        if resolution == "1H":
            select = "timestamp, eth_price as price"
            group = ""
        else:
            select = f"MAX(timestamp) as timestamp, AVG(eth_price) as price"
            group = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"

        query = f"SELECT {select} FROM hourly_stats WHERE timestamp >= 1677801600 {group} ORDER BY timestamp DESC LIMIT {limit}"
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        results = [
            EthPricePoint(
                timestamp=r["timestamp"],
                price=r["price"] if r["price"] and r["price"] == r["price"] else None,
            )
            for r in rows
        ]
        results.sort(key=lambda r: r.timestamp)

        set_cache(cache_key, results)
        return results

    except Exception as e:
        logger.error(f"GraphQL eth_prices error: {e}")
        return []


def _query_market_snapshots() -> list[MarketSnapshot]:
    cache_key = "gql:market:snapshots"
    cached = get_from_cache(cache_key)
    if cached is not None:
        return cached

    try:
        client = clickhouse_connect.get_client(host='rld_clickhouse', port=8123)
        query = """
        WITH latest AS (
            SELECT entity_id, protocol,
                   argMax(symbol, timestamp) AS symbol,
                   argMax(supply_usd, timestamp) AS supply_usd,
                   argMax(borrow_usd, timestamp) AS borrow_usd,
                   argMax(supply_apy, timestamp) AS supply_apy,
                   argMax(borrow_apy, timestamp) AS borrow_apy,
                   argMax(utilization, timestamp) AS utilization
            FROM unified_timeseries
            GROUP BY entity_id, protocol
            HAVING supply_usd >= 1000 OR protocol LIKE 'AAVE%'
        ),
        healthy AS (
            SELECT *,
                   IF(utilization < 0.995 AND supply_apy < 1.0, 1, 0) AS rate_valid
            FROM latest
        )
        SELECT symbol, protocol,
               SUM(supply_usd) AS total_supply,
               SUM(borrow_usd) AS total_borrow,
               IF(SUM(supply_usd * rate_valid) > 0,
                  SUM(supply_apy * supply_usd * rate_valid) / SUM(supply_usd * rate_valid),
                  AVG(IF(rate_valid, supply_apy, 0))) AS avg_supply_apy,
               IF(SUM(borrow_usd * rate_valid) > 0,
                  SUM(borrow_apy * borrow_usd * rate_valid) / SUM(borrow_usd * rate_valid),
                  AVG(IF(rate_valid, borrow_apy, 0))) AS avg_borrow_apy,
               IF(SUM(supply_usd) > 0,
                  SUM(borrow_usd) / SUM(supply_usd),
                  AVG(utilization)) AS avg_utilization
        FROM healthy
        GROUP BY symbol, protocol
        ORDER BY total_supply DESC
        """
        res = client.query(query)
        snapshots = []
        for r in res.result_rows:
            snapshots.append(MarketSnapshot(
                symbol=r[0],
                protocol=r[1],
                supply_usd=r[2],
                borrow_usd=r[3],
                supply_apy=r[4],
                borrow_apy=r[5],
                utilization=r[6],
            ))
        set_cache(cache_key, snapshots)
        return snapshots
    except Exception as e:
        logger.error(f"GraphQL market_snapshots error: {e}")
        return []


def _query_latest() -> Optional[LatestRates]:
    """Resolve the most recent hourly snapshot."""
    cache_key = "gql:latest"
    cached = get_from_cache(cache_key)
    if cached is not None:
        return cached

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM hourly_stats ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        def safe(v):
            if v is None:
                return None
            if isinstance(v, float) and v != v:  # NaN
                return None
            return v

        result = LatestRates(
            timestamp=row["timestamp"],
            usdc=safe(row["usdc_rate"]),
            dai=safe(row["dai_rate"]),
            usdt=safe(row["usdt_rate"]),
            sofr=safe(row["sofr_rate"]),
            susde=safe(row["susde_yield"]),
            eth_price=safe(row["eth_price"]),
        )
        set_cache(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"GraphQL latest error: {e}")
        return None


def _query_protocol_markets(protocol: str) -> list[MarketDetail]:
    """Return per-entity latest data for a given protocol prefix."""
    cache_key = f"gql:protocol:{protocol}"
    cached = get_from_cache(cache_key)
    if cached is not None:
        return cached

    try:
        # Whitelist protocol values to prevent injection
        allowed = {"AAVE_MARKET", "MORPHO_MARKET", "MORPHO_VAULT", "MORPHO_ALLOCATION", "EULER_MARKET", "FLUID_MARKET"}
        if protocol not in allowed:
            return []
        client = clickhouse_connect.get_client(host='rld_clickhouse', port=8123)

        is_morpho = protocol.startswith('MORPHO')
        if is_morpho:
            query = f"""
            SELECT t.entity_id, t.symbol, t.proto, t.supply_usd, t.borrow_usd,
                   t.supply_apy, t.borrow_apy, t.utilization,
                   COALESCE(p.collateral_symbol, '') AS collateral_symbol,
                   COALESCE(p.lltv, 0) AS lltv
            FROM (
                SELECT entity_id,
                       argMax(symbol, timestamp) AS symbol,
                       '{protocol}' AS proto,
                       argMax(supply_usd, timestamp) AS supply_usd,
                       argMax(borrow_usd, timestamp) AS borrow_usd,
                       argMax(supply_apy, timestamp) AS supply_apy,
                       argMax(borrow_apy, timestamp) AS borrow_apy,
                       argMax(utilization, timestamp) AS utilization
                FROM unified_timeseries
                WHERE protocol = '{protocol}'
                GROUP BY entity_id
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
                       argMax(symbol, timestamp) AS symbol,
                       '{protocol}' AS proto,
                       argMax(supply_usd, timestamp) AS supply_usd,
                       argMax(borrow_usd, timestamp) AS borrow_usd,
                       argMax(supply_apy, timestamp) AS supply_apy,
                       argMax(borrow_apy, timestamp) AS borrow_apy,
                       argMax(utilization, timestamp) AS utilization
                FROM unified_timeseries
                WHERE protocol = '{protocol}'
                GROUP BY entity_id
            )
            WHERE supply_usd >= 1000 OR borrow_usd >= 1000
            ORDER BY supply_usd DESC
            """

        res = client.query(query)
        results = []
        for r in res.result_rows:
            results.append(MarketDetail(
                entity_id=r[0],
                symbol=r[1],
                protocol=r[2],
                supply_usd=r[3],
                borrow_usd=r[4],
                supply_apy=r[5],
                borrow_apy=r[6],
                utilization=r[7],
                collateral_symbol=r[8] if r[8] else None,
                lltv=r[9] if r[9] else None,
            ))
        set_cache(cache_key, results)
        return results
    except Exception as e:
        logger.error(f"GraphQL protocol_markets error: {e}")
        return []


def _query_protocol_tvl_history() -> list[ProtocolTvlPoint]:
    """Return weekly protocol TVL for the stacked bar chart."""
    cache_key = "gql:protocol_tvl_history"
    cached = get_from_cache(cache_key)
    if cached is not None:
        return cached

    try:
        client = clickhouse_connect.get_client(host='rld_clickhouse', port=8123)
        query = """
        SELECT day, protocol, sum(supply_usd) AS total_supply
        FROM (
            SELECT entity_id,
                   splitByChar('_', protocol)[1] AS protocol,
                   toStartOfWeek(timestamp) AS day,
                   argMax(supply_usd, timestamp) AS supply_usd
            FROM unified_timeseries
            GROUP BY entity_id, protocol, day
        )
        GROUP BY day, protocol
        ORDER BY day ASC
        """
        res = client.query(query)
        # Pivot: {date -> {protocol -> tvl}}
        pivot = {}
        for r in res.result_rows:
            d = str(r[0])
            proto = r[1].upper()
            if d not in pivot:
                pivot[d] = {}
            pivot[d][proto] = r[2]

        results = []
        for d in sorted(pivot.keys()):
            vals = pivot[d]
            results.append(ProtocolTvlPoint(
                date=d,
                aave=vals.get('AAVE', 0.0),
                morpho=vals.get('MORPHO', 0.0),
                euler=vals.get('EULER', 0.0),
                fluid=vals.get('FLUID', 0.0),
            ))
        set_cache(cache_key, results)
        return results
    except Exception as e:
        logger.error(f"GraphQL protocol_tvl_history error: {e}")
        return []


def _query_market_timeseries(
    entity_id: str, resolution: str = "1H", limit: int = 2000
) -> list[MarketTimeseriesPoint]:
    """Return historical timeseries for a single market."""
    cache_key = f"gql:market_ts:{entity_id}:{resolution}:{limit}"
    cached = get_from_cache(cache_key)
    if cached is not None:
        return cached

    table_map = {"1H": "1H", "4H": "4H", "1D": "1D", "1W": "1W"}
    suffix = table_map.get(resolution, "1H")

    try:
        client = clickhouse_connect.get_client(host='rld_clickhouse', port=8123)
        query = f"""
        SELECT
            toUnixTimestamp(ts) AS ts,
            avgMerge(supply_apy) AS supply_apy,
            avgMerge(borrow_apy) AS borrow_apy,
            avgMerge(utilization) AS utilization,
            avgMerge(supply_usd) AS supply_usd,
            avgMerge(borrow_usd) AS borrow_usd
        FROM unified_timeseries_{suffix}
        WHERE entity_id LIKE %(eid_prefix)s
        GROUP BY ts
        ORDER BY ts DESC
        LIMIT %(lim)s
        """
        res = client.query(query, parameters={"eid_prefix": f"{entity_id}%", "lim": min(limit, MAX_LIMIT)})
        results = [
            MarketTimeseriesPoint(
                timestamp=int(r[0]),
                supply_apy=r[1],
                borrow_apy=r[2],
                utilization=r[3],
                supply_usd=r[4],
                borrow_usd=r[5],
            )
            for r in res.result_rows
        ]
        results.reverse()  # chronological order
        set_cache(cache_key, results)
        return results
    except Exception as e:
        logger.error(f"GraphQL market_timeseries error: {e}")
        return []

def _query_market_vault_allocations(entity_id: str, limit: int = 365) -> list[VaultAllocationPoint]:
    cache_key = f"gql:market_vault_allocations:{entity_id}:{limit}"
    cached = get_from_cache(cache_key)
    if cached is not None:
        return cached

    try:
        import sqlite3
        conn = sqlite3.connect('/app/morpho_data/morpho_enriched_final.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        db_eid = entity_id
        where_clause = "market_id LIKE ?" if len(entity_id) < 60 else "market_id = ?"
        if len(entity_id) < 60:
            db_eid += "%"
            
        # 1. Fetch Meta (Tiny table, < 5ms)
        cur.execute('SELECT lower(vault_address), name, symbol FROM vault_meta')
        meta = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        # 2. Fetch raw vault allocations dynamically using strict index
        cur.execute(f'''
            SELECT 
                timestamp,
                vault_address,
                supply_shares
            FROM vault_allocations
            WHERE {where_clause}
        ''', (db_eid,))
        
        rows = cur.fetchall()
        
        # 3. Pure Python memory aggregation (O(n) time, <100ms)
        daily_max = {}
        for r in rows:
            ts = r[0]
            val_addr = r[1]
            shares = r[2]
            day_ts = (ts // 86400) * 86400
            
            key = (day_ts, val_addr)
            # Find the max timestamp per day per vault
            if key not in daily_max or daily_max[key]["ts"] < ts:
                daily_max[key] = {
                    "ts": ts,
                    "shares": shares
                }

        grouped = {}
        for (day_ts, val_addr), data in daily_max.items():
            if day_ts not in grouped:
                grouped[day_ts] = []
                
            v_meta = meta.get(val_addr.lower(), (None, None))
            grouped[day_ts].append(VaultAllocationDetail(
                vault_address=val_addr,
                name=v_meta[0] or 'Unknown Vault',
                symbol=v_meta[1] or 'UNKNOWN',
                shares=str(data["shares"])
            ))
            
        # Sort grouped allocations by share size
        for day_ts in grouped:
            grouped[day_ts].sort(key=lambda x: float(x.shares), reverse=True)
        
        # Sort and limit
        sorted_ts = sorted(list(grouped.keys()), reverse=True)[:limit]
        results = [
            VaultAllocationPoint(
                timestamp=ts,
                allocations=grouped[ts]
            ) for ts in sorted_ts
        ]
        results.reverse() # Return chronological order
        
        conn.close()
        set_cache(cache_key, results)
        return results
    except Exception as e:
        logger.error(f"GraphQL vault_allocations error: {e}")
        return []



# ─── Schema ─────────────────────────────────────────────────

@strawberry.type
class Query:
    @strawberry.field(description="Historical lending rates for a given symbol and resolution")
    def rates(
        self,
        symbol: str = "USDC",
        limit: int = 500,
        resolution: str = "1H",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[RatePoint]:
        return _query_rates(symbol, limit, resolution, start_date, end_date)

    @strawberry.field(description="Historical ETH/USD prices")
    def eth_prices(
        self,
        limit: int = 500,
        resolution: str = "1H",
    ) -> list[EthPricePoint]:
        return _query_eth_prices(limit, resolution)

    @strawberry.field(description="Latest snapshot of all rates and ETH price")
    def latest_rates(self) -> Optional[LatestRates]:
        return _query_latest()

    @strawberry.field(description="Latest global snapshot of all lending markets")
    def market_snapshots(self) -> list[MarketSnapshot]:
        return _query_market_snapshots()

    @strawberry.field(description="Individual markets for a specific protocol")
    def protocol_markets(
        self,
        protocol: str = "AAVE_MARKET",
    ) -> list[MarketDetail]:
        return _query_protocol_markets(protocol)

    @strawberry.field(description="Historical weekly protocol TVL for stacked bar chart")
    def protocol_tvl_history(self) -> list[ProtocolTvlPoint]:
        return _query_protocol_tvl_history()

    @strawberry.field(description="Historical timeseries for an individual market")
    def market_timeseries(
        self,
        entity_id: str,
        resolution: str = "1H",
        limit: int = 2000,
    ) -> list[MarketTimeseriesPoint]:
        return _query_market_timeseries(entity_id, resolution, limit)

    @strawberry.field(description="Historical vault allocations for a morpho market")
    def market_vault_allocations(
        self,
        entity_id: str,
        limit: int = 365,
    ) -> list[VaultAllocationPoint]:
        return _query_market_vault_allocations(entity_id, limit)



schema = strawberry.Schema(query=Query)
graphql_router = GraphQLRouter(schema, path="/graphql")
