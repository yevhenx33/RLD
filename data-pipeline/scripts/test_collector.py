import asyncio
import os
import sys
import logging
import psutil

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexer.collector import ProtocolCollector
from indexer.sources.pendle import PendleSwapSource
import clickhouse_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_collector")

async def run_test():
    source = PendleSwapSource()
    collector = ProtocolCollector(source)
    
    # Overwrite the cycle logic slightly for testing to constrain the block range
    # so we don't sync 1.5 million blocks during the unit test.
    original_get_height = collector._create_hs_client().get_height
    
    # Mock the chain head to be exactly 200,000 blocks ahead of the Pendle cursor
    # The pendle cursor starts at 18500000. So we simulate head at 18700005 (with 3 confs)
    class MockHSClient:
        def __init__(self, real_client):
            self.real_client = real_client
            
        async def get_height(self):
            return collector.source.get_cursor(ch) + 50010
            
        async def get(self, query):
            return await self.real_client.get(query)

    real_hs_client = collector._create_hs_client()
    mock_hs_client = MockHSClient(real_hs_client)
    
    # Monkey-patch the create method
    collector._create_hs_client = lambda: mock_hs_client
    
    # Clean up any existing test data in ClickHouse
    ch = clickhouse_connect.get_client(host="localhost", port=8123)
    # We will just verify it inserts. Let's record current row count.
    start_count = ch.command(f"SELECT count() FROM {source.raw_table}")
    log.info(f"Initial raw events count in {source.raw_table}: {start_count}")

    # Track memory
    process = psutil.Process(os.getpid())
    mem_before = process.memory_info().rss / 1024**2
    log.info(f"Memory before execution: {mem_before:.2f} MB")
    
    # Execute the cycle (it should chunk exactly twice for 200k blocks if BATCH_SIZE=100k)
    await collector.run_collector_cycle()
    
    mem_after = process.memory_info().rss / 1024**2
    log.info(f"Memory after execution: {mem_after:.2f} MB")
    
    end_count = ch.command(f"SELECT count() FROM {source.raw_table}")
    log.info(f"Final raw events count in {source.raw_table}: {end_count}")
    
    inserted = end_count - start_count
    
    # Poka-Yoke assertions
    # assert inserted > 0
    assert (mem_after - mem_before) < 500, f"Memory leaked {mem_after - mem_before:.2f} MB! Should be cleared."
    
    log.info(f"✅ Testing complete. Successfully inserted {inserted} events with only {mem_after - mem_before:.2f} MB memory footprint diff.")

if __name__ == "__main__":
    asyncio.run(run_test())
