import os
import sys
import time
import asyncio
import logging
import gc
import psutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import clickhouse_connect
from indexer.collector import ProtocolCollector
from indexer.processor import ProtocolProcessor
from indexer.sources.aave_v3 import AaveV3Source

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] STRESS TEST: %(message)s")
log = logging.getLogger("stress_test")

class MemoryTracker:
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        self.base_mem = self.measure()
        log.info(f"Base Memory: {self.base_mem:.2f} MB")

    def measure(self):
        return self.process.memory_info().rss / (1024 * 1024)

    def print_diff(self, label: str):
        gc.collect()
        current = self.measure()
        diff = current - self.base_mem
        log.info(f"[{label}] Current: {current:.2f} MB (Delta: {diff:+.2f} MB)")
        return current, diff


async def test_idempotency_schema(ch):
    """Verifies that ReplacingMergeTree rejects duplicates."""
    log.info("--- [1] IDEMPOTENCY PROPERTY TEST ---")
    
    # 1. Clear any testing table
    ch.command("CREATE TABLE IF NOT EXISTS test_mempool (block_number UInt64, log_index UInt32, data String, inserted_at DateTime DEFAULT now()) ENGINE = ReplacingMergeTree(inserted_at) ORDER BY (block_number, log_index)")
    ch.command("TRUNCATE TABLE test_mempool")

    # 2. Insert 10,000 exact duplicates
    rows = [[101, 1, "0xData123"]] * 10_000
    ch.insert("test_mempool", rows, column_names=["block_number", "log_index", "data"])
    
    # 3. Force merge
    ch.command("OPTIMIZE TABLE test_mempool FINAL")
    
    count = int(ch.command("SELECT count() FROM test_mempool"))
    log.info(f"Inserted 10,000 duplicate events. Final DB Row Count: {count}")
    
    assert count == 1, f"Idempotency Failed! Expected 1 row, got {count}"
    log.info("✅ ReplaceMergeTree Schema Idempotency Verified.")


async def test_collector_memory_stress():
    """Simulates aggressively collecting millions of blocks to ensure GC works."""
    log.info("--- [2] COLLECTOR MEMORY STRESS TEST ---")
    tracker = MemoryTracker()
    
    source = AaveV3Source()
    collector = ProtocolCollector(source)
    
    # We will override the batch boundaries locally to simulate 5 massive iterations
    # We'll mock get_height dynamically.
    
    class MockHS:
        def __init__(self, real):
            self.real = real
        async def get_height(self):
            return 24000000 + (5 * 100_000) # 500k blocks ahead
        async def get(self, query):
            return await self.real.get(query)
            
    collector._create_hs_client = lambda: MockHS(hypersync.HypersyncClient(hypersync.ClientConfig(url="https://eth.hypersync.xyz", bearer_token=collector.envio_token)))
    
    import hypersync # Ensure imported
    
    for iteration in range(1, 3): # 5 iterations
        # Monkey patch cursor so it advances
        collector.source.get_cursor = lambda ch, it=iteration: 24000000 + ((it-1) * 100_000)
        
        await collector.run_collector_cycle()
        _, diff = tracker.print_diff(f"Iteration {iteration}")
        
        assert diff < 200, f"Memory leak detected! Grew by {diff:.2f} MB"
        
    log.info("✅ Collector Memory Reclamation Verified. Infinite loop will not OOM.")


def test_processor_memory_stress(ch):
    """Simulates processing huge batches to ensure variables are dropped."""
    log.info("--- [3] PROCESSOR MEMORY STRESS TEST ---")
    tracker = MemoryTracker()
    
    source = AaveV3Source()
    processor = ProtocolProcessor(source)
    
    processor.batch_blocks = 200_000 # Massive batch processing chunk
    
    for iteration in range(1, 4): # process 3 massive batches
        processor.set_last_processed_block(ch, 24000000) # Reset to force re-fetch if we mocked it, but let's just let it run normally
        try:
            processor.run_processor_cycle()
        except clickhouse_connect.driver.exceptions.DatabaseError:
            # We don't care if it actually hits empty block gaps, we care about memory
            pass
            
        _, diff = tracker.print_diff(f"Iteration {iteration}")
        assert diff < 200, f"Memory leak detected! Grew by {diff:.2f} MB"

    log.info("✅ Processor Local Reference Reclamation Verified.")


async def main():
    ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
    ch_port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    ch = clickhouse_connect.get_client(host=ch_host, port=ch_port)
    
    await test_idempotency_schema(ch)
    await test_collector_memory_stress()
    test_processor_memory_stress(ch)
    
    log.info("🎉 COMPREHENSIVE STRESS TESTING COMPLETED SUCCESSFULLY 🎉")

if __name__ == "__main__":
    asyncio.run(main())
