// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IDefaultOracle {
    /// @notice Checks if the underlying protocol is in a Default State.
    /// @dev Conditions: Utilization > 99% AND Rates > 80% (Time-Weighted or Sustained).
    /// @param underlyingPool The pool to check.
    /// @param underlyingToken The asset to check.
    /// @return isDefaulted True if the protocol is failing.
    function isDefaulted(address underlyingPool, address underlyingToken, bytes32 params) external view returns (bool isDefaulted);
}
