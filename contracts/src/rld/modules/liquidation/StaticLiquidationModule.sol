// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ILiquidationModule} from "../../../shared/interfaces/ILiquidationModule.sol";
import {IRLDCore} from "../../../shared/interfaces/IRLDCore.sol";
import {FixedPointMath} from "../../../shared/libraries/FixedPointMath.sol";

contract StaticLiquidationModule is ILiquidationModule {
    using FixedPointMath for uint256;

    /// @notice Calculates Seize Amount using a fixed bonus.
    /// @dev liquidationParams is interpreted as `uint256 liquidationBonus` packed in bytes32.
    function calculateSeizeAmount(
        uint256 debtToCover,
        uint256, /* userCollateral */
        uint256, /* userDebt */
        PriceData calldata priceData, 
        IRLDCore.MarketConfig calldata config,
        bytes32 liquidationParams
    ) external pure override returns (uint256 bonusCollateral, uint256 totalSeized) {
        
        // 1. Unpack Params
        uint256 liquidationBonus = uint256(liquidationParams);
        if (liquidationBonus == 0) liquidationBonus = 1e18; // Default to 1.0 if not set? Or assume valid.

        // 2. Calculate Cost in Underlying
        // Cost = Principal * NormFactor * IndexPrice
        uint256 costInUnderlying = debtToCover.mulWad(priceData.normalizationFactor).mulWad(priceData.indexPrice);

        // 3. Calculate Reward Value (Cost * Bonus)
        uint256 rewardValue = costInUnderlying.mulWad(liquidationBonus);

        // 4. Calculate Total Collateral to Seize (Reward / SpotPrice)
        totalSeized = rewardValue.divWad(priceData.spotPrice);

        // 5. Calculate Bonus Part (Total - CostInCollateral)
        // CostInCol = Cost / Spot
        uint256 costInCol = costInUnderlying.divWad(priceData.spotPrice);
        
        if (totalSeized > costInCol) {
            bonusCollateral = totalSeized - costInCol;
        } else {
            bonusCollateral = 0;
        }
    }
}
