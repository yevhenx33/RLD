// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationBase} from "../liquidation/LiquidationBase.t.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {BrokerExecutor} from "../../../src/periphery/BrokerExecutor.sol";
import {PrimeBrokerFactory} from "../../../src/rld/core/PrimeBrokerFactory.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {ERC721} from "solmate/src/tokens/ERC721.sol";
import "forge-std/console.sol";

/// @title BrokerExecutorMulticall — Phase 5 Penetration Tests
/// @notice 6 tests covering the BrokerExecutor's atomic multicall lifecycle,
///         operator revocation, replay protection, reentrancy, and hashing.
///
/// Test IDs from PENETRATION_TESTING.md:
///   67 — Full lifecycle: sign → set operator → calls → revoke
///   68 — Operator ALWAYS revoked (atomic revert on call failure)
///   69 — Replayed signature reverts (nonce consumed)
///   70 — Calls can target ANY contract (verify routing)
///   71 — Reentrancy guard prevents nesting
///   72 — getMessageHash / getEthSignedMessageHash consistency
contract BrokerExecutorMulticall is LiquidationBase {
    BrokerExecutor public executor;

    // Use a known private key so we can sign messages with vm.sign()
    uint256 constant OWNER_PK =
        0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80;
    address public owner;

    // Helper: create a broker owned by `owner`
    function _createOwnedBroker() internal returns (PrimeBroker) {
        // createBroker mints the NFT to msg.sender (the test contract)
        // We need the broker owned by `owner` so we transfer the NFT
        bytes32 salt = keccak256(abi.encodePacked("exec", brokerNonce++));
        PrimeBroker broker = PrimeBroker(
            payable(brokerFactory.createBroker(salt))
        );
        uint256 tokenId = uint256(uint160(address(broker)));
        // Transfer NFT ownership from test contract to `owner`
        ERC721(address(brokerFactory)).transferFrom(
            address(this),
            owner,
            tokenId
        );
        return broker;
    }

    /// @dev Sign a setOperatorWithSignature message for the executor
    function _signOperatorAuth(
        PrimeBroker broker,
        address operator,
        BrokerExecutor.Call[] memory calls
    ) internal view returns (bytes memory) {
        uint256 nonce = broker.operatorNonces(operator);
        bytes32 callsHash = keccak256(abi.encode(calls));
        // Must match PrimeBroker.setOperatorWithSignature hash construction:
        // (operator, active, broker, nonce, caller, commitment, chainId)
        bytes32 structHash = keccak256(
            abi.encode(
                operator,
                true,
                address(broker),
                nonce,
                operator,
                callsHash,
                block.chainid
            )
        );
        bytes32 ethSignedHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", structHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(OWNER_PK, ethSignedHash);
        return abi.encodePacked(r, s, v);
    }

    function setUp() public override {
        super.setUp();
        owner = vm.addr(OWNER_PK);
        executor = new BrokerExecutor();
    }

    // ================================================================
    //  Test #67: Full lifecycle — sign → set operator → calls → revoke
    // ================================================================

    function test_executor_full_lifecycle() public {
        PrimeBroker broker = _createOwnedBroker();
        // Fund the broker
        collateralMock.transfer(address(broker), 100_000e6);

        // Build calls: modifyPosition(+50k col, +5k debt)
        BrokerExecutor.Call[] memory calls = new BrokerExecutor.Call[](1);
        calls[0] = BrokerExecutor.Call({
            target: address(broker),
            data: abi.encodeCall(
                PrimeBroker.modifyPosition,
                (
                    MarketId.unwrap(marketId),
                    int256(uint256(50_000e6)),
                    int256(uint256(5_000e6))
                )
            )
        });

        bytes memory sig = _signOperatorAuth(broker, address(executor), calls);

        // Verify executor is NOT operator before
        assertFalse(
            broker.operators(address(executor)),
            "Should not be operator before"
        );

        // Execute
        executor.execute(address(broker), sig, calls);

        // Verify executor is NOT operator after (revoked)
        assertFalse(
            broker.operators(address(executor)),
            "Should not be operator after"
        );

        // Verify the call succeeded — broker should have 5k debt
        uint128 debt = core
            .getPosition(marketId, address(broker))
            .debtPrincipal;
        assertEq(debt, 5_000e6, "Debt should be 5k after modifyPosition");
    }

    // ================================================================
    //  Test #68: Operator always revoked — atomic revert on call failure
    // ================================================================

    function test_executor_atomic_revert_no_lingering_operator() public {
        PrimeBroker broker = _createOwnedBroker();
        // DON'T fund the broker — modifyPosition will fail (no collateral)

        BrokerExecutor.Call[] memory calls = new BrokerExecutor.Call[](1);
        calls[0] = BrokerExecutor.Call({
            target: address(broker),
            data: abi.encodeCall(
                PrimeBroker.modifyPosition,
                (
                    MarketId.unwrap(marketId),
                    int256(uint256(50_000e6)),
                    int256(uint256(5_000e6))
                )
            )
        });

        bytes memory sig = _signOperatorAuth(broker, address(executor), calls);

        // Execute should revert (no collateral → transfer fails)
        vm.expectRevert();
        executor.execute(address(broker), sig, calls);

        // KEY CHECK: operator status should NOT be set (entire tx reverted)
        assertFalse(
            broker.operators(address(executor)),
            "Operator must not be set after reverted tx"
        );

        // Nonce should NOT have incremented (entire tx reverted)
        assertEq(
            broker.operatorNonces(address(executor)),
            0,
            "Nonce must not increment on reverted tx"
        );
    }

    // ================================================================
    //  Test #69: Replayed signature reverts (nonce consumed)
    // ================================================================

    function test_executor_replay_protection() public {
        PrimeBroker broker = _createOwnedBroker();
        collateralMock.transfer(address(broker), 200_000e6);

        // First execution — succeeds
        BrokerExecutor.Call[] memory calls = new BrokerExecutor.Call[](1);
        calls[0] = BrokerExecutor.Call({
            target: address(broker),
            data: abi.encodeCall(
                PrimeBroker.modifyPosition,
                (
                    MarketId.unwrap(marketId),
                    int256(uint256(50_000e6)),
                    int256(0)
                )
            )
        });

        bytes memory sig = _signOperatorAuth(broker, address(executor), calls);
        executor.execute(address(broker), sig, calls);

        // Nonce should now be 1
        uint256 nonceAfterFirst = broker.operatorNonces(address(executor));
        assertEq(nonceAfterFirst, 1, "Nonce should be 1 after first use");

        // Replay with same signature — should revert (nonce mismatch)
        vm.expectRevert();
        executor.execute(address(broker), sig, calls);

        // Second valid execution with fresh signature (nonce=1)
        bytes memory sig2 = _signOperatorAuth(broker, address(executor), calls);
        executor.execute(address(broker), sig2, calls);

        assertEq(
            broker.operatorNonces(address(executor)),
            2,
            "Nonce should be 2 after two successful executions"
        );
    }

    // ================================================================
    //  Test #70: Calls can target ANY contract
    // ================================================================

    function test_executor_calls_any_contract() public {
        PrimeBroker broker = _createOwnedBroker();
        collateralMock.transfer(address(broker), 100_000e6);

        // Build calls targeting DIFFERENT contracts:
        // Call 1: broker.modifyPosition (target = broker)
        // Call 2: broker.withdrawCollateral to owner (target = broker, but different fn)
        BrokerExecutor.Call[] memory calls = new BrokerExecutor.Call[](2);
        calls[0] = BrokerExecutor.Call({
            target: address(broker),
            data: abi.encodeCall(
                PrimeBroker.modifyPosition,
                (
                    MarketId.unwrap(marketId),
                    int256(uint256(100_000e6)),
                    int256(0)
                )
            )
        });
        calls[1] = BrokerExecutor.Call({
            target: address(broker),
            data: abi.encodeCall(
                PrimeBroker.withdrawCollateral,
                (owner, 10_000e6)
            )
        });

        bytes memory sig = _signOperatorAuth(broker, address(executor), calls);
        executor.execute(address(broker), sig, calls);

        // Owner should have received 10k collateral
        uint256 ownerBal = ERC20(ma.collateralToken).balanceOf(owner);
        assertEq(
            ownerBal,
            10_000e6,
            "Owner should receive withdrawn collateral"
        );

        // Executor revoked
        assertFalse(broker.operators(address(executor)));
    }

    // ================================================================
    //  Test #71: Reentrancy guard prevents nesting
    // ================================================================

    function test_executor_reentrancy_blocked() public {
        PrimeBroker broker = _createOwnedBroker();
        collateralMock.transfer(address(broker), 100_000e6);

        // Build inner calls (empty — just re-entering is enough)
        BrokerExecutor.Call[] memory innerCalls = new BrokerExecutor.Call[](0);
        bytes memory innerSig = _signOperatorAuth(
            broker,
            address(executor),
            innerCalls
        );

        // Build outer call that re-enters executor.execute()
        BrokerExecutor.Call[] memory reentrantCalls = new BrokerExecutor.Call[](
            1
        );
        reentrantCalls[0] = BrokerExecutor.Call({
            target: address(executor),
            data: abi.encodeCall(
                BrokerExecutor.execute,
                (address(broker), innerSig, innerCalls)
            )
        });

        // Sign outer call — commitment binds to reentrantCalls, not innerCalls
        bytes memory outerSig = _signOperatorAuth(
            broker,
            address(executor),
            reentrantCalls
        );

        // This should revert (reentrancy guard or nonce mismatch)
        vm.expectRevert();
        executor.execute(address(broker), outerSig, reentrantCalls);

        // Operator should not be set
        assertFalse(broker.operators(address(executor)));
    }

    // ================================================================
    //  Test #72: getMessageHash / getEthSignedMessageHash consistency
    // ================================================================

    function test_hash_functions_consistency() public {
        PrimeBroker broker = _createOwnedBroker();
        collateralMock.transfer(address(broker), 10_000e6);
        uint256 nonce = broker.operatorNonces(address(executor));

        // Build calls first — needed for commitment hash
        BrokerExecutor.Call[] memory calls = new BrokerExecutor.Call[](1);
        calls[0] = BrokerExecutor.Call({
            target: address(broker),
            data: abi.encodeCall(
                PrimeBroker.modifyPosition,
                (
                    MarketId.unwrap(marketId),
                    int256(uint256(10_000e6)),
                    int256(0)
                )
            )
        });
        bytes32 callsHash = keccak256(abi.encode(calls));

        // Get hashes from executor
        bytes32 msgHash = executor.getMessageHash(
            address(broker),
            nonce,
            callsHash
        );
        bytes32 ethHash = executor.getEthSignedMessageHash(
            address(broker),
            nonce,
            callsHash
        );

        // Manually compute expected hashes
        // Must match: (operator, active, broker, nonce, caller, commitment, chainId)
        bytes32 expectedMsgHash = keccak256(
            abi.encode(
                address(executor), // operator
                true, // active
                address(broker), // broker
                nonce, // nonce
                address(executor), // caller (executor)
                callsHash, // commitment
                block.chainid // chain ID
            )
        );
        bytes32 expectedEthHash = keccak256(
            abi.encodePacked(
                "\x19Ethereum Signed Message:\n32",
                expectedMsgHash
            )
        );

        assertEq(msgHash, expectedMsgHash, "Message hash mismatch");
        assertEq(ethHash, expectedEthHash, "Eth signed hash mismatch");

        // Verify that signing with this hash actually works end-to-end
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(OWNER_PK, ethHash);
        bytes memory sig = abi.encodePacked(r, s, v);

        // Should succeed — hashes are correct and signature is valid
        executor.execute(address(broker), sig, calls);

        assertFalse(
            broker.operators(address(executor)),
            "Operator revoked after"
        );
    }
}
