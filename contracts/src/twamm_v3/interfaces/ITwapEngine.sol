// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IGhostEngine} from "./IGhostEngine.sol";

/// @title ITwapEngine — TWAMM-specific Spoke Interface
/// @notice Extends IGhostEngine with stream order lifecycle methods.
interface ITwapEngine is IGhostEngine {
    struct StreamOrder {
        address owner;
        uint256 sellRate;
        uint256 earningsFactorLast;
        uint256 startEpoch;
        uint256 expiration;
        bool zeroForOne;
    }

    struct StreamPool {
        uint256 sellRateCurrent;
        uint256 earningsFactorCurrent;
        mapping(uint256 => uint256) sellRateEndingAtInterval;
        mapping(uint256 => uint256) earningsFactorAtInterval;
        mapping(uint256 => uint256) sellRateStartingAtInterval;
    }

    struct TwapState {
        uint256 streamGhostT0;
        uint256 streamGhostT1;
        uint256 lastUpdateTime;
        uint256 lastClearTime;
        uint256 epochInterval;
    }

    // ─── TWAMM Retail Entrypoints ───────────────────────────

    function submitStream(
        bytes32 marketId,
        bool zeroForOne,
        uint256 duration,
        uint256 amountIn
    ) external returns (bytes32 orderId);

    function claimTokens(bytes32 marketId, bytes32 orderId) external returns (uint256 earningsOut);

    function cancelOrder(bytes32 marketId, bytes32 orderId) external returns (uint256 refund, uint256 earningsOut);

    function clearAuction(bytes32 marketId, bool zeroForOne, uint256 maxAmount, uint256 minDiscountBps) external;

    // ─── Liquidation & Valuation ────────────────────────────

    /// @notice Force-settle all ghost for a direction into V4 AMM (PrimeBroker liquidation path)
    function forceSettle(bytes32 marketId, bool zeroForOne) external;

    /// @notice Preview cancel state without mutation (NAV valuation for PrimeBrokerLens)
    function getCancelOrderState(bytes32 marketId, bytes32 orderId)
        external view returns (uint256 buyTokensOwed, uint256 sellTokensRefund);

    /// @notice Preview cancel state at the current block timestamp without mutating engine state.
    ///         This endpoint simulates time progression from committed state to "now".
    function getCancelOrderStateExact(bytes32 marketId, bytes32 orderId)
        external
        view
        returns (uint256 buyTokensOwed, uint256 sellTokensRefund);
}

