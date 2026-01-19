// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IFundingModel {
    /// @notice Calculates the Funding Rate and the new Normalization Factor.
    /// @param markPrice The current Mark Price (from Uniswap V4).
    /// @param indexPrice The current Index Price (from Rate Oracle).
    /// @param currentNormalizationFactor The existing normalization factor.
    /// @param lastUpdateTimestamp The last time funding was applied.
    /// @return newNormalizationFactor The updated factor.
    /// @return fundingRate The instantaneous annualized funding rate (for logging).
    function calculateFunding(
        uint256 markPrice, 
        uint256 indexPrice, 
        uint256 currentNormalizationFactor, 
        uint48 lastUpdateTimestamp
    ) external view returns (uint256 newNormalizationFactor, int256 fundingRate);
}
