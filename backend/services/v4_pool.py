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

import os
import math
import logging
from web3 import Web3
from eth_abi import encode

logger = logging.getLogger(__name__)

# Constants
V4_POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"
V4_QUOTER_ADDRESS = os.getenv("V4_QUOTER", "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203")
POOLS_SLOT = 6
LIQUIDITY_OFFSET = 3
Q96 = 2**96
Q192 = 2**192
FEE = 500
TICK_SPACING = 5

# V4 Quoter ABI
V4_QUOTER_ABI = [
    {
        "name": "quoteExactInputSingle",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {
                        "name": "poolKey",
                        "type": "tuple",
                        "components": [
                            {"name": "currency0", "type": "address"},
                            {"name": "currency1", "type": "address"},
                            {"name": "fee", "type": "uint24"},
                            {"name": "tickSpacing", "type": "int24"},
                            {"name": "hooks", "type": "address"},
                        ],
                    },
                    {"name": "zeroForOne", "type": "bool"},
                    {"name": "exactAmount", "type": "uint128"},
                    {"name": "hookData", "type": "bytes"},
                ],
            },
        ],
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "gasEstimate", "type": "uint256"},
        ],
    },
]

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
        
        # V4 Quoter for mark price
        try:
            self._quoter = w3.eth.contract(
                address=Web3.to_checksum_address(V4_QUOTER_ADDRESS),
                abi=V4_QUOTER_ABI
            )
            self._pool_key = (
                Web3.to_checksum_address(token0),
                Web3.to_checksum_address(token1),
                FEE,
                TICK_SPACING,
                Web3.to_checksum_address(hook),
            )
            # Selling wRLP: if wRLP is NOT token0 (i.e. waUSDC is token0) → zeroForOne=False
            self._sell_wrlp_zfo = not self.wausdc_is_token0
        except Exception:
            self._quoter = None
    
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
        """Get wRLP price in waUSDC terms (what you'd pay in waUSDC per wRLP).
        
        Uses V4 Quoter: quote selling 1 wRLP for waUSDC.
        Falls back to sqrtPriceX96 raw price if quoter unavailable.
        """
        # Primary: V4 Quoter (token-ordering agnostic)
        if self._quoter:
            try:
                one_wrlp = 10 ** 6  # 1 wRLP (6 decimals)
                params = (
                    self._pool_key,
                    self._sell_wrlp_zfo,
                    one_wrlp,
                    b"",
                )
                result = self._quoter.functions.quoteExactInputSingle(params).call()
                amount_out = result[0]
                return amount_out / (10 ** 6)
            except Exception as e:
                logger.debug(f"Quoter mark price failed: {e}")
        
        # Fallback: raw sqrtPriceX96 (factory handles inversion at init)
        sqrtPriceX96, _, _, _ = self.get_slot0()
        if sqrtPriceX96 == 0:
            return None
        raw_price = (sqrtPriceX96 * sqrtPriceX96 * 10**18) // Q192
        if raw_price == 0:
            return None
        return raw_price / 10**18
    
    def calculate_swap_amount(self, target_price: float) -> tuple:
        """
        Calculate exact swap amount to move pool to target price.
        
        target_price is the wRLP mark price in waUSDC terms (waUSDC per wRLP).
        
        Returns: (amount_in, zero_for_one, direction)
            amount_in: raw token amount
            zero_for_one: swap direction
            direction: "BUY_WRLP" or "SELL_WRLP"
        """
        sqrtPriceX96_current, _, _, _ = self.get_slot0()
        liquidity = self.get_liquidity()
        
        if sqrtPriceX96_current == 0 or liquidity == 0:
            return (0, True, "UNKNOWN")
        
        # raw_price_wad is token1/token0 from sqrtPriceX96.
        # When wausdc_is_token0: raw = wRLP/waUSDC (inverse of mark)
        # When wRLP is token0:   raw = waUSDC/wRLP (= mark price)
        raw_price_wad = (sqrtPriceX96_current * sqrtPriceX96_current * 10**18) // Q192
        
        # Convert target_price (waUSDC/wRLP) to raw token1/token0 units
        if self.wausdc_is_token0:
            # raw = wRLP/waUSDC = 1/mark_price
            target_raw_wad = int(10**36 // int(target_price * 10**18)) if target_price > 0 else 0
        else:
            # raw = waUSDC/wRLP = mark_price
            target_raw_wad = int(target_price * 10**18)
        
        # target sqrtPriceX96 = sqrt(target_raw * 2^192 / 1e18)
        target_price_q192 = (target_raw_wad * Q192) // (10**18)
        sqrtPriceX96_target = int(math.isqrt(target_price_q192))
        
        # Direction: compare in raw units (token1/token0)
        # If raw > target_raw: raw price too high → need to move it down
        # When wausdc_is_token0: raw=wRLP/waUSDC too high means wRLP overpriced → sell wRLP
        # When wRLP is token0:  raw=waUSDC/wRLP too high means waUSDC overvalued → buy wRLP
        if self.wausdc_is_token0:
            sell_wrlp = raw_price_wad < target_raw_wad  # raw < target means mark > target_mark → sell
        else:
            sell_wrlp = raw_price_wad > target_raw_wad
        
        delta_sqrt = abs(sqrtPriceX96_target - sqrtPriceX96_current)
        
        if sell_wrlp:
            if self.wausdc_is_token0:
                # Sell wRLP (token1→token0): zeroForOne=False
                zero_for_one = False
                amount_in = (liquidity * delta_sqrt) // Q96
            else:
                # Sell wRLP (token0→token1): zeroForOne=True
                zero_for_one = True
                product = (sqrtPriceX96_current * sqrtPriceX96_target) // Q96
                amount_in = (liquidity * delta_sqrt) // product if product > 0 else 0
        else:
            if self.wausdc_is_token0:
                # Buy wRLP (token0→token1): zeroForOne=True
                zero_for_one = True
                product = (sqrtPriceX96_current * sqrtPriceX96_target) // Q96
                amount_in = (liquidity * delta_sqrt) // product if product > 0 else 0
            else:
                # Buy wRLP (token1→token0): zeroForOne=False
                zero_for_one = False
                amount_in = (liquidity * delta_sqrt) // Q96
        
        direction = "SELL_WRLP" if sell_wrlp else "BUY_WRLP"
        return (amount_in, zero_for_one, direction)
