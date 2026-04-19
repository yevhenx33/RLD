// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IFundingModel} from "../../../shared/interfaces/IFundingModel.sol";
import {FixedPointMathLib} from "../../../shared/utils/FixedPointMathLib.sol";
import {IRLDCore, MarketId} from "../../../shared/interfaces/IRLDCore.sol";

/// @title CDSDecayFundingModel
/// @notice Implements continuous absolute rate decay for CDS markets.
/// @dev Normalization Factor *= exp(-F * dt). Unlike Mark-Index divergence models,
///      this applies a mathematically constant depreciation parameter independent of spot price.
contract CDSDecayFundingModel is IFundingModel {

    /* ============================================================================================ */
    /*                                          CONSTANTS                                          */
    /* ============================================================================================ */

    uint256 public constant SECONDS_PER_YEAR = 31536000;

    /* ============================================================================================ */
    /*                                            ERRORS                                           */
    /* ============================================================================================ */

    /// @notice Thrown when the F parameter is physically unsupported
    error InvalidDecayParameter();
    
    /// @notice Thrown when expWad returns a non-positive value
    error InvalidExponentialResult();

    /* ============================================================================================ */
    /*                                      EXTERNAL FUNCTIONS                                     */
    /* ============================================================================================ */

    /// @notice Calculates deterministically decayed NF using per-market `decayRateWad`.
    /// @param marketId The market identifier
    /// @param core The RLDCore contract address
    /// @param currentNormalizationFactor The current normalization factor (WAD)
    /// @param lastUpdateTimestamp The timestamp of the last funding update
    /// @return newNormalizationFactor The updated normalization factor (WAD)
    /// @return fundingRate The instantaneous rate returning int256 F
    function calculateFunding(
        bytes32 marketId, 
        address core,
        uint256 currentNormalizationFactor, 
        uint48 lastUpdateTimestamp
    ) external view returns (uint256 newNormalizationFactor, int256 fundingRate) {
        
        uint256 dt = block.timestamp - lastUpdateTimestamp;
        
        // Short-circuit to save gas if called multiple times in same block
        if (dt == 0) {
            return (currentNormalizationFactor, 0);
        }
        
        // 1. Fetch market configuration
        IRLDCore.MarketConfig memory config = IRLDCore(core).getMarketConfig(MarketId.wrap(marketId));
        uint256 F = uint256(config.decayRateWad);

        // Ensure decay parameter is explicitly configured for CDS markets.
        if (F == 0) revert InvalidDecayParameter();

        // 2. Continuous State Decay (The Physics)
        // exponent = -F * dt / seconds_per_year
        // F is in WAD, dt is in seconds.
        int256 exponent = -int256((F * dt) / SECONDS_PER_YEAR);
        
        // expWad evaluates the mathematical exponent exactly as Fuzzed in python
        int256 multiplier = FixedPointMathLib.expWad(exponent);
        
        // 3. Safety checks (Mathematical collapse prevention)
        if (multiplier < 0) {
            revert InvalidExponentialResult();
        }
        
        newNormalizationFactor = FixedPointMathLib.mulWad(
            currentNormalizationFactor, 
            uint256(multiplier)
        );
        
        // We technically return F as the "funding rate" for upstream protocol accounting
        fundingRate = int256(F);
    }
}
