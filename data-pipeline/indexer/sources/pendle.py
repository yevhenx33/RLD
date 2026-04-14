from indexer.base import BaseSource
import hypersync
from typing import Optional
import json
import os
import logging
from eth_utils import keccak

log = logging.getLogger("pendle_source")

TOPIC_SWAP = "0x" + keccak(text="Swap(address,address,int256,int256,uint256,uint256)").hex()

class PendleSwapSource(BaseSource):
    name = "PENDLE_MARKET"
    # Write directly to morpho_oracle_historical
    raw_table = "morpho_oracle_historical"

    def __init__(self):
        map_path = os.path.join(os.path.dirname(__file__), "pendle_market_mapping.json")
        with open(map_path, "r") as f:
            self.morpho_to_pendle = json.load(f)
        
        # We need the inverse mapping to know which Morpho Market ID a swapped Pendle market belongs to
        self.pendle_to_morpho = {v.lower(): k for k, v in self.morpho_to_pendle.items()}
        
        self.contracts = list(self.pendle_to_morpho.keys())
        self.topics = [TOPIC_SWAP]
        log.info(f"PendleSwapSource tracking {len(self.contracts)} active markets")

    def get_cursor(self, ch) -> int:
        cursor = super().get_cursor(ch)
        if cursor == 0:
            return 18500000
        return cursor

    def insert_raw(self, ch, logs: list, block_ts_map: dict) -> int:
        """
        Store the interpolated spot yield ratios directly to morpho_oracle_historical
        so that replay_morpho_full.py can ingest it directly as if it came from the RPC!
        """
        rows = []
        for log_data in logs:
            if not log_data.topics or log_data.topics[0] != TOPIC_SWAP:
                continue
            
            raw = log_data.data[2:] if log_data.data.startswith("0x") else log_data.data
            if len(raw) >= 256:
                pt_hex = raw[0:64]
                sy_hex = raw[64:128]
                pt_val = int.from_bytes(bytes.fromhex(pt_hex), byteorder='big', signed=True)
                sy_val = int.from_bytes(bytes.fromhex(sy_hex), byteorder='big', signed=True)

                if pt_val != 0 and sy_val != 0:
                    spot_ratio = abs(sy_val) / abs(pt_val)
                    scaled_price = float(spot_ratio * 10**36)
                    
                    market_addr = (log_data.address or "").lower()
                    morpho_id = self.pendle_to_morpho.get(market_addr)
                    if morpho_id:
                        rows.append([
                            log_data.block_number,
                            morpho_id,
                            scaled_price
                        ])
        
        # Deduplicate to 1 output per block per market (mimicking old TWAP daily)
        unique_rows = { (r[0], r[1]): r[2] for r in rows }
        final_rows = [ [b, mid, p] for (b, mid), p in unique_rows.items() ]

        if final_rows:
            ch.insert(self.raw_table, final_rows, column_names=["block_number", "market_id", "oracle_price"])
        return len(final_rows)

    def decode(self, log_entry, block_ts_map: dict) -> Optional[dict]:
        # We process everything in insert_raw directly to morpho_oracle_historical.
        # No timeseries decoding required natively for core Engine metric aggregation.
        return None

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        return 0
