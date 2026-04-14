import json
import clickhouse_connect
from datetime import datetime, timezone, timedelta
import math

RPC_URL = "https://ethereum-rpc.publicnode.com"
CH_HOST = "localhost"
CH_PORT = 8123

def run_batch():
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT)
    
    # 1. Fetch all unique market IDs that contain PENDLE
    print("Loading oracle compositions...")
    with open("/tmp/oracle_compositions.json", "r") as f:
        comp = json.load(f)
        
    pendle_markets = []
    for k, v in comp.items():
        feeds_str = json.dumps(v["feeds"]).lower()
        if "pendle" in feeds_str or "pt-" in feeds_str:
            pendle_markets.append(k)
            
    print(f"Found {len(pendle_markets)} Pendle markets.")
    
    # 2. Get Oracle Addresses and Block Ranges from DB
    # 2. Get Oracle Addresses via Morpho API
    market_to_oracle = {}
    
    # Fill in any missing from Graph API
    import requests
    url = "https://blue-api.morpho.org/graphql"
    
    # Chunk 'missing' into chunks of 50
    missing = pendle_markets
    chunk_size = 50
    for i in range(0, len(missing), chunk_size):
        chunk = missing[i:i + chunk_size]
        q = f"""query {{ markets(first: 1000, where: {{ uniqueKey_in: {json.dumps(chunk)} }}) {{ items {{ uniqueKey, oracleAddress }} }} }}"""
        try:
            r = requests.post(url, json={"query": q}).json()
            items = r.get("data", {}).get("markets", {}).get("items", [])
            for item in items:
                market_to_oracle[item["uniqueKey"]] = item["oracleAddress"]
        except Exception as e:
            print("Graphql error", e)

    print(f"Mapped {len(market_to_oracle)} Pendle oracles.")
    
    # 3. Create Daily block boundaries for the last 6 months (approx Genesis of Pendle Blue markets)
    # Approx blocks: 19_000_000 to 22_000_000 (roughly 1 year)
    # 1 block per day = 7160 blocks
    start_block = 19400000
    end_block = 22000000 # Latest we need for DB consistency
    blocks_per_day = 7160
    
    blocks = list(range(start_block, end_block, blocks_per_day))
    print(f"Polling {len(blocks)} daily block snapshots.")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import requests
    
    # 4. Batch RPC calls
    results = [] # list of (block, market_id, oracle_price)
    
    def fetch_block(b):
        batch = []
        req_map = []
        for m_id, oracle in market_to_oracle.items():
            req_id = len(batch)
            batch.append({
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": oracle, "data": "0xa035b1fe"}, hex(b)],
                "id": req_id
            })
            req_map.append(m_id)
            
        b_results = []
        try:
            # Chunk batches to avoid publicnode max batch size of 100
            for i in range(0, len(batch), 50):
                c_batch = batch[i:i + 50]
                resp = requests.post(RPC_URL, json=c_batch, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    for d in data:
                        raw = d.get("result", "")
                        req_idx = d.get("id")
                        if len(raw) >= 64 and req_idx is not None:
                            val = int(raw, 16)
                            b_results.append((b, req_map[req_idx], val))
                else:
                    print(f"Failed chunk in block {b}: {resp.status_code}")
            return b, b_results
        except Exception as e:
            print(f"Exception block {b}: {e}")
            return b, []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_block, b): b for b in blocks}
        for future in as_completed(futures):
            b, res = future.result()
            results.extend(res)
            print(f"Block {b} processed.")

    # 5. Insert to ClickHouse
    print(f"Obtained {len(results)} exact historical Pendle Oracle prices.")
    
    # Create Table
    ch.command("""
        CREATE TABLE IF NOT EXISTS morpho_oracle_historical (
            block_number UInt64,
            market_id String,
            oracle_price Float64
        ) ENGINE = ReplacingMergeTree()
        ORDER BY (block_number, market_id)
    """)
    
    # Insert Data
    ch.insert("morpho_oracle_historical", results, column_names=["block_number", "market_id", "oracle_price"])
    print("Succesfully populated morpho_oracle_historical table!")

if __name__ == "__main__":
    run_batch()
