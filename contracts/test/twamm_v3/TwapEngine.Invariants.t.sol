// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {StdInvariant} from "forge-std/StdInvariant.sol";

import {TwapEngine} from "../../src/twamm_v3/TwapEngine.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockGhostRouterForEngine} from "./mocks/MockGhostRouterForEngine.sol";
import {TwapEngineInvariantHandler} from "./invariants/TwapEngineInvariantHandler.sol";

contract TwapEngineStatefulInvariantTest is StdInvariant, Test {
    bytes32 internal constant MARKET_A = keccak256("INVARIANT_MARKET_A");
    bytes32 internal constant MARKET_B = keccak256("INVARIANT_MARKET_B");
    uint256 internal constant INTERVAL = 60;
    uint256 internal constant ROUNDING_BUFFER = 10e18;

    MockERC20 internal token0;
    MockERC20 internal token1;
    MockGhostRouterForEngine internal router;
    TwapEngine internal engine;
    TwapEngineInvariantHandler internal handler;

    function setUp() public {
        token0 = new MockERC20("InvariantToken0", "IT0", 18);
        token1 = new MockERC20("InvariantToken1", "IT1", 18);
        router = new MockGhostRouterForEngine();
        engine = new TwapEngine(address(router), INTERVAL, 500, 1e10);

        router.setMarket(MARKET_A, address(token0), address(token1));
        router.setMarket(MARKET_B, address(token0), address(token1));
        router.setSpotPrice(MARKET_A, 2e18);
        router.setSpotPrice(MARKET_B, 2e18);

        handler = new TwapEngineInvariantHandler(engine, router, token0, token1, MARKET_A, MARKET_B);
        handler.initializeAccounts();

        bytes4[] memory selectors = new bytes4[](10);
        selectors[0] = handler.warpTime.selector;
        selectors[1] = handler.tuneSpotPrice.selector;
        selectors[2] = handler.syncMarket.selector;
        selectors[3] = handler.submitStream.selector;
        selectors[4] = handler.claimOrder.selector;
        selectors[5] = handler.cancelOrder.selector;
        selectors[6] = handler.clearAuction.selector;
        selectors[7] = handler.applyFairNetting.selector;
        selectors[8] = handler.takeGhost.selector;
        selectors[9] = handler.forceSettle.selector;

        targetSelector(FuzzSelector({addr: address(handler), selectors: selectors}));
    }

    function invariant_routerBalancesCoverCurrentLiabilities() external view {
        (uint256 token0Liability, uint256 token1Liability) = handler.computeTokenLiabilities();

        assertGe(
            token0.balanceOf(address(router)) + ROUNDING_BUFFER,
            token0Liability,
            "router token0 balance below modeled liabilities"
        );
        assertGe(
            token1.balanceOf(address(router)) + ROUNDING_BUFFER,
            token1Liability,
            "router token1 balance below modeled liabilities"
        );
    }

    function invariant_orderMetadataIsConsistent() external view {
        handler.assertOrderMetadataConsistency();
    }
}
