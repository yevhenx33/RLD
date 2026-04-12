"""
ChainlinkSource — Chainlink AnswerUpdated price feed decoder.

Indexes ETH/USD and BTC/USD price updates from Chainlink aggregator
contracts into the chainlink_prices table.
"""

import datetime
import logging
from typing import Optional

from ..base import BaseSource

log = logging.getLogger("indexer.chainlink")

# Current Chainlink aggregator addresses (rotate periodically)
AGGREGATORS = {
    "0x7d4e742018fb52e48b08be73d041c18b21de6fb5": "ETH/USD",
    "0x4a3411ac2948b33c69666b35cc6d055b27ea84f1": "BTC/USD",
}


class ChainlinkSource(BaseSource):
    name = "CHAINLINK"
    contracts = list(AGGREGATORS.keys())
    topics = [
        "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f",  # AnswerUpdated
    ]
    raw_table = None  # Prices go directly to chainlink_prices

    def get_cursor(self, ch) -> int:
        """Track cursor via chainlink_prices table."""
        result = ch.command("SELECT max(block_number) FROM chainlink_prices")
        return int(result) if result else 0

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        """Decode AnswerUpdated: price from topic1, timestamp from data."""
        topics = log_entry.topics or []
        if len(topics) < 2:
            return None

        addr = (log_entry.address or "").lower()
        feed_name = AGGREGATORS.get(addr)
        if not feed_name:
            return None

        # topic1 = indexed int256 price (8 decimal precision)
        price_raw = int(topics[1], 16)
        if price_raw > (1 << 255):
            price_raw -= (1 << 256)
        price = price_raw / 1e8

        if price <= 0:
            return None

        # data = updatedAt (uint256 unix timestamp)
        data = log_entry.data or "0x"
        updated_at = int(data, 16) if data != "0x" and len(data) > 2 else 0
        ts = (datetime.datetime.fromtimestamp(updated_at, tz=datetime.UTC)
              if updated_at > 0
              else datetime.datetime.now(datetime.UTC))

        return {
            "block_number": log_entry.block_number,
            "timestamp": ts.replace(tzinfo=None),
            "feed": feed_name,
            "price": price,
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        """Insert decoded prices into chainlink_prices table."""
        if not decoded_rows:
            return 0

        rows = [
            [d["block_number"], d["timestamp"], d["feed"], d["price"]]
            for d in decoded_rows
        ]
        ch.insert("chainlink_prices", rows,
                   column_names=["block_number", "timestamp", "feed", "price"])
        return len(rows)
