// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IRLDOracle {
    /// @notice Returns the current Index Price (P = K * r) in 18 decimals (WAD).
    /// @param underlyingPool The address of the underlying protocol pool (e.g., Aave Pool).
    /// @param underlyingToken The address of the asset (e.g., USDC).
    /// @return indexPrice The calculated index price (e.g., 5% -> 5e18 if K=100).
    function getIndexPrice(address underlyingPool, address underlyingToken) external view returns (uint256 indexPrice);
}
