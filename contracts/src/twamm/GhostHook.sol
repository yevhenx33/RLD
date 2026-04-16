// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId} from "@uniswap/v4-core/src/types/PoolId.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {BeforeSwapDelta, toBeforeSwapDelta} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {IERC20Minimal} from "@uniswap/v4-core/src/interfaces/external/IERC20Minimal.sol";
import {GhostPool} from "./GhostPool.sol";
import {FillResult} from "./types/GhostTypes.sol";

/// @title GhostHook — Uniswap v4 Hook for the Ghost Matching Engine
/// @notice Intercepts swaps via beforeSwap to fill from ghost pool at TWAP.
///         Remaining unfilled amount passes through to the underlying AMM.
///
/// Token Flow:
///   1. Users deposit tokens on submitStream / submitLimit → tokens escrowed in hook
///   2. On swap, beforeSwap settles output tokens with PoolManager (sync → transfer → settle)
///   3. afterSwap takes input tokens from PoolManager → hook receives payment
///   4. Users claim earnings/refund → tokens transferred from hook to user
///
/// Hook Permissions:
///   - BEFORE_INITIALIZE:          Initialize ghost pool state + store currency addresses
///   - BEFORE_SWAP:                Run ghost fill pipeline + settle output
///   - BEFORE_SWAP_RETURNS_DELTA:  Custom accounting (return filled amount to taker)
///   - AFTER_SWAP:                 Take input tokens + record TWAP observation
///
/// @dev Deploy to an address whose lowest 14 bits encode the correct permission flags.
///      Use CREATE2 with appropriate salt for address mining.
contract GhostHook is GhostPool, IHooks {
    using StateLibrary for IPoolManager;

    IPoolManager public immutable poolManager;

    // Pool currencies — set once in beforeInitialize
    Currency internal _currency0;
    Currency internal _currency1;
    bool internal _poolInitialized;

    // Transient fill state between beforeSwap and afterSwap
    uint256 internal _pendingFillIn;
    bool internal _pendingFillZeroForOne;
    bool internal _hasPendingFill;

    error OnlyPoolManager();
    error PoolAlreadyInitialized();

    modifier onlyPoolManager() {
        if (msg.sender != address(poolManager)) revert OnlyPoolManager();
        _;
    }

    constructor(IPoolManager _poolManager) {
        poolManager = _poolManager;
    }

    /// @notice Returns the hook permissions for verification
    function getHookPermissions() public pure returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: true,
            afterInitialize: false,
            beforeAddLiquidity: false,
            afterAddLiquidity: false,
            beforeRemoveLiquidity: false,
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

    // ─── Hook Lifecycle ───────────────────────────────────────────────

    /// @notice Called when the pool is first initialized — stores currencies and inits ghost state
    function beforeInitialize(address, PoolKey calldata key, uint160 sqrtPriceX96)
        external
        override
        onlyPoolManager
        returns (bytes4)
    {
        if (_poolInitialized) revert PoolAlreadyInitialized();
        _currency0 = key.currency0;
        _currency1 = key.currency1;
        _poolInitialized = true;

        _initPool(
            300, // 5-minute epochs
            sqrtPriceX96,
            block.timestamp
        );
        return IHooks.beforeInitialize.selector;
    }

    function afterInitialize(address, PoolKey calldata, uint160, int24)
        external
        pure
        override
        returns (bytes4)
    {
        return IHooks.afterInitialize.selector;
    }

    /// @notice Ghost fill pipeline: ACCRUE → NET → CROSS → ACTIVATE → FILL → SETTLE
    function beforeSwap(address, PoolKey calldata, IPoolManager.SwapParams calldata params, bytes calldata)
        external
        override
        onlyPoolManager
        returns (bytes4, BeforeSwapDelta, uint24)
    {
        FillResult memory result = _executeFill(params.amountSpecified, params.zeroForOne);

        if (!result.filled) {
            return (IHooks.beforeSwap.selector, toBeforeSwapDelta(0, 0), 0);
        }

        // ── SETTLE OUTPUT: send fillOut tokens to PoolManager ──
        // For zeroForOne=false (buy token0): output is currency0
        // For zeroForOne=true  (buy token1): output is currency1
        Currency outputCurrency = params.zeroForOne ? _currency1 : _currency0;
        address outputToken = Currency.unwrap(outputCurrency);

        // sync → transfer → settle: credits hook's output delta
        poolManager.sync(outputCurrency);
        IERC20Minimal(outputToken).transfer(address(poolManager), result.fillOut);
        poolManager.settle();

        // ── STORE PENDING: afterSwap will take the input payment ──
        _pendingFillIn = result.fillIn;
        _pendingFillZeroForOne = params.zeroForOne;
        _hasPendingFill = true;

        // Return delta to PoolManager
        int128 deltaSpecified = int128(int256(result.fillIn));
        int128 deltaUnspecified = -int128(int256(result.fillOut));

        return (IHooks.beforeSwap.selector, toBeforeSwapDelta(deltaSpecified, deltaUnspecified), 0);
    }

    /// @notice Take input payment from PoolManager + record TWAP observation
    function afterSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata, BalanceDelta, bytes calldata)
        external
        override
        onlyPoolManager
        returns (bytes4, int128)
    {
        // ── TAKE INPUT: receive fillIn tokens from PoolManager ──
        if (_hasPendingFill) {
            Currency inputCurrency = _pendingFillZeroForOne ? _currency0 : _currency1;
            poolManager.take(inputCurrency, address(this), _pendingFillIn);

            // Clear transient state
            _hasPendingFill = false;
            _pendingFillIn = 0;
        }

        // Update TWAP from pool's current sqrtPrice
        (uint160 sqrtPriceX96,,,) = poolManager.getSlot0(key.toId());
        poolState.twap = sqrtPriceX96;

        return (IHooks.afterSwap.selector, 0);
    }

    // ─── Liquidity Hooks (no-op) ──────────────────────────────────────

    function beforeAddLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        return IHooks.beforeAddLiquidity.selector;
    }

    function afterAddLiquidity(
        address,
        PoolKey calldata,
        IPoolManager.ModifyLiquidityParams calldata,
        BalanceDelta delta,
        BalanceDelta,
        bytes calldata
    ) external pure override returns (bytes4, BalanceDelta) {
        return (IHooks.afterAddLiquidity.selector, delta);
    }

    function beforeRemoveLiquidity(
        address,
        PoolKey calldata,
        IPoolManager.ModifyLiquidityParams calldata,
        bytes calldata
    ) external pure override returns (bytes4) {
        return IHooks.beforeRemoveLiquidity.selector;
    }

    function afterRemoveLiquidity(
        address,
        PoolKey calldata,
        IPoolManager.ModifyLiquidityParams calldata,
        BalanceDelta delta,
        BalanceDelta,
        bytes calldata
    ) external pure override returns (bytes4, BalanceDelta) {
        return (IHooks.afterRemoveLiquidity.selector, delta);
    }

    function beforeDonate(address, PoolKey calldata, uint256, uint256, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        return IHooks.beforeDonate.selector;
    }

    function afterDonate(address, PoolKey calldata, uint256, uint256, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        return IHooks.afterDonate.selector;
    }

    // ─── User-Facing Functions ────────────────────────────────────────

    /// @notice Submit a TWAP stream order — escrows tokens in hook
    function submitStream(uint256 amountIn, uint256 duration, bool zeroForOne) external returns (bytes32 orderId) {
        // Escrow: transfer deposit token from user to hook
        Currency depositCurrency = zeroForOne ? _currency0 : _currency1;
        IERC20Minimal(Currency.unwrap(depositCurrency)).transferFrom(msg.sender, address(this), amountIn);

        return _submitStream(msg.sender, amountIn, duration, zeroForOne);
    }

    /// @notice Claim earnings from a stream order — transfers earned tokens to user
    function claimStream(bytes32 orderId) external returns (uint256 earnings) {
        require(streamOrders[orderId].owner == msg.sender, "Not owner");
        bool z41 = streamOrders[orderId].zeroForOne;
        earnings = _claimStream(orderId);

        if (earnings > 0) {
            // Earnings are in the opposite currency (sold T0 → earned T1, or vice versa)
            Currency earningsCurrency = z41 ? _currency1 : _currency0;
            IERC20Minimal(Currency.unwrap(earningsCurrency)).transfer(msg.sender, earnings);
        }
    }

    /// @notice Cancel a stream order — refunds remaining deposit to user
    function cancelStream(bytes32 orderId) external returns (uint256 refund) {
        require(streamOrders[orderId].owner == msg.sender, "Not owner");
        bool z41 = streamOrders[orderId].zeroForOne;
        refund = _cancelStream(orderId);

        if (refund > 0) {
            // Refund is in the deposit currency
            Currency refundCurrency = z41 ? _currency0 : _currency1;
            IERC20Minimal(Currency.unwrap(refundCurrency)).transfer(msg.sender, refund);
        }
    }

    /// @notice Submit a sell limit order — escrows token0
    function submitSellLimit(uint256 amount, int24 tick) external {
        IERC20Minimal(Currency.unwrap(_currency0)).transferFrom(msg.sender, address(this), amount);
        _submitSellLimit(msg.sender, amount, tick);
    }

    /// @notice Submit a buy limit order — escrows token1
    function submitBuyLimit(uint256 amount, int24 tick) external {
        IERC20Minimal(Currency.unwrap(_currency1)).transferFrom(msg.sender, address(this), amount);
        _submitBuyLimit(msg.sender, amount, tick);
    }

    /// @notice Claim a limit order — transfers proceeds + refund to user
    function claimLimit(int24 tick, bool isSell) external returns (uint256 proceeds, uint256 refund) {
        (proceeds, refund) = _claimLimit(msg.sender, tick, isSell);

        if (proceeds > 0) {
            // Proceeds are in the opposite currency
            Currency proceedsCurrency = isSell ? _currency1 : _currency0;
            IERC20Minimal(Currency.unwrap(proceedsCurrency)).transfer(msg.sender, proceeds);
        }
        if (refund > 0) {
            // Refund is in the deposit currency
            Currency refundCurrency = isSell ? _currency0 : _currency1;
            IERC20Minimal(Currency.unwrap(refundCurrency)).transfer(msg.sender, refund);
        }
    }
}
