// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
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
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";

/// @title Ghost Router (Sovereign Clearing Hub)
/// @notice The centralized vault and routing engine for Ghost intent-based liquidity.
///         Completely bypasses Uniswap V4 Hook architecture for a sovereign model.
///         Executes the 3-Layer matching trap natively.
contract GhostRouter is IGhostRouter, ReentrancyGuard, Owned {
    using SafeERC20 for IERC20;
    using CurrencySettler for Currency;
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    uint256 public constant PRICE_SCALE = 1e18;

    enum OracleMode {
        External,
        UniswapV4Spot
    }

    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    struct Market {
        address token0;
        address token1;
        address oracle;
        OracleMode oracleMode;
        PoolKey vanillaKey;
    }

    /// @notice Registry of Sovereign Markets
    mapping(bytes32 => Market) public markets;

    /// @notice Registry of approved engines allowed to command the vault
    mapping(address => bool) public isEngine;
    mapping(address => uint256) internal engineIndexPlusOne;

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
    error OraclePriceUnavailable();
    error MarketAlreadyInitialized();
    error InvalidEngineAddress();
    error InvalidEngineContract();
    error EngineNotRegistered();
    error InvalidPoolManager();
    error InvalidOwner();

    uint8 internal constant ENGINE_OP_SYNC = 1;
    uint8 internal constant ENGINE_OP_APPLY_NETTING = 2;
    uint8 internal constant ENGINE_OP_TAKE_GHOST = 3;

    event EngineRegistered(address indexed engine);
    event EngineDeregistered(address indexed engine);
    event EngineCallFailed(address indexed engine, bytes32 indexed marketId, uint8 indexed operation);
    event MarketInitialized(
        bytes32 indexed marketId, address indexed token0, address indexed token1, OracleMode oracleMode, address oracle
    );
    event OracleModeUpdated(bytes32 indexed marketId, OracleMode oracleMode, address oracle);
    event SwapExecuted(
        bytes32 indexed marketId,
        address indexed sender,
        bool zeroForOne,
        uint256 amountIn,
        uint256 amountOut,
        uint256 amountOutMinimum
    );
    event GlobalNettingExecuted(
        bytes32 indexed marketId,
        uint256 spotPrice,
        uint256 totalGhost0,
        uint256 totalGhost1,
        uint256 macroToken0,
        uint256 macroToken1
    );
    event GhostSettledViaAMM(
        bytes32 indexed marketId, address indexed engine, bool zeroForOne, uint256 amountIn, uint256 amountOut
    );

    // ─── Modifiers ────────────────────────────────────────────────────────────

    modifier onlyEngine() {
        if (!isEngine[msg.sender]) revert UnauthorizedEngine();
        _;
    }

    constructor(address _poolManager, address _owner) Owned(_owner) {
        if (_poolManager == address(0)) revert InvalidPoolManager();
        if (_owner == address(0)) revert InvalidOwner();
        poolManager = IPoolManager(_poolManager);
    }

    /// @notice The internal list of registered engines for Native routing iteration
    address[] public approvedEngines;

    /// @notice Register a new Sovereign Engine (Spoke)
    function registerEngine(address engine) external onlyOwner {
        if (engine == address(0)) revert InvalidEngineAddress();
        if (engine.code.length == 0) revert InvalidEngineContract();
        if (isEngine[engine]) revert EngineAlreadyRegistered();
        isEngine[engine] = true;
        approvedEngines.push(engine);
        engineIndexPlusOne[engine] = approvedEngines.length;
        emit EngineRegistered(engine);
    }

    /// @notice Deregister an existing Sovereign Engine (Spoke).
    function deregisterEngine(address engine) external onlyOwner {
        if (!isEngine[engine]) revert EngineNotRegistered();

        uint256 idx = engineIndexPlusOne[engine] - 1;
        uint256 lastIdx = approvedEngines.length - 1;
        if (idx != lastIdx) {
            address lastEngine = approvedEngines[lastIdx];
            approvedEngines[idx] = lastEngine;
            engineIndexPlusOne[lastEngine] = idx + 1;
        }

        approvedEngines.pop();
        delete engineIndexPlusOne[engine];
        delete isEngine[engine];
        emit EngineDeregistered(engine);
    }

    // ─── MARKET REGISTRY ──────────────────────────────────────────────────────

    /// @inheritdoc IGhostRouter
    function initializeMarket(PoolKey calldata vanillaKey, address _oracle)
        external
        override
        onlyOwner
        returns (bytes32 marketId)
    {
        if (_oracle == address(0)) revert InvalidOracle();
        marketId = _initializeMarket(vanillaKey);
        _setExternalOracle(marketId, _oracle);
        Market storage market = markets[marketId];
        emit MarketInitialized(marketId, market.token0, market.token1, market.oracleMode, market.oracle);
    }

    /// @inheritdoc IGhostRouter
    function initializeMarketWithUniswapOracle(PoolKey calldata vanillaKey)
        external
        override
        onlyOwner
        returns (bytes32 marketId)
    {
        marketId = _initializeMarket(vanillaKey);
        markets[marketId].oracleMode = OracleMode.UniswapV4Spot;
        Market storage market = markets[marketId];
        emit MarketInitialized(marketId, market.token0, market.token1, market.oracleMode, market.oracle);
    }

    /// @inheritdoc IGhostRouter
    function setExternalOracle(bytes32 marketId, address oracle) external override onlyOwner {
        if (oracle == address(0)) revert InvalidOracle();
        _requireMarket(marketId);
        _setExternalOracle(marketId, oracle);
        emit OracleModeUpdated(marketId, OracleMode.External, oracle);
    }

    /// @inheritdoc IGhostRouter
    function setUniswapOracle(bytes32 marketId) external override onlyOwner {
        _requireMarket(marketId);
        Market storage market = markets[marketId];
        market.oracleMode = OracleMode.UniswapV4Spot;
        market.oracle = address(0);
        emit OracleModeUpdated(marketId, OracleMode.UniswapV4Spot, address(0));
    }

    /// @inheritdoc IGhostRouter
    function getSpotPrice(bytes32 marketId) public view override returns (uint256 price) {
        Market storage market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();

        if (market.oracleMode == OracleMode.UniswapV4Spot) {
            (uint160 sqrtPriceX96,,,) = poolManager.getSlot0(market.vanillaKey.toId());
            price = _sqrtPriceX96ToPriceX18(sqrtPriceX96);
        } else {
            if (market.oracle == address(0)) revert InvalidOracle();
            price = IGhostOracle(market.oracle).getSpotPrice(marketId);
        }

        if (price == 0) revert OraclePriceUnavailable();
    }

    function _initializeMarket(PoolKey calldata vanillaKey) internal returns (bytes32 marketId) {
        if (address(vanillaKey.hooks) != address(0)) revert MustBeHookless();

        address token0 = Currency.unwrap(vanillaKey.currency0);
        address token1 = Currency.unwrap(vanillaKey.currency1);
        if (token0 >= token1) revert InvalidCurrencyOrder();

        marketId = PoolId.unwrap(vanillaKey.toId());
        if (markets[marketId].token0 != address(0)) revert MarketAlreadyInitialized();
        markets[marketId] = Market({
            token0: token0,
            token1: token1,
            oracle: address(0),
            oracleMode: OracleMode.External,
            vanillaKey: vanillaKey
        });
    }

    function _setExternalOracle(bytes32 marketId, address oracle) internal {
        Market storage market = markets[marketId];
        market.oracleMode = OracleMode.External;
        market.oracle = oracle;
    }

    function _requireMarket(bytes32 marketId) internal view {
        if (markets[marketId].token0 == address(0)) revert MarketNotFound();
    }

    function _sqrtPriceX96ToPriceX18(uint160 sqrtPriceX96) internal pure returns (uint256) {
        if (sqrtPriceX96 == 0) return 0;

        if (sqrtPriceX96 <= type(uint128).max) {
            uint256 ratioX192 = uint256(sqrtPriceX96) * uint256(sqrtPriceX96);
            return FullMath.mulDiv(ratioX192, PRICE_SCALE, 1 << 192);
        }

        uint256 ratioX128 = FullMath.mulDiv(uint256(sqrtPriceX96), uint256(sqrtPriceX96), 1 << 64);
        return FullMath.mulDiv(ratioX128, PRICE_SCALE, 1 << 128);
    }

    // ─── VAULT MECHANICS (Auth by Engines) ────────────────────────────────────

    /// @inheritdoc IGhostRouter
    function pushMarketFunds(bytes32 marketId, bool zeroForOne, address to, uint256 amount)
        external
        override
        onlyEngine
    {
        Market storage market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();
        address token = zeroForOne ? market.token0 : market.token1;
        IERC20(token).safeTransfer(to, amount);
    }

    /// @inheritdoc IGhostRouter
    function pullMarketFunds(bytes32 marketId, bool zeroForOne, address from, uint256 amount)
        external
        override
        onlyEngine
    {
        Market storage market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();
        address token = zeroForOne ? market.token0 : market.token1;
        IERC20(token).safeTransferFrom(from, address(this), amount);
    }

    // ─── TAKER ROUTING ────────────────────────────────────────────────────────

    /// @inheritdoc IGhostRouter
    function swap(bytes32 marketId, bool zeroForOne, uint256 amountIn, uint256 amountOutMinimum)
        external
        override
        nonReentrant
        returns (uint256 amountOut)
    {
        Market storage market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();

        address tokenIn = zeroForOne ? market.token0 : market.token1;
        address tokenOut = zeroForOne ? market.token1 : market.token0;

        // 1. Pull Taker Tokens into Vault
        IERC20(tokenIn).safeTransferFrom(msg.sender, address(this), amountIn);

        // 2. Fetch Oracle Spot Price (Token1 per Token0, scaled by 1e18)
        uint256 spotPrice = getSpotPrice(marketId);

        // 3. [Layer 1] Global Ghost Netting — cross all engines at oracle price
        _executeGlobalNetting(marketId, spotPrice);

        // 4. [Layer 2] Taker Intercept — fill from remaining ghost
        uint256 remainingIn = amountIn;
        (uint256 ghostFilled, uint256 inputUsed) = _takerIntercept(marketId, zeroForOne, remainingIn, spotPrice);
        amountOut += ghostFilled;
        remainingIn -= inputUsed;

        // 5. [Layer 3] V4 AMM Fallback — route remainder through Uniswap
        if (remainingIn > 0) {
            amountOut += _executeVanillaV4Swap(market.vanillaKey, zeroForOne, remainingIn);
        }

        if (amountOut < amountOutMinimum) revert SlippageExceeded();

        // 6. Deliver output to Taker
        if (amountOut > 0) {
            IERC20(tokenOut).safeTransfer(msg.sender, amountOut);
        }

        emit SwapExecuted(marketId, msg.sender, zeroForOne, amountIn, amountOut, amountOutMinimum);
    }

    /// @inheritdoc IGhostRouter
    function settleGhost(bytes32 marketId, bool zeroForOne, uint256 amountIn)
        external
        override
        onlyEngine
        returns (uint256 amountOut)
    {
        Market storage market = markets[marketId];
        if (market.token0 == address(0)) revert MarketNotFound();
        amountOut = _executeVanillaV4Swap(market.vanillaKey, zeroForOne, amountIn);
        emit GhostSettledViaAMM(marketId, msg.sender, zeroForOne, amountIn, amountOut);
    }

    // ─── LAYER 1: GLOBAL GHOST NETTING ────────────────────────────────────────

    /// @notice Aggregate ghost balances from all engines, compute price-weighted
    ///         macro intersection, and distribute settlement pro-rata.
    function _executeGlobalNetting(bytes32 marketId, uint256 spotPrice) internal {
        (uint256[] memory engineG0s, uint256[] memory engineG1s, uint256 totalG0, uint256 totalG1) =
            _aggregateGhostBalances(marketId);

        if (totalG0 == 0 || totalG1 == 0) return;

        (uint256 macroToken0, uint256 macroToken1) = _computeMacroIntersection(totalG0, totalG1, spotPrice);

        if (macroToken0 == 0 && macroToken1 == 0) return;

        _distributeNettingProRata(marketId, spotPrice, engineG0s, engineG1s, totalG0, totalG1, macroToken0, macroToken1);
        emit GlobalNettingExecuted(marketId, spotPrice, totalG0, totalG1, macroToken0, macroToken1);
    }

    /// @notice Poll all engines to sync state and collect ghost balances.
    function _aggregateGhostBalances(bytes32 marketId)
        internal
        returns (uint256[] memory engineG0s, uint256[] memory engineG1s, uint256 totalG0, uint256 totalG1)
    {
        uint256 numEngines = approvedEngines.length;
        engineG0s = new uint256[](numEngines);
        engineG1s = new uint256[](numEngines);

        for (uint256 i = 0; i < numEngines; ++i) {
            address engine = approvedEngines[i];
            try IGhostEngine(engine).syncAndFetchGhost(marketId) returns (uint256 g0, uint256 g1) {
                engineG0s[i] = g0;
                engineG1s[i] = g1;
                totalG0 += g0;
                totalG1 += g1;
            } catch {
                emit EngineCallFailed(engine, marketId, ENGINE_OP_SYNC);
            }
        }
    }

    /// @notice Compute the price-weighted overlap between opposing ghost flows.
    function _computeMacroIntersection(uint256 totalG0, uint256 totalG1, uint256 spotPrice)
        internal
        pure
        returns (uint256 macroToken0, uint256 macroToken1)
    {
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
                address engine = approvedEngines[i];
                try IGhostEngine(engine).applyNettingResult(marketId, consumed0, consumed1, spotPrice) {} catch {
                    emit EngineCallFailed(engine, marketId, ENGINE_OP_APPLY_NETTING);
                }
            }
        }
    }

    // ─── LAYER 2: TAKER INTERCEPT ─────────────────────────────────────────────

    /// @notice Route the Taker's input against remaining directional ghost.
    function _takerIntercept(bytes32 marketId, bool zeroForOne, uint256 amountIn, uint256 spotPrice)
        internal
        returns (uint256 totalFilled, uint256 totalInput)
    {
        uint256 remaining = amountIn;

        for (uint256 i = 0; i < approvedEngines.length; ++i) {
            if (remaining == 0) break;

            address engine = approvedEngines[i];
            try IGhostEngine(engine).takeGhost(marketId, zeroForOne, remaining, spotPrice) returns (
                uint256 filledOut,
                uint256 inputConsumed
            ) {
                if (filledOut > 0) {
                    totalFilled += filledOut;
                    totalInput += inputConsumed;
                    remaining -= inputConsumed;
                }
            } catch {
                emit EngineCallFailed(engine, marketId, ENGINE_OP_TAKE_GHOST);
            }
        }
    }

    // ─── LAYER 3: V4 AMM FALLBACK ─────────────────────────────────────────────

    function _executeVanillaV4Swap(PoolKey memory key, bool zeroForOne, uint256 amountIn)
        internal
        returns (uint256 amountOut)
    {
        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(amountIn),
            sqrtPriceLimitX96: zeroForOne ? 4295128740 : 1461446703485210103287273052203988822378723970341
        });

        BalanceDelta delta = abi.decode(
            poolManager.unlock(abi.encode(SwapCallback({sender: address(this), key: key, params: swapParams}))),
            (BalanceDelta)
        );

        amountOut = zeroForOne ? uint256(int256(delta.amount1())) : uint256(int256(delta.amount0()));
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
    function _settleCurrency(PoolKey memory key, BalanceDelta delta, address sender, bool isSettle) internal {
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
