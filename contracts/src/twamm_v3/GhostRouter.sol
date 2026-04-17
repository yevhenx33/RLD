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
import {ITwapEngine} from "./interfaces/ITwapEngine.sol";
import {IGhostOracle} from "./interfaces/IGhostOracle.sol";

import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
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

    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    struct Market {
        address token0;
        address token1;
        PoolKey vanillaKey;
    }

    /// @notice Registry of Sovereign Markets cached against pure Hookless Vanilla config
    mapping(bytes32 => Market) public markets;

    /// @notice Registry of approved engines allowed to command the vault
    mapping(address => bool) public isEngine;

    /// @notice The external Oracle tracking spot prices
    address public immutable oracle;

    /// @notice The global Uniswap V4 PoolManager
    IPoolManager public immutable poolManager;

    error UnauthorizedEngine();
    error EngineAlreadyRegistered();
    error UnauthorizedCallback();
    error MarketNotFound();

    modifier onlyEngine() {
        if (!isEngine[msg.sender]) {
            revert UnauthorizedEngine();
        }
        _;
    }

    constructor(address _oracle, address _poolManager, address _owner) Owned(_owner) {
        oracle = _oracle;
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
        PoolKey calldata vanillaKey
    ) external override returns (bytes32 marketId) {
        if (address(vanillaKey.hooks) != address(0)) revert("Must be hookless");
        
        address token0 = Currency.unwrap(vanillaKey.currency0);
        address token1 = Currency.unwrap(vanillaKey.currency1);
        
        if (token0 >= token1) revert("Invalid currency ordering");

        marketId = PoolId.unwrap(vanillaKey.toId());
        markets[marketId] = Market({
            token0: token0,
            token1: token1,
            vanillaKey: vanillaKey
        });
    }

    // ─── VAULT MECHANICS (Auth by Engines) ────────────────────────────────────

    /// @inheritdoc IGhostRouter
    /// @dev Called strictly by Spokes (Engines) to command payouts to users
    function pushMarketFunds(
        bytes32 marketId,
        bool zeroForOne,
        address to,
        uint256 amount
    ) external override onlyEngine {
        Market memory market = markets[marketId];
        address token = zeroForOne ? market.token1 : market.token0;
        IERC20(token).safeTransfer(to, amount);
    }

    /// @inheritdoc IGhostRouter
    /// @dev Called strictly by Spokes (Engines) when users deposit
    function pullMarketFunds(
        bytes32 marketId,
        bool zeroForOne,
        address from,
        uint256 amount
    ) external override onlyEngine {
        // If zeroForOne = true, User is supplying Token0
        Market memory market = markets[marketId];
        address token = zeroForOne ? market.token0 : market.token1;
        IERC20(token).safeTransferFrom(from, address(this), amount);
    }

    // ─── TAKER ROUTING (Layer 1 & 2) ──────────────────────────────────────────

    /// @inheritdoc IGhostRouter
    function swap(
        bytes32 marketId,
        bool zeroForOne,
        uint256 amountIn,
        uint256 amountOutMinimum
    ) external override nonReentrant returns (uint256 amountOut) {
        Market memory market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();
        
        PoolKey memory key = market.vanillaKey;
        address tokenIn = zeroForOne ? market.token0 : market.token1;
        address tokenOut = zeroForOne ? market.token1 : market.token0;

        // 1. Pull Taker Tokens
        IERC20(tokenIn).safeTransferFrom(msg.sender, address(this), amountIn);

        // 2. Fetch Spot Price safely
        uint256 spotPrice = IGhostOracle(oracle).getSpotPrice(key);

        uint256 remainingIn = amountIn;
        uint256 internalOut = 0;

        // 3. [Layer 1] Internal Netting against specialized Sovereign Engines
        for (uint256 i = 0; i < approvedEngines.length; ++i) {
            address engine = approvedEngines[i];
            
            if (remainingIn == 0) break;

            uint256 engineFilledOut = ITwapEngine(engine).requestNetting(
                marketId,
                zeroForOne,
                remainingIn,
                spotPrice
            );

            if (engineFilledOut > 0) {
                // Convert output matched to input equivalent to deduct remainder
                // This assumes `requestNetting` returned the exact amountOut filled.
                // Depending on the price formula representation (Q64.96), adjust `remainingIn`.
                // Note: We leave exact formula scaling to PrimeBroker phase.

                // For scaffolding, we deduct an approximated `inputMatched`:
                // uint256 inputMatched = (engineFilledOut * something) / something;
                // remainingIn -= inputMatched;
                internalOut += engineFilledOut;
            }
        }

        amountOut += internalOut;

        // 4. [Layer 2] External Netting (Fallback routing via V4)
        if (remainingIn > 0) {
            uint256 fallbackOut = _executeVanillaV4Swap(key, zeroForOne, remainingIn, tokenIn);
            amountOut += fallbackOut;
        }

        if (amountOut < amountOutMinimum) revert("Slippage Exceeded");

        // 5. Deliver
        if (amountOut > 0) {
            IERC20(tokenOut).safeTransfer(msg.sender, amountOut);
        }
    }

    /// @inheritdoc IGhostRouter
    function settleGhost(bytes32 marketId, bool zeroForOne, uint256 amountIn) external override onlyEngine returns (uint256 amountOut) {
        Market memory market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();
        
        PoolKey memory key = market.vanillaKey;
        address tokenIn = zeroForOne ? market.token0 : market.token1;

        // Execute natively through V4 using Escrow's own accumulated balances
        amountOut = _executeVanillaV4Swap(key, zeroForOne, amountIn, tokenIn);
    }

    // ─── INTERNAL HELPERS ───────────────────────────────────────────────────

    function _executeVanillaV4Swap(
        PoolKey memory key,
        bool zeroForOne,
        uint256 amountIn,
        address tokenIn
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).approve(address(poolManager), amountIn);

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(amountIn), // Exact Input
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740
                : 1461446703485210103287273052203988822378723970341
        });

        BalanceDelta delta = abi.decode(
            poolManager.unlock(
                abi.encode(
                    SwapCallback({
                        sender: address(this),
                        key: key,
                        params: swapParams
                    })
                )
            ),
            (BalanceDelta)
        );

        // Delta is negative for tokens we pull (input), positive for tokens we push (output)
        amountOut = zeroForOne
            ? uint256(int256(delta.amount1()))
            : uint256(int256(delta.amount0()));
    }

    /* ============================================================================================ */
    /*                                     V4 CALLBACK                                              */
    /* ============================================================================================ */

    /// @notice Uniswap V4 swap settlement fallback
    function unlockCallback(
        bytes calldata rawData
    ) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert UnauthorizedCallback();
        
        SwapCallback memory data = abi.decode(rawData, (SwapCallback));

        BalanceDelta delta = poolManager.swap(
            data.key,
            data.params,
            new bytes(0)
        );

        // Settle: pay tokens we owe, take tokens we're owed
        if (data.params.zeroForOne) {
            if (delta.amount0() < 0) {
                data.key.currency0.settle(
                    poolManager,
                    data.sender,
                    uint256(-int256(delta.amount0())),
                    false
                );
            }
            if (delta.amount1() > 0) {
                data.key.currency1.take(
                    poolManager,
                    data.sender,
                    uint256(int256(delta.amount1())),
                    false
                );
            }
        } else {
            if (delta.amount1() < 0) {
                data.key.currency1.settle(
                    poolManager,
                    data.sender,
                    uint256(-int256(delta.amount1())),
                    false
                );
            }
            if (delta.amount0() > 0) {
                data.key.currency0.take(
                    poolManager,
                    data.sender,
                    uint256(int256(delta.amount0())),
                    false
                );
            }
        }

        return abi.encode(delta);
    }
}
