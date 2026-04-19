// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {GhostRouter} from "../../src/twamm_v3/GhostRouter.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockStrictApproveERC20} from "./mocks/MockStrictApproveERC20.sol";
import {MockGhostOracle} from "./mocks/MockGhostOracle.sol";
import {MockGhostEngine} from "./mocks/MockGhostEngine.sol";
import {MockRevertingGhostEngine} from "./mocks/MockRevertingGhostEngine.sol";
import {MockPoolManager} from "./mocks/MockPoolManager.sol";

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {FixedPoint96} from "v4-core/src/libraries/FixedPoint96.sol";

contract GhostRouterIntegrationTest is Test {
    address internal taker = address(0xCAFE);

    MockERC20 internal tokenA;
    MockERC20 internal tokenB;
    MockPoolManager internal poolManager;
    GhostRouter internal router;
    MockGhostEngine internal engine;
    MockGhostOracle internal oracle;

    PoolKey internal key;
    address internal token0;
    address internal token1;

    function setUp() public {
        tokenA = new MockERC20("TokenA", "TKA", 18);
        tokenB = new MockERC20("TokenB", "TKB", 18);

        poolManager = new MockPoolManager();
        router = new GhostRouter(address(poolManager), address(this));
        engine = new MockGhostEngine();
        oracle = new MockGhostOracle();

        router.registerEngine(address(engine));

        (token0, token1) =
            address(tokenA) < address(tokenB) ? (address(tokenA), address(tokenB)) : (address(tokenB), address(tokenA));

        key = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
    }

    function _token0Contract() internal view returns (MockERC20) {
        return token0 == address(tokenA) ? tokenA : tokenB;
    }

    function _token1Contract() internal view returns (MockERC20) {
        return token1 == address(tokenA) ? tokenA : tokenB;
    }

    function _fundSwapPath(uint256 takerAmountIn, uint256 routerOutputLiquidity) internal {
        MockERC20 t0 = _token0Contract();
        MockERC20 t1 = _token1Contract();

        t0.mint(taker, takerAmountIn * 10);
        t1.mint(address(router), routerOutputLiquidity);

        vm.prank(taker);
        t0.approve(address(router), type(uint256).max);
    }

    function test_getSpotPriceUsesExternalOracleAndSwapPassesPriceToEngine() external {
        bytes32 marketId = router.initializeMarket(key, address(oracle));
        oracle.setPrice(marketId, 2e18);

        engine.setGhost(0, 1_000e18);
        engine.setTakeBehavior(true, 0, 0);

        _fundSwapPath(100e18, 1_000e18);

        vm.prank(taker);
        uint256 amountOut = router.swap(marketId, true, 10e18, 1);

        assertEq(router.getSpotPrice(marketId), 2e18, "external oracle price mismatch");
        assertEq(engine.lastTakeSpotPrice(), 2e18, "engine did not receive external oracle price");
        assertEq(amountOut, 10e18, "unexpected amountOut");
    }

    function test_getSpotPriceUsesUniswapMode() external {
        poolManager.setSqrtPriceX96(uint160(FixedPoint96.Q96));
        bytes32 marketId = router.initializeMarketWithUniswapOracle(key);

        uint256 spot = router.getSpotPrice(marketId);
        assertEq(spot, 1e18, "uniswap spot conversion mismatch");
    }

    function test_swapUsesUniswapSpotWhenConfigured() external {
        poolManager.setSqrtPriceX96(uint160(FixedPoint96.Q96));
        bytes32 marketId = router.initializeMarketWithUniswapOracle(key);

        engine.setGhost(0, 1_000e18);
        engine.setTakeBehavior(true, 0, 0);

        _fundSwapPath(100e18, 1_000e18);

        vm.prank(taker);
        router.swap(marketId, true, 10e18, 1);

        assertEq(engine.lastTakeSpotPrice(), 1e18, "engine did not receive uniswap oracle price");
    }

    function test_canToggleBetweenUniswapAndExternalOracle() external {
        bytes32 marketId = router.initializeMarketWithUniswapOracle(key);
        poolManager.setSqrtPriceX96(uint160(FixedPoint96.Q96));
        assertEq(router.getSpotPrice(marketId), 1e18, "initial uniswap price mismatch");

        oracle.setPrice(marketId, 3e18);
        router.setExternalOracle(marketId, address(oracle));
        assertEq(router.getSpotPrice(marketId), 3e18, "external oracle price mismatch after toggle");

        router.setUniswapOracle(marketId);
        assertEq(router.getSpotPrice(marketId), 1e18, "uniswap price mismatch after toggle back");
    }

    function test_registerEngineValidatesAddressAndContract() external {
        vm.expectRevert(GhostRouter.InvalidEngineAddress.selector);
        router.registerEngine(address(0));

        vm.expectRevert(GhostRouter.InvalidEngineContract.selector);
        router.registerEngine(address(0xBEEF));
    }

    function test_swapSurvivesRevertingEngineAndFallsBackToV4() external {
        bytes32 marketId = router.initializeMarket(key, address(oracle));
        oracle.setPrice(marketId, 2e18);

        MockRevertingGhostEngine failingEngine = new MockRevertingGhostEngine();
        router.registerEngine(address(failingEngine));

        // Healthy engine contributes nothing, forcing fallback.
        engine.setGhost(0, 0);
        engine.setTakeBehavior(false, 0, 0);

        MockERC20 t0 = _token0Contract();
        MockERC20 t1 = _token1Contract();
        t0.mint(taker, 1_000e18);
        t1.mint(address(poolManager), 1_000e18);

        vm.prank(taker);
        t0.approve(address(router), type(uint256).max);

        vm.prank(taker);
        uint256 amountOut = router.swap(marketId, true, 10e18, 1);
        assertEq(amountOut, 10e18, "swap should continue via fallback despite reverting engine");
    }

    function test_fallbackSwapSupportsStrictApproveTokensAcrossRepeatedSwaps() external {
        MockStrictApproveERC20 strictA = new MockStrictApproveERC20("StrictA", "STA", 18);
        MockStrictApproveERC20 strictB = new MockStrictApproveERC20("StrictB", "STB", 18);
        MockPoolManager localPoolManager = new MockPoolManager();
        GhostRouter localRouter = new GhostRouter(address(localPoolManager), address(this));
        MockGhostEngine localEngine = new MockGhostEngine();
        MockGhostOracle localOracle = new MockGhostOracle();
        localRouter.registerEngine(address(localEngine));

        (address localToken0, address localToken1) =
            address(strictA) < address(strictB) ? (address(strictA), address(strictB)) : (address(strictB), address(strictA));

        PoolKey memory localKey = PoolKey({
            currency0: Currency.wrap(localToken0),
            currency1: Currency.wrap(localToken1),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });

        bytes32 marketId = localRouter.initializeMarket(localKey, address(localOracle));
        localOracle.setPrice(marketId, 1e18);
        localPoolManager.setSwapOutputRatio(1, 1);

        // Disable intercept so the router must traverse the fallback path.
        localEngine.setGhost(0, 0);
        localEngine.setTakeBehavior(false, 0, 0);

        MockStrictApproveERC20 tokenIn = localToken0 == address(strictA) ? strictA : strictB;
        MockStrictApproveERC20 tokenOut = localToken1 == address(strictA) ? strictA : strictB;

        tokenIn.mint(taker, 1_000e18);
        tokenOut.mint(address(localPoolManager), 1_000e18);

        vm.prank(taker);
        tokenIn.approve(address(localRouter), type(uint256).max);

        vm.startPrank(taker);
        uint256 firstOut = localRouter.swap(marketId, true, 10e18, 1);
        uint256 secondOut = localRouter.swap(marketId, true, 10e18, 1);
        vm.stopPrank();

        assertEq(firstOut, 10e18, "unexpected first fallback output");
        assertEq(secondOut, 10e18, "unexpected second fallback output");
    }
}
