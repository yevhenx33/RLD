import json
import os
import sys
from web3 import Web3

RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
w3 = Web3(Web3.HTTPProvider(RPC_URL))

if not w3.is_connected():
    print("Failed to connect to JSON-RPC")
    sys.exit(1)

# Get user key from .env
try:
    with open('/home/ubuntu/RLD/docker/.env') as f:
        env = dict(line.strip().split('=', 1) for line in f if '=' in line and not line.startswith('#'))
    user_key = env.get('USER_A_KEY')
    if not user_key:
        user_key = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
except Exception:
    user_key = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

user_acc = w3.eth.account.from_key(user_key)
print(f"User Address: {user_acc.address}")

with open('/home/ubuntu/RLD/docker/deployment.json') as f:
    deploy = json.load(f)

broker_router = deploy['broker_router']
wausdc = deploy['wausdc']
pos_token = deploy['position_token']
twamm_hook = deploy.get('twamm_hook', "0x0000000000000000000000000000000000000000")
broker_factory = deploy['broker_factory']

print(f"waUSDC: {wausdc}, PosToken: {pos_token}")

ERC20_ABI = [
    {"inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
]

wausdc_ct = w3.eth.contract(address=w3.to_checksum_address(wausdc), abi=ERC20_ABI)
bal = wausdc_ct.functions.balanceOf(user_acc.address).call()
print(f"User waUSDC Balance: {bal/1e6}")

# 1. Get Broker
BROKER_FACTORY_ABI = [
    {"inputs": [{"name": "collateral", "type": "address"}, {"name": "position", "type": "address"}], "name": "getBroker", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "collateral", "type": "address"}, {"name": "position", "type": "address"}], "name": "deployBroker", "outputs": [{"name": "", "type": "address"}], "stateMutability": "nonpayable", "type": "function"}
]
factory_ct = w3.eth.contract(address=w3.to_checksum_address(broker_factory), abi=BROKER_FACTORY_ABI)

broker_addr = factory_ct.functions.getBroker(w3.to_checksum_address(wausdc), w3.to_checksum_address(pos_token)).call()
print(f"Broker Address: {broker_addr}")

# 2. Broker approval & Operator setup
BROKER_ABI = [
    {"inputs": [{"name": "operator", "type": "address"}, {"name": "active", "type": "bool"}], "name": "setOperator", "outputs": [], "stateMutability": "nonpayable", "type": "function"}
]
if broker_addr != "0x0000000000000000000000000000000000000000":
    broker_ct = w3.eth.contract(address=w3.to_checksum_address(broker_addr), abi=BROKER_ABI)
    nonce = w3.eth.get_transaction_count(user_acc.address)
    tx = broker_ct.functions.setOperator(w3.to_checksum_address(broker_router), True).build_transaction({'from': user_acc.address, 'nonce': nonce, 'gas': 200000})
    try:
        signed = w3.eth.account.sign_transaction(tx, user_key)
        w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(signed.hash)
        print("BrokerRouter set as operator.")
    except Exception as e:
        print("Skipping operator set (maybe already operator).")

# 3. Simulate Long
BROKER_ROUTER_ABI = [
  {
    "name": "executeLong",
    "type": "function",
    "stateMutability": "nonpayable",
    "inputs": [
      { "name": "broker", "type": "address" },
      { "name": "amountIn", "type": "uint256" },
      { "name": "poolKey", "type": "tuple",
        "components": [
          { "name": "currency0", "type": "address" },
          { "name": "currency1", "type": "address" },
          { "name": "fee", "type": "uint24" },
          { "name": "tickSpacing", "type": "int24" },
          { "name": "hooks", "type": "address" }
        ]
      }
    ],
    "outputs": [{ "name": "amountOut", "type": "uint256" }]
  }
]

router_ct = w3.eth.contract(address=w3.to_checksum_address(broker_router), abi=BROKER_ROUTER_ABI)

t0 = wausdc if wausdc.lower() < pos_token.lower() else pos_token
t1 = pos_token if wausdc.lower() < pos_token.lower() else wausdc

poolKey = (
    w3.to_checksum_address(t0),
    w3.to_checksum_address(t1),
    deploy['pool_fee'],
    deploy['tick_spacing'],
    w3.to_checksum_address(twamm_hook)
)

amount_in = 50 * 10**6

# Approve router to spend user's wausdc
nonce = w3.eth.get_transaction_count(user_acc.address)
tx = wausdc_ct.functions.approve(broker_router, amount_in).build_transaction({'from': user_acc.address, 'nonce': nonce, 'gas': 100000})
try:
    signed = w3.eth.account.sign_transaction(tx, user_key)
    w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(signed.hash)
    print("Approved BrokerRouter to spend waUSDC.")
except Exception:
    pass

nonce = w3.eth.get_transaction_count(user_acc.address)
tx = router_ct.functions.executeLong(
    w3.to_checksum_address(broker_addr),
    amount_in,
    poolKey
).build_transaction({
    'from': user_acc.address,
    'nonce': nonce,
    'gas': 3_000_000
})

try:
    print("Simulating executeLong...")
    w3.eth.call(tx)
    print("Simulation succeeded!")
except Exception as e:
    print(f"Revert encountered: {e}")
