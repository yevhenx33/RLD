// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title IGhostEngine — Generic Sovereign Spoke Interface
/// @notice Any engine (TWAMM, Limit, CDS trigger) must implement these hooks
///         to participate in the Hub's global ghost netting pipeline.
interface IGhostEngine {
    /// @notice Sync accrual state and return raw ghost balances for aggregation
    function syncAndFetchGhost(bytes32 marketId) external returns (uint256 ghost0, uint256 ghost1);

    /// @notice Hub commands the spoke to apply the results of global ghost netting.
    /// @param marketId The market identifier
    /// @param consumed0 Amount of Token0 ghost consumed (matched by the Hub)
    /// @param consumed1 Amount of Token1 ghost consumed (matched by the Hub)
    /// @param spotPrice Oracle price: Token1 per Token0 (scaled by 1e18)
    function applyNettingResult(
        bytes32 marketId,
        uint256 consumed0,
        uint256 consumed1,
        uint256 spotPrice
    ) external;

    /// @notice Taker intercepts remaining directional ghost after netting.
    /// @param marketId The market identifier
    /// @param zeroForOne Taker swap direction
    /// @param amountIn Taker's remaining input budget
    /// @param spotPrice Oracle price: Token1 per Token0 (scaled by 1e18)
    function takeGhost(
        bytes32 marketId,
        bool zeroForOne,
        uint256 amountIn,
        uint256 spotPrice
    ) external returns (uint256 filledOut, uint256 inputConsumed);
}
