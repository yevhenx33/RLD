import clickhouse_connect
import requests
import datetime
import time
from indexer.base import BaseSource

class SofrSource(BaseSource):
    name = "SOFR_RATES"
    raw_table = "raw_sofr_rates"
    is_offchain = True
    genesis_date = "2023-03-01"

    def __init__(self):
        super().__init__()

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
        last_ts = res.first_item if res.result_rows and res.first_item else None
        
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
            ch.insert(self.raw_table, data, column_names=["timestamp", "apy"])
            return len(data)
        return 0

    def process(self, raw_events, block_map):
        pass # Optional if we need a processor step, but SOFR goes straight to timeseries format

    def merge(self, ch: clickhouse_connect.driver.Client, items: list):
        pass

    def run_processor_cycle(self):
        # Processor can be a no-op since the collector normalizes directly or we copy to a target table
        ch = clickhouse_connect.get_client(
            host=os.getenv("CLICKHOUSE_HOST", "localhost"), 
            port=int(os.getenv("CLICKHOUSE_PORT", "8123"))
        )
        # Typically the UI reads exactly from this table, or we materialize it.
        # We can just construct a materialized view in the codebase.
        pass
