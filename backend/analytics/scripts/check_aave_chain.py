import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()
RPC_URL = os.getenv("MAINNET_RPC_URL", "https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY")

AAVE_DATA_PROVIDER = '0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3'

def eth_call(target, signature_hash, return_type='uint256'):
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": target, "data": signature_hash}, "latest"],
        "id": 1
    }
    res = requests.post(RPC_URL, json=payload).json()
    if 'result' not in res: return 0
    raw = res['result']
    if raw == '0x': return 0
    if return_type == 'uint256':
        return int(raw, 16)
    return raw

assets = {
    'WETH': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    'USDC': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'
}
for name, addr in assets.items():
    data = eth_call(AAVE_DATA_PROVIDER, "0x35ea6a75" + addr[2:].rjust(64, '0'), 'raw')
    if data and data != 0:
        data = data[2:]
        total_a_token = int(data[128:192], 16)
        total_variable_debt = int(data[256:320], 16)
        if name == 'WETH':
            print(f"{name} on-chain totalAToken: {total_a_token/1e18} ({total_a_token})")
            print(f"{name} on-chain totalVariableDebt: {total_variable_debt/1e18} ({total_variable_debt})")
        else:
            print(f"{name} on-chain totalAToken: {total_a_token/1e6} ({total_a_token})")
            print(f"{name} on-chain totalVariableDebt: {total_variable_debt/1e6} ({total_variable_debt})")

