import os
import requests
import json
import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

RPC_URL = os.getenv("MAINNET_RPC_URL", "http://localhost:8545")

ch = clickhouse_connect.get_client(host='localhost', port=8123)
res = ch.query_df("SELECT DISTINCT substring(topic2, 27) as addr FROM fluid_events WHERE topic2 != ''")
addrs = ["0x" + a for a in res['addr'].tolist()]

for a in addrs:
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{
            "to": a,
            "data": "0x95d89b41" # symbol() signature
        }, "latest"],
        "id": 1
    }
    try:
        resp = requests.post(RPC_URL, json=payload).json()
        if 'result' in resp and resp['result'] != '0x':
            # Decode string
            raw = resp['result']
            # Drop the offset (32 bytes) and length (32 bytes)
            if len(raw) > 130:
                length = int(raw[66:130], 16)
                symbol_hex = raw[130:130+(length*2)]
                print(f"{a}: {bytes.fromhex(symbol_hex).decode('utf-8')}")
            else:
                print(f"{a}: Short result {raw}")
        else:
            print(f"{a}: Empty or failed {resp}")
    except Exception as e:
        print(f"{a}: Error {e}")
