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

log = logging.getLogger("indexer")


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
