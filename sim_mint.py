import json
import os
import sys
from web3 import Web3

RPC_URL = "http://127.0.0.1:8545"
w3 = Web3(Web3.HTTPProvider(RPC_URL))


with open('/home/ubuntu/RLD/docker/.env') as f:
    env = dict(line.strip().split('=', 1) for line in f if '=' in line and not line.startswith('#'))
user_key = env.get('USER_A_KEY', "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
user_acc = w3.eth.account.from_key(user_key)
print(f"User: {user_acc.address}")

with open('/home/ubuntu/RLD/docker/deployment.json') as f:
    deploy = json.load(f)

wausdc = deploy['wausdc']
pos_token = deploy['position_token']
market_id = deploy['market_id']
factory = deploy['broker_factory']

# 1. Get broker from event
BROKER_FACTORY_ABI = [
    {"anonymous": False, "inputs": [{"indexed": True, "name": "broker", "type": "address"}, {"indexed": True, "name": "owner", "type": "address"}, {"indexed": False, "name": "tokenId", "type": "uint256"}], "name": "BrokerCreated", "type": "event"}
]
factory_ct = w3.eth.contract(address=w3.to_checksum_address(factory), abi=BROKER_FACTORY_ABI)
events = factory_ct.events.BrokerCreated.get_logs(fromBlock=0, argument_filters={'owner': user_acc.address})
if len(events) == 0:
    print("No broker found for user.")
    sys.exit(1)
broker = events[-1].args.broker
print(f"Broker: {broker}")

if broker == "0x0000000000000000000000000000000000000000":
    print("Broker not deployed?!")
    sys.exit(1)

# 2. Add Collateral & Mint
BROKER_ABI = [
    {"inputs": [{"name": "rawMarketId", "type": "bytes32"}, {"name": "deltaCollateral", "type": "int256"}, {"name": "deltaDebt", "type": "int256"}], "name": "modifyPosition", "outputs": [], "stateMutability": "nonpayable", "type": "function"}
]
ERC20_ABI = [
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
]
wausdc_ct = w3.eth.contract(address=w3.to_checksum_address(wausdc), abi=ERC20_ABI)
broker_ct = w3.eth.contract(address=w3.to_checksum_address(broker), abi=BROKER_ABI)

print("Approving broker...")
nonce = w3.eth.get_transaction_count(user_acc.address)
tx = wausdc_ct.functions.approve(broker_ct.address, 2**256-1).build_transaction({'from': user_acc.address, 'nonce': nonce})
signed = w3.eth.account.sign_transaction(tx, user_key)
w3.eth.send_raw_transaction(signed.raw_transaction)
w3.eth.wait_for_transaction_receipt(signed.hash)

print("Adding 100 USDC collateral...")
nonce = w3.eth.get_transaction_count(user_acc.address)
tx = broker_ct.functions.modifyPosition(bytes.fromhex(market_id[2:]), 100 * 10**6, 0).build_transaction({'from': user_acc.address, 'nonce': nonce, 'gas': 2000000})
signed = w3.eth.account.sign_transaction(tx, user_key)
w3.eth.send_raw_transaction(signed.raw_transaction)
rec = w3.eth.wait_for_transaction_receipt(signed.hash)
print(f"Collateral Added. Status: {rec.status}")

print("Simulating Minting 50 wRLP (deltaDebt > 0)...")
nonce = w3.eth.get_transaction_count(user_acc.address)
tx = broker_ct.functions.modifyPosition(bytes.fromhex(market_id[2:]), 0, 50 * 10**6).build_transaction({'from': user_acc.address, 'nonce': nonce, 'gas': 2000000})
try:
    w3.eth.call(tx)
    print("Simulation succeeded!")
except Exception as e:
    print(f"Revert encountered: {e}")
