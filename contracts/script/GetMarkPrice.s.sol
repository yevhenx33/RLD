// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";

/**
 * @title GetMarkPrice
 * @notice Query V4 pool mark price - returns wRLP price in waUSDC terms
 * 
 * sqrtPriceX96² gives price = token1/token0
 * - If waUSDC is token0, wRLP is token1: price = wRLP/waUSDC (correct)
 * - If wRLP is token0, waUSDC is token1: price = waUSDC/wRLP (need to invert)
 */
contract GetMarkPrice is Script {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;
    
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    uint24 constant FEE = 500;
    int24 constant TICK_SPACING = 5;

    function run() external view {
        // Read from environment
        address token0 = vm.envAddress("TOKEN0");
        address token1 = vm.envAddress("TOKEN1");
        address hook = vm.envAddress("TWAMM_HOOK");
        address wausdc = vm.envAddress("WAUSDC");
        
        // Build pool key
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(hook)
        });
        
        IPoolManager pm = IPoolManager(V4_POOL_MANAGER);
        PoolId poolId = poolKey.toId();
        
        // Get slot0
        (uint160 sqrtPriceX96, int24 tick,,) = pm.getSlot0(poolId);
        
        // Calculate raw price from sqrtPriceX96
        // price = (sqrtPriceX96 / 2^96)^2 = sqrtPriceX96^2 / 2^192
        // This gives price = token1/token0
        uint256 rawPriceX18 = FullMath.mulDiv(
            uint256(sqrtPriceX96) * uint256(sqrtPriceX96),
            1e18,
            1 << 192
        );
        
        // We want wRLP price in waUSDC terms (how many waUSDC per wRLP)
        // If waUSDC is token0: rawPrice = token1/token0 = wRLP/waUSDC → need to invert
        // If waUSDC is token1: rawPrice = token1/token0 = waUSDC/wRLP → this is correct
        
        uint256 wrlpPriceX18;
        if (wausdc == token0) {
            // waUSDC is token0, wRLP is token1
            // rawPrice = wRLP/waUSDC, we want waUSDC/wRLP = 1/rawPrice
            wrlpPriceX18 = FullMath.mulDiv(1e18, 1e18, rawPriceX18);
        } else {
            // wRLP is token0, waUSDC is token1
            // rawPrice = waUSDC/wRLP (this is what we want)
            wrlpPriceX18 = rawPriceX18;
        }
        
        // Output in structured format for parsing
        console.log("MARK_PRICE_X18:", wrlpPriceX18);
        console.log("RAW_PRICE_X18:", rawPriceX18);
        console.log("SQRT_PRICE_X96:", sqrtPriceX96);
        console.log("TICK:", uint256(uint24(tick)));
        console.log("WAUSDC_IS_TOKEN0:", wausdc == token0 ? 1 : 0);
    }
}
