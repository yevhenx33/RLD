import os
import requests
from dotenv import load_dotenv

load_dotenv()

addrs = {
    'tBTC': '0x18084fba666a33d37592fa2633fd49a74dd93a88',
    'eBTC': '0x657e8c867d8b37dcc18fa4caead9c45eb088c642',
    'LBTC': '0x8236a87084f8b84306f72007f36f2618a5634494'
}

RPC_URL = os.getenv("MAINNET_RPC_URL", "http://localhost:8545")

for name, a in addrs.items():
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": a, "data": "0x313ce567"}, "latest"], "id": 1} # decimals()
    resp = requests.post(RPC_URL, json=payload).json()
    if 'result' in resp and resp['result'] != '0x':
        num = int(resp['result'], 16)
        print(f"{name}: {num} decimals")
