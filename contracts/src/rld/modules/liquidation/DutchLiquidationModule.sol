// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ILiquidationModule} from "../../../shared/interfaces/ILiquidationModule.sol";
import {IRLDCore} from "../../../shared/interfaces/IRLDCore.sol";
import {FixedPointMath} from "../../../shared/libraries/FixedPointMath.sol";

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
    ) external pure override returns (uint256 bonusCollateral, uint256 seizeAmount) {
        uint256 bonus = _calculateBonus(userCollateral, userDebt, priceData, config, liquidationParams);
        return _calculateSeize(debtToCover, bonus, priceData);
    }

    function _calculateBonus(
        uint256 userCollateral,
        uint256 userDebt,
        PriceData calldata priceData, 
        IRLDCore.MarketConfig calldata config,
        bytes32 liquidationParams
    ) internal pure returns (uint256 bonus) {
        uint256 params = uint256(liquidationParams);
        uint256 baseDiscount = (params & 0xFFFF) * 1e14; // bps -> wad (e.g. 100 -> 0.01e18)
        uint256 maxDiscount = ((params >> 16) & 0xFFFF) * 1e14;
        uint256 slope = ((params >> 32) & 0xFFFF) * 1e16; // 100 -> 1.0e18 (scale 100)

        uint256 healthScore = 0;
        if (userDebt > 0) {
            uint256 debtVal = userDebt.mulWad(priceData.normalizationFactor).mulWad(priceData.indexPrice);
            healthScore = userCollateral.mulWad(priceData.spotPrice).divWad(
                debtVal.mulWad(uint256(config.maintenanceMargin))
            );
        }

        bonus = baseDiscount;
        if (healthScore < 1e18) {
            bonus += (1e18 - healthScore).mulWad(slope);
        }

        if (bonus > maxDiscount) {
            bonus = maxDiscount;
        }
    }

    function _calculateSeize(
        uint256 debtToCover,
        uint256 bonus,
        PriceData calldata priceData
    ) internal pure returns (uint256 bonusCollateral, uint256 seizeAmount) {
        uint256 costInUnderlying = debtToCover.mulWad(priceData.normalizationFactor).mulWad(priceData.indexPrice);
        seizeAmount = costInUnderlying.mulWad(1e18 + bonus).divWad(priceData.spotPrice);
        uint256 costInCol = costInUnderlying.divWad(priceData.spotPrice);
        if (seizeAmount > costInCol) {
            bonusCollateral = seizeAmount - costInCol;
        } else {
            bonusCollateral = 0;
        }
    }
}
