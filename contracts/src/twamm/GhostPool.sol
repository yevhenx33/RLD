// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    GhostPoolState,
    NettingResult,
    StreamPool,
    StreamOrder,
    LimitBucket,
    LimitOrder,
    FillResult,
    RATE_SCALER
} from "./types/GhostTypes.sol";
import {PriceMath} from "./libraries/PriceMath.sol";
import {StreamLib} from "./libraries/StreamLib.sol";
import {JitFillLib} from "./libraries/JitFillLib.sol";
import {TickBitmapLib} from "./libraries/TickBitmapLib.sol";

/// @title GhostPool — Core Ghost Matching Engine state & coordination
/// @notice Manages ghost balances, stream/limit state, L1 netting, and L2 JIT fills.
///         Designed as an abstract contract inherited by GhostHook.
abstract contract GhostPool {
    using PriceMath for uint256;
    using TickBitmapLib for mapping(int16 => uint256);

    // ─── State ────────────────────────────────────────────────────────

    GhostPoolState public poolState;

    // Stream state (both directions)
    StreamPool public streamPool0for1;
    StreamPool public streamPool1for0;

    // Stream mappings (per direction)
    mapping(uint256 => uint256) public sellRateStartingT0;
    mapping(uint256 => uint256) public sellRateEndingT0;
    mapping(uint256 => uint256) public earningsFactorAtEpochT0;
    mapping(uint256 => uint256) public sellRateStartingT1;
    mapping(uint256 => uint256) public sellRateEndingT1;
    mapping(uint256 => uint256) public earningsFactorAtEpochT1;

    // Stream orders
    mapping(bytes32 => StreamOrder) public streamOrders;
    uint256 public streamOrderCount;

    // Limit state
    mapping(int24 => LimitBucket) public sellBuckets;
    mapping(int24 => LimitBucket) public buyBuckets;
    mapping(int16 => uint256) public sellBitmap;
    mapping(int16 => uint256) public buyBitmap;

    // Limit orders packed into arrays per bucket
    mapping(int24 => LimitOrder[]) internal _sellOrders;
    mapping(int24 => LimitOrder[]) internal _buyOrders;

    // Tracked active sell/buy ticks for iteration
    int24[] public activeSellTicks;
    int24[] public activeBuyTicks;

    // ─── Events ───────────────────────────────────────────────────────

    event StreamSubmitted(bytes32 indexed orderId, address indexed owner, uint256 sellRate, uint256 startEpoch, uint256 expiration, bool zeroForOne);
    event StreamCancelled(bytes32 indexed orderId, uint256 refund);
    event StreamClaimed(bytes32 indexed orderId, uint256 earnings);
    event LimitSubmitted(int24 indexed tick, address indexed owner, uint256 amount, bool isSell);
    event LimitClaimed(int24 indexed tick, address indexed claimant, uint256 proceeds, uint256 refund);
    event GhostFill(uint256 fillOut, uint256 fillIn, bool zeroForOne);
    event Netted(uint256 matchedT0, uint256 matchedT1);

    // ─── Initialization ───────────────────────────────────────────────

    function _initPool(uint256 epochInterval, uint160 twap, uint256 startTime) internal {
        poolState.epochInterval = epochInterval;
        poolState.twap = twap;
        poolState.lastUpdateTime = startTime;
    }

    // ─── L1: Internal Netting ─────────────────────────────────────────

    function _internalNet() internal returns (NettingResult memory result) {
        uint256 tg0 = poolState.streamGhostT0 + poolState.limitGhostT0;
        uint256 tg1 = poolState.streamGhostT1 + poolState.limitGhostT1;

        if (tg0 == 0 || tg1 == 0) return result;

        uint160 twap = poolState.twap;
        if (twap == 0) return result;

        // Convert T1 to T0 terms to find bottleneck
        uint256 ghost1InT0 = PriceMath.convertAtPrice(tg1, twap, false);

        if (ghost1InT0 <= tg0) {
            result.matchedT0 = ghost1InT0;
            result.matchedT1 = tg1;
        } else {
            result.matchedT0 = tg0;
            result.matchedT1 = PriceMath.convertAtPrice(tg0, twap, true);
        }

        if (result.matchedT0 == 0 || result.matchedT1 == 0) return result;
        result.netted = true;

        // Consume pro-rata from stream and limit on T0 side
        result.streamShareT0 = tg0 > 0 ? (result.matchedT0 * poolState.streamGhostT0) / tg0 : 0;
        result.limitShareT0 = tg0 > 0 ? (result.matchedT0 * poolState.limitGhostT0) / tg0 : 0;
        poolState.streamGhostT0 -= result.streamShareT0;
        poolState.limitGhostT0 -= result.limitShareT0;

        // Same for T1 side
        result.streamShareT1 = tg1 > 0 ? (result.matchedT1 * poolState.streamGhostT1) / tg1 : 0;
        result.limitShareT1 = tg1 > 0 ? (result.matchedT1 * poolState.limitGhostT1) / tg1 : 0;
        poolState.streamGhostT1 -= result.streamShareT1;
        poolState.limitGhostT1 -= result.limitShareT1;

        emit Netted(result.matchedT0, result.matchedT1);
    }

    // ─── Full Accrual Pipeline ────────────────────────────────────────

    function _accrueAll() internal {
        uint256 last = poolState.lastUpdateTime;
        uint256 now_ = block.timestamp;
        if (now_ <= last) return;

        // Phase 1: ACCRUE
        StreamLib.accrueStreamGhost(poolState, streamPool0for1, streamPool1for0, last, now_);

        // Phase 2: NET
        NettingResult memory netResult = _internalNet();
        if (netResult.netted && netResult.matchedT0 > 0 && netResult.matchedT1 > 0) {
            // T0 suppliers earned T1
            if (netResult.streamShareT0 > 0) {
                uint256 earn = (netResult.matchedT1 * netResult.streamShareT0) / netResult.matchedT0;
                StreamLib.recordEarnings(streamPool0for1, earn);
            }
            // T1 suppliers earned T0
            if (netResult.streamShareT1 > 0) {
                uint256 earn = (netResult.matchedT0 * netResult.streamShareT1) / netResult.matchedT1;
                StreamLib.recordEarnings(streamPool1for0, earn);
            }
        }

        // Phase 3: CROSS
        uint256 effectiveEnd = StreamLib.crossEpochs(
            streamPool0for1,
            streamPool1for0,
            sellRateStartingT0,
            sellRateEndingT0,
            earningsFactorAtEpochT0,
            sellRateStartingT1,
            sellRateEndingT1,
            earningsFactorAtEpochT1,
            last,
            now_,
            poolState.epochInterval
        );

        // Phase 4: ACTIVATE limits
        _activateLimits();

        poolState.lastUpdateTime = effectiveEnd;
    }

    // ─── Limit Activation ─────────────────────────────────────────────

    function _activateLimits() internal {
        uint160 twap = poolState.twap;
        if (twap == 0) return;

        // Approximate tick from sqrtPriceX96 (simplified for hackathon)
        // In production, use TickMath.getTickAtSqrtPrice
        int24 twapTick = _approxTickFromSqrtPrice(twap);

        // Activate sell limits where TWAP >= tick
        for (uint256 i = 0; i < activeSellTicks.length; i++) {
            int24 tick = activeSellTicks[i];
            LimitBucket storage bucket = sellBuckets[tick];
            if (!bucket.activated && twapTick >= tick && bucket.totalDeposit > 0) {
                bucket.activated = true;
                poolState.limitGhostT0 += bucket.totalDeposit;
                poolState.totalActiveLimitT0 += bucket.totalDeposit;
            }
        }

        // Activate buy limits where TWAP <= tick
        for (uint256 i = 0; i < activeBuyTicks.length; i++) {
            int24 tick = activeBuyTicks[i];
            LimitBucket storage bucket = buyBuckets[tick];
            if (!bucket.activated && twapTick <= tick && bucket.totalDeposit > 0) {
                bucket.activated = true;
                poolState.limitGhostT1 += bucket.totalDeposit;
                poolState.totalActiveLimitT1 += bucket.totalDeposit;
            }
        }
    }

    function _approxTickFromSqrtPrice(uint160 sqrtPriceX96) internal pure returns (int24) {
        // Using log approximation: tick ≈ 2 * log2(sqrtPrice/2^96) / log2(1.0001)
        // log2(1.0001) ≈ 1/6932
        // For hackathon: simplified integer math
        uint256 Q96 = 1 << 96;
        if (uint256(sqrtPriceX96) == Q96) return 0;
        if (uint256(sqrtPriceX96) > Q96) {
            uint256 ratio = (uint256(sqrtPriceX96) * 1e18) / Q96;
            // Approximate log
            int256 logVal = 0;
            uint256 r = ratio;
            while (r > 1e18 + 1e14) {
                logVal++;
                r = (r * 1e18) / (1e18 + 1e14); // divide by 1.0001 iteratively
                if (logVal > 10000) break; // safety cap
            }
            return int24(int256(logVal));
        } else {
            uint256 ratio = (Q96 * 1e18) / uint256(sqrtPriceX96);
            int256 logVal = 0;
            uint256 r = ratio;
            while (r > 1e18 + 1e14) {
                logVal++;
                r = (r * 1e18) / (1e18 + 1e14);
                if (logVal > 10000) break;
            }
            return -int24(int256(logVal));
        }
    }

    // ─── Execute Fill ─────────────────────────────────────────────────

    function _executeFill(int256 amountSpecified, bool zeroForOne) internal returns (FillResult memory result) {
        _accrueAll();

        uint160 twap = poolState.twap;
        if (twap == 0) return result;

        uint256 available;
        bool consumingT0;
        if (zeroForOne) {
            available = poolState.streamGhostT1 + poolState.limitGhostT1;
            consumingT0 = false;
        } else {
            available = poolState.streamGhostT0 + poolState.limitGhostT0;
            consumingT0 = true;
        }

        if (available == 0 || amountSpecified == 0) return result;

        (uint256 fillOut, uint256 fillIn) = JitFillLib.computeFill(amountSpecified, zeroForOne, twap, available);
        if (fillOut == 0 || fillIn == 0) return result;

        // Distribute earnings (fillIn is the payment)
        _distributeFillEarnings(consumingT0, fillIn);

        // Consume ghost
        JitFillLib.consumeGhostProRata(poolState, consumingT0, fillOut);

        result.fillOut = fillOut;
        result.fillIn = fillIn;
        result.filled = true;

        emit GhostFill(fillOut, fillIn, zeroForOne);
    }

    function _distributeFillEarnings(bool consumingGhostT0, uint256 earnings) internal {
        uint256 total;
        uint256 streamGhost;

        if (consumingGhostT0) {
            total = poolState.streamGhostT0 + poolState.limitGhostT0;
            streamGhost = poolState.streamGhostT0;
        } else {
            total = poolState.streamGhostT1 + poolState.limitGhostT1;
            streamGhost = poolState.streamGhostT1;
        }

        if (total == 0) return;

        if (streamGhost > 0) {
            uint256 streamShare = (earnings * streamGhost) / total;
            if (consumingGhostT0) {
                StreamLib.recordEarnings(streamPool0for1, streamShare);
            } else {
                StreamLib.recordEarnings(streamPool1for0, streamShare);
            }
        }
        // Limit earnings distribution handled via bucket accumulators (simplified)
    }

    // ─── Stream User Functions ────────────────────────────────────────

    function _submitStream(address owner, uint256 amountIn, uint256 duration, bool zeroForOne)
        internal
        returns (bytes32 orderId)
    {
        _accrueAll();

        uint256 epochInterval = poolState.epochInterval;
        uint256 nextEpoch = ((block.timestamp / epochInterval) * epochInterval) + epochInterval;
        uint256 scaledRate = (amountIn * RATE_SCALER) / duration;
        require(scaledRate > 0, "Rate must be > 0");

        uint256 expiration = nextEpoch + duration;

        // Register rate changes
        if (zeroForOne) {
            sellRateStartingT0[nextEpoch] += scaledRate;
            sellRateEndingT0[expiration] += scaledRate;
        } else {
            sellRateStartingT1[nextEpoch] += scaledRate;
            sellRateEndingT1[expiration] += scaledRate;
        }

        // Create order
        orderId = keccak256(abi.encodePacked(owner, expiration, zeroForOne, block.timestamp, streamOrderCount));
        streamOrders[orderId] = StreamOrder({
            owner: owner,
            sellRate: scaledRate,
            earningsFactorLast: zeroForOne
                ? streamPool0for1.earningsFactorCurrent
                : streamPool1for0.earningsFactorCurrent,
            startEpoch: nextEpoch,
            expiration: expiration,
            zeroForOne: zeroForOne
        });
        streamOrderCount++;

        emit StreamSubmitted(orderId, owner, scaledRate, nextEpoch, expiration, zeroForOne);
    }

    function _claimStream(bytes32 orderId) internal returns (uint256 earnings) {
        _accrueAll();
        StreamOrder storage order = streamOrders[orderId];
        require(order.owner != address(0), "Order not found");

        if (order.zeroForOne) {
            earnings =
                StreamLib.syncOrder(streamPool0for1, order, earningsFactorAtEpochT0, poolState.lastUpdateTime);
        } else {
            earnings =
                StreamLib.syncOrder(streamPool1for0, order, earningsFactorAtEpochT1, poolState.lastUpdateTime);
        }

        emit StreamClaimed(orderId, earnings);
    }

    function _cancelStream(bytes32 orderId) internal returns (uint256 refund) {
        _accrueAll();
        StreamOrder storage order = streamOrders[orderId];
        require(order.owner != address(0), "Order not found");

        uint256 effectiveEnd = block.timestamp < order.expiration ? block.timestamp : order.expiration;
        uint256 remainingTime = order.expiration - effectiveEnd;
        refund = (order.sellRate * remainingTime) / RATE_SCALER;

        // Remove ghost share
        if (order.zeroForOne) {
            uint256 rate = streamPool0for1.sellRateCurrent;
            if (rate > 0) {
                uint256 ghostShare = (poolState.streamGhostT0 * order.sellRate) / rate;
                poolState.streamGhostT0 -= ghostShare;
            }
            if (poolState.lastUpdateTime < order.startEpoch) {
                sellRateStartingT0[order.startEpoch] -= order.sellRate;
            } else {
                streamPool0for1.sellRateCurrent -= order.sellRate;
            }
            sellRateEndingT0[order.expiration] -= order.sellRate;
        } else {
            uint256 rate = streamPool1for0.sellRateCurrent;
            if (rate > 0) {
                uint256 ghostShare = (poolState.streamGhostT1 * order.sellRate) / rate;
                poolState.streamGhostT1 -= ghostShare;
            }
            if (poolState.lastUpdateTime < order.startEpoch) {
                sellRateStartingT1[order.startEpoch] -= order.sellRate;
            } else {
                streamPool1for0.sellRateCurrent -= order.sellRate;
            }
            sellRateEndingT1[order.expiration] -= order.sellRate;
        }

        delete streamOrders[orderId];
        emit StreamCancelled(orderId, refund);
    }

    // ─── Limit User Functions ─────────────────────────────────────────

    function _submitSellLimit(address owner, uint256 amount, int24 tick) internal {
        _accrueAll();

        LimitBucket storage bucket = sellBuckets[tick];
        if (bucket.totalDeposit == 0 && !bucket.activated) {
            sellBitmap.setTick(tick);
            activeSellTicks.push(tick);
        }

        _sellOrders[tick].push(
            LimitOrder({owner: owner, original: amount, remaining: amount, startAccumulator: bucket.accumulator})
        );
        bucket.totalDeposit += amount;
        bucket.totalOriginal += amount;

        emit LimitSubmitted(tick, owner, amount, true);
    }

    function _submitBuyLimit(address owner, uint256 amount, int24 tick) internal {
        _accrueAll();

        LimitBucket storage bucket = buyBuckets[tick];
        if (bucket.totalDeposit == 0 && !bucket.activated) {
            buyBitmap.setTick(tick);
            activeBuyTicks.push(tick);
        }

        _buyOrders[tick].push(
            LimitOrder({owner: owner, original: amount, remaining: amount, startAccumulator: bucket.accumulator})
        );
        bucket.totalDeposit += amount;
        bucket.totalOriginal += amount;

        emit LimitSubmitted(tick, owner, amount, false);
    }

    function _claimLimit(address claimant, int24 tick, bool isSell) internal returns (uint256 proceeds, uint256 refund) {
        _accrueAll();

        LimitBucket storage bucket = isSell ? sellBuckets[tick] : buyBuckets[tick];
        LimitOrder[] storage orders = isSell ? _sellOrders[tick] : _buyOrders[tick];

        uint256 i = 0;
        while (i < orders.length) {
            LimitOrder storage order = orders[i];
            if (order.owner == claimant && order.original > 0) {
                // Compute share
                uint256 accDelta = bucket.accumulator > order.startAccumulator
                    ? bucket.accumulator - order.startAccumulator
                    : 0;
                uint256 share = bucket.totalOriginal > 0 ? (order.original * accDelta) / bucket.totalOriginal : 0;
                proceeds += share;
                refund += order.remaining;

                // Update bucket
                bucket.totalDeposit -= order.remaining;
                bucket.totalOriginal -= order.original;

                // Remove order (swap and pop)
                if (i < orders.length - 1) {
                    orders[i] = orders[orders.length - 1];
                }
                orders.pop();
                continue;
            }
            i++;
        }

        // Update ghost counters
        if (refund > 0) {
            if (isSell) {
                poolState.limitGhostT0 -= refund;
                poolState.totalActiveLimitT0 -= refund;
            } else {
                poolState.limitGhostT1 -= refund;
                poolState.totalActiveLimitT1 -= refund;
            }
        }

        emit LimitClaimed(tick, claimant, proceeds, refund);
    }
}
