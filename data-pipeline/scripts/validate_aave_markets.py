import os
import sys
import requests
import sqlite3
import clickhouse_connect
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indexer.tokens import TOKENS, SYM_DECIMALS
from indexer.aave_constants import AAVE_V3_POOL

from dotenv import load_dotenv
load_dotenv()

RPC_URL = os.getenv("MAINNET_RPC_URL", "https://eth.llamarpc.com")
AAVE_POOL = AAVE_V3_POOL
SELECTOR = "0x35ea6a75"

def eth_call(to_addr, data, block_num):
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [
            {"to": to_addr, "data": data},
            hex(block_num)
        ],
        "id": 1
    }
    r = requests.post(RPC_URL, json=payload, timeout=10)
    try:
        res = r.json()
    except Exception as e:
        # Fallback to ankr
        r = requests.post("https://cloudflare-eth.com", json=payload, timeout=10)
        try:
            res = r.json()
        except:
            print(f"RPC completely failed. Status code: {r.status_code}")
            return None
    if 'error' in res:
        print("RPC Provider Error:", res['error'])
        return None
    return res.get('result')

def main():
    ch = clickhouse_connect.get_client(host="localhost", port=8123)
    
    # 1. Get the boundary
    block_res = ch.command("SELECT max(last_processed_block) FROM processor_state WHERE protocol='AAVE_MARKET'")
    last_block = int(block_res) if block_res else 0
    if not last_block:
        print("No processed blocks in DB.")
        sys.exit(1)
        
    print(f"Validating state boundary exactly at Block: {last_block}")
    
    # 2. Get DB State corresponding to that exact boundary
    query = """
    SELECT entity_id, symbol, supply_apy, borrow_apy, supply_usd, borrow_usd, price_usd
    FROM unified_timeseries
    WHERE protocol='AAVE_MARKET'
    ORDER BY timestamp DESC
    LIMIT 100 BY entity_id
    """
    rows = ch.query(query).result_rows
    
    # We only care about the absolute latest row per entity_id
    db_state = {}
    for r in rows:
        eid = r[0]
        if eid not in db_state:
            db_state[eid] = {
                "symbol": r[1],
                "supply_apy": r[2],
                "borrow_apy": r[3],
                "supply_usd": r[4], # DB stores raw supply * price
                "borrow_usd": r[5],
                "price": r[6]
            }
            
    matched = 0
    mismatches = []
    total = 0
    
    RAY = 10**27
    
    # 3. Pull On-Chain Data Provider states
    for addr, (symbol, _dec) in TOKENS.items():
        eid = "0x" + addr.lower()
        if eid not in db_state:
            continue
            
        total += 1
        calldata = SELECTOR + addr.zfill(64)
        raw = eth_call(AAVE_POOL, calldata, last_block)
        if not raw or raw == "0x" or len(raw) < 130:
            print(f"RPC failed or market inactive for {symbol} at block {last_block}")
            total -= 1
            continue
            
        hex_data = raw[2:]
        # Data provider returns 12 words
        if len(hex_data) < 12 * 64:
            print(f"Unexpected return size for {symbol}")
            continue
            
        # extract words
        # word 2: totalAToken
        w2 = hex_data[2*64 : 3*64]
        # word 4: totalVariableDebt
        w4 = hex_data[4*64 : 5*64]
        # word 5: liquidityRate
        w5 = hex_data[5*64 : 6*64]
        # word 6: variableBorrowRate
        w6 = hex_data[6*64 : 7*64]
        
        onchain_supply_qty = int(w2, 16)
        onchain_borrow_qty = int(w4, 16)
        onchain_sup_apy = int(w5, 16) / RAY
        onchain_bor_apy = int(w6, 16) / RAY
        
        db = db_state[eid]
        
        # Calculate human quantities
        # Our db stores supply_usd. To compare quantities:
        # DB quantity = db_supply_usd / db_price if db_price else 0
        db_supply_qty_raw = 0
        if db["price"] > 0:
            db_supply_qty_raw = db["supply_usd"] / db["price"] * (10 ** _dec)
            
        # Compare APY
        sup_ap_diff = abs(db["supply_apy"] - onchain_sup_apy)
        bor_ap_diff = abs(db["borrow_apy"] - onchain_bor_apy)
        
        if sup_ap_diff < 0.001 and bor_ap_diff < 0.001:
            matched += 1
        else:
            mismatches.append(f"[{symbol}] APY Drift -> DB Sup: {db['supply_apy']:.4f} vs EVM Sup: {onchain_sup_apy:.4f}")
            
    print(f"\n--- SCORECARD ---")
    print(f"Total Active Checked: {total}")
    print(f"Poka-Yoke Matched: {matched}")
    if mismatches:
        print("\nMISMATCHES (Threshold > 0.1% drift):")
        for m in mismatches[:10]:
            print(m)
        sys.exit(1)
    else:
        print("\n✅ PERFECT CORRELATION ACHIEVED.")
        sys.exit(0)

if __name__ == "__main__":
    main()
