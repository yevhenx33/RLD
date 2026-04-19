// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import {ILimitEngine} from "./interfaces/ILimitEngine.sol";
import {IGhostRouter} from "./interfaces/IGhostRouter.sol";

/// @title LimitEngine
/// @notice IGhostEngine spoke for demand-triggered, one-way activated limit orders.
/// @dev Once a bucket is activated, all inventory merges into a single active pool per direction.
contract LimitEngine is ILimitEngine, ReentrancyGuard {
    uint256 public constant PRICE_SCALE = 1e18;
    uint256 public constant FACTOR_SCALE = 1e18;

    address public immutable ghostRouter;
    uint256 public orderNonce;

    // marketId => direction => global active pool
    mapping(bytes32 => mapping(bool => ActivePool)) public activePools;
    // marketId => direction => triggerPrice => bucket
    mapping(bytes32 => mapping(bool => mapping(uint256 => LimitBucket))) public limitBuckets;
    // marketId => orderId => order
    mapping(bytes32 => mapping(bytes32 => LimitOrder)) public limitOrders;

    // Sparse index of bucket prices per market/direction for demand-trigger scans.
    mapping(bytes32 => mapping(bool => uint256[])) internal bucketPrices;
    mapping(bytes32 => mapping(bool => mapping(uint256 => bool))) internal bucketPriceIndexed;

    error InvalidRouter();
    error UnauthorizedRouter();
    error InvalidAmount();
    error InvalidPrice();
    error OrderDoesNotExist();
    error UnauthorizedOrderOwner();
    error InconsistentPoolState();

    event LimitOrderSubmitted(
        bytes32 indexed marketId,
        bytes32 indexed orderId,
        address indexed owner,
        bool zeroForOne,
        uint256 amountIn,
        uint256 triggerPrice,
        bool activatedOnSubmit
    );
    event LimitBucketActivated(
        bytes32 indexed marketId,
        bool indexed zeroForOne,
        uint256 indexed triggerPrice,
        uint256 activatedShares,
        uint256 spotPrice
    );
    event TokensClaimed(bytes32 indexed marketId, bytes32 indexed orderId, address indexed owner, uint256 earningsOut);
    event OrderCancelled(
        bytes32 indexed marketId,
        bytes32 indexed orderId,
        address indexed owner,
        uint256 refund,
        uint256 earningsOut,
        bool activated
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

    modifier onlyRouter() {
        if (msg.sender != ghostRouter) revert UnauthorizedRouter();
        _;
    }

    constructor(address _ghostRouter) {
        if (_ghostRouter == address(0)) revert InvalidRouter();
        ghostRouter = _ghostRouter;
    }

    /// @inheritdoc ILimitEngine
    function submitLimitOrder(bytes32 marketId, bool zeroForOne, uint256 triggerPrice, uint256 amountIn)
        external
        nonReentrant
        returns (bytes32 orderId)
    {
        if (triggerPrice == 0) revert InvalidPrice();
        if (amountIn == 0) revert InvalidAmount();

        // Pull maker sell-side inventory directly into the hub vault.
        IGhostRouter(ghostRouter).pullMarketFunds(marketId, zeroForOne, msg.sender, amountIn);

        orderId = keccak256(abi.encode(msg.sender, marketId, zeroForOne, triggerPrice, ++orderNonce));

        LimitOrder storage order = limitOrders[marketId][orderId];
        order.owner = msg.sender;
        order.shares = amountIn;
        order.triggerPrice = triggerPrice;
        order.zeroForOne = zeroForOne;

        LimitBucket storage bucket = limitBuckets[marketId][zeroForOne][triggerPrice];
        bucket.totalShares += amountIn;

        if (!bucketPriceIndexed[marketId][zeroForOne][triggerPrice]) {
            bucketPriceIndexed[marketId][zeroForOne][triggerPrice] = true;
            bucketPrices[marketId][zeroForOne].push(triggerPrice);
        }

        bool activatedOnSubmit = bucket.activated;
        if (activatedOnSubmit) {
            ActivePool storage pool = activePools[marketId][zeroForOne];
            order.activationSnapshotSet = true;
            order.earningsFactorLast = pool.earningsFactor;
            order.depletionFactorAtActivation = pool.depletionFactor;

            pool.totalShares += amountIn;
            pool.remainingInput += amountIn;
        }

        emit LimitOrderSubmitted(marketId, orderId, msg.sender, zeroForOne, amountIn, triggerPrice, activatedOnSubmit);
    }

    /// @inheritdoc ILimitEngine
    function claimTokens(bytes32 marketId, bytes32 orderId) external nonReentrant returns (uint256 earningsOut) {
        LimitOrder storage order = limitOrders[marketId][orderId];
        if (order.shares == 0) revert OrderDoesNotExist();
        if (order.owner != msg.sender) revert UnauthorizedOrderOwner();

        LimitBucket storage bucket = limitBuckets[marketId][order.zeroForOne][order.triggerPrice];
        bool activated = _initializeOrderSnapshotsIfActivated(order, bucket);
        if (!activated) {
            emit TokensClaimed(marketId, orderId, msg.sender, 0);
            return 0;
        }

        ActivePool storage pool = activePools[marketId][order.zeroForOne];
        earningsOut = _pendingEarnings(order, pool);
        order.earningsFactorLast = pool.earningsFactor;

        if (earningsOut > 0) {
            // Sell token0 orders receive token1 and vice-versa.
            IGhostRouter(ghostRouter).pushMarketFunds(marketId, !order.zeroForOne, msg.sender, earningsOut);
        }

        emit TokensClaimed(marketId, orderId, msg.sender, earningsOut);
    }

    /// @inheritdoc ILimitEngine
    function cancelOrder(bytes32 marketId, bytes32 orderId)
        external
        nonReentrant
        returns (uint256 refund, uint256 earningsOut)
    {
        LimitOrder storage order = limitOrders[marketId][orderId];
        if (order.shares == 0) revert OrderDoesNotExist();
        if (order.owner != msg.sender) revert UnauthorizedOrderOwner();

        LimitBucket storage bucket = limitBuckets[marketId][order.zeroForOne][order.triggerPrice];
        if (bucket.totalShares < order.shares) revert InconsistentPoolState();
        bucket.totalShares -= order.shares;

        bool activated = _initializeOrderSnapshotsIfActivated(order, bucket);
        if (activated) {
            ActivePool storage pool = activePools[marketId][order.zeroForOne];
            earningsOut = _pendingEarnings(order, pool);

            refund = _remainingInput(order, pool);
            if (refund > pool.remainingInput) refund = pool.remainingInput;

            if (order.shares > pool.totalShares) revert InconsistentPoolState();
            pool.totalShares -= order.shares;
            pool.remainingInput -= refund;
        } else {
            refund = order.shares;
        }

        bool zeroForOne = order.zeroForOne;
        delete limitOrders[marketId][orderId];

        if (refund > 0) {
            IGhostRouter(ghostRouter).pushMarketFunds(marketId, zeroForOne, msg.sender, refund);
        }
        if (earningsOut > 0) {
            IGhostRouter(ghostRouter).pushMarketFunds(marketId, !zeroForOne, msg.sender, earningsOut);
        }

        emit OrderCancelled(marketId, orderId, msg.sender, refund, earningsOut, activated);
    }

    /// @inheritdoc ILimitEngine
    function getOrderState(bytes32 marketId, bytes32 orderId)
        external
        view
        returns (bool activated, uint256 claimableOut, uint256 refundableIn)
    {
        LimitOrder storage order = limitOrders[marketId][orderId];
        if (order.shares == 0) return (false, 0, 0);

        LimitBucket storage bucket = limitBuckets[marketId][order.zeroForOne][order.triggerPrice];
        if (!bucket.activated) {
            return (false, 0, order.shares);
        }

        ActivePool storage pool = activePools[marketId][order.zeroForOne];

        uint256 earningsFactorLast =
            order.activationSnapshotSet ? order.earningsFactorLast : bucket.activationEarningsFactor;
        uint256 depletionStart =
            order.activationSnapshotSet ? order.depletionFactorAtActivation : bucket.activationDepletionFactor;

        claimableOut = _pendingEarningsFromFactors(order.shares, earningsFactorLast, pool.earningsFactor);
        refundableIn = _remainingInputFromFactors(order.shares, depletionStart, pool.depletionFactor);
        if (refundableIn > pool.remainingInput) refundableIn = pool.remainingInput;
        activated = true;
    }

    /// @notice Router hook: activate pending buckets and expose directional ghost inventory.
    function syncAndFetchGhost(bytes32 marketId) external override onlyRouter returns (uint256 ghost0, uint256 ghost1) {
        uint256 spotPrice = IGhostRouter(ghostRouter).getSpotPrice(marketId);
        if (spotPrice == 0) revert InvalidPrice();

        _activatePendingBuckets(marketId, spotPrice);
        return (activePools[marketId][true].remainingInput, activePools[marketId][false].remainingInput);
    }

    /// @notice Router hook: apply global netting consumption and credit proceeds to makers.
    function applyNettingResult(bytes32 marketId, uint256 consumed0, uint256 consumed1, uint256 spotPrice)
        external
        override
        onlyRouter
    {
        if (spotPrice == 0) revert InvalidPrice();

        _activatePendingBuckets(marketId, spotPrice);

        if (consumed0 > 0) {
            uint256 actualConsumed0 = _consumeInput(activePools[marketId][true], consumed0);
            if (actualConsumed0 > 0) {
                uint256 token1Earned = Math.mulDiv(actualConsumed0, spotPrice, PRICE_SCALE);
                _recordEarnings(activePools[marketId][true], token1Earned);
                consumed0 = actualConsumed0;
            } else {
                consumed0 = 0;
            }
        }

        if (consumed1 > 0) {
            uint256 actualConsumed1 = _consumeInput(activePools[marketId][false], consumed1);
            if (actualConsumed1 > 0) {
                uint256 token0Earned = Math.mulDiv(actualConsumed1, PRICE_SCALE, spotPrice);
                _recordEarnings(activePools[marketId][false], token0Earned);
                consumed1 = actualConsumed1;
            } else {
                consumed1 = 0;
            }
        }

        if (consumed0 > 0 || consumed1 > 0) {
            emit NettingApplied(marketId, consumed0, consumed1, spotPrice);
        }
    }

    /// @notice Router hook: taker intercept against active directional ghost.
    function takeGhost(bytes32 marketId, bool zeroForOne, uint256 amountIn, uint256 spotPrice)
        external
        override
        onlyRouter
        returns (uint256 filledOut, uint256 inputConsumed)
    {
        if (amountIn == 0) return (0, 0);
        if (spotPrice == 0) revert InvalidPrice();

        _activatePendingBuckets(marketId, spotPrice);

        // Taker zeroForOne=true consumes token1 ghost from false-direction pool, and vice-versa.
        bool sourceDirection = !zeroForOne;
        ActivePool storage pool = activePools[marketId][sourceDirection];
        uint256 availableGhost = pool.remainingInput;
        if (availableGhost == 0) return (0, 0);

        uint256 desiredOut =
            zeroForOne ? Math.mulDiv(amountIn, spotPrice, PRICE_SCALE) : Math.mulDiv(amountIn, PRICE_SCALE, spotPrice);

        filledOut = desiredOut > availableGhost ? availableGhost : desiredOut;
        if (filledOut == 0) return (0, 0);

        inputConsumed = zeroForOne
            ? Math.mulDiv(filledOut, PRICE_SCALE, spotPrice, Math.Rounding.Ceil)
            : Math.mulDiv(filledOut, spotPrice, PRICE_SCALE, Math.Rounding.Ceil);
        if (inputConsumed > amountIn) inputConsumed = amountIn;

        uint256 actualConsumed = _consumeInput(pool, filledOut);
        if (actualConsumed < filledOut) {
            filledOut = actualConsumed;
            inputConsumed = zeroForOne
                ? Math.mulDiv(filledOut, PRICE_SCALE, spotPrice, Math.Rounding.Ceil)
                : Math.mulDiv(filledOut, spotPrice, PRICE_SCALE, Math.Rounding.Ceil);
            if (inputConsumed > amountIn) inputConsumed = amountIn;
        }

        _recordEarnings(pool, inputConsumed);
        emit GhostTaken(marketId, zeroForOne, amountIn, filledOut, inputConsumed, spotPrice);
    }

    function getBucketPriceCount(bytes32 marketId, bool zeroForOne) external view returns (uint256) {
        return bucketPrices[marketId][zeroForOne].length;
    }

    function getBucketPriceAt(bytes32 marketId, bool zeroForOne, uint256 index) external view returns (uint256) {
        return bucketPrices[marketId][zeroForOne][index];
    }

    function _activatePendingBuckets(bytes32 marketId, uint256 spotPrice) internal {
        _activateDirection(marketId, true, spotPrice);
        _activateDirection(marketId, false, spotPrice);
    }

    function _activateDirection(bytes32 marketId, bool zeroForOne, uint256 spotPrice) internal {
        uint256[] storage prices = bucketPrices[marketId][zeroForOne];
        ActivePool storage pool = activePools[marketId][zeroForOne];

        for (uint256 i = 0; i < prices.length; ++i) {
            uint256 triggerPrice = prices[i];
            LimitBucket storage bucket = limitBuckets[marketId][zeroForOne][triggerPrice];
            if (bucket.activated || bucket.totalShares == 0) continue;
            if (!_isExecutable(zeroForOne, triggerPrice, spotPrice)) continue;

            bucket.activated = true;
            bucket.activationEarningsFactor = pool.earningsFactor;
            bucket.activationDepletionFactor = pool.depletionFactor;

            pool.totalShares += bucket.totalShares;
            pool.remainingInput += bucket.totalShares;

            emit LimitBucketActivated(marketId, zeroForOne, triggerPrice, bucket.totalShares, spotPrice);
        }
    }

    function _isExecutable(bool zeroForOne, uint256 triggerPrice, uint256 spotPrice) internal pure returns (bool) {
        // zeroForOne=true (sell token0): activate when spot >= trigger.
        // zeroForOne=false (sell token1): activate when spot <= trigger.
        return zeroForOne ? spotPrice >= triggerPrice : spotPrice <= triggerPrice;
    }

    function _consumeInput(ActivePool storage pool, uint256 requested) internal returns (uint256 consumed) {
        if (requested == 0 || pool.remainingInput == 0) return 0;
        consumed = requested > pool.remainingInput ? pool.remainingInput : requested;
        pool.remainingInput -= consumed;

        if (pool.totalShares > 0) {
            pool.depletionFactor += Math.mulDiv(consumed, FACTOR_SCALE, pool.totalShares);
        }
    }

    function _recordEarnings(ActivePool storage pool, uint256 earnings) internal {
        if (earnings == 0 || pool.totalShares == 0) return;
        pool.earningsFactor += Math.mulDiv(earnings, FACTOR_SCALE, pool.totalShares);
    }

    function _initializeOrderSnapshotsIfActivated(LimitOrder storage order, LimitBucket storage bucket)
        internal
        returns (bool activated)
    {
        if (order.activationSnapshotSet) return true;
        if (!bucket.activated) return false;

        order.activationSnapshotSet = true;
        order.earningsFactorLast = bucket.activationEarningsFactor;
        order.depletionFactorAtActivation = bucket.activationDepletionFactor;
        return true;
    }

    function _pendingEarnings(LimitOrder storage order, ActivePool storage pool) internal view returns (uint256) {
        return _pendingEarningsFromFactors(order.shares, order.earningsFactorLast, pool.earningsFactor);
    }

    function _pendingEarningsFromFactors(uint256 shares, uint256 factorLast, uint256 factorCurrent)
        internal
        pure
        returns (uint256)
    {
        if (shares == 0 || factorCurrent <= factorLast) return 0;
        return Math.mulDiv(shares, factorCurrent - factorLast, FACTOR_SCALE);
    }

    function _remainingInput(LimitOrder storage order, ActivePool storage pool) internal view returns (uint256) {
        return _remainingInputFromFactors(order.shares, order.depletionFactorAtActivation, pool.depletionFactor);
    }

    function _remainingInputFromFactors(uint256 shares, uint256 depletionStart, uint256 depletionCurrent)
        internal
        pure
        returns (uint256)
    {
        if (shares == 0) return 0;
        if (depletionCurrent <= depletionStart) return shares;

        uint256 consumed = Math.mulDiv(shares, depletionCurrent - depletionStart, FACTOR_SCALE);
        if (consumed >= shares) return 0;
        return shares - consumed;
    }
}
