// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.26;

import {BaseHook} from "v4-periphery/src/utils/BaseHook.sol";
import {IHooks, Hooks} from "v4-core/src/libraries/Hooks.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {SafeCast} from "v4-core/src/libraries/SafeCast.sol";
import {IERC20Minimal} from "v4-core/src/interfaces/external/IERC20Minimal.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {
    BeforeSwapDelta,
    BeforeSwapDeltaLibrary,
    toBeforeSwapDelta
} from "v4-core/src/types/BeforeSwapDelta.sol";
import {
    ModifyLiquidityParams,
    SwapParams
} from "v4-core/src/types/PoolOperation.sol";
import {FixedPoint96} from "v4-core/src/libraries/FixedPoint96.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Owned} from "solmate/src/auth/Owned.sol";

import {IJTM} from "./IJTM.sol";
import {TransferHelper} from "./libraries/TransferHelper.sol";
import {TwapOracle} from "./libraries/TwapOracle.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";
import {IRLDCore, MarketId} from "../shared/interfaces/IRLDCore.sol";
import {IBrokerVerifier} from "../shared/interfaces/IBrokerVerifier.sol";

/// @dev Scaling factor for sell rates to maintain precision
uint256 constant RATE_SCALER = 1e18;

/// @title JTM — Just-In-Time Time-Weighted Average Market Maker
/// @notice Redesign of Paradigm's TWAMM. Keeps O(1) accounting (aggregated sellRates +
///         earningsFactor snapshots) but replaces the broken execution layer (virtual curve
///         simulation → poolManager.swap dump) with a 3-layer matching engine:
///         Layer 1: Internal netting of opposing streams at TWAP (free)
///         Layer 2: JIT matching against external takers via beforeSwap (free)
///         Layer 3: Dynamic time-based auction for arb clearing (gas-only cost)
/// @author Zaha Studio
contract JTM is BaseHook, Owned, ReentrancyGuard, IJTM {
    using TransferHelper for IERC20Minimal;
    using PoolIdLibrary for PoolKey;
    using TickMath for int24;
    using SafeCast for uint256;
    using StateLibrary for IPoolManager;
    using TwapOracle for mapping(uint256 => TwapOracle.Observation);
    using TwapOracle for TwapOracle.State;
    using CurrencySettler for Currency;

    /* ======================================================================== */
    /*                              CONSTANTS                                   */
    /* ======================================================================== */

    /// @notice Epoch interval for order expiry bucketing (e.g. 3600 = 1 hour)
    uint256 public immutable expirationInterval;

    /// @notice Discount growth rate: basis points per second, scaled by DISCOUNT_RATE_PRECISION
    /// @dev Default 3000 (= 0.3 bps/s = 0.003%/s). Divide by DISCOUNT_RATE_PRECISION for real bps/s.
    uint256 public discountRateScaled;

    /// @notice Precision for discountRateScaled: 10_000 = 1.0 bps/s
    uint256 public constant DISCOUNT_RATE_PRECISION = 10_000;

    /// @notice Maximum discount cap in basis points
    /// @dev Default 200 (= 2%). Set in constructor.
    uint256 public maxDiscountBps;

    /// @notice TWAP observation window in seconds for pricing
    /// @dev Default 300 (5 minutes). Set in constructor.
    uint32 public twapWindow;

    /* ======================================================================== */
    /*                              STATE                                       */
    /* ======================================================================== */

    /// @notice Core JTM state per pool
    mapping(PoolId => JITState) internal poolStates;

    /// @notice Tokens owed to users from order earnings (scoped per pool)
    mapping(PoolId => mapping(Currency => mapping(address => uint256)))
        public tokensOwed;

    /// @notice TWAP oracle observations
    mapping(PoolId => mapping(uint256 => TwapOracle.Observation))
        public observations;
    mapping(PoolId => TwapOracle.State) public oracleStates;

    /// @notice Reference to RLDCore (wired post-deployment via setRldCore)
    address public rldCore;

    /// @notice Asset-specific price boundaries per pool (sqrtPriceX96 min/max)
    struct PriceBounds {
        uint160 min;
        uint160 max;
    }

    /// @notice Price bounds per pool (if bounds.max == 0, no bounds are set)
    mapping(PoolId => PriceBounds) public priceBounds;

    /// @notice Authorized factory address (can call setPriceBounds)
    address public authorizedFactory;

    // NOTE: collectedDust mapping removed — auto-settle eliminates dust entirely

    /// @notice Deferred settle: queued by _preEpochSettle (inside hook callbacks),
    ///         executed by _executePendingSettles (in external functions only).
    /// @dev ghostAmount is the sell-token ghost to swap via AMM.
    ///      sellRateSnapshot is the sellRateCurrent at queue time (for _recordEarnings).
    struct PendingSettle {
        uint256 ghostAmount;
        uint256 sellRateSnapshot;
    }
    mapping(PoolId => PendingSettle) internal _pendingSettle0;
    mapping(PoolId => PendingSettle) internal _pendingSettle1;

    /// @notice Per-owner auto-incrementing nonce for unique order IDs
    mapping(address => uint256) public orderNonces;

    /* ======================================================================== */
    /*                           CONSTRUCTOR                                    */
    /* ======================================================================== */

    constructor(
        IPoolManager _manager,
        uint256 _expirationInterval,
        address initialOwner,
        address _rldCore
    ) BaseHook(_manager) Owned(initialOwner) {
        if (_expirationInterval == 0) revert InvalidExpirationInterval();
        expirationInterval = _expirationInterval;
        rldCore = _rldCore;
        // L2: Sensible defaults
        discountRateScaled = 3000; // 0.3 bps/s (3000 / 10_000 = 0.3)
        maxDiscountBps = 200; // 2% max
        twapWindow = 300; // 5-minute TWAP
    }

    /* ======================================================================== */
    /*                        ADMIN CONFIGURATION                               */
    /* ======================================================================== */

    /// @notice Wire the RLDCore reference (post-deployment)
    function setRldCore(address _rldCore) external onlyOwner {
        rldCore = _rldCore;
    }

    /// @notice Set the authorized factory address (one-time only)
    function setAuthorizedFactory(address _factory) external onlyOwner {
        require(authorizedFactory == address(0), "Factory already set");
        require(_factory != address(0), "Zero address");
        authorizedFactory = _factory;
    }

    /// @notice Update the discount rate (governance)
    /// @param _rateScaled Scaled discount rate. 10_000 = 1.0 bps/s, 3000 = 0.3 bps/s
    function setDiscountRate(uint256 _rateScaled) external onlyOwner {
        discountRateScaled = _rateScaled;
    }

    /// @notice Update the max discount cap (governance)
    function setMaxDiscount(uint256 _maxBps) external onlyOwner {
        require(_maxBps <= 10000, "Exceeds 100%");
        maxDiscountBps = _maxBps;
    }

    /// @notice Update the TWAP window (governance)
    function setTwapWindow(uint32 _seconds) external onlyOwner {
        require(_seconds > 0, "Zero window");
        twapWindow = _seconds;
    }

    /// @notice Sets asset-specific price boundary for a pool (one-time only)
    /// @dev Bounds prevent price manipulation by gating LP range and reverting
    ///      swaps that move price outside the allowed range.
    ///      e.g., for wRLP: min=sqrtPrice(0.0001), max=sqrtPrice(100)
    ///      Can only be set once per pool (bounds.max == 0 check)
    /// @param key The pool key
    /// @param min Minimum allowed sqrtPriceX96
    /// @param max Maximum allowed sqrtPriceX96
    function setPriceBounds(
        PoolKey calldata key,
        uint160 min,
        uint160 max
    ) external {
        require(
            msg.sender == owner || msg.sender == authorizedFactory,
            "UNAUTHORIZED"
        );
        PriceBounds storage bounds = priceBounds[key.toId()];
        if (bounds.max != 0) revert("Bounds already set");
        bounds.min = min;
        bounds.max = max;
    }

    // NOTE: claimDust removed — auto-settle eliminates dust entirely

    /* ======================================================================== */
    /*                        V4 HOOK LIFECYCLE                                 */
    /* ======================================================================== */

    function getHookPermissions()
        public
        pure
        override
        returns (Hooks.Permissions memory)
    {
        return
            Hooks.Permissions({
                beforeInitialize: true,
                afterInitialize: false,
                beforeAddLiquidity: true,
                beforeRemoveLiquidity: true,
                afterAddLiquidity: false,
                afterRemoveLiquidity: false,
                beforeSwap: true,
                afterSwap: true,
                beforeDonate: false,
                afterDonate: false,
                beforeSwapReturnDelta: true,
                afterSwapReturnDelta: false,
                afterAddLiquidityReturnDelta: false,
                afterRemoveLiquidityReturnDelta: false
            });
    }

    function _beforeInitialize(
        address,
        PoolKey calldata key,
        uint160
    ) internal override returns (bytes4) {
        if (key.currency0.isAddressZero()) revert PoolWithNativeNotSupported();

        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];
        state.lastUpdateTimestamp = _getIntervalTime(block.timestamp);
        state.lastClearTimestamp = block.timestamp;

        // C2 + L1: Bootstrap TWAP oracle with enough capacity for meaningful TWAP
        observations[poolId].initialize(
            oracleStates[poolId],
            uint32(block.timestamp)
        );
        observations[poolId].grow(oracleStates[poolId], 65535);

        return BaseHook.beforeInitialize.selector;
    }

    function _beforeAddLiquidity(
        address,
        PoolKey calldata key,
        ModifyLiquidityParams calldata params,
        bytes calldata
    ) internal override returns (bytes4) {
        _updateOracle(key);
        _accrueAndNet(key);

        // Enforce price bounds on LP range
        PriceBounds memory bounds = priceBounds[key.toId()];
        if (bounds.min != 0) {
            uint160 lowerSqrt = TickMath.getSqrtPriceAtTick(params.tickLower);
            uint160 upperSqrt = TickMath.getSqrtPriceAtTick(params.tickUpper);
            if (lowerSqrt < bounds.min || upperSqrt > bounds.max) {
                revert("LP Range Out of Bounds");
            }
        }

        return BaseHook.beforeAddLiquidity.selector;
    }

    function _beforeRemoveLiquidity(
        address,
        PoolKey calldata key,
        ModifyLiquidityParams calldata,
        bytes calldata
    ) internal override returns (bytes4) {
        _updateOracle(key);
        _accrueAndNet(key);
        return BaseHook.beforeRemoveLiquidity.selector;
    }

    /// @notice Layer 2: JIT intercept — fill external takers from accrued ghost balances
    function _beforeSwap(
        address,
        PoolKey calldata key,
        SwapParams calldata params,
        bytes calldata
    ) internal override returns (bytes4, BeforeSwapDelta, uint24) {
        _updateOracle(key);
        _accrueAndNet(key);

        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];

        // Determine what the taker wants and what we have
        // If taker is zeroForOne (selling token0 for token1):
        //   - Taker provides token0, wants token1
        //   - We can fill if we have accrued1 (token1 ghost balance from 1→0 streams)
        // If taker is oneForZero (selling token1 for token0):
        //   - Taker provides token1, wants token0
        //   - We can fill if we have accrued0 (token0 ghost balance from 0→1 streams)

        uint256 availableToFill;
        if (params.zeroForOne) {
            availableToFill = state.accrued1; // We have token1 to give
        } else {
            availableToFill = state.accrued0; // We have token0 to give
        }

        if (availableToFill == 0 || params.amountSpecified == 0) {
            return (
                BaseHook.beforeSwap.selector,
                BeforeSwapDeltaLibrary.ZERO_DELTA,
                0
            );
        }

        // Get TWAP price for fair pricing
        uint160 twapSqrtPriceX96 = _getTwapPrice(key);

        // Calculate how much we can fill at TWAP price
        // For exact input (amountSpecified < 0): taker specifies input amount
        // For exact output (amountSpecified > 0): taker specifies output amount
        uint256 takerAmount;
        if (params.amountSpecified < 0) {
            takerAmount = uint256(-params.amountSpecified);
        } else {
            takerAmount = uint256(params.amountSpecified);
        }

        // Convert takerAmount to the token we're providing using TWAP price
        uint256 fillAmountInOutputToken;
        uint256 fillAmountInInputToken;

        if (params.amountSpecified < 0) {
            // Exact input: taker gives us X input tokens, we give them output tokens
            // Convert input amount to output amount at TWAP
            fillAmountInInputToken = takerAmount;
            fillAmountInOutputToken = _convertAtPrice(
                fillAmountInInputToken,
                twapSqrtPriceX96,
                params.zeroForOne
            );

            // Cap by our available balance
            if (fillAmountInOutputToken > availableToFill) {
                fillAmountInOutputToken = availableToFill;
                // Recalculate input based on capped output
                fillAmountInInputToken = _convertAtPrice(
                    fillAmountInOutputToken,
                    twapSqrtPriceX96,
                    !params.zeroForOne
                );
            }
        } else {
            // Exact output: taker wants X output tokens, will give us input tokens
            fillAmountInOutputToken = takerAmount;
            if (fillAmountInOutputToken > availableToFill) {
                fillAmountInOutputToken = availableToFill;
            }
            fillAmountInInputToken = _convertAtPrice(
                fillAmountInOutputToken,
                twapSqrtPriceX96,
                !params.zeroForOne
            );
        }

        if (fillAmountInOutputToken == 0 || fillAmountInInputToken == 0) {
            return (
                BaseHook.beforeSwap.selector,
                BeforeSwapDeltaLibrary.ZERO_DELTA,
                0
            );
        }

        // Update ghost balances
        if (params.zeroForOne) {
            state.accrued1 -= fillAmountInOutputToken;
            // Record earnings for the 1→0 stream (they sold token1, earned token0)
            _recordEarnings(state.stream1For0, fillAmountInInputToken);
        } else {
            state.accrued0 -= fillAmountInOutputToken;
            // Record earnings for the 0→1 stream (they sold token0, earned token1)
            _recordEarnings(state.stream0For1, fillAmountInInputToken);
        }

        // Build the BeforeSwapDelta
        // specified = amount the hook takes from the taker's specified side (positive = hook takes)
        // unspecified = amount on the other side (negative = hook gives to taker)
        BeforeSwapDelta delta;
        if (params.amountSpecified < 0) {
            // Exact input: hook takes input tokens, gives output tokens
            delta = toBeforeSwapDelta(
                int128(uint128(fillAmountInInputToken)), // take from specified (input)
                -int128(uint128(fillAmountInOutputToken)) // give on unspecified (output)
            );
        } else {
            // Exact output: hook gives output tokens, takes input tokens
            delta = toBeforeSwapDelta(
                int128(uint128(fillAmountInOutputToken)), // take from specified (output side)
                -int128(uint128(fillAmountInInputToken)) // give on unspecified (input side)
            );
        }

        emit JITFill(poolId, fillAmountInOutputToken, params.zeroForOne);

        // ── C1: Settle with V4 PoolManager ──
        // The hook promised a BeforeSwapDelta; V4 will create currency deltas
        // on address(this). We must settle by moving real tokens.
        Currency inputCurrency = params.zeroForOne
            ? key.currency0
            : key.currency1;
        Currency outputCurrency = params.zeroForOne
            ? key.currency1
            : key.currency0;

        // 1. SEND output tokens (from ghost custody) to PoolManager
        poolManager.sync(outputCurrency);
        IERC20Minimal(Currency.unwrap(outputCurrency)).safeTransfer(
            address(poolManager),
            fillAmountInOutputToken
        );
        poolManager.settle();

        // 2. RECEIVE input tokens (earnings for streamers) from PoolManager
        poolManager.take(inputCurrency, address(this), fillAmountInInputToken);

        return (BaseHook.beforeSwap.selector, delta, 0);
    }

    /// @notice Enforce price bounds after every swap
    function _afterSwap(
        address,
        PoolKey calldata key,
        SwapParams calldata,
        BalanceDelta,
        bytes calldata
    ) internal override returns (bytes4, int128) {
        PriceBounds memory bounds = priceBounds[key.toId()];
        if (bounds.min != 0) {
            (uint160 sqrtPriceX96, , , ) = poolManager.getSlot0(key.toId());
            if (sqrtPriceX96 < bounds.min || sqrtPriceX96 > bounds.max) {
                revert("Price Out of Bounds");
            }
        }
        return (BaseHook.afterSwap.selector, 0);
    }

    /* ======================================================================== */
    /*                       ORDER SUBMISSION & MANAGEMENT                      */
    /* ======================================================================== */

    /// @inheritdoc IJTM
    function submitOrder(
        SubmitOrderParams calldata params
    )
        external
        nonReentrant
        returns (bytes32 orderId, OrderKey memory orderKey)
    {
        _accrueAndNet(params.key);
        _executePendingSettles(params.key);

        PoolId poolId = params.key.toId();
        // Option E: start at NEXT epoch boundary — guarantees exact duration
        uint256 nextEpoch = _getIntervalTime(block.timestamp) +
            expirationInterval;

        orderKey = OrderKey({
            owner: msg.sender,
            expiration: (nextEpoch + params.duration).toUint160(),
            zeroForOne: params.zeroForOne,
            nonce: orderNonces[msg.sender]++
        });

        if (orderKey.expiration <= block.timestamp) {
            revert ExpirationLessThanBlockTime(orderKey.expiration);
        }
        if (orderKey.expiration % expirationInterval != 0) {
            revert ExpirationNotOnInterval(orderKey.expiration);
        }

        // Explicit minimum order size: at least 1 epoch-interval of wei.
        // This makes the floor self-documenting and prevents sub-dust orders.
        if (params.amountIn < expirationInterval) revert SellRateCannotBeZero();

        // Scale-before-divide: multiply by RATE_SCALER BEFORE dividing by duration
        // to preserve 18 extra digits of precision. Reduces truncation loss from
        // up to (duration-1) tokens to at most (duration-1) wei.
        uint256 scaledSellRate = (params.amountIn * RATE_SCALER) / params.duration;
        if (scaledSellRate == 0) revert SellRateCannotBeZero();
        uint256 actualDeposit = (scaledSellRate * params.duration) / RATE_SCALER;

        orderId = _orderId(orderKey);

        JITState storage state = poolStates[poolId];
        if (state.lastUpdateTimestamp == 0) revert NotInitialized();

        // Update aggregate stream state
        StreamPool storage stream = params.zeroForOne
            ? state.stream0For1
            : state.stream1For0;

        // Option E: defer sellRate activation until startEpoch.
        // This ensures ghost only accrues during the paid-for period [startEpoch, expiration].
        // _crossEpoch will add this to sellRateCurrent when startEpoch is crossed.
        stream.sellRateStartingAtInterval[nextEpoch] += scaledSellRate;
        stream.sellRateEndingAtInterval[orderKey.expiration] += scaledSellRate;

        uint256 earningsFactorLast = stream.earningsFactorCurrent;
        state.orders[orderId] = Order({
            sellRate: scaledSellRate,
            earningsFactorLast: earningsFactorLast
        });

        // Transfer FULL amountIn from user. The dust (amountIn - actualDeposit)
        // stays as contract surplus, guaranteeing solvency when aggregated
        // ghost accrual benefits from floor(SUM/RS) >= sum(floor(x_i/RS)).
        IERC20Minimal(
            params.zeroForOne
                ? Currency.unwrap(params.key.currency0)
                : Currency.unwrap(params.key.currency1)
        ).safeTransferFrom(
                msg.sender,
                address(this),
                params.amountIn
            );

        emit SubmitOrder(
            poolId,
            orderId,
            msg.sender,
            params.amountIn,
            orderKey.expiration,
            params.zeroForOne,
            scaledSellRate / RATE_SCALER,
            earningsFactorLast,
            nextEpoch,
            orderKey.nonce
        );
    }

    /// @inheritdoc IJTM
    function cancelOrder(
        PoolKey calldata key,
        OrderKey calldata orderKey
    )
        external
        nonReentrant
        returns (uint256 buyTokensOut, uint256 sellTokensRefund)
    {
        if (msg.sender != orderKey.owner) revert Unauthorized();

        buyTokensOut = _sync(key, orderKey);

        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];
        bytes32 orderId = _orderId(orderKey);
        Order storage order = state.orders[orderId];

        uint256 sellRate = order.sellRate;
        if (sellRate == 0) revert OrderDoesNotExist(orderKey);
        if (state.lastUpdateTimestamp >= orderKey.expiration) {
            revert OrderAlreadyExpired(orderKey);
        }

        // Update stream state
        StreamPool storage stream = orderKey.zeroForOne
            ? state.stream0For1
            : state.stream1For0;

        // Determine if order has started: check if sellRate is in sellRateCurrent
        // (activated by _crossEpoch) or still in sellRateStartingAtInterval (pending).
        // We can infer startEpoch from expiration - duration, but we don't store duration.
        // Instead: try to find startEpoch by checking where the matching starting rate is.
        // Simplest approach: if order's rate is NOT in sellRateCurrent, it hasn't started.
        bool orderStarted = stream.sellRateCurrent >= sellRate;

        if (orderStarted) {
            // AUTO-SETTLE on cancel: if removing this order kills the stream,
            // settle ghost first (while sellRateCurrent is still > 0).
            // cancelOrder is an external function, so poolManager.unlock() is safe.
            if (stream.sellRateCurrent == sellRate) {
                uint256 ghost = orderKey.zeroForOne
                    ? state.accrued0
                    : state.accrued1;
                if (ghost > 0) {
                    _settleGhostAgainstPool(
                        key,
                        state,
                        stream,
                        ghost,
                        orderKey.zeroForOne
                    );
                }
            }

            stream.sellRateCurrent -= sellRate;
            stream.sellRateEndingAtInterval[orderKey.expiration] -= sellRate;

            // Calculate refund: remaining time from lastUpdate to expiration
            uint256 remainingSeconds = orderKey.expiration -
                state.lastUpdateTimestamp;
            sellTokensRefund = (sellRate * remainingSeconds) / RATE_SCALER;
        } else {
            // Order hasn't started yet: remove from starting map.
            // Find the startEpoch by scanning (or use the fact that expiration = startEpoch + duration).
            // Since we know the order was submitted in the current epoch or earlier,
            // and startEpoch = nextEpoch at submit time, we can derive it from
            // the earningsFactorLast snapshot (if EFL == current EF, order is from this epoch).
            // Simpler: refund = full deposit (order hasn't accrued anything).
            // Remove from ending map + find and remove from starting map.
            stream.sellRateEndingAtInterval[orderKey.expiration] -= sellRate;

            // Scan backwards from expiration to find the startEpoch
            // (startEpoch < expiration, aligned to interval)
            uint256 ep = orderKey.expiration - expirationInterval;
            while (ep > 0) {
                if (stream.sellRateStartingAtInterval[ep] >= sellRate) {
                    stream.sellRateStartingAtInterval[ep] -= sellRate;
                    break;
                }
                if (ep < expirationInterval) break;
                ep -= expirationInterval;
            }

            // Full refund: deposit = sellRate * duration / RATE_SCALER
            // duration = expiration - startEpoch. Since the order hasn't accrued,
            // the full deposit is the original amountIn.
            // sellRate is scaled. remainingSeconds = full duration.
            uint256 startEpoch = ep; // found above
            uint256 duration = orderKey.expiration - startEpoch;
            sellTokensRefund = (sellRate * duration) / RATE_SCALER;
        }

        delete state.orders[orderId];

        // Transfer refund
        Currency sellToken = orderKey.zeroForOne
            ? key.currency0
            : key.currency1;
        IERC20Minimal(Currency.unwrap(sellToken)).safeTransfer(
            msg.sender,
            sellTokensRefund
        );

        // Claim earned tokens
        Currency buyToken = orderKey.zeroForOne ? key.currency1 : key.currency0;
        uint256 owed = tokensOwed[poolId][buyToken][msg.sender];
        if (owed > 0) {
            tokensOwed[poolId][buyToken][msg.sender] = 0;
            IERC20Minimal(Currency.unwrap(buyToken)).safeTransfer(
                msg.sender,
                owed
            );
            buyTokensOut += owed;
        }

        emit CancelOrder(poolId, orderId, msg.sender, sellTokensRefund);
    }

    /// @inheritdoc IJTM
    function sync(
        SyncParams calldata params
    ) public returns (uint256 earningsAmount) {
        return _sync(params.key, params.orderKey);
    }

    /// @notice Internal sync implementation accepting memory params
    function _sync(
        PoolKey memory poolKey,
        OrderKey memory orderKey
    ) internal returns (uint256 earningsAmount) {
        _accrueAndNet(poolKey);
        _executePendingSettles(poolKey);

        PoolId poolId = poolKey.toId();
        JITState storage state = poolStates[poolId];
        bytes32 orderId = _orderId(orderKey);
        Order storage order = state.orders[orderId];

        if (order.sellRate == 0) {
            revert OrderDoesNotExist(
                OrderKey(
                    orderKey.owner,
                    orderKey.expiration,
                    orderKey.zeroForOne,
                    orderKey.nonce
                )
            );
        }

        StreamPool storage stream = orderKey.zeroForOne
            ? state.stream0For1
            : state.stream1For0;

        // Calculate earnings since last sync
        // GHOST EARNINGS FIX: For expired orders, cap earningsFactor at the
        // snapshot taken when the order's epoch was crossed. This prevents
        // post-expiry accrual inflation that would make the contract insolvent.
        uint256 effectiveEF = stream.earningsFactorCurrent;
        if (block.timestamp >= orderKey.expiration) {
            uint256 snap = stream.earningsFactorAtInterval[orderKey.expiration];
            if (snap > 0 && snap < effectiveEF) {
                effectiveEF = snap;
            }
        }

        // DEFERRED-START FIX: Use the earningsFactorAtInterval snapshot from
        // the order's startEpoch as a floor for earningsFactorLast. This
        // prevents crediting earnings that accrued between submit and start.
        uint256 effectiveEFL = order.earningsFactorLast;
        bool orderStarted = stream.sellRateCurrent >= order.sellRate;
        if (!orderStarted) {
            // Order hasn't started — zero earnings
            effectiveEFL = effectiveEF;
        } else {
            // Find startEpoch and use its snapshot as floor
            uint256 ep = orderKey.expiration - expirationInterval;
            while (ep > 0) {
                if (stream.sellRateStartingAtInterval[ep] >= order.sellRate) break;
                if (ep < expirationInterval) break;
                ep -= expirationInterval;
            }
            uint256 startSnap = stream.earningsFactorAtInterval[ep];
            if (startSnap > effectiveEFL) {
                effectiveEFL = startSnap;
            }
        }

        uint256 earningsFactorDelta = effectiveEF > effectiveEFL
            ? effectiveEF - effectiveEFL
            : 0;
        if (earningsFactorDelta > 0) {
            earningsAmount = Math.mulDiv(
                order.sellRate,
                earningsFactorDelta,
                FixedPoint96.Q96 * RATE_SCALER
            );

            // Credit buy token to user
            Currency buyToken = orderKey.zeroForOne
                ? poolKey.currency1
                : poolKey.currency0;
            tokensOwed[poolId][buyToken][orderKey.owner] += earningsAmount;
        }

        // Update snapshot
        order.earningsFactorLast = stream.earningsFactorCurrent;
    }

    /// @inheritdoc IJTM
    function claimTokens(
        PoolKey calldata key,
        Currency currency
    ) external returns (uint256 amount) {
        PoolId poolId = key.toId();
        amount = tokensOwed[poolId][currency][msg.sender];
        if (amount > 0) {
            tokensOwed[poolId][currency][msg.sender] = 0;
            IERC20Minimal(Currency.unwrap(currency)).safeTransfer(
                msg.sender,
                amount
            );
        }
    }

    /* ======================================================================== */
    /*                     LAYER 3: DYNAMIC AUCTION CLEAR                       */
    /* ======================================================================== */

    /// @inheritdoc IJTM
    function clear(
        PoolKey calldata key,
        bool zeroForOne,
        uint256 maxAmount,
        uint256 minDiscountBps
    ) external nonReentrant {
        _accrueAndNet(key);
        _executePendingSettles(key);

        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];

        // zeroForOne = true means arb wants to buy accrued token0 (pays token1)
        uint256 available = zeroForOne ? state.accrued0 : state.accrued1;
        if (available == 0) revert NothingToClear();

        // Revert if the corresponding stream has no active orders.
        // Otherwise _recordEarnings would be a no-op and the arb's
        // payment tokens would be permanently stranded in the contract.
        StreamPool storage stream = zeroForOne
            ? state.stream0For1
            : state.stream1For0;
        if (stream.sellRateCurrent == 0) revert NoActiveStream();

        uint256 clearAmount = available > maxAmount ? maxAmount : available;

        // Calculate dynamic discount (fixed-point: discountRateScaled / DISCOUNT_RATE_PRECISION = bps/s)
        uint256 elapsedSinceClear = block.timestamp - state.lastClearTimestamp;
        uint256 discountBps = (elapsedSinceClear * discountRateScaled) /
            DISCOUNT_RATE_PRECISION;
        if (discountBps > maxDiscountBps) discountBps = maxDiscountBps;

        // H3: MEV protection — revert if discount is below caller's minimum
        if (discountBps < minDiscountBps) {
            revert InsufficientDiscount(discountBps, minDiscountBps);
        }

        // Get TWAP price
        uint160 twapSqrtPriceX96 = _getTwapPrice(key);

        // Calculate payment: arb pays (clearAmount * price * (1 - discount))
        uint256 fullPayment = _convertAtPrice(
            clearAmount,
            twapSqrtPriceX96,
            zeroForOne
        );
        uint256 discountedPayment = fullPayment -
            ((fullPayment * discountBps) / 10000);

        // Take payment from arb
        Currency paymentToken = zeroForOne ? key.currency1 : key.currency0;
        IERC20Minimal(Currency.unwrap(paymentToken)).safeTransferFrom(
            msg.sender,
            address(this),
            discountedPayment
        );

        // Give arb the cleared tokens
        Currency clearToken = zeroForOne ? key.currency0 : key.currency1;
        IERC20Minimal(Currency.unwrap(clearToken)).safeTransfer(
            msg.sender,
            clearAmount
        );

        // Update ghost balance and record earnings
        if (zeroForOne) {
            state.accrued0 -= clearAmount;
        } else {
            state.accrued1 -= clearAmount;
        }
        _recordEarnings(stream, discountedPayment);

        state.lastClearTimestamp = block.timestamp;

        emit AuctionClear(poolId, msg.sender, clearAmount, discountBps);
    }

    /* ======================================================================== */
    /*                          VIEW FUNCTIONS                                  */
    /* ======================================================================== */

    /// @inheritdoc IJTM
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
        )
    {
        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];

        // Calculate pending accrual (not yet committed)
        uint256 deltaTime = block.timestamp - state.lastUpdateTimestamp;
        accrued0 =
            state.accrued0 +
            ((state.stream0For1.sellRateCurrent * deltaTime) / RATE_SCALER);
        accrued1 =
            state.accrued1 +
            ((state.stream1For0.sellRateCurrent * deltaTime) / RATE_SCALER);

        timeSinceLastClear = block.timestamp - state.lastClearTimestamp;
        currentDiscount =
            (timeSinceLastClear * discountRateScaled) /
            DISCOUNT_RATE_PRECISION;
        if (currentDiscount > maxDiscountBps) currentDiscount = maxDiscountBps;
    }

    /// @inheritdoc IJTM
    function getOrder(
        PoolKey calldata key,
        OrderKey calldata orderKey
    ) external view returns (Order memory) {
        return poolStates[key.toId()].orders[_orderId(orderKey)];
    }

    /// @inheritdoc IJTM
    function getStreamPool(
        PoolKey calldata key,
        bool zeroForOne
    )
        external
        view
        returns (uint256 sellRateCurrent, uint256 earningsFactorCurrent)
    {
        JITState storage state = poolStates[key.toId()];
        StreamPool storage stream = zeroForOne
            ? state.stream0For1
            : state.stream1For0;
        return (stream.sellRateCurrent, stream.earningsFactorCurrent);
    }

    /// @inheritdoc IJTM
    function getCancelOrderState(
        PoolKey calldata key,
        OrderKey calldata orderKey
    ) external view returns (uint256 buyTokensOwed, uint256 sellTokensRefund) {
        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];
        bytes32 orderId = _orderId(orderKey);
        Order storage order = state.orders[orderId];

        if (order.sellRate == 0) return (0, 0);

        StreamPool storage stream = orderKey.zeroForOne
            ? state.stream0For1
            : state.stream1For0;

        // Compute pending earnings
        // GHOST EARNINGS FIX: cap at snapshot for expired orders
        uint256 effectiveEF = stream.earningsFactorCurrent;
        if (state.lastUpdateTimestamp >= orderKey.expiration) {
            uint256 snap = stream.earningsFactorAtInterval[orderKey.expiration];
            if (snap > 0 && snap < effectiveEF) {
                effectiveEF = snap;
            }
        }

        // DEFERRED-START FIX: use startEpoch snapshot as floor for earningsFactorLast
        uint256 effectiveEFL = order.earningsFactorLast;
        bool orderStarted = stream.sellRateCurrent >= order.sellRate;
        if (!orderStarted) {
            // Order hasn't started — zero earnings
            effectiveEFL = effectiveEF;
        } else {
            // startEpoch is found below (line ~936); preview it here for EFL floor
            uint256 epScan = orderKey.expiration - expirationInterval;
            while (epScan > 0) {
                if (stream.sellRateStartingAtInterval[epScan] >= order.sellRate) break;
                if (epScan < expirationInterval) break;
                epScan -= expirationInterval;
            }
            uint256 startSnap = stream.earningsFactorAtInterval[epScan];
            if (startSnap > effectiveEFL) {
                effectiveEFL = startSnap;
            }
        }

        uint256 earningsFactorDelta = effectiveEF > effectiveEFL
            ? effectiveEF - effectiveEFL
            : 0;
        if (earningsFactorDelta > 0) {
            buyTokensOwed = Math.mulDiv(
                order.sellRate,
                earningsFactorDelta,
                FixedPoint96.Q96 * RATE_SCALER
            );
        }

        // Compute refund for remaining time
        if (state.lastUpdateTimestamp < orderKey.expiration) {
            // Find the order's start epoch by scanning backwards
            uint256 ep = orderKey.expiration - expirationInterval;
            while (ep > 0) {
                if (stream.sellRateStartingAtInterval[ep] >= order.sellRate) break;
                if (ep < expirationInterval) break;
                ep -= expirationInterval;
            }
            uint256 startEpoch = ep;

            if (state.lastUpdateTimestamp >= startEpoch) {
                // Active order: refund from lastUpdate to expiration
                uint256 remainingSeconds = orderKey.expiration -
                    state.lastUpdateTimestamp;
                sellTokensRefund =
                    (order.sellRate * remainingSeconds) /
                    RATE_SCALER;
            } else {
                // Pending order: refund = full deposit (sellRate × duration)
                uint256 duration = orderKey.expiration - startEpoch;
                sellTokensRefund = (order.sellRate * duration) / RATE_SCALER;
            }
        }
    }

    /// @notice Alias for getStreamPool — backward compatible with old test suite
    function getOrderPool(
        PoolKey calldata key,
        bool zeroForOne
    )
        external
        view
        returns (uint256 sellRateCurrent, uint256 earningsFactorCurrent)
    {
        JITState storage state = poolStates[key.toId()];
        StreamPool storage stream = zeroForOne
            ? state.stream0For1
            : state.stream1For0;
        return (stream.sellRateCurrent, stream.earningsFactorCurrent);
    }

    /// @notice Returns the last update timestamp for a pool (analog of lastVirtualOrderTimestamp)
    function lastVirtualOrderTimestamp(
        PoolId poolId
    ) external view returns (uint256) {
        return poolStates[poolId].lastUpdateTimestamp;
    }

    /* ======================================================================== */
    /*                     EXECUTION CONVENIENCE METHODS                        */
    /* ======================================================================== */

    /// @notice Trigger accrual + internal netting without a swap (equivalent to old executeJTMOrders)
    function executeJTMOrders(PoolKey memory key) public {
        _updateOracle(key);
        _accrueAndNet(key);
        _executePendingSettles(key);
    }

    /// @notice Trigger accrual up to a specific timestamp
    function executeJTMOrders(
        PoolKey memory key,
        uint256 /*targetTimestamp*/
    ) public {
        // In JTM, accrual is continuous up to block.timestamp
        // targetTimestamp is accepted for API compat but we accrue to now
        _updateOracle(key);
        _accrueAndNet(key);
        _executePendingSettles(key);
    }

    /// @notice Sync an order and claim all owed tokens in one call
    function syncAndClaimTokens(
        SyncParams calldata params
    ) external returns (uint256 claimed0, uint256 claimed1) {
        _sync(params.key, params.orderKey);

        // If order is expired, delete it
        PoolId poolId = params.key.toId();
        JITState storage state = poolStates[poolId];
        bytes32 orderId = _orderId(params.orderKey);
        Order storage order = state.orders[orderId];

        if (
            order.sellRate > 0 &&
            state.lastUpdateTimestamp >= params.orderKey.expiration
        ) {
            // Order expired — _crossEpoch already subtracted sellRate from
            // stream.sellRateCurrent and sellRateEndingAtInterval during
            // _accrueAndNet(). We only need to clean up the order record.
            delete state.orders[orderId];
        }

        // Claim both currencies
        Currency c0 = params.key.currency0;
        Currency c1 = params.key.currency1;

        claimed0 = tokensOwed[poolId][c0][msg.sender];
        if (claimed0 > 0) {
            tokensOwed[poolId][c0][msg.sender] = 0;
            IERC20Minimal(Currency.unwrap(c0)).safeTransfer(
                msg.sender,
                claimed0
            );
        }

        claimed1 = tokensOwed[poolId][c1][msg.sender];
        if (claimed1 > 0) {
            tokensOwed[poolId][c1][msg.sender] = 0;
            IERC20Minimal(Currency.unwrap(c1)).safeTransfer(
                msg.sender,
                claimed1
            );
        }
    }

    /* ======================================================================== */
    /*                         INTERNAL: CORE ENGINE                            */
    /* ======================================================================== */

    /// @notice The heart of the system: accrue ghost balances, cross epochs, and net internally
    function _accrueAndNet(PoolKey memory key) internal {
        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];

        if (state.lastUpdateTimestamp == 0) return; // Not initialized

        uint256 currentTime = block.timestamp;
        if (currentTime <= state.lastUpdateTimestamp) return; // No time passed

        uint256 deltaTime = currentTime - state.lastUpdateTimestamp;

        // === Step 1: Accrue ghost balances ===
        uint256 newAccrued0 = (state.stream0For1.sellRateCurrent * deltaTime) /
            RATE_SCALER;
        uint256 newAccrued1 = (state.stream1For0.sellRateCurrent * deltaTime) /
            RATE_SCALER;

        state.accrued0 += newAccrued0;
        state.accrued1 += newAccrued1;

        // === Step 2: Layer 1 — Internal Netting ===
        // CRITICAL: Must net BEFORE crossing epochs. If we cross first,
        // expired orders reduce sellRateCurrent to 0, making _recordEarnings
        // a no-op (divides by zero sellRate). Net first while rates are active.
        if (state.accrued0 > 0 && state.accrued1 > 0) {
            _internalNet(key, state);
        }

        // === Step 3: Cross epoch boundaries (subtract expired sellRates) ===
        uint256 lastInterval = _getIntervalTime(state.lastUpdateTimestamp);
        uint256 currentInterval = _getIntervalTime(currentTime);

        if (currentInterval > lastInterval) {
            // Walk through each crossed epoch
            for (
                uint256 epoch = lastInterval + expirationInterval;
                epoch <= currentInterval;
                epoch += expirationInterval
            ) {
                // AUTO-SETTLE: settle ghost before crossing epochs that kill a stream
                _preEpochSettle(key, state, state.stream0For1, epoch, true);
                _preEpochSettle(key, state, state.stream1For0, epoch, false);

                _crossEpoch(state.stream0For1, epoch);
                _crossEpoch(state.stream1For0, epoch);
            }
        }

        // Step 4 removed — auto-settle handles all ghost at epoch boundaries

        state.lastUpdateTimestamp = currentTime;
    }

    /// @notice Check if an epoch crossing will kill a stream; if so, QUEUE settlement
    /// @dev Only fires when expiring sellRate == total sellRate (last order in stream).
    ///      Deferred pattern: stores ghost+sellRate snapshot in PendingSettle.
    ///      Actual AMM swap happens in _executePendingSettles (safe external context).
    ///      Ghost (accrued) is zeroed immediately to prevent double-accrual.
    function _preEpochSettle(
        PoolKey memory key,
        JITState storage state,
        StreamPool storage stream,
        uint256 epoch,
        bool zeroForOne
    ) internal {
        uint256 expiring = stream.sellRateEndingAtInterval[epoch];
        if (expiring == 0 || expiring != stream.sellRateCurrent) return;

        uint256 ghost = zeroForOne ? state.accrued0 : state.accrued1;
        if (ghost == 0) return;

        PoolId poolId = key.toId();

        // Queue the settle with a snapshot of sellRateCurrent
        // (needed because _crossEpoch will zero it next)
        if (zeroForOne) {
            _pendingSettle0[poolId] = PendingSettle(
                ghost,
                stream.sellRateCurrent
            );
            state.accrued0 = 0;
        } else {
            _pendingSettle1[poolId] = PendingSettle(
                ghost,
                stream.sellRateCurrent
            );
            state.accrued1 = 0;
        }
    }

    /// @notice Execute any queued settlements from _preEpochSettle
    /// @dev MUST only be called from external functions (not hook callbacks).
    ///      Processes pending settles for both directions, executing AMM swaps
    ///      and recording earnings using the saved sellRate snapshot.
    function _executePendingSettles(PoolKey memory key) internal {
        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];

        // Process pending settle for 0→1 direction
        PendingSettle memory ps0 = _pendingSettle0[poolId];
        if (ps0.ghostAmount > 0) {
            delete _pendingSettle0[poolId];

            StreamPool storage stream0 = state.stream0For1;

            // Execute AMM swap: ghost token0 → buy token1
            SwapParams memory swapParams0 = SwapParams(
                true,
                -int256(ps0.ghostAmount),
                TickMath.MIN_SQRT_PRICE + 1
            );

            bytes memory result0 = poolManager.unlock(
                abi.encode(key, swapParams0)
            );
            BalanceDelta delta0 = abi.decode(result0, (BalanceDelta));
            uint256 proceeds0 = uint256(uint128(delta0.amount1()));

            // Record earnings using savedSellRate (stream may now have sr=0)
            if (proceeds0 > 0 && ps0.sellRateSnapshot > 0) {
                stream0.earningsFactorCurrent +=
                    (proceeds0 * FixedPoint96.Q96 * RATE_SCALER) /
                    ps0.sellRateSnapshot;
            }

            emit AutoSettle(poolId, ps0.ghostAmount, proceeds0, true);
        }

        // Process pending settle for 1→0 direction
        PendingSettle memory ps1 = _pendingSettle1[poolId];
        if (ps1.ghostAmount > 0) {
            delete _pendingSettle1[poolId];

            StreamPool storage stream1 = state.stream1For0;

            // Execute AMM swap: ghost token1 → buy token0
            SwapParams memory swapParams1 = SwapParams(
                false,
                -int256(ps1.ghostAmount),
                TickMath.MAX_SQRT_PRICE - 1
            );

            bytes memory result1 = poolManager.unlock(
                abi.encode(key, swapParams1)
            );
            BalanceDelta delta1 = abi.decode(result1, (BalanceDelta));
            uint256 proceeds1 = uint256(uint128(delta1.amount0()));

            // Record earnings using savedSellRate
            if (proceeds1 > 0 && ps1.sellRateSnapshot > 0) {
                stream1.earningsFactorCurrent +=
                    (proceeds1 * FixedPoint96.Q96 * RATE_SCALER) /
                    ps1.sellRateSnapshot;
            }

            emit AutoSettle(poolId, ps1.ghostAmount, proceeds1, false);
        }
    }

    /// @notice Swap residual ghost tokens against the AMM pool, record proceeds as earnings
    /// @dev Shared by auto-settle (epoch boundary), cancel-settle, and force-settle (liquidation).
    ///      Uses the existing unlockCallback path: poolManager.unlock → swap → settle.
    function _settleGhostAgainstPool(
        PoolKey memory key,
        JITState storage state,
        StreamPool storage stream,
        uint256 ghostAmount,
        bool zeroForOne
    ) internal {
        if (ghostAmount == 0 || stream.sellRateCurrent == 0) return;

        SwapParams memory swapParams = SwapParams(
            zeroForOne,
            -int256(ghostAmount),
            zeroForOne
                ? TickMath.MIN_SQRT_PRICE + 1
                : TickMath.MAX_SQRT_PRICE - 1
        );

        bytes memory result = poolManager.unlock(abi.encode(key, swapParams));
        BalanceDelta delta = abi.decode(result, (BalanceDelta));

        uint256 proceeds = zeroForOne
            ? uint256(uint128(delta.amount1()))
            : uint256(uint128(delta.amount0()));

        if (proceeds > 0) {
            _recordEarnings(stream, proceeds);
        }

        if (zeroForOne) {
            state.accrued0 = 0;
        } else {
            state.accrued1 = 0;
        }

        emit AutoSettle(key.toId(), ghostAmount, proceeds, zeroForOne);
    }

    /// @notice Cross an epoch boundary: activate starting orders, snapshot earningsFactor, subtract expired
    function _crossEpoch(StreamPool storage stream, uint256 epoch) internal {
        // Option E: activate deferred sell rates that start at this epoch
        uint256 starting = stream.sellRateStartingAtInterval[epoch];
        if (starting > 0) {
            // DEFERRED-START FIX: snapshot EF at activation so deferred orders
            // use this as their earnings baseline instead of submit-time snapshot
            stream.earningsFactorAtInterval[epoch] = stream
                .earningsFactorCurrent;
            stream.sellRateCurrent += starting;
        }

        uint256 expiring = stream.sellRateEndingAtInterval[epoch];
        if (expiring > 0) {
            stream.earningsFactorAtInterval[epoch] = stream
                .earningsFactorCurrent;
            stream.sellRateCurrent -= expiring;
        }
    }

    /// @notice Layer 1: Net opposing ghost balances at TWAP price
    function _internalNet(PoolKey memory key, JITState storage state) internal {
        uint160 twapPrice = _getTwapPrice(key);

        // Convert accrued1 to token0 terms at TWAP to find matchable amount
        uint256 accrued1AsToken0 = _convertAtPrice(
            state.accrued1,
            twapPrice,
            false
        );

        uint256 matchedToken0;
        uint256 matchedToken1;

        if (state.accrued0 <= accrued1AsToken0) {
            // All of accrued0 can be matched
            matchedToken0 = state.accrued0;
            matchedToken1 = Math.mulDiv(
                Math.mulDiv(matchedToken0, twapPrice, FixedPoint96.Q96),
                twapPrice,
                FixedPoint96.Q96,
                Math.Rounding.Ceil
            );
        } else {
            // All of accrued1 can be matched
            matchedToken1 = state.accrued1;
            // RF-3: Use Ceil rounding symmetrically (same as the other branch)
            matchedToken0 = Math.mulDiv(
                Math.mulDiv(matchedToken1, FixedPoint96.Q96, twapPrice),
                FixedPoint96.Q96,
                twapPrice,
                Math.Rounding.Ceil
            );
        }

        if (matchedToken0 == 0 || matchedToken1 == 0) return;

        // Record earnings for both streams
        _recordEarnings(state.stream0For1, matchedToken1); // 0→1 earns token1
        _recordEarnings(state.stream1For0, matchedToken0); // 1→0 earns token0

        state.accrued0 -= matchedToken0;
        state.accrued1 -= matchedToken1;

        emit InternalMatch(key.toId(), matchedToken0, matchedToken1);
    }

    /// @notice Record earnings into a stream's earningsFactor
    function _recordEarnings(
        StreamPool storage stream,
        uint256 earnings
    ) internal {
        if (stream.sellRateCurrent == 0 || earnings == 0) return;

        uint256 earningsFactor = Math.mulDiv(
            earnings,
            FixedPoint96.Q96 * RATE_SCALER,
            stream.sellRateCurrent
        );
        stream.earningsFactorCurrent += earningsFactor;
    }

    /* ======================================================================== */
    /*                        INTERNAL: PRICE HELPERS                           */
    /* ======================================================================== */

    /// @notice Get the TWAP price from our oracle
    function _getTwapPrice(PoolKey memory key) internal view returns (uint160) {
        PoolId poolId = key.toId();
        TwapOracle.State storage oracleState = oracleStates[poolId];

        if (oracleState.cardinality < 2) {
            // C2: Revert instead of falling back to manipulable spot price
            revert OracleNotReady();
        }

        // Get tick cumulatives for TWAP calculation
        uint32[] memory secondsAgos = new uint32[](2);
        secondsAgos[0] = twapWindow;
        secondsAgos[1] = 0;

        (, int24 currentTick, , ) = poolManager.getSlot0(poolId);

        int56[] memory tickCumulatives = observations[poolId].observe(
            oracleState,
            uint32(block.timestamp),
            secondsAgos,
            currentTick
        );

        int56 tickDelta = tickCumulatives[1] - tickCumulatives[0];
        int24 avgTick = int24(tickDelta / int56(uint56(twapWindow)));

        return TickMath.getSqrtPriceAtTick(avgTick);
    }

    /// @notice Convert an amount at a given sqrtPriceX96
    /// @param amount The input amount
    /// @param sqrtPriceX96 The price
    /// @param zeroForOne If true, convert token0 → token1. If false, token1 → token0.
    function _convertAtPrice(
        uint256 amount,
        uint160 sqrtPriceX96,
        bool zeroForOne
    ) internal pure returns (uint256) {
        if (amount == 0) return 0;

        if (zeroForOne) {
            // token0 → token1: output = amount * price^2
            // price = sqrtPriceX96 / 2^96
            // amount1 = amount0 * (sqrtPriceX96)^2 / (2^96)^2
            return
                Math.mulDiv(
                    Math.mulDiv(amount, sqrtPriceX96, FixedPoint96.Q96),
                    sqrtPriceX96,
                    FixedPoint96.Q96
                );
        } else {
            // token1 → token0: output = amount / price^2
            // amount0 = amount1 * (2^96)^2 / (sqrtPriceX96)^2
            return
                Math.mulDiv(
                    Math.mulDiv(amount, FixedPoint96.Q96, sqrtPriceX96),
                    FixedPoint96.Q96,
                    sqrtPriceX96
                );
        }
    }

    /// @notice Update the TWAP oracle
    function _updateOracle(PoolKey memory key) internal {
        PoolId poolId = key.toId();
        (, int24 tick, , ) = poolManager.getSlot0(poolId);
        observations[poolId].write(
            oracleStates[poolId],
            uint32(block.timestamp),
            tick
        );
    }

    /* ======================================================================== */
    /*                        INTERNAL: HELPERS                                 */
    /* ======================================================================== */

    /// @notice Round a timestamp down to the nearest interval boundary
    function _getIntervalTime(
        uint256 timestamp
    ) internal view returns (uint256) {
        return (timestamp / expirationInterval) * expirationInterval;
    }

    /// @notice Generate a unique order ID from an OrderKey
    function _orderId(OrderKey memory key) internal pure returns (bytes32) {
        return keccak256(abi.encode(key));
    }

    /* ======================================================================== */
    /*                    LIQUIDATION: FORCE SETTLE                             */
    /* ======================================================================== */

    /// @notice Force-settle ghost balance by market-selling into V4 pool.
    /// @dev ONLY callable by verified brokers during liquidation.
    ///      Access control: verifies msg.sender is a legitimate broker registered
    ///      in the Core market via brokerVerifier. This allows the TWAMM hook to
    ///      remain broker-implementation-agnostic — any future broker version that
    ///      is registered in Core can call forceSettle.
    ///
    ///      After this, getCancelOrderState() returns accurate buyTokensOwed.
    ///      This prevents value destruction during TWAMM order cancellation in seize.
    ///
    /// @param key The pool key
    /// @param zeroForOne The sell direction (matches the TWAMM order direction)
    /// @param marketId The Core market ID (for broker verification)
    function forceSettle(
        PoolKey calldata key,
        bool zeroForOne,
        MarketId marketId
    ) external nonReentrant {
        // Verify caller is a verified broker in the Core market
        require(rldCore != address(0), "Core not set");
        IRLDCore.MarketConfig memory config = IRLDCore(rldCore).getMarketConfig(
            marketId
        );
        require(
            config.brokerVerifier != address(0) &&
                IBrokerVerifier(config.brokerVerifier).isValidBroker(
                    msg.sender
                ),
            "Not verified broker"
        );

        _accrueAndNet(key);
        _executePendingSettles(key);

        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];

        uint256 ghostAmount = zeroForOne ? state.accrued0 : state.accrued1;
        if (ghostAmount == 0) return;

        // Ensure stream is still active (otherwise earnings can't be recorded)
        StreamPool storage stream = zeroForOne
            ? state.stream0For1
            : state.stream1For0;
        if (stream.sellRateCurrent == 0) return;

        // Use shared settle function — swaps ghost against AMM, records earnings
        _settleGhostAgainstPool(key, state, stream, ghostAmount, zeroForOne);

        // Emit ForceSettle ADDITIONALLY for liquidation audit trail
        emit ForceSettle(poolId, ghostAmount, 0, zeroForOne);
    }

    /// @notice V4 unlock callback for forceSettle swaps
    /// @dev Executes the swap and settles token transfers with the pool
    function unlockCallback(
        bytes calldata rawData
    ) external returns (bytes memory) {
        require(msg.sender == address(poolManager), "Only PoolManager");

        (PoolKey memory key, SwapParams memory swapParams) = abi.decode(
            rawData,
            (PoolKey, SwapParams)
        );

        BalanceDelta delta = poolManager.swap(key, swapParams, "");

        // Settle: pay sold tokens from this contract's balance
        if (swapParams.zeroForOne) {
            if (delta.amount0() < 0) {
                key.currency0.settle(
                    poolManager,
                    address(this),
                    uint256(uint128(-delta.amount0())),
                    false
                );
            }
            if (delta.amount1() > 0) {
                key.currency1.take(
                    poolManager,
                    address(this),
                    uint256(uint128(delta.amount1())),
                    false
                );
            }
        } else {
            if (delta.amount1() < 0) {
                key.currency1.settle(
                    poolManager,
                    address(this),
                    uint256(uint128(-delta.amount1())),
                    false
                );
            }
            if (delta.amount0() > 0) {
                key.currency0.take(
                    poolManager,
                    address(this),
                    uint256(uint128(delta.amount0())),
                    false
                );
            }
        }

        return abi.encode(delta);
    }

    /* ======================================================================== */
    /*                          ORACLE QUERIES                                  */
    /* ======================================================================== */

    /// @inheritdoc IJTM
    function observe(
        PoolId poolId,
        uint32[] calldata secondsAgos
    ) external view returns (int56[] memory tickCumulatives) {
        (, int24 currentTick, , ) = poolManager.getSlot0(poolId);
        return
            observations[poolId].observe(
                oracleStates[poolId],
                uint32(block.timestamp),
                secondsAgos,
                currentTick
            );
    }

    /// @inheritdoc IJTM
    function increaseCardinality(
        PoolId poolId,
        uint16 next
    ) external returns (uint16 cardinalityNext) {
        return observations[poolId].grow(oracleStates[poolId], next);
    }
}
