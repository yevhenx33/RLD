// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IFundingModel {
    /// @notice Calculates the Funding Rate and the new Normalization Factor.
    /// @param marketId The Market ID to query configs for.
    /// @param core The RLD Core address to fetch addresses from.
    /// @param currentNormalizationFactor The existing normalization factor.
    /// @param lastUpdateTimestamp The last time funding was applied.
    /// @return newNormalizationFactor The updated factor.
    /// @return fundingRate The instantaneous annualized funding rate (for logging).
    function calculateFunding(
        bytes32 marketId, 
        address core,
        uint256 currentNormalizationFactor, 
        uint48 lastUpdateTimestamp
    ) external view returns (uint256 newNormalizationFactor, int256 fundingRate);
}
