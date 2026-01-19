// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ILiquidationModule} from "../../interfaces/ILiquidationModule.sol";
import {IRLDCore} from "../../interfaces/IRLDCore.sol";
import {FixedPointMath} from "../../libraries/FixedPointMath.sol";

contract DutchLiquidationModule is ILiquidationModule {
    using FixedPointMath for uint256;

    /// @notice Calculates Seize Amount using Health-Based Auction.
    /// @dev liquidationParams is packed as:
    ///      [0..15]   Base Discount (bps)
    ///      [16..31]  Max Discount (bps)
    ///      [32..47]  Slope (scaled by 100, e.g. 100 = 1.0x)
    function calculateSeizeAmount(
        uint256 debtToCover,
        uint256 userCollateral,
        uint256 userDebt,
        PriceData calldata priceData, 
        IRLDCore.MarketConfig calldata config,
        bytes32 liquidationParams
    ) external pure override returns (uint256 bonusCollateral, uint256 totalSeized) {
        
        // 1. Unpack Params
        uint256 params = uint256(liquidationParams);
        uint256 baseDiscount = (params & 0xFFFF) * 1e14; // bps -> wad (e.g. 100 -> 0.01e18)
        uint256 maxDiscount = ((params >> 16) & 0xFFFF) * 1e14;
        uint256 slope = ((params >> 32) & 0xFFFF) * 1e16; // 100 -> 1.0e18 (scale 100)

        // 2. Calculate Health Score
        // HS = CollateralValue / (DebtValue * MaintenanceMargin)
        uint256 colVal = userCollateral.mulWad(priceData.spotPrice);
        uint256 debtVal = userDebt.mulWad(priceData.normalizationFactor).mulWad(priceData.indexPrice);
        
        uint256 healthScore = 0;
        if (debtVal > 0) {
            healthScore = colVal.divWad(debtVal.mulWad(uint256(config.maintenanceMargin)));
        }

        // 3. Calculate Bonus Scale (Review Euler Formula)
        // Bonus = Base + Slope * (1 - HS)
        uint256 bonus = baseDiscount;
        if (healthScore < 1e18) {
            uint256 insolvencyDepth = 1e18 - healthScore;
            uint256 dynamicPart = insolvencyDepth.mulWad(slope);
            bonus += dynamicPart;
        }

        // Cap at Max
        if (bonus > maxDiscount) {
            bonus = maxDiscount;
        }

        // 4. Calculate Seize Amount (Same logic as Static)
        uint256 costInUnderlying = debtToCover.mulWad(priceData.normalizationFactor).mulWad(priceData.indexPrice);
        
        // Reward = Cost * (1 + Bonus)
        uint256 rewardValue = costInUnderlying.mulWad(1e18 + bonus);
        totalSeized = rewardValue.divWad(priceData.spotPrice);

        uint256 costInCol = costInUnderlying.divWad(priceData.spotPrice);
        if (totalSeized > costInCol) {
            bonusCollateral = totalSeized - costInCol;
        } else {
            bonusCollateral = 0;
        }
    }
}
