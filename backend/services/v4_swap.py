#!/usr/bin/env python3
"""
V4 Swap Executor — Direct web3.py swap via pre-deployed LifecycleSwapRouter.

Replaces LifecycleSwap.s.sol forge script (~25s) with a direct contract call (~1s).
Requires SWAP_ROUTER address in .env (deployed by deploy_swap_router.py).
"""

import os
import logging
from web3 import Web3
from eth_account import Account

V4_POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"
FEE = 500
TICK_SPACING = 5
MAX_UINT256 = 2**256 - 1
GHOST_SWAP_GAS_FALLBACK = 6_000_000
GHOST_SWAP_GAS_MIN = 1_500_000
GHOST_SWAP_GAS_MAX = 8_000_000

logger = logging.getLogger(__name__)

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

# GhostRouter.swap ABI
GHOST_ROUTER_ABI = [
    {
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "zeroForOne", "type": "bool"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMinimum", "type": "uint256"},
        ],
        "name": "swap",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

ERC20_APPROVAL_ABI = [
    {
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def _tx_gas_params(w3: Web3) -> dict:
    """Shared EIP-1559 gas params with aggressive fee headroom."""
    base_fee = w3.eth.gas_price or 1_000_000_000
    max_fee = max(base_fee * 10, w3.to_wei("10", "gwei"))
    return {
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
    }


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
            if amount <= 0:
                logger.error("Swap amount must be positive")
                return False

            account = Account.from_key(private_key)
            nonce = self.w3.eth.get_transaction_count(account.address, "pending")
            
            # Negative amountSpecified = exact input
            amount_specified = -amount
            
            tx = self.router.functions.swap(
                self.pool_key,
                zero_for_one,
                amount_specified
            ).build_transaction({
                "from": account.address,
                "nonce": nonce,
                "gas": 1_000_000,
                **_tx_gas_params(self.w3),
            })
            
            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            
            return receipt.status == 1
            
        except Exception as e:
            logger.error(f"Swap failed: {e}")
            return False


class GhostRouterSwapExecutor:
    """Execute swaps through GhostRouter.swap with per-token auto-approvals."""

    def __init__(
        self,
        w3: Web3,
        token0: str,
        token1: str,
        ghost_router_addr: str,
        market_id: str,
        amount_out_minimum: int = 1,
    ):
        self.w3 = w3
        self.token0 = Web3.to_checksum_address(token0)
        self.token1 = Web3.to_checksum_address(token1)
        self.market_id = market_id if market_id.startswith("0x") else f"0x{market_id}"
        self.amount_out_minimum = amount_out_minimum

        self.router = w3.eth.contract(
            address=Web3.to_checksum_address(ghost_router_addr),
            abi=GHOST_ROUTER_ABI,
        )
        self._token_contracts = {
            self.token0: w3.eth.contract(address=self.token0, abi=ERC20_APPROVAL_ABI),
            self.token1: w3.eth.contract(address=self.token1, abi=ERC20_APPROVAL_ABI),
        }

    def _token_in(self, zero_for_one: bool) -> str:
        return self.token0 if zero_for_one else self.token1

    def _ensure_approval(self, private_key: str, token_addr: str, required_amount: int) -> bool:
        account = Account.from_key(private_key)
        token = self._token_contracts[token_addr]
        spender = self.router.address

        try:
            allowance = token.functions.allowance(account.address, spender).call()
        except Exception as e:
            logger.error(f"Approval check failed for {token_addr}: {e}")
            return False

        if allowance >= required_amount:
            return True

        try:
            nonce = self.w3.eth.get_transaction_count(account.address, "pending")
            approve_tx = token.functions.approve(spender, MAX_UINT256).build_transaction({
                "from": account.address,
                "nonce": nonce,
                "gas": 120_000,
                **_tx_gas_params(self.w3),
            })
            signed = self.w3.eth.account.sign_transaction(approve_tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            if receipt.status != 1:
                logger.error(f"Approve reverted for token {token_addr}")
                return False
            return True
        except Exception as e:
            logger.error(f"Approve failed for {token_addr}: {e}")
            return False

    def execute_swap(self, private_key: str, zero_for_one: bool, amount: int) -> bool:
        if amount <= 0:
            logger.error("Swap amount must be positive")
            return False

        token_in = self._token_in(zero_for_one)
        if not self._ensure_approval(private_key, token_in, amount):
            return False

        try:
            account = Account.from_key(private_key)
            swap_fn = self.router.functions.swap(
                self.market_id,
                zero_for_one,
                amount,
                self.amount_out_minimum,
            )

            # Dry-run to avoid burning input in empty-liquidity scenarios.
            try:
                quoted_out = int(swap_fn.call({"from": account.address}))
                if quoted_out <= 0:
                    logger.warning(
                        "GhostRouter swap skipped: zero quoted output "
                        "(market=%s zfo=%s amountIn=%s)",
                        self.market_id,
                        zero_for_one,
                        amount,
                    )
                    return False
            except Exception as quote_err:
                logger.error(f"GhostRouter preflight quote failed: {quote_err}")
                return False

            nonce = self.w3.eth.get_transaction_count(account.address, "pending")
            gas_limit = GHOST_SWAP_GAS_FALLBACK
            try:
                estimated_gas = int(swap_fn.estimate_gas({"from": account.address}))
                gas_limit = max(
                    GHOST_SWAP_GAS_MIN,
                    min(int(estimated_gas * 1.25), GHOST_SWAP_GAS_MAX),
                )
            except Exception as gas_err:
                logger.warning(f"GhostRouter gas estimation failed, using fallback: {gas_err}")

            tx = swap_fn.build_transaction({
                "from": account.address,
                "nonce": nonce,
                "gas": gas_limit,
                **_tx_gas_params(self.w3),
            })

            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status != 1:
                logger.error(
                    "GhostRouter swap reverted (tx=%s gasUsed=%s/%s)",
                    tx_hash.hex(),
                    receipt.gasUsed,
                    gas_limit,
                )
                return False
            return True
        except Exception as e:
            logger.error(f"GhostRouter swap failed: {e}")
            return False
