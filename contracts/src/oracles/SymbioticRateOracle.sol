// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../libs/Ownable08.sol"; // Local Modern Ownable
import "../libs/SymbioticUtils.sol"; // Local ECDSA Utils
import "./RLDAaveOracle.sol";

/**
 * @title SymbioticRateOracle
 * @notice A 'Restaked' Oracle that validates off-chain TWAR against on-chain Spot.
 */
contract SymbioticRateOracle is Ownable {
    using SymbioticUtils for bytes32;

    // --- Components ---
    RLDAaveOracle public immutable spotOracle;
    address public operator;

    // --- State ---
    uint256 public currentTwar;
    uint256 public lastUpdateTimestamp;

    // --- Configuration ---
    uint256 public constant MAX_DEVIATION_BPS = 2000; // 20% max deviation
    uint256 public constant STALE_TIMEOUT = 1 hours;

    // --- Events ---
    event TwarUpdated(
        uint256 indexed timestamp,
        uint256 twar,
        uint256 spotDiffBps
    );
    event OperatorUpdated(address indexed newOperator);

    // --- Errors ---
    error InvalidSignature();
    error DataTooStale();
    error DeviationTooHigh(uint256 spot, uint256 twar, uint256 diffBps);

    constructor(address _spotOracle, address _operator) Ownable(msg.sender) {
        spotOracle = RLDAaveOracle(_spotOracle);
        operator = _operator;
    }

    function updateTwar(
        uint256 twarWad,
        uint256 timestamp,
        bytes calldata signature
    ) external {
        // 1. Freshness Check
        if (timestamp < lastUpdateTimestamp) revert DataTooStale();
        if (block.timestamp > timestamp + 10 minutes) revert DataTooStale();

        // 2. Symbiotic Validation
        // Hash payload: (twar, timestamp, chainId, contractAddr)
        bytes32 messageHash = keccak256(
            abi.encodePacked(twarWad, timestamp, block.chainid, address(this))
        );

        // Use local SymbioticUtils to verify signer
        address signer = messageHash.toEthSignedMessageHash().recover(
            signature
        );
        if (signer != operator) revert InvalidSignature();

        // 3. Sanity Check (On-Chain Spot Validation)
        uint256 liveSpot = spotOracle.getIndexPrice();

        uint256 diff = liveSpot > twarWad
            ? liveSpot - twarWad
            : twarWad - liveSpot;
        uint256 deviationBps = (diff * 10000) / liveSpot;

        if (deviationBps > MAX_DEVIATION_BPS) {
            revert DeviationTooHigh(liveSpot, twarWad, deviationBps);
        }

        // 4. Update State
        currentTwar = twarWad;
        lastUpdateTimestamp = timestamp;

        emit TwarUpdated(timestamp, twarWad, deviationBps);
    }

    function setOperator(address _newOperator) external onlyOwner {
        operator = _newOperator;
        emit OperatorUpdated(_newOperator);
    }
}
