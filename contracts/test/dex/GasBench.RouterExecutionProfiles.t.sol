// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {GhostRouter} from "../../src/dex/GhostRouter.sol";
import {TwapEngine} from "../../src/dex/TwapEngine.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockGhostOracle} from "./mocks/MockGhostOracle.sol";
import {MockPoolManager} from "./mocks/MockPoolManager.sol";

import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";

interface IERC20Like {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @notice Minimal direct-to-pool swap executor (no ghost layers).
contract DirectPoolSwapExecutor {
    using CurrencySettler for Currency;

    IPoolManager public immutable poolManager;

    struct SwapCallback {
        PoolKey key;
        SwapParams params;
    }

    error UnauthorizedCallback();

    constructor(address _poolManager) {
        poolManager = IPoolManager(_poolManager);
    }

    function swap(PoolKey memory key, bool zeroForOne, uint256 amountIn) external returns (uint256 amountOut) {
        address tokenIn = zeroForOne ? Currency.unwrap(key.currency0) : Currency.unwrap(key.currency1);
        address tokenOut = zeroForOne ? Currency.unwrap(key.currency1) : Currency.unwrap(key.currency0);

        IERC20Like(tokenIn).transferFrom(msg.sender, address(this), amountIn);

        SwapParams memory params = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(amountIn),
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740
                : 1461446703485210103287273052203988822378723970341
        });

        BalanceDelta delta = abi.decode(poolManager.unlock(abi.encode(SwapCallback({key: key, params: params}))), (BalanceDelta));
        amountOut = zeroForOne ? uint256(int256(delta.amount1())) : uint256(int256(delta.amount0()));

        if (amountOut > 0) {
            IERC20Like(tokenOut).transfer(msg.sender, amountOut);
        }
    }

    function unlockCallback(bytes calldata rawData) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert UnauthorizedCallback();

        SwapCallback memory data = abi.decode(rawData, (SwapCallback));
        BalanceDelta delta = poolManager.swap(data.key, data.params, new bytes(0));

        _settleCurrency(data.key, delta, true);
        _settleCurrency(data.key, delta, false);

        return abi.encode(delta);
    }

    function _settleCurrency(PoolKey memory key, BalanceDelta delta, bool isSettle) internal {
        int128 amount0 = delta.amount0();
        int128 amount1 = delta.amount1();

        if (isSettle) {
            if (amount0 < 0) key.currency0.settle(poolManager, address(this), uint256(-int256(amount0)), false);
            if (amount1 < 0) key.currency1.settle(poolManager, address(this), uint256(-int256(amount1)), false);
        } else {
            if (amount0 > 0) key.currency0.take(poolManager, address(this), uint256(int256(amount0)), false);
            if (amount1 > 0) key.currency1.take(poolManager, address(this), uint256(int256(amount1)), false);
        }
    }
}

contract RouterExecutionProfilesGasBenchTest is Test {
    uint256 internal constant INTERVAL = 60;
    uint256 internal constant SWAP_IN = 100e18;
    uint256 internal constant SPOT_PRICE = 1e18;

    address internal maker = address(0xA11CE);
    address internal taker = address(0xCAFE);

    MockERC20 internal tokenA;
    MockERC20 internal tokenB;
    MockPoolManager internal poolManager;
    MockGhostOracle internal oracle;
    GhostRouter internal router;
    TwapEngine internal twapEngine;
    DirectPoolSwapExecutor internal direct;

    address internal token0;
    address internal token1;
    PoolKey internal key;
    bytes32 internal marketId;

    function setUp() public {
        tokenA = new MockERC20("TokenA", "TKA", 18);
        tokenB = new MockERC20("TokenB", "TKB", 18);
        poolManager = new MockPoolManager();
        oracle = new MockGhostOracle();

        (token0, token1) =
            address(tokenA) < address(tokenB) ? (address(tokenA), address(tokenB)) : (address(tokenB), address(tokenA));

        key = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });

        router = new GhostRouter(address(poolManager), address(this));
        marketId = router.initializeMarket(key, address(oracle));
        oracle.setPrice(marketId, SPOT_PRICE);

        twapEngine = new TwapEngine(address(router), INTERVAL, 500, 0);
        direct = new DirectPoolSwapExecutor(address(poolManager));

        MockERC20 t0 = token0 == address(tokenA) ? tokenA : tokenB;
        MockERC20 t1 = token1 == address(tokenA) ? tokenA : tokenB;

        t0.mint(taker, 2_000e18);
        t1.mint(address(poolManager), 2_000_000e18);

        vm.startPrank(taker);
        t0.approve(address(router), type(uint256).max);
        t0.approve(address(direct), type(uint256).max);
        vm.stopPrank();
    }

    function test_gasProfile_directPool_vs_routerFallback_noEngines() external {
        poolManager.setSwapOutputRatio(1, 1);

        uint256 snap = vm.snapshot();
        (uint256 directGas, uint256 directOut) = _measureDirectSwap();
        vm.revertTo(snap);

        (uint256 routerGas, uint256 routerOut) = _measureRouterSwap();

        assertEq(directOut, SWAP_IN, "direct baseline output mismatch");
        assertEq(routerOut, directOut, "router fallback output mismatch");

        _emitGasProfile("no_engines", directGas, routerGas, directOut, routerOut);
    }

    function test_gasProfile_directPool_vs_routerFallback_idleTwapEngine() external {
        poolManager.setSwapOutputRatio(1, 1);
        router.registerEngine(address(twapEngine));

        uint256 snap = vm.snapshot();
        (uint256 directGas, uint256 directOut) = _measureDirectSwap();
        vm.revertTo(snap);

        (uint256 routerGas, uint256 routerOut) = _measureRouterSwap();

        assertEq(routerOut, directOut, "idle-engine router output mismatch");
        _emitGasProfile("idle_twap_engine", directGas, routerGas, directOut, routerOut);
    }

    function test_gasProfile_directPool_vs_router_withPassiveTwapLiquidity() external {
        router.registerEngine(address(twapEngine));

        // Baseline pool path gives 95 output for 100 input.
        poolManager.setSwapOutputRatio(95, 100);

        MockERC20 sellToken = token1 == address(tokenA) ? tokenA : tokenB;
        sellToken.mint(maker, 5_000e18);
        vm.startPrank(maker);
        sellToken.approve(address(router), type(uint256).max);
        twapEngine.submitStream(marketId, false, 600, 1_200e18);
        vm.stopPrank();

        uint256 nextEpoch = ((block.timestamp / INTERVAL) * INTERVAL) + INTERVAL;
        vm.warp(nextEpoch + 120); // enough to accrue > SWAP_IN ghost inventory

        uint256 snap = vm.snapshot();
        (uint256 directGas, uint256 directOut) = _measureDirectSwap();
        vm.revertTo(snap);

        (uint256 routerGas, uint256 routerOut) = _measureRouterSwap();

        assertEq(directOut, 95e18, "direct pool output mismatch");
        assertEq(routerOut, SWAP_IN, "router should fill at spot via passive TWAP inventory");

        _emitGasProfile("active_twap_passive_liquidity", directGas, routerGas, directOut, routerOut);
    }

    function _measureDirectSwap() internal returns (uint256 gasUsed, uint256 amountOut) {
        vm.prank(taker);
        uint256 startGas = gasleft();
        amountOut = direct.swap(key, true, SWAP_IN);
        gasUsed = startGas - gasleft();
    }

    function _measureRouterSwap() internal returns (uint256 gasUsed, uint256 amountOut) {
        vm.prank(taker);
        uint256 startGas = gasleft();
        amountOut = router.swap(marketId, true, SWAP_IN, 1);
        gasUsed = startGas - gasleft();
    }

    function _emitGasProfile(
        string memory mode,
        uint256 directGas,
        uint256 routerGas,
        uint256 directOut,
        uint256 routerOut
    ) internal {
        emit log("----- Router vs Direct Pool Gas Profile -----");
        emit log_named_string("mode", mode);
        emit log_named_uint("direct_pool_swap_gas", directGas);
        emit log_named_uint("ghost_router_swap_gas", routerGas);
        emit log_named_int("router_minus_direct_gas", int256(routerGas) - int256(directGas));
        emit log_named_uint("direct_pool_amount_out", directOut);
        emit log_named_uint("ghost_router_amount_out", routerOut);
        if (directOut > 0) {
            emit log_named_uint("router_out_premium_bps", ((routerOut - directOut) * 10_000) / directOut);
        }
    }
}

