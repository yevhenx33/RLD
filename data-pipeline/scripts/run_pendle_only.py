import os
import sys
import logging
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
from indexer.core import IndexerEngine
from indexer.sources import PendleSwapSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CH_HOST = os.getenv("CH_HOST", "localhost")
CH_PORT = int(os.getenv("CH_PORT", "8123"))

if __name__ == "__main__":
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT)
    
    # Run only Pendle in a standalone engine
    engine = IndexerEngine(
        sources=[PendleSwapSource()],
        poll_interval=1,
        clickhouse_host=CH_HOST,
        clickhouse_port=CH_PORT,
    )
    
    engine.run()
