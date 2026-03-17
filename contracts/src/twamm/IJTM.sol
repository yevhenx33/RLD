// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {MarketId} from "../shared/interfaces/IRLDCore.sol";

/// @title IJTM - Interface for the JIT Time-Weighted Average Market Maker
/// @notice A complete redesign of the Paradigm TWAMM model. Instead of simulating a virtual
///         AMM curve and dumping via poolManager.swap(), this hook operates as a JIT Limit
///         Order Maker with a 3-layer matching engine:
///         Layer 1: Internal netting of opposing streams at Oracle TWAP (free)
///         Layer 2: JIT matching against external takers via beforeSwap (free / earns spread)
///         Layer 3: Dynamic time-based Dutch Auction for arb clearing (gas-only cost)
interface IJTM {
    /* ======== ERRORS ======== */

    error PoolWithNativeNotSupported();
    error InvalidExpirationInterval();
    error ExpirationNotOnInterval(uint256 expiration);
    error ExpirationLessThanBlockTime(uint256 expiration);
    error NotInitialized();
    error OrderAlreadyExists(OrderKey orderKey);
    error OrderDoesNotExist(OrderKey orderKey);
    error OrderAlreadyExpired(OrderKey orderKey);
    error SellRateCannotBeZero();
    error Unauthorized();
    error NothingToClear();
    error NoActiveStream();
    error InsufficientPayment();
    error OracleNotReady();
    error InsufficientDiscount(uint256 actual, uint256 minimum);

    /* ======== STRUCTS ======== */

    /// @notice Per-user order state
    /// @param sellRate Tokens sold per second (scaled by RATE_SCALER)
    /// @param earningsFactorLast Snapshot of global earningsFactor at order creation/last sync
    struct Order {
        uint256 sellRate;
        uint256 earningsFactorLast;
    }

    /// @notice Global stream state per pool direction
    /// @param sellRateCurrent Aggregate sell rate of all active orders
    /// @param earningsFactorCurrent Cumulative earnings per unit sellRate (Q96 fixed point)
    /// @param sellRateEndingAtInterval Mapping: epoch => aggregate sellRate expiring
    /// @param earningsFactorAtInterval Mapping: epoch => earningsFactor snapshot at expiry
    struct StreamPool {
        uint256 sellRateCurrent;
        uint256 earningsFactorCurrent;
        mapping(uint256 => uint256) sellRateEndingAtInterval;
        mapping(uint256 => uint256) earningsFactorAtInterval;
        /// @notice Sell rates that begin at a given interval (Option E: deferred start)
        mapping(uint256 => uint256) sellRateStartingAtInterval;
    }

    /// @notice Core TWAMM state per V4 pool
    /// @param lastUpdateTimestamp Last time accrual was computed
    /// @param lastClearTimestamp Last time the clear() auction was executed
    /// @param accrued0 Ghost balance of token0 waiting to be sold
    /// @param accrued1 Ghost balance of token1 waiting to be sold
    /// @param stream0For1 Stream pool for orders selling token0 -> token1
    /// @param stream1For0 Stream pool for orders selling token1 -> token0
    /// @param orders Per-user order storage
    struct JITState {
        uint256 lastUpdateTimestamp;
        uint256 lastClearTimestamp;
        uint256 accrued0;
        uint256 accrued1;
        StreamPool stream0For1;
        StreamPool stream1For0;
        mapping(bytes32 => Order) orders;
    }

    /// @notice Identifies a specific order
    struct OrderKey {
        address owner;
        uint160 expiration;
        bool zeroForOne;
        uint256 nonce;
    }

    /// @notice Parameters for submitting a new order
    struct SubmitOrderParams {
        PoolKey key;
        bool zeroForOne;
        uint256 duration;
        uint256 amountIn;
    }

    /// @notice Parameters for syncing an order
    struct SyncParams {
        PoolKey key;
        OrderKey orderKey;
    }

    /* ======== EVENTS ======== */

    event SubmitOrder(
        PoolId indexed poolId,
        bytes32 indexed orderId,
        address owner,
        uint256 amountIn,
        uint160 expiration,
        bool zeroForOne,
        uint256 sellRate,
        uint256 earningsFactorLast,
        uint256 startEpoch,
        uint256 nonce
    );

    event CancelOrder(
        PoolId indexed poolId,
        bytes32 indexed orderId,
        address owner,
        uint256 sellTokensRefund
    );

    event InternalMatch(
        PoolId indexed poolId,
        uint256 matched0,
        uint256 matched1
    );

    event JITFill(PoolId indexed poolId, uint256 filledAmount, bool zeroForOne);

    event AuctionClear(
        PoolId indexed poolId,
        address indexed clearer,
        uint256 amount,
        uint256 discount
    );

    /// @notice Emitted when ghost tokens are auto-settled against the AMM pool
    /// @dev Fires at epoch boundaries (when a stream expires) and on cancel
    ///      (when the last order in a stream is cancelled). Ghost is swapped
    ///      against the pool and proceeds recorded as earnings.
    event AutoSettle(
        PoolId indexed poolId,
        uint256 ghostAmount,
        uint256 proceeds,
        bool zeroForOne
    );

    /// @notice Emitted when ghost balance is force-settled during liquidation
    event ForceSettle(
        PoolId indexed poolId,
        uint256 ghostAmount,
        uint256 swapProceeds,
        bool zeroForOne
    );

    /* ======== EXTERNAL FUNCTIONS ======== */

    /// @notice Submit a new streaming order
    function submitOrder(
        SubmitOrderParams calldata params
    ) external returns (bytes32 orderId, OrderKey memory orderKey);

    /// @notice Cancel an active order and refund remaining tokens
    function cancelOrder(
        PoolKey calldata key,
        OrderKey calldata orderKey
    ) external returns (uint256 buyTokensOut, uint256 sellTokensRefund);

    /// @notice Sync an order's earnings (updates tokensOwed)
    function sync(
        SyncParams calldata params
    ) external returns (uint256 earningsAmount);

    /// @notice Sync + claim all owed tokens + auto-delete expired orders
    function syncAndClaimTokens(
        SyncParams calldata params
    ) external returns (uint256 claimed0, uint256 claimed1);

    /// @notice Claim owed tokens for a specific pool
    function claimTokens(
        PoolKey calldata key,
        Currency currency
    ) external returns (uint256 amount);

    /// @notice Layer 3: Clear accumulated ghost balance via dynamic auction
    /// @param key Pool to clear
    /// @param zeroForOne Direction to clear (true = buy accrued token0, false = buy accrued token1)
    /// @param maxAmount Maximum amount to clear
    /// @param minDiscountBps Minimum discount bps required (MEV protection)
    function clear(
        PoolKey calldata key,
        bool zeroForOne,
        uint256 maxAmount,
        uint256 minDiscountBps
    ) external;

    /// @notice View the current ghost balances and discount
    function getStreamState(
        PoolKey calldata key
    )
        external
        view
        returns (
            uint256 accrued0,
            uint256 accrued1,
            uint256 currentDiscount,
            uint256 timeSinceLastClear
        );

    /// @notice View an order's current state
    function getOrder(
        PoolKey calldata key,
        OrderKey calldata orderKey
    ) external view returns (Order memory);

    /// @notice View a stream pool's aggregate state
    function getStreamPool(
        PoolKey calldata key,
        bool zeroForOne
    )
        external
        view
        returns (uint256 sellRateCurrent, uint256 earningsFactorCurrent);

    /// @notice View what a cancel would return without executing it
    function getCancelOrderState(
        PoolKey calldata key,
        OrderKey calldata orderKey
    ) external view returns (uint256 buyTokensOwed, uint256 sellTokensRefund);

    /// @notice Force-settle ghost balance by market-selling into V4 pool
    /// @dev ONLY callable by verified brokers during liquidation
    /// @param key The pool key
    /// @param zeroForOne The sell direction
    /// @param marketId Core market ID for broker verification
    function forceSettle(
        PoolKey calldata key,
        bool zeroForOne,
        MarketId marketId
    ) external;

    /// @notice Returns the accumulator values as of each time seconds ago
    /// @param poolId The ID of the pool
    /// @param secondsAgos Each amount of time to look back, in seconds
    /// @return tickCumulatives The tick * time elapsed since the pool was first initialized
    function observe(
        PoolId poolId,
        uint32[] calldata secondsAgos
    ) external view returns (int56[] memory tickCumulatives);

    /// @notice Increases the cardinality of the oracle array
    /// @param poolId The ID of the pool
    /// @param next The new length of the oracle array
    /// @return cardinalityNext The new length of the oracle array
    function increaseCardinality(
        PoolId poolId,
        uint16 next
    ) external returns (uint16 cardinalityNext);
}
