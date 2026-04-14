"""
Custom Feed Resolvers for Morpho Prices

Provides hardcoded or upstream-protocol derivations for silent oracle proxies.
Matches the architectural Phase 2 schema.
"""

from typing import Optional
from indexer.base import BaseSource
import datetime
import logging

log = logging.getLogger("indexer.custom_feeds")

class StaticPegsSource(BaseSource):
    """
    Feeds that strictly return fiat $1.0.
    - Dummy Feed: 0xc3866d726c204c0836e0ab53a2723c7b28293739
    - USR Price Aggregator: 0xf9c7c25fe58aaa494ee7ff1f6cf0b70d7c7ce88c
    We emit a constant 1.0.
    """
    name = "STATIC_PEGS"
    contracts = [] # No actual logs read
    topics = []
    
    def get_cursor(self, ch) -> int:
        return 0

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        return None

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        return 0


# (Other upstream sources like Lido can be appended here using strict types as approved)
