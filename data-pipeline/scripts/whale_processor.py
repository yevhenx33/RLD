import os
import sys
import asyncio
import clickhouse_connect
from eth_utils import keccak

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indexer.tokens import TOKENS, SYM_DECIMALS

def main():
    ch = clickhouse_connect.get_client()
    
    print("Initializing whale_activity schema...")
    ch.command("DROP TABLE IF EXISTS whale_activity")
    schema = """
    CREATE TABLE whale_activity (
        user_address String,
        symbol String,
        event_type String,
        amount Float64,
        block_number UInt64,
        timestamp DateTime
    ) ENGINE = MergeTree()
    ORDER BY (user_address, timestamp)
    """
    ch.command(schema)
    
    sigs = {
        'Supply': '0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61',
        'Withdraw': '0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7',
        'Borrow': '0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0',
        'Repay': '0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051',
    }
    
    reverse_tokens = {("0x" + k.lower()): v for k, v in TOKENS.items()}
    
    # We will process in batches of 1M rows
    limit = 500_000
    offset = 0
    total_written = 0
    total_events = int(ch.command("SELECT count() FROM aave_events"))
    
    print(f"Starting Whale Execution... scanning {total_events} events")
    
    while True:
        query = f"""
        SELECT 
            topic0, topic1, topic2, topic3, data, block_number, block_timestamp
        FROM aave_events
        ORDER BY block_number ASC
        LIMIT {limit} OFFSET {offset}
        """
        rows = ch.query(query).result_rows
        if not rows:
            break
            
        writes = []
        for r in rows:
            t0, t1, t2, t3, data, block, ts = r
            
            # Map Reserve to Symbol
            # Topic 1 contains the reserve address in aave events
            if not t1 or len(t1) < 42:
                continue
            reserve_addr = "0x" + t1[-40:].lower()
            if reserve_addr not in reverse_tokens:
                continue
                
            symbol, decimals = reverse_tokens[reserve_addr]
            
            user_addr = None
            amount = 0
            event_type = ""
            
            try:
                hex_data = data[2:]
                
                if t0 == sigs['Supply']:
                    # Supply(address indexed reserve, address user, address indexed onBehalfOf, uint256 amount, uint16 indexed referralCode)
                    # topic1 = reserve, topic2 = onBehalfOf
                    user_addr = "0x" + t2[-40:].lower()
                    # data = user (32), amount (32), referralCode (32)
                    if len(hex_data) >= 64:
                        amount = int(hex_data[64:128], 16) / (10**decimals)
                    event_type = "Supply"
                    
                elif t0 == sigs['Withdraw']:
                    # Withdraw(address indexed reserve, address indexed user, address indexed to, uint256 amount)
                    user_addr = "0x" + t2[-40:].lower()
                    if len(hex_data) >= 64:
                        amount = int(hex_data[0:64], 16) / (10**decimals)
                    event_type = "Withdraw"
                    
                elif t0 == sigs['Borrow']:
                    # Borrow(address indexed reserve, address user, address indexed onBehalfOf, uint256 amount, ...)
                    user_addr = "0x" + t2[-40:].lower()
                    if len(hex_data) >= 64:
                        amount = int(hex_data[64:128], 16) / (10**decimals)
                    event_type = "Borrow"
                    
                elif t0 == sigs['Repay']:
                    # Repay(address indexed reserve, address indexed user, address indexed repayer, uint256 amount, ...)
                    user_addr = "0x" + t2[-40:].lower()
                    if len(hex_data) >= 64:
                        amount = int(hex_data[0:64], 16) / (10**decimals)
                    event_type = "Repay"
                    
            except Exception:
                continue
                
            if user_addr and event_type and amount > 0:
                writes.append([user_addr, symbol, event_type, amount, block, ts])
                
        if writes:
            ch.insert("whale_activity", writes, column_names=["user_address", "symbol", "event_type", "amount", "block_number", "timestamp"])
            total_written += len(writes)
            
        print(f"Processed offset {offset}... Written: {total_written} Whale Flow Events")
        offset += limit
        
    print(f"✅ Executed. Total valid actions filtered: {total_written}")

if __name__ == "__main__":
    main()
