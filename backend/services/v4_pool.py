#!/usr/bin/env python3
"""
V4 Pool State Reader — Direct web3.py access to Uniswap V4 PoolManager.

Replaces GetMarkPrice.s.sol and CalculateSwapAmount.s.sol forge scripts
with direct extsload() calls for ~100x speedup.

Storage layout from StateLibrary.sol:
    POOLS_SLOT = 6
    pool_state_slot = keccak256(abi.encodePacked(poolId, POOLS_SLOT))
    slot0         = pool_state_slot + 0  (sqrtPriceX96 | tick | protocolFee | lpFee)
    liquidity     = pool_state_slot + 3
"""

import math
from web3 import Web3
from eth_abi import encode

# Constants
V4_POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"
POOLS_SLOT = 6
LIQUIDITY_OFFSET = 3
Q96 = 2**96
Q192 = 2**192
FEE = 500
TICK_SPACING = 5

# ABI for PoolManager.extsload
EXTSLOAD_ABI = [
    {
        "inputs": [{"name": "slot", "type": "bytes32"}],
        "name": "extsload",
        "outputs": [{"name": "value", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function"
    }
]


def compute_pool_id(token0: str, token1: str, hook: str) -> bytes:
    """Compute PoolId = keccak256(abi.encode(PoolKey))."""
    pool_key_encoded = encode(
        ["address", "address", "uint24", "int24", "address"],
        [
            Web3.to_checksum_address(token0),
            Web3.to_checksum_address(token1),
            FEE,
            TICK_SPACING,
            Web3.to_checksum_address(hook),
        ]
    )
    return Web3.keccak(pool_key_encoded)


def _get_pool_state_slot(pool_id: bytes) -> bytes:
    """Compute the base storage slot for a pool's state."""
    # keccak256(abi.encodePacked(poolId, POOLS_SLOT))
    packed = pool_id + POOLS_SLOT.to_bytes(32, 'big')
    return Web3.keccak(packed)


class V4PoolReader:
    """Read V4 pool state directly via extsload — no forge scripts needed."""
    
    def __init__(self, w3: Web3, token0: str, token1: str, hook: str, wausdc: str):
        self.w3 = w3
        self.token0 = token0.lower()
        self.token1 = token1.lower()
        self.hook = hook
        self.wausdc = wausdc.lower()
        self.wausdc_is_token0 = self.wausdc == self.token0
        
        self.pm = w3.eth.contract(
            address=Web3.to_checksum_address(V4_POOL_MANAGER),
            abi=EXTSLOAD_ABI
        )
        
        self.pool_id = compute_pool_id(token0, token1, hook)
        self.state_slot = _get_pool_state_slot(self.pool_id)
    
    def get_slot0(self) -> tuple:
        """Read slot0: returns (sqrtPriceX96, tick, protocolFee, lpFee)."""
        data = self.pm.functions.extsload(self.state_slot).call()
        data_int = int.from_bytes(data, 'big')
        
        sqrtPriceX96 = data_int & ((1 << 160) - 1)
        tick_raw = (data_int >> 160) & 0xFFFFFF
        # Sign-extend 24-bit tick to int
        if tick_raw & 0x800000:
            tick = tick_raw - 0x1000000
        else:
            tick = tick_raw
        protocol_fee = (data_int >> 184) & 0xFFFFFF
        lp_fee = (data_int >> 208) & 0xFFFFFF
        
        return sqrtPriceX96, tick, protocol_fee, lp_fee
    
    def get_liquidity(self) -> int:
        """Read current pool liquidity."""
        liq_slot = (int.from_bytes(self.state_slot, 'big') + LIQUIDITY_OFFSET).to_bytes(32, 'big')
        data = self.pm.functions.extsload(liq_slot).call()
        return int.from_bytes(data, 'big') & ((1 << 128) - 1)
    
    def get_mark_price(self) -> float:
        """Get wRLP price in waUSDC terms (what you'd pay in waUSDC per wRLP)."""
        sqrtPriceX96, _, _, _ = self.get_slot0()
        
        if sqrtPriceX96 == 0:
            return None
        
        # raw_price = sqrtPriceX96² / 2^192 (gives token1/token0)
        raw_price = (sqrtPriceX96 * sqrtPriceX96 * 10**18) // Q192
        
        if raw_price == 0:
            return None
        
        if self.wausdc_is_token0:
            # raw = wRLP/waUSDC, we want waUSDC/wRLP = 1/raw
            wrlp_price = (10**18 * 10**18) // raw_price
        else:
            # raw = waUSDC/wRLP (correct)
            wrlp_price = raw_price
        
        return wrlp_price / 10**18
    
    def calculate_swap_amount(self, target_price: float) -> tuple:
        """
        Calculate exact swap amount to move pool to target price.
        
        Returns: (amount_in, zero_for_one, direction)
            amount_in: raw token amount
            zero_for_one: swap direction
            direction: "BUY_WRLP" or "SELL_WRLP"
        """
        sqrtPriceX96_current, _, _, _ = self.get_slot0()
        liquidity = self.get_liquidity()
        
        if sqrtPriceX96_current == 0 or liquidity == 0:
            return (0, True, "UNKNOWN")
        
        # Current price in wRLP terms
        raw_price_wad = (sqrtPriceX96_current * sqrtPriceX96_current * 10**18) // Q192
        if self.wausdc_is_token0:
            current_wrlp_price = (10**18 * 10**18) // raw_price_wad if raw_price_wad > 0 else 0
        else:
            current_wrlp_price = raw_price_wad
        
        target_price_wad = int(target_price * 10**18)
        
        # Convert target to raw token1/token0 price
        if self.wausdc_is_token0:
            target_raw_wad = (10**18 * 10**18) // target_price_wad if target_price_wad > 0 else 0
        else:
            target_raw_wad = target_price_wad
        
        # target sqrtPriceX96 = sqrt(target_raw * 2^192 / 1e18)
        target_price_q192 = (target_raw_wad * Q192) // (10**18)
        sqrtPriceX96_target = int(math.isqrt(target_price_q192))
        
        # Direction based on wRLP price
        sell_wrlp = current_wrlp_price > target_price_wad
        
        delta_sqrt = abs(sqrtPriceX96_target - sqrtPriceX96_current)
        
        if sell_wrlp:
            # Sell wRLP: give token1 (if wausdc is token0)
            zero_for_one = False
            # amount1 = L * |delta_sqrt| / Q96
            amount_in = (liquidity * delta_sqrt) // Q96
        else:
            # Buy wRLP: give token0 (waUSDC)
            zero_for_one = True
            # amount0 = L * |delta_sqrt| / (sqrtP_current * sqrtP_target / Q96)
            product = (sqrtPriceX96_current * sqrtPriceX96_target) // Q96
            amount_in = (liquidity * delta_sqrt) // product if product > 0 else 0
        
        direction = "SELL_WRLP" if sell_wrlp else "BUY_WRLP"
        return (amount_in, zero_for_one, direction)
