// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title HookMiner
/// @notice Utility to mine CREATE2 salts for hook addresses with specific flag bits.
/// @dev Adapted from v4-periphery HookMiner (removed in newer versions).
library HookMiner {
    /// @notice Find a CREATE2 salt that produces an address with the desired flags.
    /// @param deployer The deployer address (factory)
    /// @param flags The hook permission flags (encoded in bottom 14 bits of address)
    /// @param creationCode The creation code of the hook contract
    /// @param constructorArgs ABI-encoded constructor arguments
    /// @return hookAddress The address that would be created
    /// @return salt The salt to use for CREATE2
    function find(
        address deployer,
        uint160 flags,
        bytes memory creationCode,
        bytes memory constructorArgs
    ) internal pure returns (address hookAddress, bytes32 salt) {
        bytes memory initCode = abi.encodePacked(creationCode, constructorArgs);
        bytes32 initCodeHash = keccak256(initCode);
        uint256 mask = uint256(type(uint160).max >> (160 - 14)); // Bottom 14 bits

        for (uint256 i = 0; i < 10_000; i++) {
            salt = bytes32(i);
            hookAddress = _computeCreate2Address(deployer, salt, initCodeHash);
            if (uint160(hookAddress) & uint160(mask) == flags) {
                return (hookAddress, salt);
            }
        }
        revert("HookMiner: no valid salt found");
    }

    function _computeCreate2Address(
        address deployer,
        bytes32 salt,
        bytes32 initCodeHash
    ) internal pure returns (address) {
        return
            address(
                uint160(
                    uint256(
                        keccak256(
                            abi.encodePacked(
                                bytes1(0xff),
                                deployer,
                                salt,
                                initCodeHash
                            )
                        )
                    )
                )
            );
    }
}
