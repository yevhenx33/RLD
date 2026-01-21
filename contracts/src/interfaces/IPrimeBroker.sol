// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title Prime Broker Interface
/// @notice Interface for the "Smart Margin Account" that holds assets.
interface IPrimeBroker {
    /// @notice Returns the total Net Asset Value of the account in Underlying terms.
    /// @dev Used by RLDCore for solvency checks.
    function getNetAccountValue() external view returns (uint256);

    /// @notice Seizes assets from the account to pay a Liquidator.
    /// @dev Only callable by RLDCore during liquidation.
    /// @param value The value (in Underlying terms) to seize.
    /// @param recipient The liquidator address to receive the seized assets.
    function seize(uint256 value, address recipient) external;
    
    /// @notice Emitted when a generic execution is performed.
    event Execute(address indexed target, bytes data);
}
