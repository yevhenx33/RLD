// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IFundingModel} from "../../../shared/interfaces/IFundingModel.sol";
import {FixedPointMath} from "../../../shared/libraries/FixedPointMath.sol";
import {IRLDCore, MarketId} from "../../../shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../../shared/interfaces/ISpotOracle.sol";

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
        
        // 1. Fetch Market Addresses
        IRLDCore.MarketAddresses memory addresses = IRLDCore(core).getMarketAddresses(MarketId.wrap(marketId));
        
        // 2. Query Oracles
        address priceBase = addresses.positionToken != address(0) ? addresses.positionToken : addresses.collateralToken;
        
        uint256 markPrice = ISpotOracle(addresses.markOracle).getSpotPrice(
            priceBase,
            addresses.underlyingToken
        );
        
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool,
            addresses.underlyingToken
        );
        
        if (indexPrice == 0 || markPrice == 0) return (currentNormalizationFactor, 0);

        // 3. Calculate Normalized Funding Rate
        // Rate = (Mark - Index) / Index
        int256 priceDiff = int256(markPrice) - int256(indexPrice);
        int256 baseRate = (priceDiff * 1e18) / int256(indexPrice); // Instantaneous deviation
        
        // 4. Time Delta
        uint256 dt = block.timestamp - lastUpdateTimestamp;
        uint256 fundingPeriod = 30 days; // Standard Period
        
        // 5. Exponential Scaling
        // Coeff = -1 * Rate * (dt / Period)
        // Note: Sign is INVERTED. Mark > Index => Debt DECREASES.
        int256 exponent = -baseRate * int256(dt) / int256(fundingPeriod);
        
        // 6. Apply Change
        // NewFactor = OldFactor * Exp(Exponent)
        int256 multiplier = FixedPointMath.expWad(exponent);
        
        newNormalizationFactor = uint256(currentNormalizationFactor).mulWadDown(uint256(multiplier));
        
        fundingRate = baseRate; // Just report deviation
    }
}
