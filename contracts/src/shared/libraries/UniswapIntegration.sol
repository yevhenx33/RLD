// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {FixedPointMath} from "./FixedPointMath.sol";

/// @notice Library for interacting with Uniswap V4.
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolId} from "@uniswap/v4-core/src/types/PoolId.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {ITWAMM} from "../../twamm/ITWAMM.sol";

/// @notice Library for interacting with Uniswap V4.
library UniswapIntegration {
    using FixedPointMath for uint256;
    using StateLibrary for IPoolManager;

    /// @notice Gets the TWAP from a Uniswap V4 Pool.
    /// @dev Uses `oracle` (e.g. TWAMM hook) to fetch geometric mean tick.
    /// @param poolManager The Uniswap V4 PoolManager address.
    /// @param oracle The Observer Contract (e.g. TWAMM Hook) that has historical data.
    /// @param poolId The Pool ID.
    /// @param secondsAgo The TWAP period in seconds. If 0, returns spot price from standard Slot0.
    /// @return price The price (WAD).
    function getTWAP(address poolManager, address oracle, bytes32 poolId, uint32 secondsAgo) internal view returns (uint256 price) {
        if (secondsAgo == 0) {
            // Return current spot price if secondsAgo is 0
            (uint160 sqrtPrice,,,) = IPoolManager(poolManager).getSlot0(PoolId.wrap(poolId));
            uint256 ratio = uint256(sqrtPrice) * sqrtPrice;
            return FullMath.mulDiv(ratio, 1e18, 1 << 192);
        }

        uint32[] memory secondsAgos = new uint32[](2);
        secondsAgos[0] = secondsAgo;
        secondsAgos[1] = 0;

        // Use standard observe from the Oracle/Hook
        int56[] memory tickCumulatives = ITWAMM(oracle).observe(
            PoolId.wrap(poolId), 
            secondsAgos
        );
        
        int56 tickCumulativesDelta = tickCumulatives[1] - tickCumulatives[0];
        
        // Calculate arithmetic mean tick
        int24 arithmeticMeanTick = int24(tickCumulativesDelta / int56(uint56(secondsAgo)));
        
        // Adjust round down for negative ticks
        if (tickCumulativesDelta < 0 && (tickCumulativesDelta % int56(uint56(secondsAgo)) != 0)) {
             arithmeticMeanTick--;
        }

        uint160 sqrtPriceX96 = TickMath.getSqrtPriceAtTick(arithmeticMeanTick);
        
        // Convert to Price (WAD)
        uint256 priceRatioX192 = uint256(sqrtPriceX96) * sqrtPriceX96;
        return FullMath.mulDiv(priceRatioX192, 1e18, 1 << 192);
    }
}
