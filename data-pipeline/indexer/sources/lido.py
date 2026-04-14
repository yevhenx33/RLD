import datetime
import logging
from typing import Optional

from indexer.base import BaseSource

log = logging.getLogger("indexer.lido")

class LidoRebaseSource(BaseSource):
    """
    Decodes Lido TokenRebased events to compute wstETH/stETH exchange rate historically.
    stEthPerToken = postTotalEther / postTotalShares
    """
    name = "LIDO_REBASE"
    contracts = ["0xae7ab96520de3a18e5e111b5eaab095312d7fe84"]  # stETH
    topics = [
        "0xff08c3ef606d198e316ef5b822193c489965899eb4e3c248cea1a4626c3eda50" # TokenRebased
    ]
    raw_table = "lido_events"

    def get_cursor(self, ch) -> int:
        result = ch.command(
            "SELECT max(block_number) FROM chainlink_prices WHERE feed = 'wstETH/stETH exchange rate'"
        )
        return int(result) if result else 0

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        topic0 = log_entry.topics[0] if log_entry.topics else ""
        if topic0.lower() != self.topics[0]:
            return None

        # Payload is simply a sequence of uint256s in data
        # TokenRebased(uint256 reportTimestamp, uint256 timeElapsed, uint256 preTotalShares, uint256 preTotalEther, uint256 postTotalShares, uint256 postTotalEther, uint256 sharesMintedAsFees)
        data = log_entry.data.removeprefix("0x")
        if len(data) < 384: # Need at least 6 uint256s
            return None

        post_shares_hex = data[192:256] # 4th data word (index 3)
        post_ether_hex = data[256:320]  # 5th data word (index 4)
        
        if not post_shares_hex or not post_ether_hex:
            return None

        post_shares = int(post_shares_hex, 16)
        post_ether = int(post_ether_hex, 16)

        if post_shares == 0:
            return None

        # wstETH value = post_ether / post_shares
        price_wad = (post_ether * 10**18) // post_shares
        
        # We need a timestamp: use block timestamp
        ts = block_ts_map.get(str(log_entry.block_number))
        if not ts:
            return None

        return {
            "block_number": log_entry.block_number,
            "timestamp": ts,
            "feed": "wstETH/stETH exchange rate",
            "price": price_wad,
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        """Insert decoded prices into chainlink_prices table."""
        if not decoded_rows:
            return 0
            
        rows_to_insert = []
        # Multi-cast to both feed aliases mapped by our system
        for d in decoded_rows:
            rows_to_insert.append([d["block_number"], d["timestamp"], d["feed"], d["price"]])
            rows_to_insert.append([d["block_number"], d["timestamp"], "Custom price feed for wstETH / ETH", d["price"]])

        ch.insert(
            "chainlink_prices", 
            rows_to_insert,
            column_names=["block_number", "timestamp", "feed", "price"]
        )
        return len(rows_to_insert)
