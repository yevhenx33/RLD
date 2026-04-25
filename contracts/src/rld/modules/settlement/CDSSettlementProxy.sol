// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Owned} from "solmate/src/auth/Owned.sol";
import {IRLDCore, MarketId} from "../../../shared/interfaces/IRLDCore.sol";

/// @title CDSSettlementProxy
/// @notice Settlement-module proxy for CDS markets, ready to receive operator attestations.
/// @dev The proxy is configured as `settlementModule` in RLDCore. Today it supports owner
///      emergency settlement and allowlisted operator attestations; later the attestation
///      entrypoint can be wired to Symbiotic validation before forwarding to Core.
contract CDSSettlementProxy is Owned {
    IRLDCore public immutable core;

    uint8 public constant TRACK_UTILIZATION_FREEZE = 1 << 0;
    uint8 public constant TRACK_COLLATERAL_COLLAPSE = 1 << 1;
    uint8 public constant TRACK_BAD_DEBT_ACCRUAL = 1 << 2;
    uint8 public constant SUPPORTED_TRACK_MASK =
        TRACK_UTILIZATION_FREEZE |
        TRACK_COLLATERAL_COLLAPSE |
        TRACK_BAD_DEBT_ACCRUAL;
    uint8 public constant MIN_SETTLEMENT_TRACKS = 2;

    mapping(address operator => bool active) public operators;
    mapping(bytes32 attestationHash => bool consumed) public consumedAttestations;

    event OperatorUpdated(address indexed operator, bool active);
    event SettlementAttested(
        MarketId indexed marketId,
        address indexed operator,
        bytes32 indexed attestationHash,
        uint8 trackMask,
        uint64 observedAt,
        bytes32 dataRoot,
        bytes operatorData
    );
    event GlobalSettlementForwarded(
        MarketId indexed marketId,
        address indexed trigger,
        bytes32 indexed attestationHash
    );
    event BrokerWithdrawalQueueInvalidated(
        MarketId indexed marketId,
        address indexed broker,
        address indexed operator
    );

    error InvalidCore();
    error InvalidOwner();
    error InvalidOperator();
    error UnauthorizedOperator();
    error InvalidTrackMask();
    error InsufficientSettlementTracks();
    error InvalidObservationTimestamp();
    error AttestationAlreadyConsumed();
    error InvalidBroker();

    modifier onlyOperatorOrOwner() {
        if (msg.sender != owner && !operators[msg.sender]) revert UnauthorizedOperator();
        _;
    }

    constructor(address core_, address owner_) Owned(owner_) {
        if (core_ == address(0) || core_.code.length == 0) revert InvalidCore();
        if (owner_ == address(0)) revert InvalidOwner();
        core = IRLDCore(core_);
    }

    /// @notice Allow or revoke a settlement operator.
    /// @dev Symbiotic operator-set management can replace this owner-controlled path later.
    function setOperator(address operator, bool active) external onlyOwner {
        if (operator == address(0)) revert InvalidOperator();
        operators[operator] = active;
        emit OperatorUpdated(operator, active);
    }

    /// @notice Owner emergency path for simulation and governance-controlled interventions.
    function enterGlobalSettlement(MarketId marketId) external onlyOwner {
        _forwardGlobalSettlement(marketId, bytes32(0));
    }

    /// @notice Submit a multi-track CDS settlement attestation and forward settlement to Core.
    /// @param marketId CDS market id configured to use this proxy as settlement module.
    /// @param trackMask Bitmask of triggered tracks. At least 2 supported tracks are required.
    /// @param observedAt Timestamp of the attested observation window.
    /// @param dataRoot Commitment to off-chain/operator-observed settlement data.
    /// @param operatorData Raw operator payload reserved for future Symbiotic/ZK verification.
    function submitSettlementAttestation(
        MarketId marketId,
        uint8 trackMask,
        uint64 observedAt,
        bytes32 dataRoot,
        bytes calldata operatorData
    ) external onlyOperatorOrOwner returns (bytes32 attestationHash) {
        _validateTrackMask(trackMask);
        if (observedAt == 0 || observedAt > block.timestamp) {
            revert InvalidObservationTimestamp();
        }

        attestationHash = keccak256(
            abi.encode(marketId, trackMask, observedAt, dataRoot, operatorData)
        );
        if (consumedAttestations[attestationHash]) revert AttestationAlreadyConsumed();
        consumedAttestations[attestationHash] = true;

        emit SettlementAttested(
            marketId,
            msg.sender,
            attestationHash,
            trackMask,
            observedAt,
            dataRoot,
            operatorData
        );

        _forwardGlobalSettlement(marketId, attestationHash);
    }

    /// @notice Invalidate a single broker's delayed-withdrawal queue after settlement.
    function invalidateBrokerWithdrawalQueue(
        MarketId marketId,
        address broker
    ) public onlyOperatorOrOwner {
        if (broker == address(0) || broker.code.length == 0) revert InvalidBroker();
        core.invalidateBrokerWithdrawalQueue(marketId, broker);
        emit BrokerWithdrawalQueueInvalidated(marketId, broker, msg.sender);
    }

    /// @notice Batch helper for settlement sweeps over known underwriter brokers.
    function invalidateBrokerWithdrawalQueues(
        MarketId marketId,
        address[] calldata brokers
    ) external onlyOperatorOrOwner {
        for (uint256 i = 0; i < brokers.length; ++i) {
            invalidateBrokerWithdrawalQueue(marketId, brokers[i]);
        }
    }

    function _forwardGlobalSettlement(MarketId marketId, bytes32 attestationHash) internal {
        core.enterGlobalSettlement(marketId);
        emit GlobalSettlementForwarded(marketId, msg.sender, attestationHash);
    }

    function _validateTrackMask(uint8 trackMask) internal pure {
        if (trackMask == 0 || (trackMask & ~SUPPORTED_TRACK_MASK) != 0) {
            revert InvalidTrackMask();
        }
        if (_trackCount(trackMask) < MIN_SETTLEMENT_TRACKS) {
            revert InsufficientSettlementTracks();
        }
    }

    function _trackCount(uint8 trackMask) internal pure returns (uint8 count) {
        uint8 mask = trackMask;
        while (mask != 0) {
            count += mask & 1;
            mask >>= 1;
        }
    }
}
