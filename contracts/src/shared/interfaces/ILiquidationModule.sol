// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore} from "../interfaces/IRLDCore.sol";

interface ILiquidationModule {
    struct PriceData {
        uint256 indexPrice;
        uint256 spotPrice;
        uint256 normalizationFactor;
    }

    /// @notice Calculates the liquidation outcome.
    /// @param debtToCover The amount of debt the liquidator wants to repay (in Principal).
    /// @param userCollateral The user's total collateral balance.
    /// @param userDebt The user's total debt balance (principal).
    /// @param priceData Struct containing (IndexPrice, SpotPrice, NormFactor).
    /// @param config The market's configuration params.
    /// @param liquidationParams The module-specific params stored in MarketConfig.
    /// @return bonusCollateral The amount of EXTRA collateral to seize (Bonus).
    /// @return seizeAmount The TOTAL collateral to seize (Base Cost + Bonus).
    function calculateSeizeAmount(
        uint256 debtToCover,
        uint256 userCollateral,
        uint256 userDebt,
        PriceData calldata priceData, 
        IRLDCore.MarketConfig calldata config,
        bytes32 liquidationParams
    ) external view returns (uint256 bonusCollateral, uint256 seizeAmount);
}
