// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IFundingModel} from "../../../shared/interfaces/IFundingModel.sol";
import {FixedPointMathLib} from "solady/utils/FixedPointMathLib.sol";
import {IRLDCore, MarketId} from "../../../shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../../shared/interfaces/ISpotOracle.sol";

/// @title StandardFundingModel
/// @notice Implements continuous funding using exponential normalization factor decay.
/// @dev Funding Rate = (Mark - Index) / Index. NormFactor *= exp(-Rate * dt / Period).
///      Uses Solady's battle-tested expWad for maximum precision.
contract StandardFundingModel is IFundingModel {

    /* ============================================================================================ */
    /*                                          CONSTANTS                                          */
    /* ============================================================================================ */

    /// @notice Default funding period if not configured
    uint256 public constant DEFAULT_FUNDING_PERIOD = 30 days;

    /* ============================================================================================ */
    /*                                            ERRORS                                           */
    /* ============================================================================================ */

    /// @notice Thrown when mark price is zero
    error ZeroMarkPrice();

    /// @notice Thrown when index price is zero
    error ZeroIndexPrice();

    /// @notice Thrown when expWad returns a non-positive value
    error InvalidExponentialResult();

    /* ============================================================================================ */
    /*                                            EVENTS                                           */
    /* ============================================================================================ */

    /// @notice Emitted when funding is applied to a market
    /// @param marketId The market identifier
    /// @param oldNormFactor Previous normalization factor
    /// @param newNormFactor Updated normalization factor
    /// @param fundingRate The calculated funding rate (Mark - Index) / Index
    /// @param timeDelta Seconds since last update
    event FundingApplied(
        bytes32 indexed marketId,
        uint256 oldNormFactor,
        uint256 newNormFactor,
        int256 fundingRate,
        uint256 timeDelta
    );

    /* ============================================================================================ */
    /*                                      EXTERNAL FUNCTIONS                                     */
    /* ============================================================================================ */

    /// @notice Calculates the updated normalization factor based on mark-index divergence.
    /// @dev Formula: NewNF = OldNF × exp(-FundingRate × dt / fundingPeriod)
    ///      Where FundingRate = (Mark - Index) / Index
    ///      Positive rate (Mark > Index) → NF decreases → shorts earn
    ///      Negative rate (Mark < Index) → NF increases → longs earn
    /// @param marketId The market identifier
    /// @param core The RLDCore contract address
    /// @param currentNormalizationFactor The current normalization factor (WAD)
    /// @param lastUpdateTimestamp The timestamp of the last funding update
    /// @return newNormalizationFactor The updated normalization factor (WAD)
    /// @return fundingRate The instantaneous funding rate (WAD)
    function calculateFunding(
        bytes32 marketId, 
        address core,
        uint256 currentNormalizationFactor, 
        uint48 lastUpdateTimestamp
    ) external view returns (uint256 newNormalizationFactor, int256 fundingRate) {
        // Early return if no time has passed
        if (block.timestamp == lastUpdateTimestamp) {
            return (currentNormalizationFactor, 0);
        }
        
        // 1. Fetch market configuration
        IRLDCore.MarketAddresses memory addresses = IRLDCore(core).getMarketAddresses(MarketId.wrap(marketId));
        
        // 2. Query oracles for mark and index prices
        (uint256 markPrice, uint256 indexPrice) = _fetchPrices(addresses);
        
        // 3. Fetch funding period from config
        IRLDCore.MarketConfig memory config = IRLDCore(core).getMarketConfig(MarketId.wrap(marketId));
        uint256 fundingPeriod = config.fundingPeriod;
        if (fundingPeriod == 0) {
            fundingPeriod = DEFAULT_FUNDING_PERIOD;
        }

        // 4. Calculate funding rate: (Mark - Index) / Index
        int256 priceDiff = int256(markPrice) - int256(indexPrice);
        fundingRate = (priceDiff * 1e18) / int256(indexPrice);
        
        // 5. Calculate time delta
        uint256 dt = block.timestamp - lastUpdateTimestamp;
        
        // 6. Calculate exponent for exponential decay
        // exponent = -fundingRate × dt / fundingPeriod
        // Sign inverted: Mark > Index → debt decreases (shorts earn)
        int256 exponent = (-fundingRate * int256(dt)) / int256(fundingPeriod);
        
        // 7. Apply exponential using Solady
        int256 multiplier = FixedPointMathLib.expWad(exponent);
        
        // 8. Safety check: expWad should never return negative
        // Note: expWad CAN return 0 for extreme negative exponents (underflow saturation)
        // This is mathematically correct (e^(-inf) -> 0) and acceptable behavior
        if (multiplier < 0) {
            revert InvalidExponentialResult();
        }
        
        // 9. Apply multiplier to normalization factor
        newNormalizationFactor = FixedPointMathLib.mulWad(currentNormalizationFactor, uint256(multiplier));
        
        // Note: Event emission would happen in RLDCore._applyFunding, not here (view function)
        // The FundingApplied event is defined for use by the caller
    }

    /* ============================================================================================ */
    /*                                     INTERNAL FUNCTIONS                                      */
    /* ============================================================================================ */

    /// @notice Fetches mark and index prices from oracles with validation
    /// @dev Reverts on zero prices
    function _fetchPrices(
        IRLDCore.MarketAddresses memory addresses
    ) internal view returns (uint256 markPrice, uint256 indexPrice) {
        
        // Query mark price (TWAP from V4 pool)
        markPrice = ISpotOracle(addresses.markOracle).getSpotPrice(
            addresses.positionToken,
            addresses.underlyingToken
        );
        
        // Query index price (from rate oracle, e.g., Aave)
        indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool,
            addresses.underlyingToken
        );
        
        // Explicit revert on zero prices
        if (markPrice == 0) revert ZeroMarkPrice();
        if (indexPrice == 0) revert ZeroIndexPrice();
    }
}
