// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IPrimeBroker} from "../shared/interfaces/IPrimeBroker.sol";

/// @title  BrokerExecutor — Atomic Multicall with Ephemeral Operator
/// @author RLD Protocol
/// @notice Enables atomic execution of multiple arbitrary calls against
///         any contract, using a one-transaction operator pattern.
contract BrokerExecutor is ReentrancyGuard {
    /// @notice A call to execute
    struct Call {
        address target; // Contract to call
        bytes data; // Encoded function call
    }

    /// @notice Execute multiple calls atomically
    /// @dev Sets executor as operator via signature, executes calls, then revokes
    ///
    /// @param broker The PrimeBroker address (for operator management)
    /// @param ownerSignature EIP-191 signature from broker owner authorizing this execution
    /// @param calls Array of calls to execute (can target any contract)
    function execute(
        address broker,
        bytes calldata ownerSignature,
        Call[] calldata calls
    ) external nonReentrant {
        IPrimeBroker pb = IPrimeBroker(broker);

        // Get current nonce for this executor on this broker.
        uint256 nonce = pb.operatorNonces(address(this));

        // Bind signature to these specific calls via commitment.
        bytes32 callsHash = keccak256(abi.encode(calls));

        // Set self as operator using owner's signature.
        pb.setOperatorWithSignature(
            address(this),
            true,
            ownerSignature,
            nonce,
            callsHash
        );

        // Execute all calls.
        for (uint256 i = 0; i < calls.length; i++) {
            (bool success, bytes memory result) = calls[i].target.call(
                calls[i].data
            );
            if (!success) {
                // Bubble up revert reason.
                if (result.length > 0) {
                    assembly {
                        revert(add(32, result), mload(result))
                    }
                } else {
                    revert("BrokerExecutor: call failed");
                }
            }
        }

        // ALWAYS revoke operator status at the end.
        pb.setOperator(address(this), false);
    }

    /// @notice Generate the message hash that the owner needs to sign
    /// @dev Helper function for clients to generate the correct signature
    ///
    /// @param broker The broker address
    /// @param nonce The current nonce from broker.operatorNonces(executor)
    /// @param callsHash The keccak256 hash of the encoded calls array
    /// @return The keccak256 hash to be signed (before EIP-191 prefix)
    function getMessageHash(
        address broker,
        uint256 nonce,
        bytes32 callsHash
    ) external view returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    address(this), // operator (this executor)
                    true, // active (always granting)
                    broker, // broker address
                    nonce, // nonce
                    address(this), // caller (also this executor)
                    callsHash, // commitment (calls binding)
                    block.chainid // chain ID
                )
            );
    }

    /// @notice Generate the EIP-191 prefixed hash that the owner signs
    /// @dev This is the actual hash that should be signed
    ///
    /// @param broker The broker address
    /// @param nonce The current nonce from broker.operatorNonces(executor)
    /// @param callsHash The keccak256 hash of the encoded calls array
    /// @return The EIP-191 prefixed hash to sign
    function getEthSignedMessageHash(
        address broker,
        uint256 nonce,
        bytes32 callsHash
    ) external view returns (bytes32) {
        bytes32 messageHash = keccak256(
            abi.encode(
                address(this),
                true,
                broker,
                nonce,
                address(this),
                callsHash,
                block.chainid
            )
        );
        return
            keccak256(
                abi.encodePacked(
                    "\x19Ethereum Signed Message:\n32",
                    messageHash
                )
            );
    }
}

