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

import hypersync
import pandas as pd

log = logging.getLogger("indexer")


def forward_fill_hourly(df: pd.DataFrame, ch, protocol: str) -> pd.DataFrame:
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
        last_known = ch.query_df(f"""
            SELECT entity_id, symbol,
                   argMax(timestamp, timestamp) AS last_ts,
                   argMax(supply_usd, timestamp) AS supply_usd,
                   argMax(borrow_usd, timestamp) AS borrow_usd,
                   argMax(supply_apy, timestamp) AS supply_apy,
                   argMax(borrow_apy, timestamp) AS borrow_apy,
                   argMax(utilization, timestamp) AS utilization,
                   argMax(price_usd, timestamp) AS price_usd
            FROM unified_timeseries
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
        # Multiplying it by the cumulative factor perfectly synthesizes mechanical compounding.
        merged['supply_usd'] = merged['supply_usd'] * merged['sup_multiplier']
        merged['borrow_usd'] = merged['borrow_usd'] * merged['bor_multiplier']

        # Strip computational degrees of freedom
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

        if rows:
            ch.insert(self.raw_table, rows, column_names=[
                "block_number", "block_timestamp", "tx_hash", "log_index",
                "contract", "event_name", "topic0", "topic1", "topic2",
                "topic3", "data",
            ])
        return len(rows)

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
