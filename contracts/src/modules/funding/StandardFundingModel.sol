// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IFundingModel} from "../../interfaces/IFundingModel.sol";
import {FixedPointMath} from "../../libraries/FixedPointMath.sol";

/// @title StandardFundingModel
/// @notice Implements the "Power Perp" style funding: Funding = Mark - Index.
/// @dev Updates the Normalization Factor over time.
contract StandardFundingModel is IFundingModel {
    using FixedPointMath for uint256;
    using FixedPointMath for int256;

    /// @notice Calculates continuous funding.
    /// @dev Funding Payment = (Mark - Index) * PositionSize * Time
    ///      Here we update Normalization Factor directly.
    ///      Rate = (Mark - Index) / Index  (Approximate, for 1h funding?)
    ///      RLD Whitepaper: "Funding accumulates continuously."
    ///      F = (Mark - Index).
    ///      NormFactor_new = NormFactor_old * (1 + F/Index * dt)?
    ///      Better: Squeeth Model -> NormFactor *= Exp(FundingRate * time)
    ///      Where FundingRate = (Mark - Index) / Index? Or just (Mark - Index)?
    ///      RLD: "If Mark > Index, Longs pay Shorts." 
    ///      Wait, RLD is a perp on rate. Price is K * r.
    ///      Let's used Standard Power Perp logic: Log returns?
    ///      Simplified: 
    ///      Funding Rate per SECOND = (Mark - Index) / Index / FundingPeriod?
    ///      Let's try simpler:
    ///      FundingRate = (Mark - Index) / Index. (Instantaneous)
    ///      Factor *= (1 + FundingRate * dt)
    function calculateFunding(
        uint256 markPrice, 
        uint256 indexPrice, 
        uint256 currentNormalizationFactor, 
        uint48 lastUpdateTimestamp
    ) external view returns (uint256 newNormalizationFactor, int256 fundingRate) {
        if (block.timestamp == lastUpdateTimestamp) {
            return (currentNormalizationFactor, 0);
        }
        
        // 1. Calculate Time Delta
        uint256 dt = block.timestamp - lastUpdateTimestamp;
        
        // 2. Safety Checks
        if (indexPrice == 0) return (currentNormalizationFactor, 0);
        
        // NOTE: If MarkPrice is 0 (e.g. core passed 0), we assume no funding or we need fallback.
        // For now, if mark is 0, return same.
        if (markPrice == 0) return (currentNormalizationFactor, 0);

        // 3. Calculate Normalized Funding Rate (Annualized?)
        // Let's assume Funding transfers purely based on price diff per day?
        // Standard: Rate = (Mark - Index) / Index (This is % diff)
        // Scaled by time.
        // Funding Period = 1 day? (86400)
        // RatePerSecond = ((Mark - Index) / Index) / 1 days?
        
        int256 priceDiff = int256(markPrice) - int256(indexPrice);
        
        // FundingRatePerSecond (WAD)
        // = (priceDiff * 1e18 / indexPrice) / 86400
        int256 ratePerSecond = (priceDiff * 1e18) / int256(indexPrice);
        ratePerSecond = ratePerSecond / 1 days; 

        // 4. Update Normalization Factor
        // New = Old * (1 + Rate * dt)
        // Use WAD math
        int256 factorChange = (int256(currentNormalizationFactor) * ratePerSecond * int256(dt)) / 1e18;
        
        int256 newFactorInt = int256(currentNormalizationFactor) + factorChange;
        
        if (newFactorInt < 0) newNormalizationFactor = 0; // Should not happen easily
        else newNormalizationFactor = uint256(newFactorInt);
        
        fundingRate = ratePerSecond * 365 days; // Return APY for logs
    }
}
