import asyncio
import os
import requests
import json
import clickhouse_connect
import pandas as pd

MORPHO_BLUE = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
CREATE_MARKET_TOPIC = "0xac4b2400f169220b0c0afdde7a0b32e775ba727ea1cb30b35f935cdaab8683ac"

RPC_URL = os.environ.get("MAINNET_RPC_URL", "https://eth.llamarpc.com")

def hex_to_addr(hex_str):
    return "0x" + hex_str[-40:].lower()

def _fetch_token_metadata(token_address: str) -> tuple[str, int]:
    if token_address == "0x0000000000000000000000000000000000000000":
        return "NONE", 18
        
    sym = "UNKNOWN"
    dec = 18
    
    # 1. Fetch Symbol
    payload_sym = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": token_address, "data": "0x95d89b41"}, "latest"],
        "id": 1
    }
    try:
        resp = requests.post(RPC_URL, json=payload_sym, timeout=2.0)
        resp.raise_for_status()
        res = resp.json().get("result")
        if res and res.startswith("0x") and len(res) > 130:
            length = int(res[66:130], 16)
            hex_str = res[130:130 + (length * 2)]
            sym = bytes.fromhex(hex_str).decode('utf-8', errors='ignore').replace('\x00', '')
    except Exception:
        pass
        
    # 2. Fetch Decimals
    payload_dec = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": token_address, "data": "0x313ce567"}, "latest"],
        "id": 2
    }
    try:
        resp = requests.post(RPC_URL, json=payload_dec, timeout=2.0)
        resp.raise_for_status()
        res = resp.json().get("result")
        if res and res != "0x":
            dec = int(res, 16)
    except Exception:
        pass
        
    return sym, dec

def main():
    print("Connecting to ClickHouse...")
    ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
    ch_port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    ch = clickhouse_connect.get_client(host=ch_host, port=ch_port)

    print("Fetching CreateMarket events from morpho_events...")
    df = ch.query_df(f"SELECT block_number, topic0, topic1, data FROM morpho_events WHERE topic0 = '{CREATE_MARKET_TOPIC}' ORDER BY block_number ASC")
    
    markets = []
    metadata = {}

    for i, row in df.iterrows():
        topic1 = row['topic1']
        data = row['data']
        
        if not topic1:
            continue
            
        market_id = topic1.lstrip("0x").zfill(64)
        if data and data.startswith("0x"):
            raw = data[2:]
        else:
            raw = data if isinstance(data, str) else ''
        
        if len(raw) >= 160:
            loanToken = hex_to_addr(raw[0:64])
            collateralToken = hex_to_addr(raw[64:128])
            oracle = hex_to_addr(raw[128:192])
            irm = hex_to_addr(raw[192:256])
            lltv = int(raw[256:320], 16)
            
            if loanToken not in metadata:
                metadata[loanToken] = _fetch_token_metadata(loanToken)
            if collateralToken not in metadata:
                metadata[collateralToken] = _fetch_token_metadata(collateralToken)
                
            markets.append({
                "market_id": market_id,
                "loan_token": loanToken,
                "collateral_token": collateralToken,
                "loan_symbol": metadata[loanToken][0],
                "collateral_symbol": metadata[collateralToken][0],
                "loan_decimals": metadata[loanToken][1],
                "collateral_decimals": metadata[collateralToken][1],
                "oracle": oracle,
                "irm": irm,
                "lltv": float(lltv)
            })
            
            # Logging progress
            if len(markets) % 100 == 0:
                print(f"Mapped {len(markets)} markets...")

    print(f"Finished mapping {len(markets)} Morpho Markets.")
    
    # Dump to DB
    print("Rebuilding morpho_market_params table schema...")
    ch.command("DROP TABLE IF EXISTS morpho_market_params")
    schema = '''
    CREATE TABLE morpho_market_params (
        market_id String,
        loan_token String,
        collateral_token String,
        loan_symbol LowCardinality(String),
        collateral_symbol LowCardinality(String),
        loan_decimals UInt8,
        collateral_decimals UInt8,
        oracle String,
        irm String,
        lltv Float64
    ) ENGINE = MergeTree ORDER BY market_id
    '''
    ch.command(schema)
    
    if len(markets) > 0:
        insert_df = pd.DataFrame(markets)
        ch.insert_df("morpho_market_params", insert_df)
        print(f"Successfully inserted {len(insert_df)} parameters into ClickHouse.")
        
    out_path = os.path.join(os.path.dirname(__file__), "morpho_markets.json")
    with open(out_path, "w") as f:
        json.dump(markets, f, indent=2)
    print(f"Local backup exported to {out_path}.")

if __name__ == "__main__":
    main()
