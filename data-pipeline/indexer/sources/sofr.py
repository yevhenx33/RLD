import os
import clickhouse_connect
import requests
import datetime
import time
from typing import Optional
from indexer.base import BaseSource
from indexer.base import insert_rows_batched

class SofrSource(BaseSource):
    name = "SOFR_RATES"
    raw_table = "raw_sofr_rates"
    is_offchain = True
    genesis_date = "2023-03-01"

    def __init__(self):
        super().__init__()

    @staticmethod
    def _normalize_last_timestamp(raw_value) -> Optional[datetime.datetime]:
        if raw_value is None:
            return None
        if isinstance(raw_value, dict):
            if not raw_value:
                return None
            raw_value = next(iter(raw_value.values()))
        if isinstance(raw_value, datetime.datetime):
            return raw_value
        if isinstance(raw_value, datetime.date):
            return datetime.datetime.combine(raw_value, datetime.time.min)
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                return None
            try:
                return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    async def poll_and_insert(self, ch: clickhouse_connect.driver.Client) -> int:
        # 1. Initialize table
        ch.command(f"""
            CREATE TABLE IF NOT EXISTS {self.raw_table} (
                timestamp DateTime,
                apy Float64
            ) ENGINE = ReplacingMergeTree()
            ORDER BY (timestamp)
        """)
        
        # 2. Get cursor
        res = ch.query(f"SELECT MAX(timestamp) FROM {self.raw_table}")
        last_ts = self._normalize_last_timestamp(res.first_item if res.result_rows else None)
        
        start_date = self.genesis_date
        if last_ts and last_ts.year > 2000:
            start_date = last_ts.strftime("%Y-%m-%d")
            
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if start_date == today:
            return 0 # up to date
            
        url = f"https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate={start_date}&endDate={today}"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        
        rates = r.json().get("refRates", [])
        if not rates:
            return 0
            
        data = []
        for item in rates:
            dt_str = item.get("effectiveDate")
            rate = item.get("percentRate")
            if dt_str and rate is not None:
                dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d")
                apy = float(rate) / 100.0  # Decimal APY format standard
                data.append([dt, apy])
                
        if data:
            return insert_rows_batched(ch, self.raw_table, data, ["timestamp", "apy"])
        return 0

    def process(self, raw_events, block_map):
        pass # Optional if we need a processor step, but SOFR goes straight to timeseries format

    def decode(self, log_entry, block_ts_map: dict) -> Optional[dict]:
        # Offchain source path does not decode on-chain logs.
        return None

    def merge(self, ch: clickhouse_connect.driver.Client, items: list):
        pass

    def run_processor_cycle(self):
        # Processor can be a no-op since the collector normalizes directly or we copy to a target table
        ch = clickhouse_connect.get_client(
            host=os.getenv("CLICKHOUSE_HOST", "localhost"), 
            port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
            username=os.getenv("CLICKHOUSE_USER", "default"),
            password=os.getenv("CLICKHOUSE_PASSWORD", ""),
            settings={
                "async_insert": 1 if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"} else 0,
                "wait_for_async_insert": 1 if os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"} else 0,
            },
        )
        # Typically the UI reads exactly from this table, or we materialize it.
        # We can just construct a materialized view in the codebase.
        pass
