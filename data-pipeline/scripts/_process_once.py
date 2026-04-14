import os
import sys
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexer.sources.aave_v3 import AaveV3Source
from indexer.processor import ProtocolProcessor
import clickhouse_connect
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

async def main():
    source = AaveV3Source()
    source.genesis_block = 16291127
    
    print("--- STARTING SINGLE-TRIGGER PROCESSING ---")
    processor = ProtocolProcessor(source)
    
    ch = clickhouse_connect.get_client(host="localhost", port=8123)
    max_mempool = int(ch.command(f"SELECT max(block_number) FROM {source.raw_table}") or 0)
    
    while True:
        last_proc = processor.get_last_processed_block(ch)
        if last_proc >= max_mempool:
            print(f"Fully processed to mempool head (Block {max_mempool})!")
            break
        print(f"Processor at {last_proc}, heading to {max_mempool}...")
        processor.run_processor_cycle()

if __name__ == "__main__":
    asyncio.run(main())
