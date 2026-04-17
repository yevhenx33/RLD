// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;


interface ITwapEngine {
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

    // ─── External Entrypoints ───────────────────────────────

    function submitStream(
        bytes32 marketId,
        bool zeroForOne,
        uint256 duration,
        uint256 amountIn
    ) external returns (bytes32 orderId);

    function claimTokens(bytes32 marketId, bytes32 orderId) external returns (uint256 earningsOut);

    function cancelOrder(bytes32 marketId, bytes32 orderId) external returns (uint256 refund, uint256 earningsOut);

    function clearAuction(bytes32 marketId, bool zeroForOne, uint256 maxAmount, uint256 minDiscountBps) external;

    // ─── Router Hooks & Views ───────────────────────────────

    // Request engine to cross a specific volume against its internal unnetted flow
    function requestNetting(bytes32 marketId, bool zeroForOne, uint256 amountIn, uint256 spotPrice) external returns (uint256 filledAmount);
}
