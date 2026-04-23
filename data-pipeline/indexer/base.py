"""
BaseSource — Abstract base class for all protocol indexer sources.

Each source defines:
  - Which contracts and events to monitor
  - How to decode raw log entries into structured data
  - How to merge decoded data into unified_timeseries
"""

from abc import ABC, abstractmethod
from typing import Optional
import datetime
import logging
import os

import hypersync
import pandas as pd

log = logging.getLogger("indexer")

# POKA-YOKE: Central routing table for protocol-specific output tables.
# Each protocol writes to its own physically isolated ClickHouse table.
# The Merge-engine 'unified_timeseries' view combines them for reads.
PROTOCOL_TABLES = {
    "AAVE_MARKET": "aave_timeseries",
    "MORPHO_MARKET": "morpho_timeseries",
    "MORPHO_ALLOCATION": "morpho_timeseries",
    "MORPHO_VAULT": "morpho_timeseries",
}

DEFAULT_INSERT_BATCH_SIZE = int(os.getenv("CLICKHOUSE_INSERT_BATCH_SIZE", "20000"))
CLICKHOUSE_MUTATION_REWRITE_ENABLED = (
    os.getenv("CLICKHOUSE_MUTATION_REWRITE_ENABLED", "false").strip().lower()
    in {"1", "true", "yes"}
)

_API_TABLES_READY = False
API_MARKET_TIMESERIES_AGG_TABLE = "api_market_timeseries_hourly_agg"
API_PROTOCOL_TVL_AGG_TABLE = "api_protocol_tvl_entity_weekly_agg"


def insert_rows_batched(ch, table: str, rows: list[list], column_names: list[str], batch_size: int = DEFAULT_INSERT_BATCH_SIZE) -> int:
    """Insert rows in bounded batches to avoid tiny-part explosion."""
    if not rows:
        return 0
    written = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        ch.insert(table, chunk, column_names=column_names)
        written += len(chunk)
    return written


def insert_df_batched(ch, table: str, df, batch_size: int = DEFAULT_INSERT_BATCH_SIZE) -> int:
    """Insert DataFrame in bounded batches to control part count."""
    if df is None or len(df) == 0:
        return 0
    written = 0
    for start in range(0, len(df), batch_size):
        chunk = df.iloc[start:start + batch_size]
        ch.insert_df(table, chunk)
        written += len(chunk)
    return written


def ensure_api_preagg_tables(ch) -> None:
    global _API_TABLES_READY
    if _API_TABLES_READY:
        return
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
    try:
        hourly_rows = int(ch.command("SELECT count() FROM api_market_timeseries_hourly_agg") or 0)
    except Exception:
        hourly_rows = 0
    if hourly_rows == 0:
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
    try:
        weekly_rows = int(ch.command("SELECT count() FROM api_protocol_tvl_entity_weekly_agg") or 0)
    except Exception:
        weekly_rows = 0
    if weekly_rows == 0:
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
                  AND entity_id NOT IN ('AAVE_MARKET_SYNTHETIC')
                GROUP BY day, clean_protocol, entity_id
            )
            """
        )
    _API_TABLES_READY = True


def rewrite_protocol_window_if_enabled(ch, table: str, protocol: str, min_ts: str, max_ts: str) -> None:
    if not CLICKHOUSE_MUTATION_REWRITE_ENABLED:
        return
    ch.command(
        f"DELETE FROM {table} "
        f"WHERE protocol='{protocol}' "
        f"AND timestamp >= '{min_ts}' AND timestamp <= '{max_ts}'"
    )


def rewrite_protocol_timestamp_if_enabled(ch, table: str, protocol: str, ts_str: str) -> None:
    if not CLICKHOUSE_MUTATION_REWRITE_ENABLED:
        return
    ch.command(
        f"DELETE FROM {table} "
        f"WHERE protocol='{protocol}' "
        f"AND timestamp = '{ts_str}'"
    )


def upsert_api_market_latest(ch, df) -> int:
    """
    Maintain a fast API-facing latest snapshot table.
    Keeps one logical row per (protocol, entity_id) via ReplacingMergeTree.
    """
    if df is None or len(df) == 0:
        return 0
    required = {
        "protocol",
        "entity_id",
        "symbol",
        "target_id",
        "timestamp",
        "supply_usd",
        "borrow_usd",
        "supply_apy",
        "borrow_apy",
        "utilization",
        "price_usd",
    }
    if not required.issubset(set(df.columns)):
        return 0
    ensure_api_preagg_tables(ch)
    latest = (
        df.sort_values("timestamp")
        .groupby(["protocol", "entity_id"], as_index=False)
        .tail(1)
        .copy()
    )
    if latest.empty:
        return 0
    latest["target_id"] = latest["target_id"].fillna("").astype(str)
    latest["symbol"] = latest["symbol"].fillna("").astype(str)
    latest["protocol"] = latest["protocol"].fillna("").astype(str)
    latest["entity_id"] = latest["entity_id"].fillna("").astype(str)
    return insert_df_batched(
        ch,
        "api_market_latest",
        latest[
            [
                "protocol",
                "entity_id",
                "symbol",
                "target_id",
                "timestamp",
                "supply_usd",
                "borrow_usd",
                "supply_apy",
                "borrow_apy",
                "utilization",
                "price_usd",
            ]
        ],
    )


def upsert_api_market_timeseries_hourly(ch, df) -> int:
    ensure_api_preagg_tables(ch)
    return 0


def refresh_api_protocol_tvl_weekly(ch, min_ts, max_ts) -> int:
    ensure_api_preagg_tables(ch)
    if min_ts is None or max_ts is None:
        return 0
    try:
        min_dt = pd.to_datetime(min_ts)
        max_dt = pd.to_datetime(max_ts)
        if pd.isna(min_dt) or pd.isna(max_dt):
            return 0
        if max_dt < min_dt:
            min_dt, max_dt = max_dt, min_dt
    except Exception:
        return 0

    # Expand by one week to cover boundary updates cleanly.
    window_start = (min_dt - pd.Timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    window_end = (max_dt + pd.Timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    ch.command(
        f"""
        INSERT INTO {API_PROTOCOL_TVL_AGG_TABLE}
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
              AND timestamp >= '{window_start}'
              AND timestamp <= '{window_end}'
            GROUP BY day, clean_protocol, entity_id
        )
        """
    )
    return 1


def forward_fill_hourly(df: pd.DataFrame, ch, protocol: str, compound: bool = True) -> pd.DataFrame:
    """
    Ensure contiguous hourly data for every entity_id by forward-filling gaps.

    For each entity_id present in the incoming dataframe, queries the last known
    state from ClickHouse, then generates a row for every hour from the last
    known timestamp to the current batch's max timestamp, carrying forward the
    most recent values when no event occurred.

    Args:
        df: DataFrame with columns matching unified_timeseries schema.
            Must have 'timestamp', 'entity_id', 'symbol', 'protocol', etc.
        ch: ClickHouse client.
        protocol: Protocol name (e.g., 'AAVE_MARKET').

    Returns:
        DataFrame with all hours filled — no gaps > 1H per entity_id.
    """
    if df.empty:
        return df

    entity_ids = df["entity_id"].unique().tolist()
    batch_max_ts = df["timestamp"].max()

    # Get last known state across ALL entities in protocol from ClickHouse
    batch_min_ts = df["timestamp"].min()
    try:
        # POKA-YOKE: Read from protocol-specific table, not shared view
        read_table = PROTOCOL_TABLES.get(protocol, 'unified_timeseries')
        last_known = ch.query_df(f"""
            SELECT entity_id, symbol,
                   argMax(timestamp, timestamp) AS last_ts,
                   argMax(supply_usd, timestamp) AS supply_usd,
                   argMax(borrow_usd, timestamp) AS borrow_usd,
                   argMax(supply_apy, timestamp) AS supply_apy,
                   argMax(borrow_apy, timestamp) AS borrow_apy,
                   argMax(utilization, timestamp) AS utilization,
                   argMax(price_usd, timestamp) AS price_usd
            FROM {read_table}
            WHERE protocol = '{protocol}'
              AND timestamp < '{batch_min_ts.strftime("%Y-%m-%d %H:%M:%S")}'
            GROUP BY entity_id, symbol
        """)
        
        if not last_known.empty:
            for eid in last_known["entity_id"].unique():
                if eid not in entity_ids:
                    entity_ids.append(eid)
                    
    except Exception:
        last_known = pd.DataFrame()

    filled_parts = []

    for eid in entity_ids:
        eid_rows = df[df["entity_id"] == eid].sort_values("timestamp")
        
        fill_start = eid_rows["timestamp"].min() if not eid_rows.empty else batch_max_ts
        symbol = eid_rows["symbol"].iloc[0] if not eid_rows.empty else ""
        
        seed_row = None
        if not last_known.empty:
            lk = last_known[last_known["entity_id"] == eid]
            if not lk.empty:
                last_ts = pd.Timestamp(lk["last_ts"].iloc[0])
                symbol = lk["symbol"].iloc[0]
                if eid_rows.empty or last_ts < fill_start:
                    fill_start = last_ts + pd.Timedelta(hours=1)
                    seed_row = lk.iloc[0]

        if eid_rows.empty and seed_row is None:
            continue

        # Build complete hourly range
        full_range = pd.date_range(
            start=fill_start.floor("h"),
            end=batch_max_ts.floor("h"),
            freq="h",
        )
        if len(full_range) == 0:
            filled_parts.append(eid_rows)
            continue

        # Create template with all hours
        template = pd.DataFrame({"timestamp": full_range})
        template["entity_id"] = eid
        template["symbol"] = symbol
        template["protocol"] = protocol
        template["target_id"] = ""

        # Merge actual data onto template
        merged = template.merge(
            eid_rows[["timestamp", "supply_usd", "borrow_usd", "supply_apy",
                       "borrow_apy", "utilization", "price_usd"]],
            on="timestamp",
            how="left",
        )

        # If we have a seed row from CH, prepend it so ffill has a starting value
        if seed_row is not None and merged.iloc[0].isna().any():
            for col in ["supply_usd", "borrow_usd", "supply_apy", "borrow_apy",
                        "utilization", "price_usd"]:
                if pd.isna(merged[col].iloc[0]):
                    merged.loc[merged.index[0], col] = seed_row.get(col, 0.0)

        # Track exactly which rows were mathematically empty before filling
        is_gap = pd.isna(merged["supply_usd"])

        # Forward-fill all anchor bases
        fill_cols = ["supply_usd", "borrow_usd", "supply_apy", "borrow_apy",
                     "utilization", "price_usd"]
        merged[fill_cols] = merged[fill_cols].ffill()
        merged[fill_cols] = merged[fill_cols].fillna(0.0)

        if compound:
            # Segment the DataFrame by physical anchors. 
            # A new segment starts every time `is_gap` is False.
            merged['segment'] = (~is_gap).cumsum()

            # Calculate hour-over-hour multipliers (1 + APY / 8760)
            merged['sup_factor'] = 1.0
            merged.loc[is_gap, 'sup_factor'] = 1.0 + (merged.loc[is_gap, 'supply_apy'] / 8760)
            
            merged['bor_factor'] = 1.0
            merged.loc[is_gap, 'bor_factor'] = 1.0 + (merged.loc[is_gap, 'borrow_apy'] / 8760)

            # Cumulatively multiply these factors within each isolated segment
            merged['sup_multiplier'] = merged.groupby('segment')['sup_factor'].cumprod()
            merged['bor_multiplier'] = merged.groupby('segment')['bor_factor'].cumprod()

            # Since `supply_usd` is ffilled, it holds the absolute flat anchor.
            # Multiplying it by the cumulative factor synthesizes gap compounding.
            # Morpho tracks physical accrual events natively, so it should not be
            # synthetically compounded.
            if protocol != "MORPHO_MARKET":
                merged['supply_usd'] = merged['supply_usd'] * merged['sup_multiplier']
                merged['borrow_usd'] = merged['borrow_usd'] * merged['bor_multiplier']

        # Strip computational degrees of freedom
        if compound:
            merged = merged.drop(columns=['segment', 'sup_factor', 'bor_factor', 'sup_multiplier', 'bor_multiplier'])

        filled_parts.append(merged)

    if not filled_parts:
        return df

    result = pd.concat(filled_parts, ignore_index=True)
    return result


class BaseSource(ABC):
    """
    Abstract base class for a protocol event source.

    Subclasses must implement:
      - decode(log_entry, block_ts_map) -> dict | None
      - merge(ch, decoded_rows) -> int
    """

    # ── Required class attributes (set by subclasses) ────────
    name: str = ""                     # Protocol name, e.g. "FLUID_MARKET"
    contracts: list[str] = []          # Contract addresses to monitor
    topics: list[str] = []             # Event topic0 hashes to filter
    raw_table: Optional[str] = None    # ClickHouse table for raw events (optional)
    genesis_block: int = 0             # The starting block number for indexing

    @property
    def output_table(self) -> str:
        """Return the protocol-specific output table for writes."""
        return PROTOCOL_TABLES.get(self.name, 'unified_timeseries')

    # ── HyperSync integration ────────────────────────────────
    def log_selection(self) -> hypersync.LogSelection:
        """Build a HyperSync LogSelection for this source's events."""
        return hypersync.LogSelection(
            address=self.contracts,
            topics=[self.topics] if self.topics else [],
        )

    def route(self, log_entry) -> bool:
        """Return True if this log entry belongs to this source."""
        addr = (log_entry.address or "").lower()
        return addr in {c.lower() for c in self.contracts}

    # ── State tracking ───────────────────────────────────────
    def get_cursor(self, ch) -> int:
        """Return the last indexed block number from ClickHouse."""
        if self.raw_table:
            result = ch.command(f"SELECT max(block_number) FROM {self.raw_table}")
            return int(result) if result else 0
        return 0

    # ── Raw event storage (optional) ─────────────────────────
    def insert_raw(self, ch, logs: list, block_ts_map: dict) -> int:
        """
        Insert raw log entries into the source's raw_table.
        Override this if your raw table has a non-standard schema.
        Returns number of rows inserted.
        """
        if not self.raw_table or not logs:
            return 0

        rows = []
        for entry in logs:
            topics = entry.topics or []
            ts = block_ts_map.get(entry.block_number, datetime.datetime.now(datetime.UTC))
            ts_naive = ts.replace(tzinfo=None)

            rows.append([
                entry.block_number,
                ts_naive,
                entry.transaction_hash or "",
                entry.log_index or 0,
                (entry.address or "").lower(),
                self._event_name(entry),
                topics[0] if len(topics) > 0 else "",
                topics[1] if len(topics) > 1 else None,
                topics[2] if len(topics) > 2 else None,
                topics[3] if len(topics) > 3 else None,
                entry.data or "",
            ])

        if not rows:
            return 0
        return insert_rows_batched(
            ch,
            self.raw_table,
            rows,
            [
                "block_number", "block_timestamp", "tx_hash", "log_index",
                "contract", "event_name", "topic0", "topic1", "topic2",
                "topic3", "data",
            ],
        )

    def _event_name(self, log_entry) -> str:
        """Derive event name from topic0. Override for custom naming."""
        return self.name

    # ── Abstract methods (must be implemented) ───────────────
    @abstractmethod
    def decode(self, log_entry, block_ts_map: dict) -> Optional[dict]:
        """
        Decode a raw HyperSync log entry into a structured dict.

        Args:
            log_entry: HyperSync Log object with .block_number, .topics, .data, etc.
            block_ts_map: {block_number: datetime} mapping from the HyperSync response.

        Returns:
            A dict with decoded fields, or None if the entry should be skipped.
        """

    @abstractmethod
    def merge(self, ch, decoded_rows: list[dict]) -> int:
        """
        Merge decoded rows into unified_timeseries (or other target table).

        Args:
            ch: ClickHouse client.
            decoded_rows: List of dicts from decode().

        Returns:
            Number of rows written to the target table.
        """
