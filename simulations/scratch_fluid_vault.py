import os
import clickhouse_connect
import requests
from dotenv import load_dotenv

load_dotenv()

ch = clickhouse_connect.get_client(host='localhost', port=8123)
df = ch.query_df("SELECT DISTINCT substring(topic1, 27) as vault FROM fluid_events WHERE topic1 != ''")

RPC_URL = os.getenv("MAINNET_RPC_URL", "http://localhost:8545")

# Fluid Vaults usually have functions like "supplyToken()" and "borrowToken()"
# Let's check signatures: supplyToken() -> 0x82b9ce70, borrowToken() -> 0xac65f02c,
# NO! They are usually configured differently. Typical Vaults implement "asset()" or "collateral()" or "debt()". 

# Let's inspect ONE vault to see its ABI online using Etherscan
vault_1 = '0x' + df['vault'][0]
print(f"Inspecting vault: {vault_1}")
resp = requests.get(f"https://api.etherscan.io/api?module=contract&action=getabi&address={vault_1}", headers={"User-Agent": "Mozilla/5.0"}).json()
if 'result' in resp and resp['result'].startswith('['):
    print("Obtained ABI!")
    import json
    abi = json.loads(resp['result'])
    for method in abi:
        if method.get('type') == 'function' and method.get('stateMutability') == 'view':
            print(method.get('name'))
else:
    print(resp)
