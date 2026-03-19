#!/usr/bin/env python3
"""
V4 Swap Executor — Direct web3.py swap via pre-deployed LifecycleSwapRouter.

Replaces LifecycleSwap.s.sol forge script (~25s) with a direct contract call (~1s).
Requires SWAP_ROUTER address in .env (deployed by deploy_swap_router.py).
"""

import os
from web3 import Web3
from eth_account import Account

V4_POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"
FEE = 500
TICK_SPACING = 5

# MIN/MAX sqrtPriceX96 limits for swaps
MIN_SQRT_PRICE_LIMIT = 4295128740
MAX_SQRT_PRICE_LIMIT = 1461446703485210103287273052203988822378723970341

# LifecycleSwapRouter.swap ABI
ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
                "name": "key",
                "type": "tuple",
            },
            {"name": "zeroForOne", "type": "bool"},
            {"name": "amountSpecified", "type": "int256"},
        ],
        "name": "swap",
        "outputs": [{"name": "delta", "type": "int256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


class V4SwapExecutor:
    """Execute swaps on Uniswap V4 via pre-deployed router."""
    
    def __init__(self, w3: Web3, token0: str, token1: str, hook: str, router_addr: str):
        self.w3 = w3
        self.token0 = Web3.to_checksum_address(token0)
        self.token1 = Web3.to_checksum_address(token1)
        self.hook = Web3.to_checksum_address(hook)
        
        self.router = w3.eth.contract(
            address=Web3.to_checksum_address(router_addr),
            abi=ROUTER_ABI
        )
        
        self.pool_key = (self.token0, self.token1, FEE, TICK_SPACING, self.hook)
    
    def execute_swap(self, private_key: str, zero_for_one: bool, amount: int) -> bool:
        """
        Execute a swap.
        
        Args:
            private_key: Signer's private key
            zero_for_one: Swap direction (True = sell token0, buy token1)
            amount: Raw token amount (positive, will be negated for exact-input)
            
        Returns: True if swap succeeded
        """
        try:
            account = Account.from_key(private_key)
            nonce = self.w3.eth.get_transaction_count(account.address, 'pending')
            
            # Aggressive gas pricing: 10x base fee, minimum 10 gwei
            base_fee = self.w3.eth.gas_price or 1_000_000_000
            max_fee = max(base_fee * 10, self.w3.to_wei('10', 'gwei'))
            
            # Negative amountSpecified = exact input
            amount_specified = -amount
            
            tx = self.router.functions.swap(
                self.pool_key,
                zero_for_one,
                amount_specified
            ).build_transaction({
                'from': account.address,
                'nonce': nonce,
                'gas': 1000000,
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': self.w3.to_wei('2', 'gwei'),
            })
            
            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            
            return receipt.status == 1
            
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Swap failed: {e}")
            return False
