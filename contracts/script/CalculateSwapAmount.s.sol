// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";

/**
 * @title CalculateSwapAmount
 * @notice Calculate exact swap amount to move V4 pool price to target
 * 
 * Math for Uniswap V3/V4 concentrated liquidity:
 * - sqrtPrice = sqrt(token1/token0) * 2^96
 * - Within a tick range, liquidity L is constant
 * 
 * For price movement from sqrtP_current to sqrtP_target:
 * - amount0 = L * (1/sqrtP_target - 1/sqrtP_current)  [token0 delta]
 * - amount1 = L * (sqrtP_target - sqrtP_current)      [token1 delta]
 */
contract CalculateSwapAmount is Script {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;
    
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    uint24 constant FEE = 500;
    int24 constant TICK_SPACING = 5;
    
    // Fixed point constants
    uint256 constant Q96 = 2**96;
    uint256 constant Q192 = 2**192;

    function run() external view {
        // Read from environment
        address token0 = vm.envAddress("TOKEN0");
        address token1 = vm.envAddress("TOKEN1");
        address hook = vm.envAddress("TWAMM_HOOK");
        address wausdc = vm.envAddress("WAUSDC");
        uint256 targetPriceWad = vm.envUint("TARGET_PRICE_WAD"); // 18 decimals
        
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
        
        // Get current pool state
        (uint160 sqrtPriceX96Current, int24 tick,,) = pm.getSlot0(poolId);
        uint128 liquidity = pm.getLiquidity(poolId);
        
        // Calculate current price (token1/token0)
        // price = (sqrtPriceX96 / 2^96)^2
        uint256 currentPriceWad = FullMath.mulDiv(
            uint256(sqrtPriceX96Current) * uint256(sqrtPriceX96Current),
            1e18,
            Q192
        );
        
        // Determine if waUSDC is token0 or token1
        bool wausdcIsToken0 = (wausdc == token0);
        
        // Target price in terms of token1/token0
        // If waUSDC is token0: targetPrice = wRLP/waUSDC (we want waUSDC/wRLP, so invert)
        // If waUSDC is token1: targetPrice = waUSDC/wRLP (correct)
        uint256 targetPriceRaw;
        if (wausdcIsToken0) {
            // Input is waUSDC/wRLP, need wRLP/waUSDC = 1/input
            targetPriceRaw = FullMath.mulDiv(1e18, 1e18, targetPriceWad);
        } else {
            targetPriceRaw = targetPriceWad;
        }
        
        // Calculate target sqrtPriceX96
        // sqrtPrice = sqrt(price) * 2^96
        // sqrtPriceX96 = sqrt(price * 2^192) = sqrt(price) * 2^96
        uint256 targetPriceQ192 = FullMath.mulDiv(targetPriceRaw, Q192, 1e18);
        uint160 sqrtPriceX96Target = uint160(sqrt(targetPriceQ192));
        
        // Convert current raw price to wRLP price for comparison
        uint256 currentWrlpPrice;
        if (wausdcIsToken0) {
            currentWrlpPrice = FullMath.mulDiv(1e18, 1e18, currentPriceWad);
        } else {
            currentWrlpPrice = currentPriceWad;
        }
        
        // Calculate required swap amount
        // Compare wRLP prices directly (not raw prices) to determine direction
        // If current wRLP price > target: need to SELL wRLP (push price down)
        // If current wRLP price < target: need to BUY wRLP (push price up)
        
        int256 amountIn;
        bool zeroForOne;
        bool sellWrlp = currentWrlpPrice > targetPriceWad;
        
        // When waUSDC is token0: wRLP is token1
        // Selling wRLP = giving token1 = zeroForOne = false
        // Buying wRLP = giving token0 = zeroForOne = true
        
        if (sellWrlp) {
            // SELL wRLP: give token1 (wRLP) to get token0 (waUSDC)
            // This INCREASES sqrtPrice (more token1 in pool)
            zeroForOne = false;
            // amount1 = L * |sqrtP_target - sqrtP_current| / 2^96
            uint256 deltaSqrt;
            if (sqrtPriceX96Target > sqrtPriceX96Current) {
                deltaSqrt = uint256(sqrtPriceX96Target) - uint256(sqrtPriceX96Current);
            } else {
                deltaSqrt = uint256(sqrtPriceX96Current) - uint256(sqrtPriceX96Target);
            }
            amountIn = int256(FullMath.mulDiv(uint256(liquidity), deltaSqrt, Q96));
        } else {
            // BUY wRLP: give token0 (waUSDC) to get token1 (wRLP)
            // This DECREASES sqrtPrice (less token1 in pool)
            zeroForOne = true;
            // amount0 = L * |delta_sqrtP| / (sqrtP_current * sqrtP_target / 2^96)
            uint256 deltaSqrt;
            if (sqrtPriceX96Target > sqrtPriceX96Current) {
                deltaSqrt = uint256(sqrtPriceX96Target) - uint256(sqrtPriceX96Current);
            } else {
                deltaSqrt = uint256(sqrtPriceX96Current) - uint256(sqrtPriceX96Target);
            }
            uint256 product = FullMath.mulDiv(uint256(sqrtPriceX96Current), uint256(sqrtPriceX96Target), Q96);
            amountIn = int256(FullMath.mulDiv(uint256(liquidity), deltaSqrt, product));
        }
        
        // Output results
        console.log("CURRENT_SQRT_PRICE_X96:", sqrtPriceX96Current);
        console.log("TARGET_SQRT_PRICE_X96:", sqrtPriceX96Target);
        console.log("CURRENT_WRLP_PRICE_WAD:", currentWrlpPrice);
        console.log("TARGET_WRLP_PRICE_WAD:", targetPriceWad);
        console.log("LIQUIDITY:", liquidity);
        console.log("AMOUNT_IN:", uint256(amountIn > 0 ? amountIn : -amountIn));
        console.log("ZERO_FOR_ONE:", zeroForOne ? 1 : 0);
        console.log("DIRECTION:", sellWrlp ? "SELL_WRLP" : "BUY_WRLP");

    }
    
    /// @dev Babylonian square root
    function sqrt(uint256 x) internal pure returns (uint256 y) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }
}
