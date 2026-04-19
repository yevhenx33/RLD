// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {TwapEngine} from "./TwapEngine.sol";

/// @title TwapEngineLens — Read-only views computed from TwapEngine public state
/// @notice Reads directly from the engine's public mappings and immutables.
///         No view functions needed on the engine itself.
contract TwapEngineLens {
    TwapEngine public immutable engine;

    constructor(address _engine) {
        engine = TwapEngine(_engine);
    }

    /// @notice Get order state by marketId + orderId.
    function getOrder(bytes32 marketId, bytes32 orderId)
        external
        view
        returns (
            address owner,
            uint256 sellRate,
            uint256 earningsFactorLast,
            uint256 startEpoch,
            uint256 expiration,
            bool zeroForOne
        )
    {
        return engine.streamOrders(marketId, orderId);
    }

    /// @notice Get ghost balances and discount state for a market.
    ///         Includes pending (uncommitted) accrual since last update.
    function getStreamState(bytes32 marketId)
        external
        view
        returns (uint256 ghost0, uint256 ghost1, uint256 currentDiscount, uint256 timeSinceLastClear)
    {
        (uint256 g0, uint256 g1, uint256 lastUpdate, uint256 lastClear,) = engine.states(marketId);

        uint256 deltaTime = block.timestamp > lastUpdate ? block.timestamp - lastUpdate : 0;

        (uint256 sellRate0,) = engine.streamPools(marketId, true);
        (uint256 sellRate1,) = engine.streamPools(marketId, false);

        uint256 rateScaler = engine.RATE_SCALER();

        ghost0 = g0 + ((sellRate0 * deltaTime) / rateScaler);
        ghost1 = g1 + ((sellRate1 * deltaTime) / rateScaler);

        timeSinceLastClear = block.timestamp - lastClear;
        currentDiscount = (timeSinceLastClear * engine.discountRateScaled()) / engine.DISCOUNT_RATE_PRECISION();
        uint256 maxDiscount = engine.maxDiscountBps();
        if (currentDiscount > maxDiscount) currentDiscount = maxDiscount;
    }

    /// @notice Get sellRate and earningsFactor for a directional stream pool
    function getStreamPool(bytes32 marketId, bool zeroForOne)
        external
        view
        returns (uint256 sellRateCurrent, uint256 earningsFactorCurrent)
    {
        return engine.streamPools(marketId, zeroForOne);
    }

    /// @notice Preview cancel state using committed engine state (no simulated time drift).
    function getCancelOrderStateCommitted(bytes32 marketId, bytes32 orderId)
        external
        view
        returns (uint256 buyTokensOwed, uint256 sellTokensRefund)
    {
        return engine.getCancelOrderState(marketId, orderId);
    }

    /// @notice Preview cancel state at the current block timestamp (exact-at-now simulation).
    function getCancelOrderStateExact(bytes32 marketId, bytes32 orderId)
        external
        view
        returns (uint256 buyTokensOwed, uint256 sellTokensRefund)
    {
        return engine.getCancelOrderStateExact(marketId, orderId);
    }

    /// @notice Backward-compatible alias of committed cancel preview endpoint.
    function getCancelOrderState(bytes32 marketId, bytes32 orderId)
        external
        view
        returns (uint256 buyTokensOwed, uint256 sellTokensRefund)
    {
        return engine.getCancelOrderState(marketId, orderId);
    }
}
