// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @notice Library for interacting with EVM Transient Storage (EIP-1153).
/// @dev Uses assembly to access `tstore` (0x5d) and `tload` (0x5c).
library TransientStorage {
    // OpCodes for EIP-1153
    uint256 constant TSTORE_OPCODE = 0x5d;
    uint256 constant TLOAD_OPCODE = 0x5c;

    /// @notice Stores value at location slot in transient storage.
    /// @param slot The key in transient storage.
    /// @param value The value to store.
    function tstore(bytes32 slot, uint256 value) internal {
        assembly {
            tstore(slot, value)
        }
    }

    /// @notice Loads value from location slot in transient storage.
    /// @param slot The key in transient storage.
    /// @return value The value stored.
    function tload(bytes32 slot) internal view returns (uint256 value) {
        assembly {
            value := tload(slot)
        }
    }
}
