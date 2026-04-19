// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {FixedPoint96} from "v4-core/src/libraries/FixedPoint96.sol";
import {BitMath} from "v4-core/src/libraries/BitMath.sol";

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
    mapping(bytes32 => mapping(bytes32 => StreamOrder)) public streamOrders;
    // Epoch event index:
    // - epochEventBitmap maps epochIndex -> bit
    // - epochWordBitmap summarizes non-empty epochEventBitmap words
    mapping(bytes32 => mapping(uint256 => uint256)) internal epochEventBitmap;
    mapping(bytes32 => mapping(uint256 => uint256)) internal epochWordBitmap;

    uint256 public orderNonce;

    error Unauthorized();
    error NotIntervalAligned();
    error NothingToClear();
    error InsufficientDiscount();
    error UnauthorizedRouter();
    error OrderDoesNotExist();
    error NoActiveStream();
    error InvalidDuration();
    error InvalidAmount();
    error InvalidPrice();
    error InvalidRouter();
    error InvalidInterval();
    error InvalidMaxDiscountBps();

    uint8 internal constant SETTLE_REASON_EPOCH_CLOSE = 1;
    uint8 internal constant SETTLE_REASON_LAST_CANCEL = 2;
    uint8 internal constant SETTLE_REASON_FORCE_SETTLE = 3;

    event StreamSubmitted(
        bytes32 indexed marketId,
        bytes32 indexed orderId,
        address indexed owner,
        bool zeroForOne,
        uint256 amountIn,
        uint256 startEpoch,
        uint256 expiration,
        uint256 sellRate
    );
    event AuctionCleared(
        bytes32 indexed marketId,
        address indexed solver,
        bool zeroForOne,
        uint256 clearAmount,
        uint256 discountedPayment,
        uint256 discountBps,
        uint256 spotPrice
    );
    event TokensClaimed(bytes32 indexed marketId, bytes32 indexed orderId, address indexed owner, uint256 earningsOut);
    event OrderCancelled(
        bytes32 indexed marketId,
        bytes32 indexed orderId,
        address indexed owner,
        uint256 refund,
        uint256 earnings,
        bool orderStarted,
        bool orderExpired
    );
    event GhostSettled(
        bytes32 indexed marketId,
        bool indexed zeroForOne,
        uint8 indexed reason,
        uint256 ghostIn,
        uint256 amountOut
    );
    event NettingApplied(bytes32 indexed marketId, uint256 consumed0, uint256 consumed1, uint256 spotPrice);
    event GhostTaken(
        bytes32 indexed marketId,
        bool indexed zeroForOne,
        uint256 amountIn,
        uint256 filledOut,
        uint256 inputConsumed,
        uint256 spotPrice
    );
    event ForceSettled(bytes32 indexed marketId, bool indexed zeroForOne, uint256 ghostAmount, uint256 amountOut);

    struct OrderSettlement {
        uint256 earningsOut;
        uint256 effectiveEF;
        uint256 sellTokensRefund;
        bool orderStarted;
        bool orderExpired;
    }

    modifier onlyRouter() {
        if (msg.sender != ghostRouter) revert UnauthorizedRouter();
        _;
    }

    constructor(address _ghostRouter, uint256 _interval, uint256 _maxDiscountBps, uint256 _discountRateScaled) {
        if (_ghostRouter == address(0)) revert InvalidRouter();
        if (_interval == 0) revert InvalidInterval();
        if (_maxDiscountBps > 10_000) revert InvalidMaxDiscountBps();
        ghostRouter = _ghostRouter;
        expirationInterval = _interval;
        maxDiscountBps = _maxDiscountBps;
        discountRateScaled = _discountRateScaled;
    }

    /// @notice Submit a TWAP flow (User Entrypoint)
    function submitStream(bytes32 marketId, bool zeroForOne, uint256 duration, uint256 amountIn)
        external
        override
        nonReentrant
        returns (bytes32 orderId)
    {
        if (duration == 0) revert InvalidDuration();
        if (amountIn == 0) revert InvalidAmount();

        // Securely pull funds directly into the Vault Hub from the User
        IGhostRouter(ghostRouter).pullMarketFunds(marketId, zeroForOne, msg.sender, amountIn);

        _accrueInternal(marketId);

        uint256 nextEpoch = ((block.timestamp / expirationInterval) * expirationInterval) + expirationInterval;
        uint256 expiration = nextEpoch + duration;

        if (expiration % expirationInterval != 0) revert NotIntervalAligned();

        // Calc scale
        uint256 scaledSellRate = (amountIn * RATE_SCALER) / duration;
        if (scaledSellRate == 0) revert InvalidAmount();

        StreamPool storage stream = streamPools[marketId][zeroForOne];
        stream.sellRateStartingAtInterval[nextEpoch] += scaledSellRate;
        stream.sellRateEndingAtInterval[expiration] += scaledSellRate;
        _syncEpochEventBit(marketId, nextEpoch);
        _syncEpochEventBit(marketId, expiration);

        orderId = keccak256(abi.encode(msg.sender, nextEpoch, zeroForOne, ++orderNonce));

        streamOrders[marketId][orderId] = StreamOrder({
            owner: msg.sender,
            sellRate: scaledSellRate,
            earningsFactorLast: stream.earningsFactorCurrent,
            startEpoch: nextEpoch,
            expiration: expiration,
            zeroForOne: zeroForOne
        });

        emit StreamSubmitted(marketId, orderId, msg.sender, zeroForOne, amountIn, nextEpoch, expiration, scaledSellRate);

        return orderId;
    }

    /// @notice Perform internal accounting accrual up to block.timestamp
    function _accrueInternal(bytes32 marketId) internal {
        TwapState storage state = states[marketId];
        if (state.lastUpdateTime == 0) {
            state.lastUpdateTime = block.timestamp;
            state.lastClearTime = block.timestamp;
            state.epochInterval = expirationInterval;
            return;
        }

        uint256 currentTime = block.timestamp;
        if (currentTime <= state.lastUpdateTime) return;

        StreamPool storage pool0 = streamPools[marketId][true];
        StreamPool storage pool1 = streamPools[marketId][false];

        uint256 cursor = state.lastUpdateTime;
        uint256 interval = expirationInterval;
        uint256 nextSearchEpoch = ((cursor / interval) * interval) + interval;

        while (nextSearchEpoch <= currentTime) {
            (bool found, uint256 nextEventEpoch) = _findNextEventEpoch(marketId, nextSearchEpoch, currentTime, interval);
            if (!found) break;

            _accrueGhostSegment(state, pool0, pool1, nextEventEpoch - cursor);
            cursor = nextEventEpoch;

            _crossEpoch(marketId, state, pool0, true, nextEventEpoch);
            _crossEpoch(marketId, state, pool1, false, nextEventEpoch);
            _syncEpochEventBit(marketId, nextEventEpoch);
            nextSearchEpoch = cursor + interval;
        }

        _accrueGhostSegment(state, pool0, pool1, currentTime - cursor);

        state.lastUpdateTime = currentTime;
    }

    /// @notice Cross an epoch boundary for one stream direction.
    function _crossEpoch(bytes32 marketId, TwapState storage state, StreamPool storage stream, bool zeroForOne, uint256 epoch)
        internal
    {
        uint256 starting = stream.sellRateStartingAtInterval[epoch];
        uint256 expiring = stream.sellRateEndingAtInterval[epoch];

        // If all previously-active flow expires at this boundary and ghost remains,
        // settle before mutating rates so those expiring orders receive final proceeds.
        if (expiring > 0 && stream.sellRateCurrent == expiring) {
            _settleGhostForDirection(marketId, state, stream, zeroForOne, SETTLE_REASON_EPOCH_CLOSE);
        }

        if (starting > 0 || expiring > 0) {
            stream.earningsFactorAtInterval[epoch] = stream.earningsFactorCurrent;
        }

        if (expiring > 0) {
            stream.sellRateCurrent -= expiring;
            delete stream.sellRateEndingAtInterval[epoch];
        }

        if (starting > 0) {
            stream.sellRateCurrent += starting;
            delete stream.sellRateStartingAtInterval[epoch];
        }
    }

    function _accrueGhostSegment(
        TwapState storage state,
        StreamPool storage pool0,
        StreamPool storage pool1,
        uint256 segmentDelta
    ) internal {
        if (segmentDelta == 0) return;
        state.streamGhostT0 += (pool0.sellRateCurrent * segmentDelta) / RATE_SCALER;
        state.streamGhostT1 += (pool1.sellRateCurrent * segmentDelta) / RATE_SCALER;
    }

    function _settleGhostForDirection(
        bytes32 marketId,
        TwapState storage state,
        StreamPool storage stream,
        bool zeroForOne,
        uint8 reason
    ) internal returns (uint256 settledGhost, uint256 amountOut) {
        uint256 ghostAmount = zeroForOne ? state.streamGhostT0 : state.streamGhostT1;
        if (ghostAmount == 0) return (0, 0);

        amountOut = IGhostRouter(ghostRouter).settleGhost(marketId, zeroForOne, ghostAmount);
        _recordEarnings(stream, amountOut);

        if (zeroForOne) {
            state.streamGhostT0 = 0;
        } else {
            state.streamGhostT1 = 0;
        }

        emit GhostSettled(marketId, zeroForOne, reason, ghostAmount, amountOut);
        return (ghostAmount, amountOut);
    }

    function _findNextEventEpoch(bytes32 marketId, uint256 fromEpoch, uint256 toTime, uint256 interval)
        internal
        view
        returns (bool found, uint256 nextEventEpoch)
    {
        if (fromEpoch > toTime) return (false, 0);

        uint256 fromIndex = fromEpoch / interval;
        uint256 toIndex = toTime / interval;
        if (fromIndex > toIndex) return (false, 0);

        uint256 nextIndex;
        (found, nextIndex) = _findNextEventIndex(marketId, fromIndex, toIndex);
        if (!found) return (false, 0);

        nextEventEpoch = nextIndex * interval;
    }

    function _findNextEventIndex(bytes32 marketId, uint256 fromIndex, uint256 toIndex)
        internal
        view
        returns (bool found, uint256 nextIndex)
    {
        uint256 fromWord = fromIndex >> 8;
        uint256 toWord = toIndex >> 8;
        uint256 wordIndex = fromWord;

        while (wordIndex <= toWord) {
            uint256 bits = epochEventBitmap[marketId][wordIndex];
            if (bits != 0) {
                if (wordIndex == fromWord) bits &= _maskFromBit(fromIndex & 0xff);
                if (wordIndex == toWord) bits &= _maskToBit(toIndex & 0xff);

                if (bits != 0) {
                    uint256 bitIndex = BitMath.leastSignificantBit(bits);
                    return (true, (wordIndex << 8) | bitIndex);
                }
            }

            if (wordIndex == toWord) break;
            uint256 nextWord = _nextNonEmptyEventWord(marketId, wordIndex + 1, toWord);
            if (nextWord == type(uint256).max) break;
            wordIndex = nextWord;
        }

        return (false, 0);
    }

    function _nextNonEmptyEventWord(bytes32 marketId, uint256 startWord, uint256 endWord)
        internal
        view
        returns (uint256 nextWord)
    {
        if (startWord > endWord) return type(uint256).max;

        uint256 summaryWord = startWord >> 8;
        uint256 lastSummaryWord = endWord >> 8;
        uint256 startSummaryWord = summaryWord;

        while (summaryWord <= lastSummaryWord) {
            uint256 bits = epochWordBitmap[marketId][summaryWord];
            if (bits != 0) {
                if (summaryWord == startSummaryWord) bits &= _maskFromBit(startWord & 0xff);
                if (summaryWord == lastSummaryWord) bits &= _maskToBit(endWord & 0xff);
                if (bits != 0) {
                    uint256 summaryBit = BitMath.leastSignificantBit(bits);
                    return (summaryWord << 8) | summaryBit;
                }
            }

            if (summaryWord == lastSummaryWord) break;
            unchecked {
                ++summaryWord;
            }
        }

        return type(uint256).max;
    }

    function _syncEpochEventBit(bytes32 marketId, uint256 epoch) internal {
        if (_hasEpochEvent(marketId, epoch)) {
            _setEpochEventBit(marketId, epoch);
        } else {
            _clearEpochEventBit(marketId, epoch);
        }
    }

    function _hasEpochEvent(bytes32 marketId, uint256 epoch) internal view returns (bool) {
        StreamPool storage pool0 = streamPools[marketId][true];
        StreamPool storage pool1 = streamPools[marketId][false];

        return pool0.sellRateStartingAtInterval[epoch] != 0 || pool0.sellRateEndingAtInterval[epoch] != 0
            || pool1.sellRateStartingAtInterval[epoch] != 0 || pool1.sellRateEndingAtInterval[epoch] != 0;
    }

    function _setEpochEventBit(bytes32 marketId, uint256 epoch) internal {
        uint256 epochIndex = epoch / expirationInterval;
        uint256 wordIndex = epochIndex >> 8;
        uint256 bitMask = uint256(1) << (epochIndex & 0xff);
        uint256 word = epochEventBitmap[marketId][wordIndex];
        if ((word & bitMask) != 0) return;

        epochEventBitmap[marketId][wordIndex] = word | bitMask;

        uint256 summaryWordIndex = wordIndex >> 8;
        uint256 summaryBitMask = uint256(1) << (wordIndex & 0xff);
        epochWordBitmap[marketId][summaryWordIndex] |= summaryBitMask;
    }

    function _clearEpochEventBit(bytes32 marketId, uint256 epoch) internal {
        uint256 epochIndex = epoch / expirationInterval;
        uint256 wordIndex = epochIndex >> 8;
        uint256 bitMask = uint256(1) << (epochIndex & 0xff);
        uint256 word = epochEventBitmap[marketId][wordIndex];
        if ((word & bitMask) == 0) return;

        uint256 newWord = word & ~bitMask;
        epochEventBitmap[marketId][wordIndex] = newWord;

        if (newWord == 0) {
            uint256 summaryWordIndex = wordIndex >> 8;
            uint256 summaryBitMask = uint256(1) << (wordIndex & 0xff);
            epochWordBitmap[marketId][summaryWordIndex] &= ~summaryBitMask;
        }
    }

    function _maskFromBit(uint256 bitIndex) internal pure returns (uint256) {
        if (bitIndex == 0) return type(uint256).max;
        return type(uint256).max << bitIndex;
    }

    function _maskToBit(uint256 bitIndex) internal pure returns (uint256) {
        if (bitIndex == 255) return type(uint256).max;
        return (uint256(1) << (bitIndex + 1)) - 1;
    }

    /// @inheritdoc ITwapEngine
    function clearAuction(bytes32 marketId, bool zeroForOne, uint256 maxAmount, uint256 minDiscountBps)
        external
        override
        nonReentrant
    {
        _accrueInternal(marketId);

        TwapState storage state = states[marketId];
        uint256 available = zeroForOne ? state.streamGhostT0 : state.streamGhostT1;
        if (available == 0) revert NothingToClear();

        uint256 clearAmount = available > maxAmount ? maxAmount : available;

        uint256 elapsedSinceClear = block.timestamp - state.lastClearTime;
        uint256 discountBps = (elapsedSinceClear * discountRateScaled) / DISCOUNT_RATE_PRECISION;
        if (discountBps > maxDiscountBps) discountBps = maxDiscountBps;
        if (discountBps < minDiscountBps) revert InsufficientDiscount();

        uint256 spotPrice = IGhostRouter(ghostRouter).getSpotPrice(marketId);
        uint256 fullPayment = zeroForOne
            ? Math.mulDiv(clearAmount, spotPrice, PRICE_SCALE)
            : Math.mulDiv(clearAmount, PRICE_SCALE, spotPrice);
        uint256 discountedPayment = fullPayment - Math.mulDiv(fullPayment, discountBps, 10_000);
        if (discountedPayment == 0) revert InvalidAmount();

        // Pull payment first, then dispatch cleared tokens.
        IGhostRouter(ghostRouter).pullMarketFunds(marketId, !zeroForOne, msg.sender, discountedPayment);
        IGhostRouter(ghostRouter).pushMarketFunds(marketId, zeroForOne, msg.sender, clearAmount);

        // Record earnings for the sellers of the consumed ghost
        if (zeroForOne) {
            state.streamGhostT0 -= clearAmount;
            _recordEarnings(streamPools[marketId][true], discountedPayment);
        } else {
            state.streamGhostT1 -= clearAmount;
            _recordEarnings(streamPools[marketId][false], discountedPayment);
        }
        state.lastClearTime = block.timestamp;

        emit AuctionCleared(marketId, msg.sender, zeroForOne, clearAmount, discountedPayment, discountBps, spotPrice);
    }

    /// @inheritdoc ITwapEngine
    function claimTokens(bytes32 marketId, bytes32 orderId) public override nonReentrant returns (uint256 earningsOut) {
        _accrueInternal(marketId);

        StreamOrder storage order = streamOrders[marketId][orderId];
        if (order.sellRate == 0) return 0;

        OrderSettlement memory settlement = _previewOrderSettlementAfterAccrual(marketId, order);
        earningsOut = _claimTokensAfterAccrual(marketId, order, settlement);
        if (earningsOut > 0) {
            emit TokensClaimed(marketId, orderId, order.owner, earningsOut);
        }
    }

    function _claimTokensAfterAccrual(bytes32 marketId, StreamOrder storage order, OrderSettlement memory settlement)
        internal
        returns (uint256 earningsOut)
    {
        earningsOut = settlement.earningsOut;
        if (earningsOut > 0) {
            order.earningsFactorLast = settlement.effectiveEF;
            IGhostRouter(ghostRouter).pushMarketFunds(marketId, !order.zeroForOne, order.owner, earningsOut);
        }
    }

    /// @inheritdoc ITwapEngine
    function cancelOrder(bytes32 marketId, bytes32 orderId)
        external
        override
        nonReentrant
        returns (uint256 refund, uint256 earnings)
    {
        _accrueInternal(marketId);

        StreamOrder storage orderRef = streamOrders[marketId][orderId];
        if (orderRef.sellRate == 0) revert OrderDoesNotExist();
        if (orderRef.owner != msg.sender) revert Unauthorized();

        StreamOrder memory order = orderRef;
        TwapState storage state = states[marketId];
        StreamPool storage stream = streamPools[marketId][order.zeroForOne];
        bool orderStarted = state.lastUpdateTime >= order.startEpoch;
        bool orderExpired = state.lastUpdateTime >= order.expiration;

        // If this is the final active order in the direction, settle outstanding
        // ghost before claiming so final proceeds are not left unclaimable.
        if (!orderExpired && orderStarted && stream.sellRateCurrent == order.sellRate) {
            _settleGhostForDirection(marketId, state, stream, order.zeroForOne, SETTLE_REASON_LAST_CANCEL);
        }

        OrderSettlement memory settlement = _previewOrderSettlementAfterAccrual(marketId, orderRef);
        earnings = _claimTokensAfterAccrual(marketId, orderRef, settlement);
        if (earnings > 0) {
            emit TokensClaimed(marketId, orderId, order.owner, earnings);
        }

        // Expired orders have no refundable principal left.
        if (settlement.orderExpired) {
            delete streamOrders[marketId][orderId];
            emit OrderCancelled(marketId, orderId, msg.sender, 0, earnings, settlement.orderStarted, true);
            return (0, earnings);
        }

        if (settlement.orderStarted) {
            stream.sellRateCurrent -= order.sellRate;
            stream.sellRateEndingAtInterval[order.expiration] -= order.sellRate;
            _syncEpochEventBit(marketId, order.expiration);
            refund = settlement.sellTokensRefund;
        } else {
            // Order hasn't started — remove from starting map using stored startEpoch
            stream.sellRateEndingAtInterval[order.expiration] -= order.sellRate;
            stream.sellRateStartingAtInterval[order.startEpoch] -= order.sellRate;
            _syncEpochEventBit(marketId, order.expiration);
            _syncEpochEventBit(marketId, order.startEpoch);
            refund = settlement.sellTokensRefund;
        }

        delete streamOrders[marketId][orderId];
        if (refund > 0) {
            IGhostRouter(ghostRouter).pushMarketFunds(marketId, order.zeroForOne, msg.sender, refund);
        }

        emit OrderCancelled(marketId, orderId, msg.sender, refund, earnings, settlement.orderStarted, false);
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
    function applyNettingResult(bytes32 marketId, uint256 consumed0, uint256 consumed1, uint256 spotPrice)
        external
        override
        onlyRouter
    {
        if (spotPrice == 0) revert InvalidPrice();
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

        if (consumed0 > 0 || consumed1 > 0) {
            emit NettingApplied(marketId, consumed0, consumed1, spotPrice);
        }
    }

    /// @notice Taker intercepts remaining directional ghost after netting.
    /// @param amountIn Taker's remaining input budget (in TokenIn denomination)
    /// @param spotPrice Oracle price: Token1 per Token0, scaled by 1e18
    /// @return filledOut Amount of output token filled from ghost
    /// @return inputConsumed Amount of input token consumed (differs from filledOut at non-1:1 prices)
    function takeGhost(bytes32 marketId, bool zeroForOne, uint256 amountIn, uint256 spotPrice)
        external
        override
        onlyRouter
        returns (uint256 filledOut, uint256 inputConsumed)
    {
        if (spotPrice == 0) revert InvalidPrice();
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
                inputConsumed = Math.mulDiv(filledOut, PRICE_SCALE, spotPrice, Math.Rounding.Ceil);
                if (inputConsumed > amountIn) inputConsumed = amountIn;
                state.streamGhostT1 -= filledOut;
                // Ghost Token1 sellers (false pool) earn Token0 from the Taker
                _recordEarnings(streamPools[marketId][false], inputConsumed);
            } else {
                inputConsumed = Math.mulDiv(filledOut, spotPrice, PRICE_SCALE, Math.Rounding.Ceil);
                if (inputConsumed > amountIn) inputConsumed = amountIn;
                state.streamGhostT0 -= filledOut;
                // Ghost Token0 sellers (true pool) earn Token1 from the Taker
                _recordEarnings(streamPools[marketId][true], inputConsumed);
            }

            emit GhostTaken(marketId, zeroForOne, amountIn, filledOut, inputConsumed, spotPrice);
        }
    }

    /// @notice Record earnings directly into a stream's earningsFactor
    function _recordEarnings(StreamPool storage stream, uint256 earnings) internal {
        if (stream.sellRateCurrent == 0 || earnings == 0) return;
        uint256 earningsFactor = Math.mulDiv(earnings, FixedPoint96.Q96 * RATE_SCALER, stream.sellRateCurrent);
        stream.earningsFactorCurrent += earningsFactor;
    }

    function _previewOrderSettlementAfterAccrual(bytes32 marketId, StreamOrder storage order)
        internal
        view
        returns (OrderSettlement memory settlement)
    {
        TwapState storage state = states[marketId];
        settlement = _previewOrderSettlementAt(marketId, order, state.lastUpdateTime);
    }

    function _previewOrderSettlementAt(bytes32 marketId, StreamOrder storage order, uint256 asOfTime)
        internal
        view
        returns (OrderSettlement memory settlement)
    {
        TwapState storage state = states[marketId];
        StreamPool storage stream = streamPools[marketId][order.zeroForOne];
        uint256 committedLastUpdateTime = state.lastUpdateTime;
        if (asOfTime < committedLastUpdateTime) {
            asOfTime = committedLastUpdateTime;
        }

        (settlement.earningsOut, settlement.effectiveEF) =
            _computeEarningsAt(stream, order, committedLastUpdateTime, asOfTime);

        settlement.orderExpired = asOfTime >= order.expiration;
        if (settlement.orderExpired) return settlement;

        settlement.orderStarted = asOfTime >= order.startEpoch;
        if (settlement.orderStarted) {
            uint256 remainingSeconds = order.expiration - asOfTime;
            settlement.sellTokensRefund = (order.sellRate * remainingSeconds) / RATE_SCALER;
        } else {
            uint256 duration = order.expiration - order.startEpoch;
            settlement.sellTokensRefund = (order.sellRate * duration) / RATE_SCALER;
        }
    }

    /// @notice Shared earnings computation used by claim/cancel previews.
    ///         Returns the earnings amount and effective earningsFactor for snapshot update.
    function _computeEarningsAt(
        StreamPool storage stream,
        StreamOrder storage order,
        uint256 committedLastUpdateTime,
        uint256 asOfTime
    )
        internal
        view
        returns (uint256 earningsOut, uint256 effectiveEF)
    {
        effectiveEF = stream.earningsFactorCurrent;

        // Cap at expiration snapshot if this preview timestamp is at/after expiry.
        // If the expiry epoch hasn't been committed yet, the effective snapshot equals
        // current EF because no post-commit earnings can appear without a state mutation.
        if (asOfTime >= order.expiration) {
            uint256 expirySnap =
                _snapshotAtOrCurrent(stream, order.expiration, committedLastUpdateTime, stream.earningsFactorCurrent);
            if (expirySnap < effectiveEF) effectiveEF = expirySnap;
        }

        // Floor at start epoch snapshot for deferred-start orders
        uint256 effectiveEFL = order.earningsFactorLast;
        bool orderStarted = asOfTime >= order.startEpoch;
        if (!orderStarted) {
            effectiveEFL = effectiveEF;
        } else {
            uint256 startSnap =
                _snapshotAtOrCurrent(stream, order.startEpoch, committedLastUpdateTime, stream.earningsFactorCurrent);
            if (startSnap > effectiveEFL) effectiveEFL = startSnap;
        }

        uint256 delta = effectiveEF > effectiveEFL ? effectiveEF - effectiveEFL : 0;
        if (delta > 0) {
            earningsOut = Math.mulDiv(order.sellRate, delta, FixedPoint96.Q96 * RATE_SCALER);
        }
    }

    function _snapshotAtOrCurrent(
        StreamPool storage stream,
        uint256 epoch,
        uint256 committedLastUpdateTime,
        uint256 fallbackCurrentEF
    ) internal view returns (uint256) {
        if (committedLastUpdateTime < epoch) {
            return fallbackCurrentEF;
        }
        return stream.earningsFactorAtInterval[epoch];
    }

    /// @notice Force-settle all ghost for a direction into V4 AMM.
    ///         Called by PrimeBroker during liquidation to crystallize ghost value.
    function forceSettle(bytes32 marketId, bool zeroForOne) external override nonReentrant onlyRouter {
        _accrueInternal(marketId);

        TwapState storage state = states[marketId];
        uint256 ghostAmount = zeroForOne ? state.streamGhostT0 : state.streamGhostT1;
        if (ghostAmount == 0) return;

        StreamPool storage stream = streamPools[marketId][zeroForOne];
        if (stream.sellRateCurrent == 0) revert NoActiveStream();

        (, uint256 amountOut) = _settleGhostForDirection(marketId, state, stream, zeroForOne, SETTLE_REASON_FORCE_SETTLE);
        emit ForceSettled(marketId, zeroForOne, ghostAmount, amountOut);
    }

    /// @notice Preview what a user would receive if cancelling now (view-only, no state mutation).
    ///         Used by PrimeBrokerLens for solvency/NAV valuation.
    /// @return buyTokensOwed  Earned output tokens (not yet claimed)
    /// @return sellTokensRefund Refund of unsold input tokens
    function getCancelOrderState(bytes32 marketId, bytes32 orderId)
        external
        view
        override
        returns (uint256 buyTokensOwed, uint256 sellTokensRefund)
    {
        StreamOrder storage order = streamOrders[marketId][orderId];
        if (order.sellRate == 0) return (0, 0);

        OrderSettlement memory settlement = _previewOrderSettlementAfterAccrual(marketId, order);
        buyTokensOwed = settlement.earningsOut;
        sellTokensRefund = settlement.sellTokensRefund;
    }

    /// @inheritdoc ITwapEngine
    function getCancelOrderStateExact(bytes32 marketId, bytes32 orderId)
        external
        view
        override
        returns (uint256 buyTokensOwed, uint256 sellTokensRefund)
    {
        StreamOrder storage order = streamOrders[marketId][orderId];
        if (order.sellRate == 0) return (0, 0);

        OrderSettlement memory settlement = _previewOrderSettlementAt(marketId, order, block.timestamp);
        buyTokensOwed = settlement.earningsOut;
        sellTokensRefund = settlement.sellTokensRefund;
    }
}
