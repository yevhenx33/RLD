// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {LimitEngine} from "../../src/dex/LimitEngine.sol";
import {GhostRouter} from "../../src/dex/GhostRouter.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockGhostOracle} from "./mocks/MockGhostOracle.sol";
import {MockGhostRouterForEngine} from "./mocks/MockGhostRouterForEngine.sol";
import {MockPoolManager} from "./mocks/MockPoolManager.sol";

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";

contract LimitEngineUnitTest is Test {
    bytes32 internal constant MARKET = keccak256("MARKET_LIMIT");

    address internal alice = address(0xA11CE);
    address internal taker = address(0xCAFE);

    MockERC20 internal token0;
    MockERC20 internal token1;
    MockGhostRouterForEngine internal router;
    LimitEngine internal engine;

    function setUp() public {
        token0 = new MockERC20("Token0", "TK0", 18);
        token1 = new MockERC20("Token1", "TK1", 18);
        router = new MockGhostRouterForEngine();
        engine = new LimitEngine(address(router));

        router.setMarket(MARKET, address(token0), address(token1));
        router.setSpotPrice(MARKET, 1e18);

        token0.mint(alice, 5_000_000e18);
        token1.mint(alice, 5_000_000e18);
        token0.mint(taker, 5_000_000e18);
        token1.mint(taker, 5_000_000e18);

        vm.startPrank(alice);
        token0.approve(address(router), type(uint256).max);
        token1.approve(address(router), type(uint256).max);
        vm.stopPrank();

        vm.startPrank(taker);
        token0.approve(address(router), type(uint256).max);
        token1.approve(address(router), type(uint256).max);
        vm.stopPrank();
    }

    function test_constructorFailsFastOnInvalidRouter() external {
        vm.expectRevert(LimitEngine.InvalidRouter.selector);
        new LimitEngine(address(0));
    }

    function test_demandTriggeredActivationAndGhostVisibility() external {
        vm.prank(alice);
        engine.submitLimitOrder(MARKET, true, 2e18, 100e18);

        vm.prank(address(router));
        (uint256 ghost0Before,) = engine.syncAndFetchGhost(MARKET);
        assertEq(ghost0Before, 0, "order should remain pending before trigger");

        router.setSpotPrice(MARKET, 2e18);
        vm.prank(address(router));
        (uint256 ghost0After,) = engine.syncAndFetchGhost(MARKET);
        assertEq(ghost0After, 100e18, "order should activate on demand when trigger is crossed");
    }

    function test_oneWayActivationDoesNotBounceBack() external {
        vm.prank(alice);
        engine.submitLimitOrder(MARKET, true, 2e18, 100e18);

        router.setSpotPrice(MARKET, 3e18);
        vm.prank(address(router));
        (uint256 ghost0Active,) = engine.syncAndFetchGhost(MARKET);
        assertEq(ghost0Active, 100e18, "ghost should activate at crossed trigger");

        router.setSpotPrice(MARKET, 1e18);
        vm.prank(address(router));
        (uint256 ghost0AfterRevert,) = engine.syncAndFetchGhost(MARKET);
        assertEq(ghost0AfterRevert, 100e18, "activated ghost must remain active after price reversion");
    }

    function test_takeGhostAndClaimEarnings() external {
        vm.prank(alice);
        bytes32 orderId = engine.submitLimitOrder(MARKET, false, 2e18, 100e18);

        router.setSpotPrice(MARKET, 1e18);
        vm.prank(address(router));
        (, uint256 ghost1Before) = engine.syncAndFetchGhost(MARKET);
        assertEq(ghost1Before, 100e18, "order should activate into token1 ghost");

        vm.prank(address(router));
        (uint256 filledOut, uint256 inputConsumed) = engine.takeGhost(MARKET, true, 10e18, 1e18);
        assertEq(filledOut, 10e18, "unexpected filledOut");
        assertEq(inputConsumed, 10e18, "unexpected inputConsumed");

        token0.mint(address(router), 10e18);
        uint256 aliceToken0Before = token0.balanceOf(alice);

        vm.prank(alice);
        uint256 claimed = engine.claimTokens(MARKET, orderId);
        assertEq(claimed, 10e18, "unexpected claimed earnings");
        assertEq(token0.balanceOf(alice), aliceToken0Before + 10e18, "claim transfer mismatch");

        (, uint256 claimable, uint256 refundable) = engine.getOrderState(MARKET, orderId);
        assertEq(claimable, 0, "claimable should be zero after claiming");
        assertEq(refundable, 90e18, "remaining sell inventory mismatch");
    }

    function test_cancelPendingOrderRefundsFullAmount() external {
        vm.prank(alice);
        bytes32 orderId = engine.submitLimitOrder(MARKET, true, 3e18, 77e18);

        uint256 aliceToken0Before = token0.balanceOf(alice);
        vm.prank(alice);
        (uint256 refund, uint256 earnings) = engine.cancelOrder(MARKET, orderId);

        assertEq(refund, 77e18, "pending refund mismatch");
        assertEq(earnings, 0, "pending order should not have earnings");
        assertEq(token0.balanceOf(alice), aliceToken0Before + 77e18, "refund transfer mismatch");
    }

    function test_cancelActiveOrderReturnsRemainingPlusEarnings() external {
        vm.prank(alice);
        bytes32 orderId = engine.submitLimitOrder(MARKET, false, 2e18, 100e18);

        router.setSpotPrice(MARKET, 1e18);
        vm.prank(address(router));
        engine.syncAndFetchGhost(MARKET);

        vm.prank(address(router));
        engine.takeGhost(MARKET, true, 20e18, 1e18);

        token0.mint(address(router), 20e18);

        uint256 aliceToken0Before = token0.balanceOf(alice);
        uint256 aliceToken1Before = token1.balanceOf(alice);
        vm.prank(alice);
        (uint256 refund, uint256 earnings) = engine.cancelOrder(MARKET, orderId);

        assertEq(refund, 80e18, "active cancel refund mismatch");
        assertEq(earnings, 20e18, "active cancel earnings mismatch");
        assertEq(token0.balanceOf(alice), aliceToken0Before + 20e18, "earnings transfer mismatch");
        assertEq(token1.balanceOf(alice), aliceToken1Before + 80e18, "refund transfer mismatch");
    }

    function test_applyNettingConsumesGhostAndCreditsEarnings() external {
        vm.prank(alice);
        bytes32 orderId = engine.submitLimitOrder(MARKET, true, 1e18, 100e18);

        vm.prank(address(router));
        engine.syncAndFetchGhost(MARKET);

        vm.prank(address(router));
        engine.applyNettingResult(MARKET, 40e18, 0, 2e18);

        vm.prank(address(router));
        (uint256 ghost0After,) = engine.syncAndFetchGhost(MARKET);
        assertEq(ghost0After, 60e18, "netting should reduce token0 ghost");

        token1.mint(address(router), 80e18);
        vm.prank(alice);
        uint256 claimed = engine.claimTokens(MARKET, orderId);
        assertEq(claimed, 80e18, "netting earnings mismatch");
    }

    function test_submitOnActivatedBucketJoinsActivePoolImmediately() external {
        vm.prank(alice);
        engine.submitLimitOrder(MARKET, true, 1e18, 50e18);
        vm.prank(address(router));
        engine.syncAndFetchGhost(MARKET);

        vm.prank(alice);
        bytes32 orderId2 = engine.submitLimitOrder(MARKET, true, 1e18, 30e18);
        (bool activated,, uint256 refundable) = engine.getOrderState(MARKET, orderId2);

        assertTrue(activated, "new order on activated bucket should be active immediately");
        assertEq(refundable, 30e18, "new active order should preserve full remaining input");

        vm.prank(address(router));
        (uint256 ghost0,) = engine.syncAndFetchGhost(MARKET);
        assertEq(ghost0, 80e18, "active pool should include both orders");
    }
}

contract LimitEngineHubSpokeIntegrationTest is Test {
    address internal alice = address(0xA11CE);
    address internal taker = address(0xCAFE);

    MockERC20 internal tokenA;
    MockERC20 internal tokenB;
    MockPoolManager internal poolManager;
    MockGhostOracle internal oracle;
    GhostRouter internal router;
    LimitEngine internal engine;

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
        oracle.setPrice(marketId, 1e18);

        engine = new LimitEngine(address(router));
        router.registerEngine(address(engine));

        MockERC20 t0 = token0 == address(tokenA) ? tokenA : tokenB;
        MockERC20 t1 = token0 == address(tokenA) ? tokenB : tokenA;

        t0.mint(alice, 1_000_000e18);
        t1.mint(alice, 1_000_000e18);
        t0.mint(taker, 1_000_000e18);
        t1.mint(taker, 1_000_000e18);

        vm.startPrank(alice);
        t0.approve(address(router), type(uint256).max);
        t1.approve(address(router), type(uint256).max);
        vm.stopPrank();

        vm.startPrank(taker);
        t0.approve(address(router), type(uint256).max);
        t1.approve(address(router), type(uint256).max);
        vm.stopPrank();
    }

    function test_swapActivatesAndInterceptsLimitGhostThenMakerClaims() external {
        vm.prank(alice);
        bytes32 orderId = engine.submitLimitOrder(marketId, false, 2e18, 1_200e18);

        vm.prank(taker);
        uint256 amountOut = router.swap(marketId, true, 100e18, 1);
        assertEq(amountOut, 100e18, "unexpected intercept output");

        MockERC20 t0 = token0 == address(tokenA) ? tokenA : tokenB;
        uint256 aliceToken0Before = t0.balanceOf(alice);
        vm.prank(alice);
        uint256 claimed = engine.claimTokens(marketId, orderId);
        assertApproxEqAbs(claimed, 100e18, 1_000, "unexpected maker claim");
        assertApproxEqAbs(t0.balanceOf(alice), aliceToken0Before + claimed, 1_000, "claim transfer mismatch");
    }
}
