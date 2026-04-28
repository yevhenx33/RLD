// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {GhostRouter} from "../../src/dex/GhostRouter.sol";
import {TwapEngine} from "../../src/dex/TwapEngine.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockGhostOracle} from "./mocks/MockGhostOracle.sol";
import {MockPoolManager} from "./mocks/MockPoolManager.sol";

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";

contract HubSpokeIntegrationTest is Test {
    uint256 internal constant INTERVAL = 60;

    address internal alice = address(0xA11CE);
    address internal taker = address(0xCAFE);

    MockERC20 internal tokenA;
    MockERC20 internal tokenB;
    MockPoolManager internal poolManager;
    MockGhostOracle internal oracle;
    GhostRouter internal router;
    TwapEngine internal engine;

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
        oracle.setPrice(marketId, 2e18);

        engine = new TwapEngine(address(router), INTERVAL, 500, 0);
        router.registerEngine(address(engine));

        MockERC20 t0 = token0 == address(tokenA) ? tokenA : tokenB;
        MockERC20 t1 = token1 == address(tokenA) ? tokenA : tokenB;

        t0.mint(alice, 1_000_000e18);
        t1.mint(alice, 1_000_000e18);
        t0.mint(taker, 1_000_000e18);

        vm.startPrank(alice);
        t0.approve(address(router), type(uint256).max);
        t1.approve(address(router), type(uint256).max);
        vm.stopPrank();

        vm.startPrank(taker);
        t0.approve(address(router), type(uint256).max);
        t1.approve(address(router), type(uint256).max);
        vm.stopPrank();
    }

    function _nextEpoch(uint256 t) internal pure returns (uint256) {
        return ((t / INTERVAL) * INTERVAL) + INTERVAL;
    }

    function test_submitSwapClaimAcrossHubAndSpoke() external {
        // Alice streams token1->token0, creating token1 ghost inventory.
        vm.prank(alice);
        bytes32 orderId = engine.submitStream(marketId, false, 120, 1_200e18);

        uint256 startEpoch = _nextEpoch(block.timestamp);
        vm.warp(startEpoch + 60);

        // Taker swaps token0->token1; should be filled from ghost through router+engine path.
        vm.prank(taker);
        uint256 amountOut = router.swap(marketId, true, 100e18, 1);
        assertEq(amountOut, 200e18, "unexpected ghost intercept output");

        // Alice claims earned token0 from the taker fill.
        MockERC20 t0 = token0 == address(tokenA) ? tokenA : tokenB;
        uint256 aliceBefore = t0.balanceOf(alice);
        vm.prank(alice);
        uint256 claimed = engine.claimTokens(marketId, orderId);

        assertEq(claimed, 100e18, "unexpected claimed earnings");
        assertEq(t0.balanceOf(alice), aliceBefore + claimed, "claim transfer mismatch");
    }

    function test_routerForceSettleEngineCrystallizesTwapGhost() external {
        vm.prank(alice);
        bytes32 orderId = engine.submitStream(marketId, true, 120, 1_200e18);

        uint256 startEpoch = _nextEpoch(block.timestamp);
        vm.warp(startEpoch + 60);

        MockERC20 t1 = token1 == address(tokenA) ? tokenA : tokenB;
        t1.mint(address(poolManager), 1_000_000e18);

        router.forceSettleEngine(address(engine), marketId, true);

        (uint256 ghost0,,,,) = engine.states(marketId);
        assertEq(ghost0, 0, "force settle should clear token0 ghost");

        uint256 aliceBefore = t1.balanceOf(alice);
        vm.prank(alice);
        uint256 claimed = engine.claimTokens(marketId, orderId);

        assertEq(claimed, 600e18, "unexpected force-settle claim");
        assertEq(t1.balanceOf(alice), aliceBefore + claimed, "claim transfer mismatch");
    }
}
