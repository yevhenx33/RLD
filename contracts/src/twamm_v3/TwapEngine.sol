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

        // 1. Solver pays discounted input token to Router directly (Assuming caller transferred to Router)
        // 2. TwapEngine commands Router to dispatch `clearAmount` to Solver
        
        // 1. Solver pays discounted input token to Router directly (Assuming caller transferred to Router)
        // 2. TwapEngine commands Router to dispatch `clearAmount` to Solver
        
        // Fetch Spot Price from GhostRouter oracle interface... (Placeholder routing)
        // uint256 payout = calculateSpotDiscount(clearAmount, discountBps);
        IGhostRouter(ghostRouter).pushMarketFunds(marketId, zeroForOne, msg.sender, clearAmount);

        // Record earnings for stream owners (Placeholder: payout amount)
        uint256 payoutReceived = clearAmount; // Typically calculateSpotDiscount(clearAmount) here

        if (zeroForOne) {
            state.streamGhostT0 -= clearAmount;
            _recordEarnings(streamPools[marketId][false], payoutReceived);
        } else {
            state.streamGhostT1 -= clearAmount;
            _recordEarnings(streamPools[marketId][true], payoutReceived);
        }
        state.lastClearTime = block.timestamp;
    }

    /// @inheritdoc ITwapEngine
    function claimTokens(bytes32 marketId, bytes32 orderId) public returns (uint256 earningsOut) {
        TwapState storage state = states[marketId];
        StreamOrder storage order = streamOrders[orderId];
        if (order.sellRate == 0) return 0;
        
        StreamPool storage stream = streamPools[marketId][order.zeroForOne];
        
        uint256 effectiveEF = stream.earningsFactorCurrent;
        if (state.lastUpdateTime >= order.expiration) {
            uint256 snap = stream.earningsFactorAtInterval[order.expiration];
            if (snap > 0 && snap < effectiveEF) {
                effectiveEF = snap;
            }
        }

        uint256 effectiveEFL = order.earningsFactorLast;
        bool orderStarted = stream.sellRateCurrent >= order.sellRate;
        if (!orderStarted) {
            effectiveEFL = effectiveEF;
        } else {
            uint256 epScan = order.expiration - expirationInterval;
            while (epScan > 0) {
                if (stream.sellRateStartingAtInterval[epScan] >= order.sellRate) break;
                if (epScan < expirationInterval) break;
                epScan -= expirationInterval;
            }
            uint256 startSnap = stream.earningsFactorAtInterval[epScan];
            if (startSnap > effectiveEFL) effectiveEFL = startSnap;
        }

        uint256 delta = effectiveEF > effectiveEFL ? effectiveEF - effectiveEFL : 0;
        if (delta > 0) {
            earningsOut = Math.mulDiv(order.sellRate, delta, FixedPoint96.Q96 * RATE_SCALER);
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
            if (stream.sellRateCurrent == order.sellRate) {
                // Auto-Settle against V4 Fallback
                uint256 ghost = order.zeroForOne ? state.streamGhostT0 : state.streamGhostT1;
                if (ghost > 0) {
                    uint256 amountOut = IGhostRouter(ghostRouter).settleGhost(marketId, order.zeroForOne, ghost);
                    _recordEarnings(streamPools[marketId][!order.zeroForOne], amountOut);
                    
                    if (order.zeroForOne) state.streamGhostT0 = 0;
                    else state.streamGhostT1 = 0;
                }
            }

            stream.sellRateCurrent -= order.sellRate;
            stream.sellRateEndingAtInterval[order.expiration] -= order.sellRate;

            uint256 remainingSeconds = order.expiration - state.lastUpdateTime;
            refund = (order.sellRate * remainingSeconds) / RATE_SCALER;
        } else {
            stream.sellRateEndingAtInterval[order.expiration] -= order.sellRate;

            uint256 ep = order.expiration - expirationInterval;
            while (ep > 0) {
                if (stream.sellRateStartingAtInterval[ep] >= order.sellRate) {
                    stream.sellRateStartingAtInterval[ep] -= order.sellRate;
                    break;
                }
                if (ep < expirationInterval) break;
                ep -= expirationInterval;
            }

            uint256 duration = order.expiration - ep;
            refund = (order.sellRate * duration) / RATE_SCALER;
        }

        delete streamOrders[orderId];
        IGhostRouter(ghostRouter).pushMarketFunds(marketId, order.zeroForOne, msg.sender, refund);
    }
    
    function requestNetting(bytes32 marketId, bool zeroForOne, uint256 amountIn, uint256 spotPrice) external override onlyRouter returns (uint256 filledAmount) {
        _accrueInternal(marketId);

        TwapState storage state = states[marketId];
        
        // If zeroForOne == true, Taker provides Token0, wants Token1
        // Engine intercepts using streamGhostT1 (Token1 pending sale from Stream1For0)
        uint256 availableGhost = zeroForOne ? state.streamGhostT1 : state.streamGhostT0;
        if (availableGhost == 0) return 0;

        // Note: For this execution phase, assume spotPrice is a simple multiplier scaling amountIn to amountOut
        // We will integrate exact Q64.96/tick math formatting during the PrimeBroker rigorous math phase.
        // uint256 desiredOut = Math.mulDiv(amountIn, spotPrice, FixedPoint96.Q96);
        uint256 desiredOut = amountIn; // Scaffolding simplification pending PrimeBroker tick-math implementation
        
        filledAmount = desiredOut > availableGhost ? availableGhost : desiredOut;
        
        if (filledAmount > 0) {
            // Calculate equivalent input taken from the Taker
            uint256 inputTaken = filledAmount;

            if (zeroForOne) {
                state.streamGhostT1 -= filledAmount;
                // Tokens received are Token0, which are distributed to Stream1For0 owners
                _recordEarnings(streamPools[marketId][false], inputTaken);
            } else {
                state.streamGhostT0 -= filledAmount;
                // Tokens received are Token1, which are distributed to Stream0For1 owners
                _recordEarnings(streamPools[marketId][true], inputTaken);
            }
        }
        
        return filledAmount;
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
}
