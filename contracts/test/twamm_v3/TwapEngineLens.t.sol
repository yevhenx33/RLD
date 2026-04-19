// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {TwapEngine} from "../../src/twamm_v3/TwapEngine.sol";
import {TwapEngineLens} from "../../src/twamm_v3/TwapEngineLens.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockGhostRouterForEngine} from "./mocks/MockGhostRouterForEngine.sol";

contract TwapEngineLensTest is Test {
    uint256 internal constant INTERVAL = 60;
    bytes32 internal constant MARKET = keccak256("LENS_MARKET");

    address internal alice = address(0xA11CE);

    MockERC20 internal token0;
    MockERC20 internal token1;
    MockGhostRouterForEngine internal router;
    TwapEngine internal engine;
    TwapEngineLens internal lens;

    function setUp() public {
        token0 = new MockERC20("Token0", "TK0", 18);
        token1 = new MockERC20("Token1", "TK1", 18);

        router = new MockGhostRouterForEngine();
        engine = new TwapEngine(address(router), INTERVAL, 500, 0);
        lens = new TwapEngineLens(address(engine));

        router.setMarket(MARKET, address(token0), address(token1));
        router.setSpotPrice(MARKET, 2e18);

        token0.mint(alice, 1_000_000e18);
        vm.prank(alice);
        token0.approve(address(router), type(uint256).max);
    }

    function _nextEpoch(uint256 t) internal pure returns (uint256) {
        return ((t / INTERVAL) * INTERVAL) + INTERVAL;
    }

    function test_lensExposesCommittedAndExactCancelState() external {
        vm.prank(alice);
        bytes32 orderId = engine.submitStream(MARKET, true, 120, 1_200e18);

        uint256 startEpoch = _nextEpoch(block.timestamp);
        vm.warp(startEpoch + 30);

        (, uint256 committedRefund) = lens.getCancelOrderStateCommitted(MARKET, orderId);
        (, uint256 exactRefund) = lens.getCancelOrderStateExact(MARKET, orderId);
        (, uint256 aliasRefund) = lens.getCancelOrderState(MARKET, orderId);

        assertEq(committedRefund, 1_200e18, "committed refund mismatch");
        assertEq(exactRefund, 900e18, "exact refund mismatch");
        assertEq(aliasRefund, committedRefund, "legacy alias should remain committed");
    }
}
