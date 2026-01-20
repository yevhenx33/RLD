// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IFundingModel} from "../../interfaces/IFundingModel.sol";
import {FixedPointMath} from "../../libraries/FixedPointMath.sol";
import {IRLDCore, MarketId} from "../../interfaces/IRLDCore.sol";
import {IRLDOracle} from "../../interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../interfaces/ISpotOracle.sol";

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
        bytes32 marketId, 
        address core,
        uint256 currentNormalizationFactor, 
        uint48 lastUpdateTimestamp
    ) external view returns (uint256 newNormalizationFactor, int256 fundingRate) {
        if (block.timestamp == lastUpdateTimestamp) {
            return (currentNormalizationFactor, 0);
        }
        
        // 1. Fetch Market Addresses from Core
        IRLDCore.MarketAddresses memory addresses = IRLDCore(core).getMarketAddresses(MarketId.wrap(marketId));
        
        // 2. Query Oracles
        
        // Mark Price (Base = PositionToken if exists, else Collateral)
        address priceBase = addresses.positionToken != address(0) ? addresses.positionToken : addresses.collateralToken;
        
        uint256 markPrice = ISpotOracle(addresses.markOracle).getSpotPrice(
            priceBase,
            addresses.underlyingToken
        );
        
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool,
            addresses.underlyingToken
        );
        
        // 3. Calculate Time Delta
        uint256 dt = block.timestamp - lastUpdateTimestamp;
        
        // 4. Safety Checks
        if (indexPrice == 0 || markPrice == 0) return (currentNormalizationFactor, 0);

        // 5. Calculate Funding Rate
        // RatePerSecond = ((Mark - Index) / Index) / 1 days
        int256 priceDiff = int256(markPrice) - int256(indexPrice);
        
        int256 ratePerSecond = (priceDiff * 1e18) / int256(indexPrice);
        ratePerSecond = ratePerSecond / 1 days; 

        // 6. Update Normalization Factor
        int256 factorChange = (int256(currentNormalizationFactor) * ratePerSecond * int256(dt)) / 1e18;
        int256 newFactorInt = int256(currentNormalizationFactor) + factorChange;
        
        if (newFactorInt < 0) newNormalizationFactor = 0; 
        else newNormalizationFactor = uint256(newFactorInt);
        
        fundingRate = ratePerSecond * 365 days; // Return APY
    }
}
