// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title Broker Verifier Interface
/// @notice Used by RLDCore to verify if an address is a trusted Prime Broker.
interface IBrokerVerifier {
    /// @notice Returns true if the account is a valid Broker instance.
    /// @param account The address to check.
    function isValidBroker(address account) external view returns (bool);
}
