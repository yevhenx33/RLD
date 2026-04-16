// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {GhostPoolState, StreamPool, StreamOrder, RATE_SCALER, MAX_EPOCHS_PER_TX} from "../types/GhostTypes.sol";

/// @title StreamLib — TWAP stream order logic
/// @notice Library for O(1) ghost accrual, epoch crossing, and earnings.
///         Direct port of Python stream_module.py.
library StreamLib {
    // ─── Ghost Accrual (O(1)) ─────────────────────────────────────────

    /// @notice Accrue ghost from stream sell rates. O(1) per direction.
    function accrueStreamGhost(
        GhostPoolState storage pool,
        StreamPool storage pool0for1,
        StreamPool storage pool1for0,
        uint256 lastUpdate,
        uint256 now_
    ) internal {
        if (now_ <= lastUpdate) return;
        uint256 elapsed = now_ - lastUpdate;

        uint256 rate0 = pool0for1.sellRateCurrent;
        uint256 rate1 = pool1for0.sellRateCurrent;

        if (rate0 > 0) {
            pool.streamGhostT0 += (rate0 * elapsed) / RATE_SCALER;
        }
        if (rate1 > 0) {
            pool.streamGhostT1 += (rate1 * elapsed) / RATE_SCALER;
        }
    }

    // ─── Epoch Crossing ───────────────────────────────────────────────

    /// @notice Cross all epoch boundaries for one direction
    function crossSingleEpoch(
        StreamPool storage streamPool,
        mapping(uint256 => uint256) storage sellRateStarting,
        mapping(uint256 => uint256) storage sellRateEnding,
        mapping(uint256 => uint256) storage earningsFactorAtEpoch,
        uint256 epoch
    ) internal {
        uint256 starting = sellRateStarting[epoch];
        if (starting > 0) {
            earningsFactorAtEpoch[epoch] = streamPool.earningsFactorCurrent;
            streamPool.sellRateCurrent += starting;
        }

        uint256 expiring = sellRateEnding[epoch];
        if (expiring > 0) {
            earningsFactorAtEpoch[epoch] = streamPool.earningsFactorCurrent;
            if (streamPool.sellRateCurrent >= expiring) {
                streamPool.sellRateCurrent -= expiring;
            } else {
                streamPool.sellRateCurrent = 0;
            }
        }
    }

    /// @notice Cross all epoch boundaries between lastUpdate and now
    /// @return effectiveEnd The effective end time (may be capped by MAX_EPOCHS_PER_TX)
    function crossEpochs(
        StreamPool storage pool0for1,
        StreamPool storage pool1for0,
        mapping(uint256 => uint256) storage startingT0,
        mapping(uint256 => uint256) storage endingT0,
        mapping(uint256 => uint256) storage efAtEpochT0,
        mapping(uint256 => uint256) storage startingT1,
        mapping(uint256 => uint256) storage endingT1,
        mapping(uint256 => uint256) storage efAtEpochT1,
        uint256 lastUpdate,
        uint256 now_,
        uint256 epochInterval
    ) internal returns (uint256 effectiveEnd) {
        effectiveEnd = now_;
        uint256 lastInterval = (lastUpdate / epochInterval) * epochInterval;
        uint256 currInterval = (now_ / epochInterval) * epochInterval;

        if (currInterval <= lastInterval) return effectiveEnd;

        uint256 epochsCrossed;
        uint256 epochBoundary = lastInterval + epochInterval;

        while (epochBoundary <= currInterval) {
            if (epochsCrossed >= MAX_EPOCHS_PER_TX) {
                effectiveEnd = epochBoundary - epochInterval;
                return effectiveEnd;
            }

            crossSingleEpoch(pool0for1, startingT0, endingT0, efAtEpochT0, epochBoundary);
            crossSingleEpoch(pool1for0, startingT1, endingT1, efAtEpochT1, epochBoundary);

            epochsCrossed++;
            epochBoundary += epochInterval;
        }
    }

    // ─── Record Earnings ──────────────────────────────────────────────

    /// @notice Record earnings from a fill to the stream pool
    function recordEarnings(StreamPool storage streamPool, uint256 earnings) internal {
        if (streamPool.sellRateCurrent > 0 && earnings > 0) {
            streamPool.earningsFactorCurrent += (earnings * RATE_SCALER) / streamPool.sellRateCurrent;
        }
    }

    // ─── Sync Order ───────────────────────────────────────────────────

    /// @notice Compute claimable earnings for a stream order
    function syncOrder(
        StreamPool storage streamPool,
        StreamOrder storage order,
        mapping(uint256 => uint256) storage efAtEpoch,
        uint256 currentTime
    ) internal returns (uint256 earnings) {
        uint256 effectiveEF = streamPool.earningsFactorCurrent;

        // Cap at expiration snapshot for expired orders
        if (currentTime >= order.expiration) {
            uint256 snapshot = efAtEpoch[order.expiration];
            if (snapshot > 0 && snapshot < effectiveEF) {
                effectiveEF = snapshot;
            }
        }

        // Floor at start snapshot for deferred-start orders
        uint256 effectiveEFL = order.earningsFactorLast;
        if (currentTime >= order.startEpoch) {
            uint256 startSnapshot = efAtEpoch[order.startEpoch];
            if (startSnapshot > effectiveEFL) {
                effectiveEFL = startSnapshot;
            }
        } else {
            effectiveEFL = effectiveEF; // Not started yet
        }

        // Compute earnings
        if (effectiveEF > effectiveEFL) {
            uint256 delta = effectiveEF - effectiveEFL;
            earnings = (delta * order.sellRate) / RATE_SCALER;
        }

        // Update marker
        order.earningsFactorLast = streamPool.earningsFactorCurrent;
    }
}
