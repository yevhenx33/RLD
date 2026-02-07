#!/usr/bin/env python3
"""
Deploy LifecycleSwapRouter once and save address to .env.
Run this once at orchestrator startup instead of deploying per-swap.
"""

import os
import sys
import json
import subprocess
from dotenv import load_dotenv, set_key

load_dotenv("/home/ubuntu/RLD/.env")
load_dotenv("/home/ubuntu/RLD/contracts/.env")

CONTRACTS_DIR = "/home/ubuntu/RLD/contracts"
ENV_FILE = "/home/ubuntu/RLD/.env"


def deploy_router():
    """Deploy LifecycleSwapRouter and return its address."""
    deployer_key = os.getenv("DEPLOYER_KEY")
    rpc_url = os.getenv("RPC_URL", "http://localhost:8545")
    
    if not deployer_key:
        print("❌ DEPLOYER_KEY not set")
        sys.exit(1)
    
    # Use a minimal deploy script
    result = subprocess.run(
        ["forge", "script", "script/DeploySwapRouter.s.sol",
         "--tc", "DeploySwapRouter",
         "--rpc-url", rpc_url,
         "--broadcast", "-v"],
        cwd=CONTRACTS_DIR,
        capture_output=True, text=True,
        env={**os.environ, "DEPLOYER_KEY": deployer_key}
    )
    
    if result.returncode != 0:
        print(f"❌ Deploy failed:\n{result.stderr[-500:]}")
        sys.exit(1)
    
    # Parse router address from output
    for line in result.stdout.split('\n'):
        if "SWAP_ROUTER:" in line:
            addr = line.split(":")[-1].strip()
            return addr
    
    print(f"❌ Could not parse SWAP_ROUTER from output")
    print(result.stdout[-1000:])
    sys.exit(1)


def approve_tokens(router_addr: str):
    """Approve router + PoolManager for all trading accounts."""
    from web3 import Web3
    from eth_account import Account
    
    rpc_url = os.getenv("RPC_URL", "http://localhost:8545")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    wausdc = os.getenv("WAUSDC")
    position_token = os.getenv("POSITION_TOKEN")
    pm_addr = "0x000000000004444c5dc75cB358380D2e3dE08A90"
    
    ERC20_ABI = [
        {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
         "name": "approve", "outputs": [{"name": "", "type": "bool"}],
         "stateMutability": "nonpayable", "type": "function"},
        {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
         "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
         "stateMutability": "view", "type": "function"},
    ]
    
    MAX_UINT = 2**256 - 1
    
    # Approve for MM (PRIVATE_KEY from daemon) and Chaos trader
    keys_to_approve = []
    mm_key = os.getenv("MM_KEY") or os.getenv("PRIVATE_KEY")
    chaos_key = os.getenv("CHAOS_KEY")
    if mm_key:
        keys_to_approve.append(("MM", mm_key))
    if chaos_key:
        keys_to_approve.append(("Chaos", chaos_key))
    
    for name, key in keys_to_approve:
        account = Account.from_key(key)
        for token_addr, token_name in [(wausdc, "waUSDC"), (position_token, "wRLP")]:
            token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
            
            for spender, spender_name in [(router_addr, "Router"), (pm_addr, "PoolManager")]:
                allowance = token.functions.allowance(account.address, Web3.to_checksum_address(spender)).call()
                if allowance < MAX_UINT // 2:
                    nonce = w3.eth.get_transaction_count(account.address)
                    tx = token.functions.approve(
                        Web3.to_checksum_address(spender), MAX_UINT
                    ).build_transaction({
                        'from': account.address,
                        'nonce': nonce,
                        'gas': 60000,
                        'maxFeePerGas': w3.to_wei('2', 'gwei'),
                        'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
                    })
                    signed = w3.eth.account.sign_transaction(tx, key)
                    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
                    print(f"  ✅ {name} approved {token_name} for {spender_name}")


if __name__ == "__main__":
    print("[1] Deploying SwapRouter...")
    router_addr = deploy_router()
    print(f"  ✅ SwapRouter: {router_addr}")
    
    # Save to .env
    set_key(ENV_FILE, "SWAP_ROUTER", router_addr)
    print(f"  ✅ Saved SWAP_ROUTER to .env")
    
    print("[2] Approving tokens for trading accounts...")
    approve_tokens(router_addr)
    
    print("✅ Router setup complete")
