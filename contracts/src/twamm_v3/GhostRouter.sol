// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {
    SafeERC20
} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Owned} from "solmate/src/auth/Owned.sol";

import {IGhostRouter} from "./interfaces/IGhostRouter.sol";
import {IGhostEngine} from "./interfaces/IGhostEngine.sol";
import {IGhostOracle} from "./interfaces/IGhostOracle.sol";

import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";

/// @title Ghost Router (Sovereign Clearing Hub)
/// @notice The centralized vault and routing engine for Ghost intent-based liquidity.
///         Completely bypasses Uniswap V4 Hook architecture for a sovereign model.
///         Executes the 3-Layer matching trap natively.
contract GhostRouter is IGhostRouter, ReentrancyGuard, Owned {
    using SafeERC20 for IERC20;
    using CurrencySettler for Currency;
    using PoolIdLibrary for PoolKey;

    uint256 public constant PRICE_SCALE = 1e18;

    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    struct Market {
        address token0;
        address token1;
        address oracle;
        PoolKey vanillaKey;
    }

    /// @notice Registry of Sovereign Markets
    mapping(bytes32 => Market) public markets;

    /// @notice Registry of approved engines allowed to command the vault
    mapping(address => bool) public isEngine;

    /// @notice The global Uniswap V4 PoolManager
    IPoolManager public immutable poolManager;

    // ─── Custom Errors ────────────────────────────────────────────────────────

    error UnauthorizedEngine();
    error EngineAlreadyRegistered();
    error UnauthorizedCallback();
    error MarketNotFound();
    error InvalidOracle();
    error InvalidCurrencyOrder();
    error MustBeHookless();
    error SlippageExceeded();

    // ─── Modifiers ────────────────────────────────────────────────────────────

    modifier onlyEngine() {
        if (!isEngine[msg.sender]) revert UnauthorizedEngine();
        _;
    }

    constructor(address _poolManager, address _owner) Owned(_owner) {
        poolManager = IPoolManager(_poolManager);
    }

    /// @notice The internal list of registered engines for Native routing iteration
    address[] public approvedEngines;

    /// @notice Register a new Sovereign Engine (Spoke)
    function registerEngine(address engine) external onlyOwner {
        if (isEngine[engine]) revert EngineAlreadyRegistered();
        isEngine[engine] = true;
        approvedEngines.push(engine);
    }

    // ─── MARKET REGISTRY ──────────────────────────────────────────────────────

    /// @inheritdoc IGhostRouter
    function initializeMarket(
        PoolKey calldata vanillaKey,
        address _oracle
    ) external override onlyOwner returns (bytes32 marketId) {
        if (address(vanillaKey.hooks) != address(0)) revert MustBeHookless();
        if (_oracle == address(0)) revert InvalidOracle();

        address token0 = Currency.unwrap(vanillaKey.currency0);
        address token1 = Currency.unwrap(vanillaKey.currency1);

        if (token0 >= token1) revert InvalidCurrencyOrder();

        marketId = PoolId.unwrap(vanillaKey.toId());
        markets[marketId] = Market({
            token0: token0,
            token1: token1,
            oracle: _oracle,
            vanillaKey: vanillaKey
        });
    }

    // ─── VAULT MECHANICS (Auth by Engines) ────────────────────────────────────

    /// @inheritdoc IGhostRouter
    function pushMarketFunds(
        bytes32 marketId,
        bool zeroForOne,
        address to,
        uint256 amount
    ) external override onlyEngine {
        Market storage market = markets[marketId];
        address token = zeroForOne ? market.token1 : market.token0;
        IERC20(token).safeTransfer(to, amount);
    }

    /// @inheritdoc IGhostRouter
    function pullMarketFunds(
        bytes32 marketId,
        bool zeroForOne,
        address from,
        uint256 amount
    ) external override onlyEngine {
        Market storage market = markets[marketId];
        address token = zeroForOne ? market.token0 : market.token1;
        IERC20(token).safeTransferFrom(from, address(this), amount);
    }

    // ─── TAKER ROUTING ────────────────────────────────────────────────────────

    /// @inheritdoc IGhostRouter
    function swap(
        bytes32 marketId,
        bool zeroForOne,
        uint256 amountIn,
        uint256 amountOutMinimum
    ) external override nonReentrant returns (uint256 amountOut) {
        Market storage market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();

        address tokenIn = zeroForOne ? market.token0 : market.token1;
        address tokenOut = zeroForOne ? market.token1 : market.token0;

        // 1. Pull Taker Tokens into Vault
        IERC20(tokenIn).safeTransferFrom(msg.sender, address(this), amountIn);

        // 2. Fetch Oracle Spot Price (Token1 per Token0, scaled by 1e18)
        uint256 spotPrice = IGhostOracle(market.oracle).getSpotPrice(marketId);

        // 3. [Layer 1] Global Ghost Netting — cross all engines at oracle price
        _executeGlobalNetting(marketId, spotPrice);

        // 4. [Layer 2] Taker Intercept — fill from remaining ghost
        uint256 remainingIn = amountIn;
        (uint256 ghostFilled, uint256 inputUsed) = _takerIntercept(marketId, zeroForOne, remainingIn, spotPrice);
        amountOut += ghostFilled;
        remainingIn -= inputUsed;

        // 5. [Layer 3] V4 AMM Fallback — route remainder through Uniswap
        if (remainingIn > 0) {
            amountOut += _executeVanillaV4Swap(market.vanillaKey, zeroForOne, remainingIn, tokenIn);
        }

        if (amountOut < amountOutMinimum) revert SlippageExceeded();

        // 6. Deliver output to Taker
        if (amountOut > 0) {
            IERC20(tokenOut).safeTransfer(msg.sender, amountOut);
        }
    }

    /// @inheritdoc IGhostRouter
    function settleGhost(bytes32 marketId, bool zeroForOne, uint256 amountIn) external override onlyEngine returns (uint256 amountOut) {
        Market storage market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();
        address tokenIn = zeroForOne ? market.token0 : market.token1;
        amountOut = _executeVanillaV4Swap(market.vanillaKey, zeroForOne, amountIn, tokenIn);
    }

    // ─── LAYER 1: GLOBAL GHOST NETTING ────────────────────────────────────────

    /// @notice Aggregate ghost balances from all engines, compute price-weighted
    ///         macro intersection, and distribute settlement pro-rata.
    function _executeGlobalNetting(bytes32 marketId, uint256 spotPrice) internal {
        (
            uint256[] memory engineG0s,
            uint256[] memory engineG1s,
            uint256 totalG0,
            uint256 totalG1
        ) = _aggregateGhostBalances(marketId);

        if (totalG0 == 0 || totalG1 == 0) return;

        (uint256 macroToken0, uint256 macroToken1) = _computeMacroIntersection(
            totalG0, totalG1, spotPrice
        );

        if (macroToken0 == 0 && macroToken1 == 0) return;

        _distributeNettingProRata(
            marketId, spotPrice,
            engineG0s, engineG1s,
            totalG0, totalG1,
            macroToken0, macroToken1
        );
    }

    /// @notice Poll all engines to sync state and collect ghost balances.
    function _aggregateGhostBalances(bytes32 marketId)
        internal
        returns (
            uint256[] memory engineG0s,
            uint256[] memory engineG1s,
            uint256 totalG0,
            uint256 totalG1
        )
    {
        uint256 numEngines = approvedEngines.length;
        engineG0s = new uint256[](numEngines);
        engineG1s = new uint256[](numEngines);

        for (uint256 i = 0; i < numEngines; ++i) {
            (uint256 g0, uint256 g1) = IGhostEngine(approvedEngines[i]).syncAndFetchGhost(marketId);
            engineG0s[i] = g0;
            engineG1s[i] = g1;
            totalG0 += g0;
            totalG1 += g1;
        }
    }

    /// @notice Compute the price-weighted overlap between opposing ghost flows.
    function _computeMacroIntersection(
        uint256 totalG0,
        uint256 totalG1,
        uint256 spotPrice
    ) internal pure returns (uint256 macroToken0, uint256 macroToken1) {
        uint256 totalG0InToken1 = (totalG0 * spotPrice) / PRICE_SCALE;

        if (totalG0InToken1 <= totalG1) {
            macroToken0 = totalG0;
            macroToken1 = totalG0InToken1;
        } else {
            macroToken1 = totalG1;
            macroToken0 = (totalG1 * PRICE_SCALE) / spotPrice;
        }
    }

    /// @notice Distribute the macro intersection pro-rata across engines
    ///         using the cumulative fraction pattern (zero-dust).
    function _distributeNettingProRata(
        bytes32 marketId,
        uint256 spotPrice,
        uint256[] memory engineG0s,
        uint256[] memory engineG1s,
        uint256 totalG0,
        uint256 totalG1,
        uint256 macroToken0,
        uint256 macroToken1
    ) internal {
        uint256 numEngines = approvedEngines.length;
        uint256 runFrac0;
        uint256 runFrac1;
        uint256 runMatch0;
        uint256 runMatch1;

        for (uint256 i = 0; i < numEngines; ++i) {
            uint256 consumed0;
            uint256 consumed1;

            if (totalG0 > 0 && macroToken0 > 0) {
                runFrac0 += engineG0s[i];
                uint256 expected = (macroToken0 * runFrac0) / totalG0;
                consumed0 = expected - runMatch0;
                runMatch0 += consumed0;
            }

            if (totalG1 > 0 && macroToken1 > 0) {
                runFrac1 += engineG1s[i];
                uint256 expected = (macroToken1 * runFrac1) / totalG1;
                consumed1 = expected - runMatch1;
                runMatch1 += consumed1;
            }

            if (consumed0 > 0 || consumed1 > 0) {
                IGhostEngine(approvedEngines[i]).applyNettingResult(marketId, consumed0, consumed1, spotPrice);
            }
        }
    }

    // ─── LAYER 2: TAKER INTERCEPT ─────────────────────────────────────────────

    /// @notice Route the Taker's input against remaining directional ghost.
    function _takerIntercept(
        bytes32 marketId,
        bool zeroForOne,
        uint256 amountIn,
        uint256 spotPrice
    ) internal returns (uint256 totalFilled, uint256 totalInput) {
        uint256 remaining = amountIn;

        for (uint256 i = 0; i < approvedEngines.length; ++i) {
            if (remaining == 0) break;

            (uint256 filledOut, uint256 inputConsumed) = IGhostEngine(approvedEngines[i]).takeGhost(
                marketId, zeroForOne, remaining, spotPrice
            );

            if (filledOut > 0) {
                totalFilled += filledOut;
                totalInput += inputConsumed;
                remaining -= inputConsumed;
            }
        }
    }

    // ─── LAYER 3: V4 AMM FALLBACK ─────────────────────────────────────────────

    function _executeVanillaV4Swap(
        PoolKey memory key,
        bool zeroForOne,
        uint256 amountIn,
        address tokenIn
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).approve(address(poolManager), amountIn);

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(amountIn),
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740
                : 1461446703485210103287273052203988822378723970341
        });

        BalanceDelta delta = abi.decode(
            poolManager.unlock(
                abi.encode(SwapCallback({sender: address(this), key: key, params: swapParams}))
            ),
            (BalanceDelta)
        );

        amountOut = zeroForOne
            ? uint256(int256(delta.amount1()))
            : uint256(int256(delta.amount0()));
    }

    // ─── V4 CALLBACK ──────────────────────────────────────────────────────────

    function unlockCallback(bytes calldata rawData) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert UnauthorizedCallback();

        SwapCallback memory data = abi.decode(rawData, (SwapCallback));
        BalanceDelta delta = poolManager.swap(data.key, data.params, new bytes(0));

        // Settle input (negative delta = we owe)
        _settleCurrency(data.key, delta, data.sender, true);
        // Take output (positive delta = we're owed)
        _settleCurrency(data.key, delta, data.sender, false);

        return abi.encode(delta);
    }

    /// @notice Direction-agnostic currency settlement for V4 callbacks.
    /// @param isSettle true = settle (pay what we owe), false = take (claim what we're owed)
    function _settleCurrency(
        PoolKey memory key,
        BalanceDelta delta,
        address sender,
        bool isSettle
    ) internal {
        int128 amount0 = delta.amount0();
        int128 amount1 = delta.amount1();

        if (isSettle) {
            if (amount0 < 0) key.currency0.settle(poolManager, sender, uint256(-int256(amount0)), false);
            if (amount1 < 0) key.currency1.settle(poolManager, sender, uint256(-int256(amount1)), false);
        } else {
            if (amount0 > 0) key.currency0.take(poolManager, sender, uint256(int256(amount0)), false);
            if (amount1 > 0) key.currency1.take(poolManager, sender, uint256(int256(amount1)), false);
        }
    }
}
