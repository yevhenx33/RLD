// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IGhostOracle {
    /// @notice Returns the spot price for a given market: Token1 per Token0, scaled by 1e18.
    /// @dev Implementation is oracle-agnostic (Chainlink, Pyth, custom, etc.)
    function getSpotPrice(bytes32 marketId) external view returns (uint256 price);
}
