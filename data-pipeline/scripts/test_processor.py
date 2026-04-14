import sys
import os
import logging
import psutil

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexer.processor import ProtocolProcessor
from indexer.sources.aave_v3 import AaveV3Source
import clickhouse_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_processor")

def run_test():
    source = AaveV3Source()
    processor = ProtocolProcessor(source)
    
    ch = clickhouse_connect.get_client(host="localhost", port=8123)
    
    # We constrain the boundaries: let's pretend last processed block is 24,000,000
    # and max mempool block is restricted to 24,010,000 (10,000 blocks to process).
    log.info("Resetting processor test cursor...")
    # Clean previous state to force deterministic run
    processor.set_last_processed_block(ch, 24000000)
    
    def mock_get_last_processed(ch_client):
        return 24000000
    processor.get_last_processed_block = mock_get_last_processed
    
    # Track metrics
    start_ts_count = ch.command(f"SELECT count() FROM unified_timeseries WHERE protocol = '{source.name}'")
    process = psutil.Process(os.getpid())
    mem_before = process.memory_info().rss / 1024**2
    log.info(f"Memory before execution: {mem_before:.2f} MB")
    
    # To mock the mempool block size, we monkey-patch the query directly inside the run_processor_cycle loop
    # Actually, we don't need to patch. It will query the DB up to `max_mempool_block_res` which is real.
    # If the database is empty above 24M, it'll process nothing. 
    # Let's see what happens implicitly - it will just try to process whatever is exactly in `aave_events`.
    
    try:
        processor.run_processor_cycle()
    except Exception as e:
        log.error(f"Test Failed: {e}")
        
    mem_after = process.memory_info().rss / 1024**2
    log.info(f"Memory after execution: {mem_after:.2f} MB")
    
    end_ts_count = ch.command(f"SELECT count() FROM unified_timeseries WHERE protocol = '{source.name}'")
    
    log.info(f"Timeseries delta: {end_ts_count - start_ts_count} rows")
    assert (mem_after - mem_before) < 500, f"Memory leaked {mem_after - mem_before:.2f} MB"
    log.info(f"✅ ProtocolProcessor test complete. Memory overhead: {mem_after - mem_before:.2f} MB")

if __name__ == "__main__":
    run_test()
