// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.26;

import {BaseHook} from "v4-periphery/src/utils/BaseHook.sol";
import {IHooks, Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {TickBitmap} from "@uniswap/v4-core/src/libraries/TickBitmap.sol";
import {SqrtPriceMath} from "@uniswap/v4-core/src/libraries/SqrtPriceMath.sol";
import {FixedPoint96} from "@uniswap/v4-core/src/libraries/FixedPoint96.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {SafeCast} from "@uniswap/v4-core/src/libraries/SafeCast.sol";
import {
    IERC20Minimal
} from "@uniswap/v4-core/src/interfaces/external/IERC20Minimal.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";
import {SwapMath} from "@uniswap/v4-core/src/libraries/SwapMath.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {CurrencySettler} from "@uniswap/v4-core/test/utils/CurrencySettler.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {
    TransientStateLibrary
} from "@uniswap/v4-core/src/libraries/TransientStateLibrary.sol";
import {
    ProtocolFeeLibrary
} from "@uniswap/v4-core/src/libraries/ProtocolFeeLibrary.sol";
import {
    BeforeSwapDelta,
    BeforeSwapDeltaLibrary
} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {LiquidityMath} from "@uniswap/v4-core/src/libraries/LiquidityMath.sol";
import {
    ModifyLiquidityParams,
    SwapParams
} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Owned} from "solmate/src/auth/Owned.sol";

import {IRLDCore, MarketId} from "../shared/interfaces/IRLDCore.sol";
import {ITWAMM} from "./ITWAMM.sol";

import {PoolGetters} from "./libraries/PoolGetters.sol";
import {OrderPool} from "./libraries/OrderPool.sol";
import {TransferHelper} from "./libraries/TransferHelper.sol";

import {TwapOracle} from "./libraries/TwapOracle.sol";

import {
    IUnlockCallback
} from "@uniswap/v4-core/src/interfaces/callback/IUnlockCallback.sol";

/// @dev Scaling factor for sell rates to maintain precision (1e18)
/// All sell rates are multiplied by this to avoid rounding errors in calculations
uint256 constant RATE_SCALER = 1e18;

/**
 * @title TWAMM Hook - Time-Weighted Average Market Maker
 * @notice Implements Paradigm's TWAMM algorithm as a Uniswap V4 hook, enabling large orders
 *         to be executed gradually over time without significant price impact.
 *
 * @dev Architecture Overview:
 *      - Users submit long-term orders with a sell rate (tokens/second) and expiration
 *      - Orders are grouped into two pools per AMM: 0→1 and 1→0
 *      - Virtual order execution happens at interval boundaries (e.g., every hour)
 *      - Opposing orders are matched internally first (no swap needed)
 *      - Remaining imbalance swaps against the AMM pool
 *      - Earnings are distributed proportionally via an earnings factor
 *
 * @dev Key Mechanisms:
 *      1. Interval-based execution: Orders execute at fixed time intervals to batch processing
 *      2. Virtual matching: Opposing orders cancel out without touching the pool
 *      3. Earnings factor: Tracks cumulative earnings per unit of sell rate (like Compound's index)
 *      4. Lazy execution: Orders only execute when someone triggers executeTWAMMOrders()
 *
 * @dev Security Features:
 *      - Price bounds: Optional min/max price limits to prevent manipulation
 *      - Protocol fees: Configurable fee on swaps (max 0.05%)
 *      - Trading fee: Static fee applied to TWAMM order execution
 *      - Oracle integration: TWAP oracle for price history
 *
 * @author Uniswap Labs
 * @author Zaha Studio
 */
contract TWAMM is BaseHook, Owned, ReentrancyGuard, ITWAMM, IUnlockCallback {
    using TransferHelper for IERC20Minimal;
    using CurrencySettler for Currency;
    using OrderPool for OrderPool.State;
    using PoolIdLibrary for PoolKey;
    using TickMath for int24;
    using TickMath for uint160;
    using SafeCast for uint256;
    using PoolGetters for IPoolManager;
    using TickBitmap for mapping(int16 => uint256);
    using StateLibrary for IPoolManager;
    using TransientStateLibrary for IPoolManager;
    using TwapOracle for mapping(uint256 => TwapOracle.Observation);
    using TwapOracle for TwapOracle.State;

    bytes internal constant ZERO_BYTES = bytes("");

    /* ============================================================================ */
    /*                              STATE VARIABLES                                */
    /* ============================================================================ */

    /// @notice Time interval on which orders are allowed to expire (e.g., 3600 = 1 hour)
    /// @dev Orders must expire at multiples of this interval to enable batched processing
    ///      Smaller intervals = more frequent execution but higher gas costs
    ///      Larger intervals = less frequent execution but better gas efficiency
    uint256 public immutable expirationInterval;

    /// @notice Core TWAMM state for each pool (order pools, last execution time, orders)
    /// @dev Contains two OrderPool.State structs (0→1 and 1→0) and order mappings
    mapping(PoolId poolId => TWAMMState twammState) internal twammStates;

    /// @notice Tracks tokens owed to users from order earnings and cancellations
    /// @dev Updated during sync() and claimed via claimTokens()
    ///      Format: tokensOwed[currency][user] = amount
    mapping(Currency token => mapping(address owner => uint256 amountOwed))
        public tokensOwed;

    /// @notice TWAP oracle observations for price history
    /// @dev Stores tick observations at different timestamps for TWAP calculations
    mapping(PoolId => mapping(uint256 => TwapOracle.Observation))
        public observations;

    /// @notice Oracle state tracking (cardinality, index)
    mapping(PoolId => TwapOracle.State) public oracleStates;

    /// @notice Optional price bounds to prevent manipulation attacks
    /// @dev If set, swaps that would move price outside [min, max] will revert
    ///      Bounds are in sqrtPriceX96 format (Q64.96 fixed point)
    struct PriceBounds {
        uint160 min; // Minimum allowed sqrtPriceX96
        uint160 max; // Maximum allowed sqrtPriceX96
    }

    /// @notice Price bounds per pool (if bounds.max == 0, no bounds are set)
    mapping(PoolId => PriceBounds) public priceBounds;

    /* ============================================================================ */
    /*                          ADMIN & CONFIGURATION                              */
    /* ============================================================================ */

    /// @notice Sets price bounds for a pool (one-time only)
    /// @dev Bounds prevent price manipulation by reverting swaps that move price outside range
    ///      Can only be set once per pool (bounds.max == 0 check)
    /// @param key The pool key
    /// @param min Minimum allowed sqrtPriceX96
    /// @param max Maximum allowed sqrtPriceX96
    function setPriceBounds(
        PoolKey calldata key,
        uint160 min,
        uint160 max
    ) external {
        PriceBounds storage bounds = priceBounds[key.toId()];
        if (bounds.max != 0) revert("Bounds already set");
        bounds.min = min;
        bounds.max = max;
    }

    /// @notice Constructs the TWAMM hook
    /// @param _manager The Uniswap V4 PoolManager contract
    /// @param _expirationInterval Time interval for order expiration (e.g., 3600 for 1 hour)
    /// @param initialOwner Address that will own this contract (for admin functions)
    /// @param _rldCore The RLDCore contract address (immutable after deployment)
    constructor(
        IPoolManager _manager,
        uint256 _expirationInterval,
        address initialOwner,
        address _rldCore
    ) BaseHook(_manager) Owned(initialOwner) {
        if (_expirationInterval == 0) {
            revert InvalidExpirationInterval();
        }

        expirationInterval = _expirationInterval;
        rldCore = _rldCore;
    }

    /* ============================================================================ */
    /*                          UNISWAP V4 HOOK LIFECYCLE                          */
    /* ============================================================================ */

    /// @inheritdoc BaseHook
    /// @notice Declares which pool lifecycle events this hook wants to intercept
    /// @dev We intercept:
    ///      - beforeInitialize: Set up TWAMM state when pool is created
    ///      - beforeAddLiquidity/beforeRemoveLiquidity: Execute pending orders before LP changes
    ///      - beforeSwap: Execute pending orders before regular swaps
    ///      - afterSwap: Validate price bounds and collect fees on exact output swaps
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
                beforeSwapReturnDelta: false,
                afterSwapReturnDelta: false,
                afterAddLiquidityReturnDelta: false,
                afterRemoveLiquidityReturnDelta: false
            });
    }

    /// @notice Called when a pool is first initialized
    /// @dev Rejects pools with native ETH (only ERC20 supported)
    ///      Initializes TWAMM state and oracle for the pool
    function _beforeInitialize(
        address,
        PoolKey calldata key,
        uint160
    ) internal override returns (bytes4) {
        if (key.currency0.isAddressZero()) {
            revert PoolWithNativeNotSupported();
        }

        // one-time initialization enforced in PoolManager
        initialize(_getTWAMM(key));
        _initializeOracle(key);

        return BaseHook.beforeInitialize.selector;
    }

    /// @notice Called before liquidity is added to the pool
    /// @dev Executes pending TWAMM orders first to ensure accurate pool state
    ///      Validates that LP position doesn't violate price bounds (if set)
    function _beforeAddLiquidity(
        address,
        PoolKey calldata key,
        ModifyLiquidityParams calldata params,
        bytes calldata
    ) internal override returns (bytes4) {
        _updateOracle(key);
        executeTWAMMOrders(key);

        PriceBounds memory bounds = priceBounds[key.toId()];
        if (bounds.min != 0) {
            uint160 lowerSqrt = TickMath.getSqrtPriceAtTick(params.tickLower);
            uint160 upperSqrt = TickMath.getSqrtPriceAtTick(params.tickUpper);
            if (lowerSqrt < bounds.min || upperSqrt > bounds.max)
                revert("LP Range Out of Bounds");
        }

        return BaseHook.beforeAddLiquidity.selector;
    }

    /// @notice Called before liquidity is removed from the pool
    /// @dev Executes pending TWAMM orders (with try/catch to never block LP removal)
    ///      Liquidity removal must always succeed even if TWAMM execution fails
    function _beforeRemoveLiquidity(
        address,
        PoolKey calldata key,
        ModifyLiquidityParams calldata,
        bytes calldata
    ) internal override returns (bytes4) {
        // Look up the oracle state and update it
        _updateOracle(key);
        // Liquidity removal must always be unblocked.
        try this.executeTWAMMOrders(key) {} catch {}
        return BaseHook.beforeRemoveLiquidity.selector;
    }

    /* ============================================================================ */
    /*                            PROTOCOL FEE MANAGEMENT                          */
    /* ============================================================================ */

    /// @notice Protocol fee per pool (in pips, denominator is 1,000,000)
    /// @dev Example: 500 = 0.05% fee
    mapping(PoolId => uint24) public protocolFees;

    /// @notice Accumulated protocol fees per currency
    mapping(Currency => uint256) public collectedFees;

    /// @notice Maximum protocol fee (0.05% = 500 pips)
    uint24 public constant MAX_PROTOCOL_FEE = 500;

    /// @notice Sets the protocol fee for a specific pool
    /// @dev Only owner can call. Fee is capped at 0.05%
    /// @param key The pool key
    /// @param newFee Fee in pips (e.g., 500 = 0.05%)
    function setProtocolFee(
        PoolKey calldata key,
        uint24 newFee
    ) external onlyOwner {
        if (newFee > MAX_PROTOCOL_FEE) revert("Fee exceeds 0.05%");
        protocolFees[key.toId()] = newFee;
    }

    /// @notice Claims accumulated protocol fees
    /// @dev Only owner can call. Transfers all collected fees for a currency
    /// @param currency The currency to claim
    /// @param recipient Address to receive the fees
    function claimProtocolFees(
        Currency currency,
        address recipient
    ) external onlyOwner {
        uint256 amount = collectedFees[currency];
        collectedFees[currency] = 0;
        currency.transfer(recipient, amount);
    }

    /* ============================================================================ */
    /*                            CURATOR FEE MANAGEMENT                           */
    /* ============================================================================ */

    /// @notice RLDCore contract address
    /// @dev Changed from immutable to storage to support two-phase deployment
    ///      TWAMM must be deployed before Core (for address mining), but needs Core reference
    ///      Solution: Deploy with address(0), then call setRldCore() after Core is deployed
    address public rldCore;

    /// @notice Sets the RLDCore address (one-time only)
    /// @dev Allows two-phase deployment: deploy TWAMM first, then Core, then link them
    ///      Can only be called by owner and only when rldCore is not yet set
    /// @param _rldCore The RLDCore contract address
    function setRldCore(address _rldCore) external onlyOwner {
        require(rldCore == address(0), "Already set");
        require(_rldCore != address(0), "Invalid address");
        rldCore = _rldCore;
    }

    /// @notice Updates the dynamic LP fee for a pool
    /// @dev Only callable by the curator of the market that uses this pool
    ///      Curator is determined by querying RLDCore for the market's curator address
    /// @param marketId The RLD market ID that this pool belongs to
    /// @param key The pool key
    /// @param newFee New fee in hundredths of bips (e.g., 3000 = 0.3%)
    function updateDynamicLPFee(
        MarketId marketId,
        PoolKey calldata key,
        uint24 newFee
    ) external nonReentrant {
        // Verify caller is the curator for this market
        if (rldCore == address(0)) revert Unauthorized();

        IRLDCore.MarketAddresses memory addresses = IRLDCore(rldCore)
            .getMarketAddresses(marketId);
        if (msg.sender != addresses.curator) revert Unauthorized();

        // V4 max fee is 100% = 1000000 (in hundredths of bips)
        // LPFeeLibrary will validate the actual fee value
        poolManager.updateDynamicLPFee(key, newFee);
    }

    /* ... beforeAddLiquidity ... */

    /// @notice Called before a swap is executed
    /// @dev Executes pending TWAMM orders first, then charges protocol fee on exact input swaps
    ///      For exact input (amountSpecified < 0): Fee charged upfront on input token
    ///      For exact output (amountSpecified > 0): Fee charged in afterSwap on output token
    /// @param sender The address initiating the swap
    /// @param key The pool key
    /// @param params Swap parameters (direction, amount, price limit)
    /// @return selector Function selector to confirm execution
    /// @return delta BeforeSwapDelta (always zero for this hook)
    /// @return lpFeeOverride LP fee override (always 0, we don't override)
    function _beforeSwap(
        address sender,
        PoolKey calldata key,
        SwapParams calldata params,
        bytes calldata
    ) internal override returns (bytes4, BeforeSwapDelta, uint24) {
        _updateOracle(key);
        executeTWAMMOrders(key);

        uint24 fee = protocolFees[key.toId()];
        if (fee > 0 && params.amountSpecified < 0) {
            // Exact Input: Charge fee on input token upfront
            Currency feeCurrency = params.zeroForOne
                ? key.currency0
                : key.currency1;
            uint256 feeAmount = (uint256(-params.amountSpecified) * fee) /
                1000000;

            if (feeAmount > 0) {
                IERC20Minimal(Currency.unwrap(feeCurrency)).transferFrom(
                    sender,
                    address(this),
                    feeAmount
                );
                collectedFees[feeCurrency] += feeAmount;
            }
        }
        // Note: Exact output fees are handled in _afterSwap

        return (
            BaseHook.beforeSwap.selector,
            BeforeSwapDeltaLibrary.ZERO_DELTA,
            0
        );
    }

    /// @notice Called after a swap is executed
    /// @dev Validates price bounds and charges protocol fee on exact output swaps
    ///      For exact output swaps, we take a fee from the output amount using negative delta
    /// @param key The pool key
    /// @param params Swap parameters
    /// @param delta The balance changes from the swap
    /// @return selector Function selector to confirm execution
    /// @return hookDelta Amount to adjust user's output (negative = take fee from user)
    function _afterSwap(
        address,
        PoolKey calldata key,
        SwapParams calldata params,
        BalanceDelta delta,
        bytes calldata
    ) internal override returns (bytes4, int128) {
        (uint160 sqrtPriceX96, , , ) = poolManager.getSlot0(key.toId());

        PriceBounds memory bounds = priceBounds[key.toId()];
        if (bounds.min != 0) {
            if (sqrtPriceX96 < bounds.min || sqrtPriceX96 > bounds.max)
                revert("Price Out of Bounds");
        }

        // Handle protocol fee for exact output swaps
        uint24 fee = protocolFees[key.toId()];
        if (fee > 0 && params.amountSpecified > 0) {
            // Exact Output: Charge fee on output token
            // The output is what the user receives, we take a cut
            Currency feeCurrency = params.zeroForOne
                ? key.currency1
                : key.currency0;
            int128 outputAmount = params.zeroForOne
                ? delta.amount1()
                : delta.amount0();

            if (outputAmount > 0) {
                uint256 feeAmount = (uint256(uint128(outputAmount)) * fee) /
                    1000000;
                if (feeAmount > 0) {
                    collectedFees[feeCurrency] += feeAmount;
                    // Return negative delta to take fee from user's output
                    return (
                        BaseHook.afterSwap.selector,
                        -int128(int256(feeAmount))
                    );
                }
            }
        }

        return (BaseHook.afterSwap.selector, 0);
    }

    /* ============================================================================ */
    /*                          TWAMM ORDER STATE GETTERS                          */
    /* ============================================================================ */

    /// @inheritdoc ITWAMM
    /// @notice Returns the last timestamp at which virtual orders were executed for a pool
    /// @dev This is rounded down to the nearest interval boundary
    function lastVirtualOrderTimestamp(
        PoolId key
    ) external view returns (uint256) {
        return twammStates[key].lastVirtualOrderTimestamp;
    }

    /// @inheritdoc ITWAMM
    /// @notice Retrieves a specific order's state
    /// @param poolKey The pool containing the order
    /// @param orderKey The order identifier (owner, expiration, direction)
    /// @return Order struct with sellRate and earningsFactorLast
    function getOrder(
        PoolKey calldata poolKey,
        OrderKey calldata orderKey
    ) external view returns (Order memory) {
        return _getOrder(twammStates[poolKey.toId()], _orderId(orderKey));
    }

    /// @inheritdoc ITWAMM
    /// @notice Returns the current state of an order pool (all orders in one direction)
    /// @param key The pool key
    /// @param zeroForOne True for 0→1 pool, false for 1→0 pool
    /// @return sellRateCurrent Total sell rate across all active orders (scaled by RATE_SCALER)
    /// @return earningsFactorCurrent Cumulative earnings factor for profit distribution
    function getOrderPool(
        PoolKey calldata key,
        bool zeroForOne
    )
        external
        view
        returns (uint256 sellRateCurrent, uint256 earningsFactorCurrent)
    {
        TWAMMState storage twamm = _getTWAMM(key);

        return
            zeroForOne
                ? (
                    twamm.orderPool0For1.sellRateCurrent,
                    twamm.orderPool0For1.earningsFactorCurrent
                )
                : (
                    twamm.orderPool1For0.sellRateCurrent,
                    twamm.orderPool1For0.earningsFactorCurrent
                );
    }

    /* ============================================================================ */
    /*                          TWAMM CORE LOGIC                                   */
    /* ============================================================================ */

    /// @notice Initialize TWAMM state for a pool
    /// @dev Sets lastVirtualOrderTimestamp to current interval boundary
    ///      Called once during pool initialization in beforeInitialize hook
    function initialize(TWAMMState storage self) internal {
        self.lastVirtualOrderTimestamp = _getIntervalTime(block.timestamp);
    }

    /// @inheritdoc ITWAMM
    /// @notice Executes all pending TWAMM orders up to a specific timestamp
    /// @dev This is the main execution engine that:
    ///      1. Calculates virtual order matching between opposing directions
    ///      2. Determines if a swap against the pool is needed
    ///      3. Executes the swap if necessary
    ///      4. Updates order pool state and earnings factors
    /// @param key The pool key
    /// @param targetTimestamp The timestamp to execute orders up to (rounded to interval)
    function executeTWAMMOrders(
        PoolKey memory key,
        uint256 targetTimestamp
    ) public {
        PoolId poolId = key.toId();
        TWAMMState storage twamm = twammStates[poolId];

        if (twamm.lastVirtualOrderTimestamp == 0) {
            revert NotInitialized();
        }

        (uint160 sqrtPriceX96, , uint24 protocolFee, uint24 lpFee) = poolManager
            .getSlot0(poolId);
        (
            bool zeroForOne,
            uint160 sqrtPriceLimitX96,
            uint256 maxSwapAmount
        ) = _executeTWAMMOrders(
                twamm,
                key,
                PoolParamsOnExecute(
                    sqrtPriceX96,
                    protocolFee,
                    lpFee,
                    poolManager.getLiquidity(poolId),
                    0,
                    0
                ),
                targetTimestamp
            );

        // If virtual matching left an imbalance, execute swap against pool
        if (
            sqrtPriceLimitX96 != 0 &&
            sqrtPriceLimitX96 != sqrtPriceX96 &&
            maxSwapAmount != 0
        ) {
            SwapParams memory swapParams = SwapParams(
                zeroForOne,
                -maxSwapAmount.toInt256(),
                sqrtPriceLimitX96
            );

            if (poolManager.isUnlocked()) {
                _processSwap(key, swapParams);
            } else {
                poolManager.unlock(abi.encode(key, swapParams));
            }

            emit Fulfillment(
                poolId,
                twamm.orderPool0For1.sellRateCurrent,
                twamm.orderPool1For0.sellRateCurrent
            );
        }
    }

    /// @inheritdoc ITWAMM
    /// @notice Executes all pending TWAMM orders up to current block timestamp
    /// @dev Convenience wrapper that calls executeTWAMMOrders(key, block.timestamp)
    function executeTWAMMOrders(PoolKey memory key) public override {
        executeTWAMMOrders(key, block.timestamp);
    }

    /* ============================================================================ */
    /*                          ORDER SUBMISSION & MANAGEMENT                      */
    /* ============================================================================ */

    /// @inheritdoc ITWAMM
    /// @notice Submits multiple TWAMM orders in a single transaction
    /// @dev Gas-efficient batch submission. Each order is processed independently
    /// @param orders Array of order parameters
    /// @return orderIds Array of generated order IDs
    /// @return orderKeys Array of order keys (owner, expiration, direction)
    function batchSubmitOrders(
        SubmitOrderParams[] calldata orders
    )
        external
        returns (bytes32[] memory orderIds, OrderKey[] memory orderKeys)
    {
        orderIds = new bytes32[](orders.length);
        orderKeys = new OrderKey[](orders.length);

        for (uint256 i = 0; i < orders.length; i++) {
            (orderIds[i], orderKeys[i]) = _submitOrder(orders[i]);
        }
    }

    /// @inheritdoc ITWAMM
    function submitOrder(
        SubmitOrderParams calldata params
    ) external returns (bytes32 orderId, OrderKey memory orderKey) {
        return _submitOrder(params);
    }

    function _submitOrder(
        SubmitOrderParams calldata params
    ) internal returns (bytes32 orderId, OrderKey memory orderKey) {
        executeTWAMMOrders(params.key);

        PoolId poolId = params.key.toId();
        uint256 currentTimestampAtInterval = _getIntervalTime(block.timestamp);
        orderKey = OrderKey({
            owner: msg.sender,
            expiration: (currentTimestampAtInterval + params.duration)
                .toUint160(),
            zeroForOne: params.zeroForOne
        });

        TWAMMState storage twamm = twammStates[poolId];

        if (orderKey.expiration <= block.timestamp) {
            revert ExpirationLessThanBlockTime(orderKey.expiration);
        }
        if (orderKey.expiration % expirationInterval != 0) {
            revert ExpirationNotOnInterval(orderKey.expiration);
        }

        uint256 sellRate = params.amountIn / params.duration;
        // uint256 sellRate = Math.mulDiv(params.amountIn, RATE_SCALER, params.duration);
        if (sellRate == 0) {
            revert SellRateCannotBeZero();
        }

        // Sell rate is scaled after, since we want amounts to stay in the original scale
        uint256 scaledSellRate = sellRate * RATE_SCALER;

        orderId = _orderId(orderKey);

        if (twamm.orders[orderId].sellRate != 0) {
            revert OrderAlreadyExists(orderKey);
        }

        OrderPool.State storage orderPool = params.zeroForOne
            ? twamm.orderPool0For1
            : twamm.orderPool1For0;

        orderPool.sellRateCurrent += scaledSellRate;
        orderPool.sellRateEndingAtInterval[
            orderKey.expiration
        ] += scaledSellRate;

        uint256 earningsFactorLast = orderPool.earningsFactorCurrent;
        twamm.orders[orderId] = Order({
            sellRate: scaledSellRate,
            earningsFactorLast: earningsFactorLast
        });

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
            orderKey.owner,
            params.amountIn,
            orderKey.expiration,
            params.zeroForOne,
            sellRate,
            earningsFactorLast
        );

        return (orderId, orderKey);
    }

    /// @inheritdoc ITWAMM
    function cancelOrder(
        PoolKey calldata key,
        OrderKey calldata orderKey
    ) external returns (uint256 buyTokensOut, uint256 sellTokensRefund) {
        if (msg.sender != orderKey.owner) {
            revert Unauthorized();
        }

        // 1. Sync the order to update earnings and tokensOwed up to the last executed interval
        (buyTokensOut, ) = sync(SyncParams(key, orderKey));

        // 2. Load State
        PoolId poolId = key.toId();
        TWAMMState storage twamm = twammStates[poolId];
        bytes32 orderId = _orderId(orderKey);
        Order storage order = twamm.orders[orderId];

        uint256 sellRate = order.sellRate;
        if (sellRate == 0) {
            revert OrderDoesNotExist(orderKey);
        }

        // 3. Ensure not already expired (though sync handles expiration, we want to prevent refunding fully expired orders)
        if (twamm.lastVirtualOrderTimestamp >= orderKey.expiration) {
            revert OrderAlreadyExpired(orderKey);
        }

        // 4. Update OrderPool State (CRITICAL: Remove from current AND future expiration)
        OrderPool.State storage orderPool = orderKey.zeroForOne
            ? twamm.orderPool0For1
            : twamm.orderPool1For0;

        orderPool.sellRateCurrent -= sellRate;
        orderPool.sellRateEndingAtInterval[orderKey.expiration] -= sellRate;

        // 5. Calculate Refund
        // Refund is for the time remaining from the last processed interval until expiration
        uint256 remainingSeconds = orderKey.expiration -
            twamm.lastVirtualOrderTimestamp;
        sellTokensRefund = (sellRate * remainingSeconds) / RATE_SCALER;

        // 6. Delete Order
        delete twamm.orders[orderId];

        // 7. Transfer Refund (Sell Token) to User
        Currency sellToken = orderKey.zeroForOne
            ? key.currency0
            : key.currency1;
        sellToken.transfer(msg.sender, sellTokensRefund);

        // 8. Claim Owed Tokens (Buy Token + any previously owed Sell Token)
        // Note: sync() updated tokensOwed. We use claimTokensByPoolKey to flush everything.
        (uint256 c0, uint256 c1) = claimTokensByPoolKey(key);

        // Return total buy tokens claimed (one of c0 or c1 will be the buy token)
        buyTokensOut = orderKey.zeroForOne ? c1 : c0;

        emit CancelOrder(poolId, orderId, msg.sender, sellTokensRefund);
    }

    /// @inheritdoc ITWAMM
    function getCancelOrderState(
        PoolKey calldata key,
        OrderKey calldata orderKey
    ) external view returns (uint256 buyTokensOwed, uint256 sellTokensRefund) {
        PoolId poolId = key.toId();
        TWAMMState storage twamm = twammStates[poolId];
        bytes32 orderId = _orderId(orderKey);
        Order storage order = _getOrder(twamm, orderId);

        if (order.sellRate == 0) {
            // Return 0s if order doesn't exist
            return (0, 0);
        }

        // Calculate Refund
        // We simulate the time that WOULD be used if synced now.
        // sync() -> executeTWAMMOrders() -> uses _getIntervalTime(block.timestamp)
        uint256 currentTimestampAtInterval = _getIntervalTime(block.timestamp);

        // The lastVirtualOrderTimestamp is where the system IS.
        // The effective processing time for refund calculation is the max of the two.
        // If lastVirtual is AHEAD of current interval (unlikely unless future manip), use that.
        // If current interval is AHEAD of lastVirtual (likely), use current interval.
        uint256 lastProcessedTime = twamm.lastVirtualOrderTimestamp;
        // If intervals have passed, they will be executed upon cancel.
        // So the refund starts from the END of the current executing interval.
        uint256 effectiveStartTime = currentTimestampAtInterval >
            lastProcessedTime
            ? currentTimestampAtInterval
            : lastProcessedTime;

        if (effectiveStartTime >= orderKey.expiration) {
            sellTokensRefund = 0;
        } else {
            uint256 remainingSeconds = orderKey.expiration - effectiveStartTime;
            sellTokensRefund =
                (order.sellRate * remainingSeconds) /
                RATE_SCALER;
        }

        // Calculate Pending Earnings
        // 1. Already owed in tokensOwed (historical)
        Currency buyToken = orderKey.zeroForOne ? key.currency1 : key.currency0;
        buyTokensOwed = tokensOwed[buyToken][orderKey.owner];

        // 2. Pending from the last sync until "now" (simulated)
        // We can only simulate earnings if the system has ALREADY processed up to the simulated time, OR if we accept that we return "accrued so far".
        // BUT `OrderPool` stores `earningsFactorCurrent`. This factor increases as `advanceToInterval` is called.
        // A view function CANNOT simulate `executeTWAMMOrders` (swaps).
        // So we can only return earnings based on `earningsFactorCurrent` stored in state.
        // This means `buyTokensOwed` will be UNDER-estimated if `lastVirtual` is old.
        // However, this is the best we can do in a view function without simulating swaps.

        OrderPool.State storage orderPool = orderKey.zeroForOne
            ? twamm.orderPool0For1
            : twamm.orderPool1For0;
        bool isOrderExpired = orderKey.expiration <= lastProcessedTime;

        uint256 earningsFactorLast = isOrderExpired
            ? orderPool.earningsFactorAtInterval[orderKey.expiration]
            : orderPool.earningsFactorCurrent;

        if (earningsFactorLast > order.earningsFactorLast) {
            buyTokensOwed +=
                (
                    Math.mulDiv(
                        earningsFactorLast - order.earningsFactorLast,
                        order.sellRate,
                        RATE_SCALER
                    )
                ) >>
                FixedPoint96.RESOLUTION;
        }
    }

    /// @inheritdoc ITWAMM
    function claimTokensByPoolKey(
        PoolKey calldata key
    ) public returns (uint256 tokens0Claimed, uint256 tokens1Claimed) {
        tokens0Claimed = _claimTokens(key.currency0);
        tokens1Claimed = _claimTokens(key.currency1);
    }

    /// @inheritdoc ITWAMM
    function claimTokensByCurrencies(
        Currency[] calldata currencies
    ) public returns (uint256[] memory tokensClaimed) {
        tokensClaimed = new uint256[](currencies.length);

        for (uint256 i = 0; i < currencies.length; i++) {
            tokensClaimed[i] = _claimTokens(currencies[i]);
        }
    }

    /// @inheritdoc ITWAMM
    function syncAndClaimTokens(
        SyncParams calldata params
    ) external returns (uint256 tokens0Claimed, uint256 tokens1Claimed) {
        // Calls executeTWAMMOrders
        sync(params);

        (tokens0Claimed, tokens1Claimed) = claimTokensByPoolKey(params.key);
    }

    /// @inheritdoc ITWAMM
    function batchSyncAndClaimTokens(
        SyncParams[] calldata params,
        Currency[] calldata currencies
    ) external returns (uint256[] memory) {
        for (uint256 i = 0; i < params.length; i++) {
            // Calls executeTWAMMOrders
            sync(params[i]);
        }

        return claimTokensByCurrencies(currencies);
    }

    /// @inheritdoc ITWAMM
    function sync(
        SyncParams memory params
    ) public returns (uint256 tokens0OwedDelta, uint256 tokens1OwedDelta) {
        if (params.orderKey.owner != msg.sender) {
            revert Unauthorized();
        }

        executeTWAMMOrders(params.key);

        (
            uint256 buyTokensOwed,
            uint256 sellTokensOwed,
            uint256 newEarningsFactorLast,
            bytes32 orderId,
            bool assetsRemoved
        ) = _sync(params.key, params.orderKey);

        if (params.orderKey.zeroForOne) {
            tokens0OwedDelta += sellTokensOwed;
            tokens1OwedDelta += buyTokensOwed;
        } else {
            tokens0OwedDelta += buyTokensOwed;
            tokens1OwedDelta += sellTokensOwed;
        }

        tokensOwed[params.key.currency0][
            params.orderKey.owner
        ] += tokens0OwedDelta;
        tokensOwed[params.key.currency1][
            params.orderKey.owner
        ] += tokens1OwedDelta;

        emit SyncOrder(
            params.key.toId(),
            orderId,
            assetsRemoved, // Only true if the hook has been killed
            tokens0OwedDelta,
            tokens1OwedDelta,
            newEarningsFactorLast
        );
    }

    function _sync(
        PoolKey memory key,
        OrderKey memory orderKey
    )
        internal
        returns (
            uint256 buyTokensOwed,
            uint256 sellTokensOwed,
            uint256 earningsFactorLast,
            bytes32 orderId,
            bool assetsRemoved
        )
    {
        PoolId poolId = key.toId();
        TWAMMState storage twamm = twammStates[poolId];
        orderId = _orderId(orderKey);
        Order storage order = _getOrder(twamm, orderId);

        OrderPool.State storage orderPool = orderKey.zeroForOne
            ? twamm.orderPool0For1
            : twamm.orderPool1For0;
        bool isOrderExpired = orderKey.expiration <=
            twamm.lastVirtualOrderTimestamp;

        if (order.sellRate == 0) {
            revert OrderDoesNotExist(orderKey);
        }

        earningsFactorLast = isOrderExpired
            ? orderPool.earningsFactorAtInterval[orderKey.expiration]
            : orderPool.earningsFactorCurrent;

        buyTokensOwed =
            (
                Math.mulDiv(
                    earningsFactorLast - order.earningsFactorLast,
                    order.sellRate,
                    RATE_SCALER
                )
            ) >>
            FixedPoint96.RESOLUTION;

        if (isOrderExpired) {
            delete twamm.orders[orderId];
        } else {
            order.earningsFactorLast = earningsFactorLast;
        }
    }

    function _claimTokens(
        Currency token
    ) internal returns (uint256 amountTransferred) {
        amountTransferred = tokensOwed[token][msg.sender];

        if (amountTransferred != 0) {
            uint256 currentBalance = token.balanceOfSelf();

            if (currentBalance < amountTransferred) {
                amountTransferred = currentBalance; // offByOne
            }

            tokensOwed[token][msg.sender] -= amountTransferred;

            token.transfer(msg.sender, amountTransferred);

            emit ClaimTokens(token, msg.sender, amountTransferred);
        }
    }

    function unlockCallback(
        bytes calldata data
    ) external returns (bytes memory) {
        return _unlockCallback(data);
    }

    function _unlockCallback(
        bytes calldata rawData
    ) internal returns (bytes memory) {
        (PoolKey memory key, SwapParams memory swapParams) = abi.decode(
            rawData,
            (PoolKey, SwapParams)
        );

        _processSwap(key, swapParams);

        return ZERO_BYTES;
    }

    function _processSwap(
        PoolKey memory key,
        SwapParams memory swapParams
    ) internal {
        BalanceDelta delta = poolManager.swap(key, swapParams, ZERO_BYTES);

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

        emit SwapExecuted(key.toId(), delta);
    }

    function _getTWAMM(
        PoolKey memory key
    ) internal view returns (TWAMMState storage) {
        return twammStates[key.toId()];
    }

    struct PoolParamsOnExecute {
        uint160 sqrtPriceX96;
        uint24 protocolFee;
        uint24 lpFee;
        uint128 liquidity;
        uint256 maxSwap0For1;
        uint256 maxSwap1For0;
    }

    /// @notice Executes all existing long term orders in the TWAMM
    /// @param pool The relevant state of the pool
    function _executeTWAMMOrders(
        TWAMMState storage self,
        PoolKey memory key,
        PoolParamsOnExecute memory pool,
        uint256 targetTimestamp
    )
        internal
        returns (
            bool zeroForOne,
            uint160 newSqrtPriceX96,
            uint256 maxSwapAmount
        )
    {
        uint256 currentTimestampAtInterval = _getIntervalTime(targetTimestamp);

        if (
            currentTimestampAtInterval > block.timestamp ||
            currentTimestampAtInterval < self.lastVirtualOrderTimestamp
        ) {
            revert InvalidTargetTimestamp();
        }

        if (!_hasOutstandingOrders(self)) {
            self.lastVirtualOrderTimestamp = currentTimestampAtInterval;

            return (false, 0, 0);
        }

        uint160 initialSqrtPriceX96 = pool.sqrtPriceX96;
        uint256 prevTimestamp = self.lastVirtualOrderTimestamp;
        uint256 nextExpirationTimestamp = prevTimestamp + expirationInterval;

        while (nextExpirationTimestamp <= currentTimestampAtInterval) {
            if (
                _hasOutstandingOrdersAtInterval(self, nextExpirationTimestamp)
            ) {
                pool = _advanceTimestampForSinglePoolSell(
                    self,
                    key,
                    AdvanceSingleParams(
                        expirationInterval,
                        nextExpirationTimestamp,
                        nextExpirationTimestamp - prevTimestamp,
                        pool,
                        false,
                        0
                    )
                );

                // Finalize interval accounting and expire orders at this boundary
                self.orderPool0For1.advanceToInterval(
                    nextExpirationTimestamp,
                    0
                );
                self.orderPool1For0.advanceToInterval(
                    nextExpirationTimestamp,
                    0
                );

                prevTimestamp = nextExpirationTimestamp;
            }

            nextExpirationTimestamp += expirationInterval;

            if (!_hasOutstandingOrders(self)) {
                break;
            }
        }

        if (
            prevTimestamp < currentTimestampAtInterval &&
            _hasOutstandingOrders(self)
        ) {
            pool = _advanceTimestampForSinglePoolSell(
                self,
                key,
                AdvanceSingleParams(
                    expirationInterval,
                    currentTimestampAtInterval,
                    currentTimestampAtInterval - prevTimestamp,
                    pool,
                    false,
                    0
                )
            );
            // Finalize interval accounting for the current boundary
            self.orderPool0For1.advanceToInterval(
                currentTimestampAtInterval,
                0
            );
            self.orderPool1For0.advanceToInterval(
                currentTimestampAtInterval,
                0
            );
        }

        self.lastVirtualOrderTimestamp = currentTimestampAtInterval;
        newSqrtPriceX96 = pool.sqrtPriceX96;
        zeroForOne = initialSqrtPriceX96 > newSqrtPriceX96;

        // Only one of them would be active at a time
        maxSwapAmount = zeroForOne ? pool.maxSwap0For1 : pool.maxSwap1For0;
    }

    struct AdvanceParams {
        uint256 expirationInterval;
        uint256 nextTimestamp;
        uint256 secondsElapsed;
        PoolParamsOnExecute pool;
    }

    function _exhaustMatchedOrders(
        TWAMMState storage self,
        AdvanceParams memory params
    ) private returns (bool remainingZeroForOne) {
        uint256 priceX96 = Math.mulDiv(
            params.pool.sqrtPriceX96,
            params.pool.sqrtPriceX96,
            FixedPoint96.Q96
        );

        uint256 sellRate0To1 = self.orderPool0For1.sellRateCurrent;
        uint256 sellRate1To0 = self.orderPool1For0.sellRateCurrent;

        uint256 sellRate0To1As1 = Math.mulDiv(
            sellRate0To1,
            priceX96,
            FixedPoint96.Q96
        );
        uint256 sellRate1To0As0 = Math.mulDiv(
            sellRate1To0,
            FixedPoint96.Q96,
            priceX96
        );

        // Need to figure out how much sell rate we can adjust between the two of them.
        uint256 maxAdjustable0To1 = Math.min(sellRate0To1, sellRate1To0As0);
        uint256 maxAdjustable1To0 = Math.min(sellRate1To0, sellRate0To1As1);

        if (maxAdjustable0To1 != 0 && maxAdjustable1To0 != 0) {
            sellRate0To1As1 = Math.mulDiv(
                maxAdjustable0To1,
                priceX96,
                FixedPoint96.Q96
            );
            sellRate1To0As0 = Math.mulDiv(
                maxAdjustable1To0,
                FixedPoint96.Q96,
                priceX96
            );

            self.orderPool0For1.advanceWithoutCommit(
                Math.mulDiv(
                    sellRate0To1As1 * params.secondsElapsed,
                    FixedPoint96.Q96,
                    sellRate0To1
                ), // Earnings
                maxAdjustable0To1
            );
            self.orderPool1For0.advanceWithoutCommit(
                Math.mulDiv(
                    sellRate1To0As0 * params.secondsElapsed,
                    FixedPoint96.Q96,
                    sellRate1To0
                ), // Earnings
                maxAdjustable1To0
            );

            return sellRate0To1 - maxAdjustable0To1 != 0;
        }

        return sellRate0To1 > sellRate1To0As0;
    }

    struct AdvanceSingleParams {
        uint256 expirationInterval;
        uint256 nextTimestamp;
        uint256 secondsElapsed;
        PoolParamsOnExecute pool;
        bool zeroForOne;
        uint256 activeFee;
    }

    /// @notice The trading fee in hundredths of a bip (e.g. 3000 = 0.3%)
    uint24 public tradingFee;

    function setTradingFee(uint24 _fee) external onlyOwner {
        tradingFee = _fee;
    }

    function _advanceTimestampForSinglePoolSell(
        TWAMMState storage self,
        PoolKey memory poolKey,
        AdvanceSingleParams memory params
    ) private returns (PoolParamsOnExecute memory) {
        // Including zeroForOne & activeFee in the params because stack-too-deep
        params.zeroForOne = _exhaustMatchedOrders(
            self,
            AdvanceParams(
                expirationInterval,
                params.nextTimestamp,
                params.secondsElapsed,
                params.pool
            )
        );

        // If tradingFee is set, use it. Otherwise fall back to protocol/LP fee logic?
        // User request said: "only thing that the contract owner will be able to change is the trading fee of the twamm pool. we will make it static."
        // This implies the fee used for the swap simulation should be this static fee if set, or maybe ALWAYS this static fee.
        // Let's assume valid fee overrides everything.

        uint24 fee = tradingFee;

        // If tradingFee is 0, should we fall back? The request says "make it static".
        // Let's assume if it is set (non-zero? or just use it), it's the fee.
        // Actually typically "static" means it doesn't change based on volatility etc, but here it likely means "fixed value set by owner".
        // Use tradingFee directly.

        params.activeFee = fee;

        OrderPool.State storage orderPool = params.zeroForOne
            ? self.orderPool0For1
            : self.orderPool1For0;
        uint256 sellRateCurrent = orderPool.sellRateCurrent -
            orderPool.sellRateAccounted;

        uint256 amountSelling = Math.mulDiv(
            Math.mulDiv(sellRateCurrent, params.secondsElapsed, RATE_SCALER),
            SwapMath.MAX_SWAP_FEE - params.activeFee,
            SwapMath.MAX_SWAP_FEE
        );
        uint256 totalEarnings;

        while (true) {
            uint160 finalSqrtPriceX96 = SqrtPriceMath.getNextSqrtPriceFromInput(
                params.pool.sqrtPriceX96,
                params.pool.liquidity,
                amountSelling,
                params.zeroForOne
            );

            (
                bool crossingInitializedTick,
                int24 tick
            ) = _isCrossingInitializedTick(
                    params.pool,
                    poolKey,
                    finalSqrtPriceX96
                );

            if (crossingInitializedTick) {
                (, int128 liquidityNetAtTick) = poolManager.getTickLiquidity(
                    poolKey.toId(),
                    tick
                );
                uint160 initializedSqrtPrice = TickMath.getSqrtPriceAtTick(
                    tick
                );

                uint256 swapDelta0 = SqrtPriceMath.getAmount0Delta(
                    params.pool.sqrtPriceX96,
                    initializedSqrtPrice,
                    params.pool.liquidity,
                    true
                );
                uint256 swapDelta1 = SqrtPriceMath.getAmount1Delta(
                    params.pool.sqrtPriceX96,
                    initializedSqrtPrice,
                    params.pool.liquidity,
                    true
                );

                params.pool.sqrtPriceX96 = initializedSqrtPrice;

                unchecked {
                    totalEarnings += params.zeroForOne
                        ? swapDelta1
                        : swapDelta0;
                    amountSelling -= params.zeroForOne
                        ? swapDelta0
                        : swapDelta1;
                }

                unchecked {
                    if (params.zeroForOne)
                        liquidityNetAtTick = -liquidityNetAtTick;
                }
                params.pool.liquidity = LiquidityMath.addDelta(
                    params.pool.liquidity,
                    liquidityNetAtTick
                );

                unchecked {
                    params.pool.sqrtPriceX96 = params.zeroForOne
                        ? initializedSqrtPrice - 1
                        : initializedSqrtPrice;
                }
                continue;
            }

            // Calculate the final segment's earnings (from last tick to final price)
            uint256 swapDelta0 = SqrtPriceMath.getAmount0Delta(
                params.pool.sqrtPriceX96,
                finalSqrtPriceX96,
                params.pool.liquidity,
                true
            );
            uint256 swapDelta1 = SqrtPriceMath.getAmount1Delta(
                params.pool.sqrtPriceX96,
                finalSqrtPriceX96,
                params.pool.liquidity,
                true
            );

            params.pool.sqrtPriceX96 = finalSqrtPriceX96;
            unchecked {
                totalEarnings += params.zeroForOne ? swapDelta1 : swapDelta0;
            }

            // Accumulate the unmatched sell amount so executeTWAMMOrders
            // fires the real pool swap (via _processSwap) to move tokens
            // into the hook. Without this, earningsFactor is credited but
            // the hook holds no buy tokens → claimTokens returns 0.
            if (params.zeroForOne) {
                params.pool.maxSwap0For1 += amountSelling;
            } else {
                params.pool.maxSwap1For0 += amountSelling;
            }

            break;
        }

        // Convert raw token earnings to earnings factor with proper scaling
        uint256 earningsFactor = Math.mulDiv(
            totalEarnings,
            FixedPoint96.Q96 * RATE_SCALER,
            orderPool.sellRateCurrent
        );

        // Commit with properly scaled values (earningsFactor and sellRateCurrent used)
        orderPool.commit(earningsFactor, sellRateCurrent);

        return params.pool;
    }

    function _isCrossingInitializedTick(
        PoolParamsOnExecute memory pool,
        PoolKey memory poolKey,
        uint160 nextSqrtPriceX96
    ) internal view returns (bool crossingInitializedTick, int24 nextTickInit) {
        // use current price as a starting point for nextTickInit
        nextTickInit = pool.sqrtPriceX96.getTickAtSqrtPrice();
        int24 targetTick = nextSqrtPriceX96.getTickAtSqrtPrice();
        bool searchingLeft = nextSqrtPriceX96 < pool.sqrtPriceX96;
        bool nextTickInitFurtherThanTarget; // initialize as false

        // nextTickInit returns the furthest tick within one word if no tick within that word is initialized
        // so we must keep iterating if we haven't reached a tick further than our target tick
        while (!nextTickInitFurtherThanTarget) {
            unchecked {
                if (searchingLeft) {
                    nextTickInit -= 1;
                }
            }
            (nextTickInit, crossingInitializedTick) = poolManager
                .getNextInitializedTickWithinOneWord(
                    poolKey.toId(),
                    nextTickInit,
                    poolKey.tickSpacing,
                    searchingLeft
                );
            nextTickInitFurtherThanTarget = searchingLeft
                ? nextTickInit <= targetTick
                : nextTickInit > targetTick;
            if (crossingInitializedTick == true) {
                break;
            }
        }

        if (nextTickInitFurtherThanTarget) {
            crossingInitializedTick = false;
        }
    }

    function _getOrder(
        TWAMMState storage self,
        bytes32 orderId
    ) internal view returns (Order storage) {
        return self.orders[orderId];
    }

    function _orderId(OrderKey memory key) internal pure returns (bytes32) {
        return keccak256(abi.encode(key));
    }

    function _hasOutstandingOrders(
        TWAMMState storage self
    ) internal view returns (bool) {
        return
            self.orderPool0For1.sellRateCurrent != 0 ||
            self.orderPool1For0.sellRateCurrent != 0;
    }

    function _hasOutstandingOrdersAtInterval(
        TWAMMState storage self,
        uint256 timestamp
    ) internal view returns (bool) {
        return
            self.orderPool0For1.sellRateEndingAtInterval[timestamp] != 0 ||
            self.orderPool1For0.sellRateEndingAtInterval[timestamp] != 0;
    }

    function _getIntervalTime(
        uint256 timestamp
    ) internal view returns (uint256) {
        return timestamp - (timestamp % expirationInterval);
    }

    function _initializeOracle(PoolKey calldata key) internal {
        PoolId poolId = key.toId();
        observations[poolId].initialize(
            oracleStates[poolId],
            uint32(block.timestamp)
        );
    }

    function _updateOracle(PoolKey memory key) internal {
        PoolId poolId = key.toId();
        (, int24 tick, , ) = poolManager.getSlot0(poolId);
        observations[poolId].write(
            oracleStates[poolId],
            uint32(block.timestamp),
            tick
        );
    }

    /// @inheritdoc ITWAMM
    function observe(
        PoolId poolId,
        uint32[] calldata secondsAgos
    ) external view returns (int56[] memory tickCumulatives) {
        return
            observations[poolId].observe(
                oracleStates[poolId],
                uint32(block.timestamp),
                secondsAgos,
                _getTick(poolId)
            );
    }

    /// @inheritdoc ITWAMM
    function increaseCardinality(
        PoolId poolId,
        uint16 next
    ) external returns (uint16 cardinalityNext) {
        return observations[poolId].grow(oracleStates[poolId], next);
    }

    function _getTick(PoolId poolId) internal view returns (int24 tick) {
        (, tick, , ) = poolManager.getSlot0(poolId);
    }
}
