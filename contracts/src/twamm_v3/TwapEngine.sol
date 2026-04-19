// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {FixedPoint96} from "v4-core/src/libraries/FixedPoint96.sol";

import {ITwapEngine} from "./interfaces/ITwapEngine.sol";
import {IGhostRouter} from "./interfaces/IGhostRouter.sol";

/// @title TwapEngine (Ghost Sovereign Spoke)
/// @notice Handles O(1) TWAMM rate dynamics, internal stream netting, and Dutch Auctions.
///         Strictly a math & state engine; escrows NO physical tokens!
contract TwapEngine is ITwapEngine, ReentrancyGuard {
    uint256 public constant RATE_SCALER = 1e18;
    uint256 public constant DISCOUNT_RATE_PRECISION = 1e12;
    uint256 public constant PRICE_SCALE = 1e18;

    address public immutable ghostRouter;
    uint256 public immutable expirationInterval;
    uint256 public immutable maxDiscountBps;
    uint256 public immutable discountRateScaled;

    mapping(bytes32 => TwapState) public states;
    mapping(bytes32 => mapping(bool => StreamPool)) public streamPools;
    mapping(bytes32 => StreamOrder) public streamOrders;

    uint256 public orderNonce;

    error Unauthorized();
    error NotIntervalAligned();
    error NothingToClear();
    error InsufficientDiscount();
    error UnauthorizedRouter();
    error OrderDoesNotExist();
    error NoActiveStream();

    modifier onlyRouter() {
        if (msg.sender != ghostRouter) revert UnauthorizedRouter();
        _;
    }

    constructor(
        address _ghostRouter,
        uint256 _interval,
        uint256 _maxDiscountBps,
        uint256 _discountRateScaled
    ) {
        ghostRouter = _ghostRouter;
        expirationInterval = _interval;
        maxDiscountBps = _maxDiscountBps;
        discountRateScaled = _discountRateScaled;
    }

    /// @notice Submit a TWAP flow (User Entrypoint)
    function submitStream(
        bytes32 marketId,
        bool zeroForOne,
        uint256 duration,
        uint256 amountIn
    ) external override nonReentrant returns (bytes32 orderId) {
        // Securely pull funds directly into the Vault Hub from the User
        IGhostRouter(ghostRouter).pullMarketFunds(marketId, zeroForOne, msg.sender, amountIn);

        _accrueInternal(marketId);

        uint256 nextEpoch = ((block.timestamp / expirationInterval) * expirationInterval) + expirationInterval;
        uint256 expiration = nextEpoch + duration;

        if (expiration % expirationInterval != 0) revert NotIntervalAligned();

        // Calc scale
        uint256 scaledSellRate = (amountIn * RATE_SCALER) / duration;
        
        StreamPool storage stream = streamPools[marketId][zeroForOne];
        stream.sellRateStartingAtInterval[nextEpoch] += scaledSellRate;
        stream.sellRateEndingAtInterval[expiration] += scaledSellRate;

        orderId = keccak256(abi.encode(msg.sender, nextEpoch, zeroForOne, ++orderNonce));
        
        streamOrders[orderId] = StreamOrder({
            owner: msg.sender,
            sellRate: scaledSellRate,
            earningsFactorLast: stream.earningsFactorCurrent,
            startEpoch: nextEpoch,
            expiration: expiration,
            zeroForOne: zeroForOne
        });

        return orderId;
    }

    /// @notice Perform internal accounting accrual up to block.timestamp
    function _accrueInternal(bytes32 marketId) internal {
        TwapState storage state = states[marketId];
        if (state.lastUpdateTime == 0) {
            state.lastUpdateTime = block.timestamp;
            state.lastClearTime = block.timestamp;
            return;
        }
        
        if (block.timestamp <= state.lastUpdateTime) return;
        uint256 deltaTime = block.timestamp - state.lastUpdateTime;

        StreamPool storage pool0 = streamPools[marketId][true];
        StreamPool storage pool1 = streamPools[marketId][false];

        state.streamGhostT0 += (pool0.sellRateCurrent * deltaTime) / RATE_SCALER;
        state.streamGhostT1 += (pool1.sellRateCurrent * deltaTime) / RATE_SCALER;

        // Note: Cross epoch boundaries to activate starting/expiring flows
        uint256 expirationIntervalLocal = expirationInterval;
        uint256 lastInterval = (state.lastUpdateTime / expirationIntervalLocal) * expirationIntervalLocal;
        uint256 currentInterval = (block.timestamp / expirationIntervalLocal) * expirationIntervalLocal;

        if (currentInterval > lastInterval) {
            for (
                uint256 epoch = lastInterval + expirationIntervalLocal;
                epoch <= currentInterval;
                epoch += expirationIntervalLocal
            ) {
                _crossEpoch(pool0, epoch);
                _crossEpoch(pool1, epoch);
            }
        }
        
        state.lastUpdateTime = block.timestamp;
    }

    /// @notice Cross an epoch boundary: activate starting orders, snapshot earningsFactor, subtract expired
    function _crossEpoch(StreamPool storage stream, uint256 epoch) internal {
        uint256 starting = stream.sellRateStartingAtInterval[epoch];
        if (starting > 0) {
            stream.earningsFactorAtInterval[epoch] = stream.earningsFactorCurrent;
            stream.sellRateCurrent += starting;
        }

        uint256 expiring = stream.sellRateEndingAtInterval[epoch];
        if (expiring > 0) {
            stream.earningsFactorAtInterval[epoch] = stream.earningsFactorCurrent;
            stream.sellRateCurrent -= expiring;
        }
    }

    /// @inheritdoc ITwapEngine
    function clearAuction(
        bytes32 marketId,
        bool zeroForOne,
        uint256 maxAmount,
        uint256 minDiscountBps
    ) external override nonReentrant {
        _accrueInternal(marketId);

        TwapState storage state = states[marketId];
        uint256 available = zeroForOne ? state.streamGhostT0 : state.streamGhostT1;
        if (available == 0) revert NothingToClear();

        uint256 clearAmount = available > maxAmount ? maxAmount : available;

        uint256 elapsedSinceClear = block.timestamp - state.lastClearTime;
        uint256 discountBps = (elapsedSinceClear * discountRateScaled) / DISCOUNT_RATE_PRECISION;
        if (discountBps > maxDiscountBps) discountBps = maxDiscountBps;
        if (discountBps < minDiscountBps) revert InsufficientDiscount();

        // Dispatch cleared tokens to the solver
        IGhostRouter(ghostRouter).pushMarketFunds(marketId, zeroForOne, msg.sender, clearAmount);

        // TODO: Pull discounted payment from solver once oracle integration is wired
        uint256 payoutReceived = clearAmount;

        // Record earnings for the sellers of the consumed ghost
        if (zeroForOne) {
            state.streamGhostT0 -= clearAmount;
            _recordEarnings(streamPools[marketId][true], payoutReceived);
        } else {
            state.streamGhostT1 -= clearAmount;
            _recordEarnings(streamPools[marketId][false], payoutReceived);
        }
        state.lastClearTime = block.timestamp;
    }

    /// @inheritdoc ITwapEngine
    function claimTokens(bytes32 marketId, bytes32 orderId) public returns (uint256 earningsOut) {
        StreamOrder storage order = streamOrders[orderId];
        if (order.sellRate == 0) return 0;

        StreamPool storage stream = streamPools[marketId][order.zeroForOne];

        uint256 effectiveEF;
        (earningsOut, effectiveEF) = _computeEarnings(stream, order);

        if (earningsOut > 0) {
            order.earningsFactorLast = effectiveEF;
            IGhostRouter(ghostRouter).pushMarketFunds(marketId, !order.zeroForOne, order.owner, earningsOut);
        }
    }

    /// @inheritdoc ITwapEngine
    function cancelOrder(bytes32 marketId, bytes32 orderId) external returns (uint256 refund, uint256 earnings) {
        StreamOrder memory order = streamOrders[orderId];
        if (order.owner != msg.sender) revert Unauthorized();
        
        earnings = claimTokens(marketId, orderId);

        TwapState storage state = states[marketId];
        StreamPool storage stream = streamPools[marketId][order.zeroForOne];
        
        bool orderStarted = stream.sellRateCurrent >= order.sellRate;

        if (orderStarted) {
            // Auto-settle if this is the last order in the stream
            if (stream.sellRateCurrent == order.sellRate) {
                uint256 ghost = order.zeroForOne ? state.streamGhostT0 : state.streamGhostT1;
                if (ghost > 0) {
                    uint256 amountOut = IGhostRouter(ghostRouter).settleGhost(marketId, order.zeroForOne, ghost);
                    // Proceeds go to sellers of THIS direction (the pool being settled)
                    _recordEarnings(stream, amountOut);

                    if (order.zeroForOne) state.streamGhostT0 = 0;
                    else state.streamGhostT1 = 0;
                }
            }

            stream.sellRateCurrent -= order.sellRate;
            stream.sellRateEndingAtInterval[order.expiration] -= order.sellRate;

            uint256 remainingSeconds = order.expiration - state.lastUpdateTime;
            refund = (order.sellRate * remainingSeconds) / RATE_SCALER;
        } else {
            // Order hasn't started — remove from starting map using stored startEpoch
            stream.sellRateEndingAtInterval[order.expiration] -= order.sellRate;
            stream.sellRateStartingAtInterval[order.startEpoch] -= order.sellRate;

            uint256 duration = order.expiration - order.startEpoch;
            refund = (order.sellRate * duration) / RATE_SCALER;
        }

        delete streamOrders[orderId];
        IGhostRouter(ghostRouter).pushMarketFunds(marketId, order.zeroForOne, msg.sender, refund);
    }
    
    function syncAndFetchGhost(bytes32 marketId) external override onlyRouter returns (uint256 ghost0, uint256 ghost1) {
        _accrueInternal(marketId);
        TwapState storage state = states[marketId];
        return (state.streamGhostT0, state.streamGhostT1);
    }

    /// @notice Hub commands the spoke to apply the results of global ghost netting.
    ///         Deducts consumed ghost and credits price-converted earnings.
    /// @param consumed0 Token0 ghost consumed by the Hub's macro match
    /// @param consumed1 Token1 ghost consumed by the Hub's macro match
    /// @param spotPrice Oracle price: Token1 per Token0, scaled by 1e18
    function applyNettingResult(
        bytes32 marketId,
        uint256 consumed0,
        uint256 consumed1,
        uint256 spotPrice
    ) external override onlyRouter {
        TwapState storage state = states[marketId];

        if (consumed0 > 0) {
            state.streamGhostT0 -= consumed0;
            // Token0 sellers earn Token1: consumed0 * price
            uint256 token1Earned = Math.mulDiv(consumed0, spotPrice, PRICE_SCALE);
            _recordEarnings(streamPools[marketId][true], token1Earned);
        }
        if (consumed1 > 0) {
            state.streamGhostT1 -= consumed1;
            // Token1 sellers earn Token0: consumed1 / price
            uint256 token0Earned = Math.mulDiv(consumed1, PRICE_SCALE, spotPrice);
            _recordEarnings(streamPools[marketId][false], token0Earned);
        }
    }

    /// @notice Taker intercepts remaining directional ghost after netting.
    /// @param amountIn Taker's remaining input budget (in TokenIn denomination)
    /// @param spotPrice Oracle price: Token1 per Token0, scaled by 1e18
    /// @return filledOut Amount of output token filled from ghost
    /// @return inputConsumed Amount of input token consumed (differs from filledOut at non-1:1 prices)
    function takeGhost(
        bytes32 marketId,
        bool zeroForOne,
        uint256 amountIn,
        uint256 spotPrice
    ) external override onlyRouter returns (uint256 filledOut, uint256 inputConsumed) {
        TwapState storage state = states[marketId];

        // zeroForOne=true: Taker gives Token0, wants Token1. Ghost source = streamGhostT1.
        uint256 availableGhost = zeroForOne ? state.streamGhostT1 : state.streamGhostT0;
        if (availableGhost == 0) return (0, 0);

        // Convert Taker's input to output denomination
        uint256 desiredOut;
        if (zeroForOne) {
            // Token0 → Token1: amountIn(T0) * price = desiredOut(T1)
            desiredOut = Math.mulDiv(amountIn, spotPrice, PRICE_SCALE);
        } else {
            // Token1 → Token0: amountIn(T1) / price = desiredOut(T0)
            desiredOut = Math.mulDiv(amountIn, PRICE_SCALE, spotPrice);
        }

        filledOut = desiredOut > availableGhost ? availableGhost : desiredOut;

        if (filledOut > 0) {
            // Reverse-convert filledOut back to input denomination
            if (zeroForOne) {
                inputConsumed = Math.mulDiv(filledOut, PRICE_SCALE, spotPrice);
                state.streamGhostT1 -= filledOut;
                // Ghost Token1 sellers (false pool) earn Token0 from the Taker
                _recordEarnings(streamPools[marketId][false], inputConsumed);
            } else {
                inputConsumed = Math.mulDiv(filledOut, spotPrice, PRICE_SCALE);
                state.streamGhostT0 -= filledOut;
                // Ghost Token0 sellers (true pool) earn Token1 from the Taker
                _recordEarnings(streamPools[marketId][true], inputConsumed);
            }
        }
    }

    /// @notice Record earnings directly into a stream's earningsFactor
    function _recordEarnings(StreamPool storage stream, uint256 earnings) internal {
        if (stream.sellRateCurrent == 0 || earnings == 0) return;
        uint256 earningsFactor = Math.mulDiv(
            earnings,
            FixedPoint96.Q96 * RATE_SCALER,
            stream.sellRateCurrent
        );
        stream.earningsFactorCurrent += earningsFactor;
    }

    /// @notice Shared earnings computation used by claimTokens, getCancelOrderState, and sync.
    ///         Returns the earnings amount and effective earningsFactor for snapshot update.
    function _computeEarnings(
        StreamPool storage stream,
        StreamOrder storage order
    ) internal view returns (uint256 earningsOut, uint256 effectiveEF) {
        effectiveEF = stream.earningsFactorCurrent;

        // Cap at expiration snapshot for expired orders
        if (block.timestamp >= order.expiration) {
            uint256 snap = stream.earningsFactorAtInterval[order.expiration];
            if (snap > 0 && snap < effectiveEF) {
                effectiveEF = snap;
            }
        }

        // Floor at start epoch snapshot for deferred-start orders
        uint256 effectiveEFL = order.earningsFactorLast;
        bool orderStarted = stream.sellRateCurrent >= order.sellRate;
        if (!orderStarted) {
            effectiveEFL = effectiveEF;
        } else {
            uint256 startSnap = stream.earningsFactorAtInterval[order.startEpoch];
            if (startSnap > effectiveEFL) effectiveEFL = startSnap;
        }

        uint256 delta = effectiveEF > effectiveEFL ? effectiveEF - effectiveEFL : 0;
        if (delta > 0) {
            earningsOut = Math.mulDiv(order.sellRate, delta, FixedPoint96.Q96 * RATE_SCALER);
        }
    }

    // ─── P0: FORCE SETTLE (Liquidation Path) ──────────────────────────────────

    /// @notice Force-settle all ghost for a direction into V4 AMM.
    ///         Called by PrimeBroker during liquidation to crystallize ghost value.
    function forceSettle(bytes32 marketId, bool zeroForOne) external {
        _accrueInternal(marketId);

        TwapState storage state = states[marketId];
        uint256 ghostAmount = zeroForOne ? state.streamGhostT0 : state.streamGhostT1;
        if (ghostAmount == 0) return;

        StreamPool storage stream = streamPools[marketId][zeroForOne];
        if (stream.sellRateCurrent == 0) revert NoActiveStream();

        // Delegate physical swap to the Hub
        uint256 amountOut = IGhostRouter(ghostRouter).settleGhost(marketId, zeroForOne, ghostAmount);

        // Record proceeds as earnings for stream owners
        _recordEarnings(stream, amountOut);

        // Zero the ghost
        if (zeroForOne) {
            state.streamGhostT0 = 0;
        } else {
            state.streamGhostT1 = 0;
        }
    }

    // ─── P0: GET CANCEL ORDER STATE (NAV Valuation) ───────────────────────────

    /// @notice Preview what a user would receive if cancelling now (view-only, no state mutation).
    ///         Used by PrimeBrokerLens for solvency/NAV valuation.
    /// @return buyTokensOwed  Earned output tokens (not yet claimed)
    /// @return sellTokensRefund Refund of unsold input tokens
    function getCancelOrderState(
        bytes32 marketId,
        bytes32 orderId
    ) external view returns (uint256 buyTokensOwed, uint256 sellTokensRefund) {
        StreamOrder storage order = streamOrders[orderId];
        if (order.sellRate == 0) return (0, 0);

        TwapState storage state = states[marketId];
        StreamPool storage stream = streamPools[marketId][order.zeroForOne];

        // Compute pending earnings (read-only)
        (buyTokensOwed, ) = _computeEarnings(stream, order);

        // Compute refund for remaining time
        if (state.lastUpdateTime < order.expiration) {
            bool orderStarted = stream.sellRateCurrent >= order.sellRate;

            if (orderStarted) {
                uint256 remainingSeconds = order.expiration - state.lastUpdateTime;
                sellTokensRefund = (order.sellRate * remainingSeconds) / RATE_SCALER;
            } else {
                uint256 duration = order.expiration - order.startEpoch;
                sellTokensRefund = (order.sellRate * duration) / RATE_SCALER;
            }
        }
    }

}

