// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title Broker Module Interface
/// @notice Logic for pricing and seizing complex assets (V4 Positions, TWAMM Orders).
interface IBrokerModule {
    /// @notice Returns the value of an asset in Underlying terms.
    /// @param data Encoded asset identifier (e.g. TokenID or OrderID).
    function getValue(bytes calldata data) external view returns (uint256);

    /// @notice Seizes a portion of the asset and transfers proceeds to the recipient.
    /// @param amount The value to seize.
    /// @param recipient The receiver of the seized assets.
    /// @param data Encoded asset identifier.
    /// @return seizedValue The actual value seized.
    function seize(uint256 amount, address recipient, bytes calldata data) external returns (uint256 seizedValue);
}
