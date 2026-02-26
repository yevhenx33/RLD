// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.26;

import {BaseHook} from "v4-periphery/src/utils/BaseHook.sol";
import {IHooks, Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {SafeCast} from "@uniswap/v4-core/src/libraries/SafeCast.sol";
import {
    IERC20Minimal
} from "@uniswap/v4-core/src/interfaces/external/IERC20Minimal.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {
    BeforeSwapDelta,
    BeforeSwapDeltaLibrary,
    toBeforeSwapDelta
} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {
    ModifyLiquidityParams,
    SwapParams
} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {FixedPoint96} from "@uniswap/v4-core/src/libraries/FixedPoint96.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Owned} from "solmate/src/auth/Owned.sol";

import {IJTM} from "./IJTM.sol";
import {TransferHelper} from "./libraries/TransferHelper.sol";
import {TwapOracle} from "./libraries/TwapOracle.sol";
import {CurrencySettler} from "@uniswap/v4-core/test/utils/CurrencySettler.sol";
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

    /// @notice Discount growth rate: basis points per second
    /// @dev Default 1 (= 0.01%/s). Set in constructor.
    uint256 public discountRateBpsPerSecond;

    /// @notice Maximum discount cap in basis points
    /// @dev Default 500 (= 5%). Set in constructor.
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

    /// @notice Accumulated orphaned TWAMM dust per currency, claimable by owner
    /// @dev On mainnet (12s blocks), each expired TWAMM order orphans at most
    ///      sellRate * 12 tokens — the final block of accrual that can't be
    ///      cleared because sellRateCurrent drops to 0 at the epoch boundary.
    ///      Rather than stranding these tokens permanently, they are accumulated
    ///      here and the owner redistributes via claimDust().
    mapping(Currency => uint256) public collectedDust;

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
        discountRateBpsPerSecond = 1; // 0.01% per second
        maxDiscountBps = 500; // 5% max
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
    function setDiscountRate(uint256 _bpsPerSecond) external onlyOwner {
        discountRateBpsPerSecond = _bpsPerSecond;
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

    /// @notice Claims accumulated orphaned TWAMM dust tokens
    /// @dev Only owner can call. Transfers all collected dust for a currency
    ///      to the specified recipient. Dust originates from the final block
    ///      of accrual per expired order that could not be cleared.
    /// @param currency The currency to claim dust for
    /// @param recipient Address to receive the dust tokens
    function claimDust(
        Currency currency,
        address recipient
    ) external onlyOwner {
        uint256 amount = collectedDust[currency];
        if (amount == 0) return;
        collectedDust[currency] = 0;
        IERC20Minimal(Currency.unwrap(currency)).safeTransfer(
            recipient,
            amount
        );
    }

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
        observations[poolId].grow(oracleStates[poolId], 10);

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
        _flushDonations(key);

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
        _flushDonations(key);
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
        _flushDonations(key);

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

        PoolId poolId = params.key.toId();
        uint256 currentInterval = _getIntervalTime(block.timestamp);

        orderKey = OrderKey({
            owner: msg.sender,
            expiration: (currentInterval + params.duration).toUint160(),
            zeroForOne: params.zeroForOne
        });

        if (orderKey.expiration <= block.timestamp) {
            revert ExpirationLessThanBlockTime(orderKey.expiration);
        }
        if (orderKey.expiration % expirationInterval != 0) {
            revert ExpirationNotOnInterval(orderKey.expiration);
        }

        uint256 sellRate = params.amountIn / params.duration;
        if (sellRate == 0) revert SellRateCannotBeZero();

        uint256 scaledSellRate = sellRate * RATE_SCALER;
        orderId = _orderId(orderKey);

        JITState storage state = poolStates[poolId];
        if (state.lastUpdateTimestamp == 0) revert NotInitialized();
        if (state.orders[orderId].sellRate != 0) {
            revert OrderAlreadyExists(orderKey);
        }

        // Update aggregate stream state
        StreamPool storage stream = params.zeroForOne
            ? state.stream0For1
            : state.stream1For0;

        stream.sellRateCurrent += scaledSellRate;
        stream.sellRateEndingAtInterval[orderKey.expiration] += scaledSellRate;

        uint256 earningsFactorLast = stream.earningsFactorCurrent;
        state.orders[orderId] = Order({
            sellRate: scaledSellRate,
            earningsFactorLast: earningsFactorLast
        });

        // Transfer tokens from user
        IERC20Minimal(
            params.zeroForOne
                ? Currency.unwrap(params.key.currency0)
                : Currency.unwrap(params.key.currency1)
        ).safeTransferFrom(
                msg.sender,
                address(this),
                sellRate * params.duration
            );

        emit SubmitOrder(
            poolId,
            orderId,
            msg.sender,
            // H2: Emit actual transfer amount (sellRate * duration <= params.amountIn)
            sellRate * params.duration,
            orderKey.expiration,
            params.zeroForOne,
            sellRate,
            earningsFactorLast
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

        stream.sellRateCurrent -= sellRate;
        stream.sellRateEndingAtInterval[orderKey.expiration] -= sellRate;

        // Calculate refund
        uint256 remainingSeconds = orderKey.expiration -
            state.lastUpdateTimestamp;
        sellTokensRefund = (sellRate * remainingSeconds) / RATE_SCALER;

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

        PoolId poolId = poolKey.toId();
        JITState storage state = poolStates[poolId];
        bytes32 orderId = _orderId(orderKey);
        Order storage order = state.orders[orderId];

        if (order.sellRate == 0) {
            revert OrderDoesNotExist(
                OrderKey(
                    orderKey.owner,
                    orderKey.expiration,
                    orderKey.zeroForOne
                )
            );
        }

        StreamPool storage stream = orderKey.zeroForOne
            ? state.stream0For1
            : state.stream1For0;

        // Calculate earnings since last sync
        uint256 earningsFactorDelta = stream.earningsFactorCurrent -
            order.earningsFactorLast;
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

        // Calculate dynamic discount
        uint256 elapsedSinceClear = block.timestamp - state.lastClearTimestamp;
        uint256 discountBps = elapsedSinceClear * discountRateBpsPerSecond;
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
        currentDiscount = timeSinceLastClear * discountRateBpsPerSecond;
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
        uint256 earningsFactorDelta = stream.earningsFactorCurrent -
            order.earningsFactorLast;
        if (earningsFactorDelta > 0) {
            buyTokensOwed = Math.mulDiv(
                order.sellRate,
                earningsFactorDelta,
                FixedPoint96.Q96 * RATE_SCALER
            );
        }

        // Compute refund for remaining time
        if (state.lastUpdateTimestamp < orderKey.expiration) {
            uint256 remainingSeconds = orderKey.expiration -
                state.lastUpdateTimestamp;
            sellTokensRefund =
                (order.sellRate * remainingSeconds) /
                RATE_SCALER;
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
                _crossEpoch(state.stream0For1, epoch);
                _crossEpoch(state.stream1For0, epoch);
            }
        }

        // === Step 4: Orphan remaining accrued tokens if stream expired ===
        // When sellRateCurrent drops to 0 (all orders expired), any remaining
        // accrued tokens can never be cleared — clear() reverts with NoActiveStream
        // because _recordEarnings would divide by zero.
        //
        // TRANSPARENCY NOTE: On mainnet (12s blocks), this captures at most
        // sellRate * 12 tokens per expired order — the final block of accrual
        // between the last possible clear() and the epoch crossing. These tokens
        // are redirected to LPs as fee income via V4's donate() mechanism,
        // rather than being permanently stranded in the hook contract.
        if (state.stream0For1.sellRateCurrent == 0 && state.accrued0 > 0) {
            state.pendingDonation0 += state.accrued0;
            state.accrued0 = 0;
        }
        if (state.stream1For0.sellRateCurrent == 0 && state.accrued1 > 0) {
            state.pendingDonation1 += state.accrued1;
            state.accrued1 = 0;
        }

        state.lastUpdateTimestamp = currentTime;
    }

    /// @notice Flush pending donations: move orphaned accrued tokens to protocol fees
    /// @dev Orphaned tokens accumulate when TWAMM streams expire with residual accrued
    ///      balances. On mainnet (12s blocks), each expired order orphans at most
    ///      sellRate * 12 tokens — the final block of accrual that could not be
    ///      cleared before the epoch boundary. These tokens are moved to collectedFees
    ///      (the protocol fee pool) rather than being permanently stranded.
    ///
    ///      TRANSPARENCY NOTE: These tokens came from TWAMM trader deposits. They
    ///      represent ~1 block worth of accrual per expired order. The owner should
    ///      redistribute them to LPs or return them to traders via claimProtocolFees().
    ///
    ///      Why not V4 donate()? donate() calls beforeDonate/afterDonate hooks on
    ///      the pool's hook address (this contract), which would require the
    ///      BEFORE_DONATE_FLAG permission bit — changing the hook's deployed address.
    function _flushDonations(PoolKey calldata key) internal {
        JITState storage state = poolStates[key.toId()];
        uint256 d0 = state.pendingDonation0;
        uint256 d1 = state.pendingDonation1;
        if (d0 == 0 && d1 == 0) return;

        state.pendingDonation0 = 0;
        state.pendingDonation1 = 0;

        // Move to dust collection — owner can claim via claimDust()
        if (d0 > 0) {
            collectedDust[key.currency0] += d0;
        }
        if (d1 > 0) {
            collectedDust[key.currency1] += d1;
        }

        emit DustDonatedToLPs(key.toId(), d0, d1);
    }

    /// @notice Cross an epoch boundary: snapshot earningsFactor and subtract expired sellRate
    function _crossEpoch(StreamPool storage stream, uint256 epoch) internal {
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

        PoolId poolId = key.toId();
        JITState storage state = poolStates[poolId];

        uint256 ghostAmount = zeroForOne ? state.accrued0 : state.accrued1;
        if (ghostAmount == 0) return;

        // Ensure stream is still active (otherwise earnings can't be recorded)
        StreamPool storage stream = zeroForOne
            ? state.stream0For1
            : state.stream1For0;
        if (stream.sellRateCurrent == 0) return;

        // Market-sell ghost against the V4 pool (no price limit = accept full slippage)
        SwapParams memory swapParams = SwapParams(
            zeroForOne,
            -int256(ghostAmount),
            zeroForOne
                ? TickMath.MIN_SQRT_PRICE + 1
                : TickMath.MAX_SQRT_PRICE - 1
        );

        bytes memory result = poolManager.unlock(abi.encode(key, swapParams));
        BalanceDelta delta = abi.decode(result, (BalanceDelta));

        // Calculate swap proceeds
        uint256 proceeds = zeroForOne
            ? uint256(uint128(delta.amount1()))
            : uint256(uint128(delta.amount0()));

        // Record earnings so getCancelOrderState() reflects real value
        if (proceeds > 0) {
            _recordEarnings(stream, proceeds);
        }

        // Clear ghost
        if (zeroForOne) {
            state.accrued0 = 0;
        } else {
            state.accrued1 = 0;
        }

        emit ForceSettle(poolId, ghostAmount, proceeds, zeroForOne);
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
