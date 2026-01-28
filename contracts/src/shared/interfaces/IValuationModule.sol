// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title Valuation Module Interface
/// @author RLD Protocol
/// @notice Interface for read-only asset valuation modules used by PrimeBroker.
/// @dev This is a subset of IBrokerModule - for modules that only need getValue().
///
/// ## When to Use
///
/// Use `IValuationModule` instead of `IBrokerModule` when:
/// - The module only provides valuation (no seize logic)
/// - Seize logic is handled directly by PrimeBroker
///
/// ## Example Implementations
///
/// - `TwammBrokerModule` - TWAMM order valuation (seize handled by PrimeBroker)
///
interface IValuationModule {
    /// @notice Calculates the current value of an asset
    /// @param data ABI-encoded parameters specific to the module
    /// @return The asset value in the market's denomination (e.g., underlyingToken)
    function getValue(bytes calldata data) external view returns (uint256);
}
