// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {TwapEngine} from "../../src/dex/TwapEngine.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockGhostRouterForEngine} from "./mocks/MockGhostRouterForEngine.sol";

contract TwapEngineHarness is TwapEngine {
    constructor(address _ghostRouter, uint256 _interval, uint256 _maxDiscountBps, uint256 _discountRateScaled)
        TwapEngine(_ghostRouter, _interval, _maxDiscountBps, _discountRateScaled)
    {}

    function isEpochEventSet(bytes32 marketId, uint256 epoch) external view returns (bool) {
        uint256 epochIndex = epoch / expirationInterval;
        uint256 wordIndex = epochIndex >> 8;
        uint256 bitMask = uint256(1) << (epochIndex & 0xff);
        return (epochEventBitmap[marketId][wordIndex] & bitMask) != 0;
    }
}

abstract contract TwapEngineBaseTest is Test {
    uint256 internal constant INTERVAL = 60;
    bytes32 internal constant MARKET_A = keccak256("MARKET_A");
    bytes32 internal constant MARKET_B = keccak256("MARKET_B");

    address internal alice = address(0xA11CE);
    address internal solver = address(0xB0B);

    MockERC20 internal token0;
    MockERC20 internal token1;
    MockGhostRouterForEngine internal router;
    TwapEngineHarness internal engine;

    function setUp() public virtual {
        token0 = new MockERC20("Token0", "TK0", 18);
        token1 = new MockERC20("Token1", "TK1", 18);

        router = new MockGhostRouterForEngine();
        engine = new TwapEngineHarness(address(router), INTERVAL, 500, 0);

        router.setMarket(MARKET_A, address(token0), address(token1));
        router.setMarket(MARKET_B, address(token0), address(token1));
        router.setSpotPrice(MARKET_A, 2e18);
        router.setSpotPrice(MARKET_B, 2e18);

        token0.mint(alice, 5_000_000e18);
        token1.mint(alice, 5_000_000e18);
        token0.mint(solver, 5_000_000e18);
        token1.mint(solver, 5_000_000e18);

        vm.startPrank(alice);
        token0.approve(address(router), type(uint256).max);
        token1.approve(address(router), type(uint256).max);
        vm.stopPrank();

        vm.startPrank(solver);
        token0.approve(address(router), type(uint256).max);
        token1.approve(address(router), type(uint256).max);
        vm.stopPrank();
    }

    function _nextEpoch(uint256 t) internal pure returns (uint256) {
        return ((t / INTERVAL) * INTERVAL) + INTERVAL;
    }
}

contract TwapEngineUnitTest is TwapEngineBaseTest {
    function test_constructorFailsFastOnInvalidConfig() external {
        vm.expectRevert(TwapEngine.InvalidRouter.selector);
        new TwapEngine(address(0), INTERVAL, 500, 0);

        vm.expectRevert(TwapEngine.InvalidInterval.selector);
        new TwapEngine(address(router), 0, 500, 0);

        vm.expectRevert(TwapEngine.InvalidMaxDiscountBps.selector);
        new TwapEngine(address(router), INTERVAL, 10_001, 0);
    }

    function test_segmentedAccrualHonorsStartAndExpiry() external {
        uint256 amountIn = 1_200e18;
        uint256 duration = 120;

        vm.prank(alice);
        engine.submitStream(MARKET_A, true, duration, amountIn);

        uint256 startEpoch = _nextEpoch(block.timestamp);

        vm.warp(startEpoch + 30);
        vm.prank(address(router));
        (uint256 ghost0Mid,) = engine.syncAndFetchGhost(MARKET_A);
        assertEq(ghost0Mid, 300e18, "mid-interval ghost accrual mismatch");

        vm.warp(startEpoch + duration + 30);
        vm.prank(address(router));
        (uint256 ghost0Final,) = engine.syncAndFetchGhost(MARKET_A);
        assertEq(ghost0Final, 0, "epoch-close settlement should consume residual ghost");
    }

    function test_bitmapAccrualHandlesVeryLongIdleGap() external {
        uint256 amountIn = 3_600e18;
        uint256 duration = 3_600;
        uint256 longIdle = INTERVAL * 1_000_000;

        vm.prank(alice);
        engine.submitStream(MARKET_A, true, duration, amountIn);
        uint256 startEpoch = _nextEpoch(block.timestamp);

        vm.warp(startEpoch + duration + longIdle);
        vm.prank(address(router));
        (uint256 ghost0,) = engine.syncAndFetchGhost(MARKET_A);
        assertEq(ghost0, 0, "long-idle accrual should auto-settle at stream close");

        vm.warp(block.timestamp + longIdle);
        vm.prank(address(router));
        (uint256 ghost0AfterSecondIdle,) = engine.syncAndFetchGhost(MARKET_A);
        assertEq(ghost0AfterSecondIdle, 0, "post-expiry idle time should stay settled");
    }

    function test_bitmapSharedStartPersistsOnPartialPreStartCancel() external {
        uint256 startEpoch = _nextEpoch(block.timestamp);
        uint256 amountA = 1_200e18;
        uint256 amountB = 1_800e18;
        uint256 durationA = 120;
        uint256 durationB = 180;
        uint256 expiryA = startEpoch + durationA;
        uint256 expiryB = startEpoch + durationB;

        vm.prank(alice);
        bytes32 orderA = engine.submitStream(MARKET_A, true, durationA, amountA);
        vm.prank(alice);
        engine.submitStream(MARKET_A, true, durationB, amountB);

        assertTrue(engine.isEpochEventSet(MARKET_A, startEpoch), "shared start bit should be set");
        assertTrue(engine.isEpochEventSet(MARKET_A, expiryA), "expiryA bit should be set before cancel");
        assertTrue(engine.isEpochEventSet(MARKET_A, expiryB), "expiryB bit should be set before cancel");

        vm.prank(alice);
        engine.cancelOrder(MARKET_A, orderA);

        assertTrue(engine.isEpochEventSet(MARKET_A, startEpoch), "shared start bit should remain after partial cancel");
        assertFalse(engine.isEpochEventSet(MARKET_A, expiryA), "unique expiry bit should clear after cancel");
        assertTrue(engine.isEpochEventSet(MARKET_A, expiryB), "remaining expiry bit should stay set");
    }

    function test_bitmapSharedExpiryPersistsOnMidStreamPartialCancel() external {
        uint256 startEpochA = _nextEpoch(block.timestamp);
        uint256 amountA = 2_400e18;
        uint256 amountB = 1_800e18;
        uint256 durationA = 240;
        uint256 durationB = 180;
        uint256 sharedExpiry = startEpochA + durationA;

        vm.prank(alice);
        bytes32 orderA = engine.submitStream(MARKET_A, true, durationA, amountA);

        vm.warp(startEpochA + 10);
        vm.prank(alice);
        engine.submitStream(MARKET_A, true, durationB, amountB);

        assertTrue(engine.isEpochEventSet(MARKET_A, sharedExpiry), "shared expiry bit should be set");

        vm.warp(sharedExpiry - 60);
        vm.prank(alice);
        engine.cancelOrder(MARKET_A, orderA);

        assertTrue(
            engine.isEpochEventSet(MARKET_A, sharedExpiry), "shared expiry bit should remain after partial cancel"
        );
    }

    function test_bitmapAccountsForOppositeDirectionEpochCollisions() external {
        uint256 startEpoch = _nextEpoch(block.timestamp);
        uint256 duration = 120;
        uint256 expiry = startEpoch + duration;

        vm.prank(alice);
        bytes32 orderT0 = engine.submitStream(MARKET_A, true, duration, 1_200e18);
        vm.prank(alice);
        bytes32 orderT1 = engine.submitStream(MARKET_A, false, duration, 1_200e18);

        assertTrue(engine.isEpochEventSet(MARKET_A, startEpoch), "start bit should be set");
        assertTrue(engine.isEpochEventSet(MARKET_A, expiry), "expiry bit should be set");

        vm.prank(alice);
        engine.cancelOrder(MARKET_A, orderT0);
        assertTrue(engine.isEpochEventSet(MARKET_A, startEpoch), "opposite direction start should keep bit set");
        assertTrue(engine.isEpochEventSet(MARKET_A, expiry), "opposite direction expiry should keep bit set");

        vm.prank(alice);
        engine.cancelOrder(MARKET_A, orderT1);
        assertFalse(engine.isEpochEventSet(MARKET_A, startEpoch), "start bit should clear when all events removed");
        assertFalse(engine.isEpochEventSet(MARKET_A, expiry), "expiry bit should clear when all events removed");
    }

    function test_bitmapClearsAfterCrossAndSyncIsIdempotent() external {
        uint256 startEpoch = _nextEpoch(block.timestamp);
        uint256 duration = 120;
        uint256 expiry = startEpoch + duration;

        vm.prank(alice);
        engine.submitStream(MARKET_A, true, duration, 1_200e18);

        assertTrue(engine.isEpochEventSet(MARKET_A, startEpoch), "start bit should be set before crossing");
        assertTrue(engine.isEpochEventSet(MARKET_A, expiry), "expiry bit should be set before crossing");

        vm.warp(expiry + 1);
        vm.prank(address(router));
        (uint256 ghostAfterExpiry,) = engine.syncAndFetchGhost(MARKET_A);

        assertFalse(engine.isEpochEventSet(MARKET_A, startEpoch), "start bit should clear after crossing");
        assertFalse(engine.isEpochEventSet(MARKET_A, expiry), "expiry bit should clear after crossing");

        vm.warp(block.timestamp + (INTERVAL * 100_000));
        vm.prank(address(router));
        (uint256 ghostAfterIdleSync,) = engine.syncAndFetchGhost(MARKET_A);
        assertEq(
            ghostAfterIdleSync, ghostAfterExpiry, "idempotent sync should not change ghost after all events consumed"
        );
    }

    function test_epochCloseAutoSettleAllocatesFinalGhostProceeds() external {
        uint256 amountIn = 1_200e18;
        uint256 duration = 120;
        uint256 settleOut = 333e18;

        vm.prank(alice);
        bytes32 orderId = engine.submitStream(MARKET_A, true, duration, amountIn);
        uint256 startEpoch = _nextEpoch(block.timestamp);
        uint256 expiry = startEpoch + duration;

        router.setSettleOutOverride(MARKET_A, settleOut);
        token1.mint(address(router), settleOut);

        vm.warp(expiry + 1);
        vm.prank(address(router));
        (uint256 ghost0,) = engine.syncAndFetchGhost(MARKET_A);
        assertEq(ghost0, 0, "ghost should be settled on stream close");

        uint256 aliceToken1Before = token1.balanceOf(alice);
        vm.prank(alice);
        uint256 claimed = engine.claimTokens(MARKET_A, orderId);
        assertApproxEqAbs(claimed, settleOut, 1, "expired order should receive settled proceeds");
        assertApproxEqAbs(token1.balanceOf(alice), aliceToken1Before + settleOut, 1, "claim transfer mismatch");
    }

    function test_epochCloseAutoSettleDoesNotLeakToNewStartAtSameEpoch() external {
        uint256 amountIn = 1_200e18;
        uint256 duration = 120;
        uint256 settleOut = 444e18;

        vm.prank(alice);
        bytes32 expiringOrder = engine.submitStream(MARKET_A, true, duration, amountIn);
        uint256 startA = _nextEpoch(block.timestamp);
        uint256 expiryA = startA + duration;

        vm.warp(expiryA - 1);
        vm.prank(alice);
        bytes32 startingOrder = engine.submitStream(MARKET_A, true, duration, amountIn);

        router.setSettleOutOverride(MARKET_A, settleOut);
        token1.mint(address(router), settleOut);

        vm.warp(expiryA + 1);
        vm.prank(address(router));
        engine.syncAndFetchGhost(MARKET_A);

        vm.prank(alice);
        uint256 claimedExpiring = engine.claimTokens(MARKET_A, expiringOrder);
        vm.prank(alice);
        uint256 claimedStarting = engine.claimTokens(MARKET_A, startingOrder);

        assertApproxEqAbs(claimedExpiring, settleOut, 1, "expiring order should receive close-settlement earnings");
        assertEq(claimedStarting, 0, "order starting at same epoch should not receive prior-stream settlement");
    }

    function test_partialEpochExpirySettlesBeforeRateDecrease() external {
        uint256 amountA = 1_200e18;
        uint256 amountB = 1_800e18;
        uint256 durationA = 120;
        uint256 durationB = 180;

        vm.prank(alice);
        bytes32 expiringOrder = engine.submitStream(MARKET_A, true, durationA, amountA);
        vm.prank(alice);
        bytes32 continuingOrder = engine.submitStream(MARKET_A, true, durationB, amountB);

        uint256 startEpoch = _nextEpoch(block.timestamp);
        vm.warp(startEpoch + durationA + 1);

        vm.prank(address(router));
        (uint256 ghost0,) = engine.syncAndFetchGhost(MARKET_A);
        assertEq(ghost0, 10e18, "only post-expiry continuing flow should remain as ghost");

        vm.prank(alice);
        uint256 claimedExpiring = engine.claimTokens(MARKET_A, expiringOrder);
        vm.prank(alice);
        uint256 claimedContinuing = engine.claimTokens(MARKET_A, continuingOrder);

        assertApproxEqAbs(claimedExpiring, 1_200e18, 1, "expiring order should receive accrued proceeds");
        assertApproxEqAbs(claimedContinuing, 1_200e18, 1, "continuing order should keep its accrued share");
    }

    function test_cancelLastOrderClaimsAutoSettleProceeds() external {
        uint256 amountIn = 1_200e18;
        uint256 duration = 120;
        uint256 settleOut = 250e18;

        vm.prank(alice);
        bytes32 orderId = engine.submitStream(MARKET_A, true, duration, amountIn);
        uint256 startEpoch = _nextEpoch(block.timestamp);

        router.setSettleOutOverride(MARKET_A, settleOut);
        token1.mint(address(router), settleOut);

        vm.warp(startEpoch + 30);
        vm.prank(alice);
        (uint256 refund, uint256 earnings) = engine.cancelOrder(MARKET_A, orderId);

        assertEq(refund, 900e18, "refund mismatch");
        assertApproxEqAbs(earnings, settleOut, 1, "cancel should include auto-settle earnings");
    }

    function test_partialCancelSettlesBeforeRateDecrease() external {
        uint256 amountIn = 1_200e18;
        uint256 duration = 120;

        vm.prank(alice);
        bytes32 cancelledOrder = engine.submitStream(MARKET_A, true, duration, amountIn);
        vm.prank(alice);
        bytes32 continuingOrder = engine.submitStream(MARKET_A, true, duration, amountIn);

        uint256 startEpoch = _nextEpoch(block.timestamp);
        vm.warp(startEpoch + 30);

        vm.prank(alice);
        (uint256 refund, uint256 earnings) = engine.cancelOrder(MARKET_A, cancelledOrder);
        assertEq(refund, 900e18, "refund mismatch");
        assertApproxEqAbs(earnings, 300e18, 1, "cancelled order should receive accrued proceeds");

        vm.prank(alice);
        uint256 claimedContinuing = engine.claimTokens(MARKET_A, continuingOrder);
        assertApproxEqAbs(claimedContinuing, 300e18, 1, "continuing order should keep accrued share");
    }

    function test_takeGhostRoundsInputConsumedUpForTinyFills() external {
        // 60 wei over 60 seconds => 1 wei/sec stream.
        vm.prank(alice);
        engine.submitStream(MARKET_A, false, 60, 60);
        uint256 startEpoch = _nextEpoch(block.timestamp);

        vm.warp(startEpoch + 1);
        vm.prank(address(router));
        (, uint256 ghost1Before) = engine.syncAndFetchGhost(MARKET_A);
        assertEq(ghost1Before, 1, "expected tiny ghost inventory");

        vm.prank(address(router));
        (uint256 filledOut, uint256 inputConsumed) = engine.takeGhost(MARKET_A, true, 1, 2e18);

        assertEq(filledOut, 1, "tiny ghost should fill");
        assertEq(inputConsumed, 1, "input consumed should round up to prevent free dust");
    }

    function test_getCancelOrderStateExposesCommittedAndExactViews() external {
        uint256 amountIn = 1_200e18;
        uint256 duration = 120;

        vm.prank(alice);
        bytes32 orderId = engine.submitStream(MARKET_A, true, duration, amountIn);
        uint256 startEpoch = _nextEpoch(block.timestamp);

        vm.warp(startEpoch + 30);
        (,, uint256 lastUpdateBefore,,) = engine.states(MARKET_A);

        (uint256 committedBuyOwed, uint256 committedRefund) = engine.getCancelOrderState(MARKET_A, orderId);
        (uint256 exactBuyOwed, uint256 exactRefund) = engine.getCancelOrderStateExact(MARKET_A, orderId);

        assertEq(committedBuyOwed, 0, "committed buy owed mismatch");
        assertEq(exactBuyOwed, 0, "exact buy owed mismatch");
        assertEq(committedRefund, amountIn, "committed refund should reflect unaccrued state");
        assertEq(exactRefund, 900e18, "exact refund should simulate accrual to now");

        (,, uint256 lastUpdateAfter,,) = engine.states(MARKET_A);
        assertEq(lastUpdateAfter, lastUpdateBefore, "exact preview must not mutate state");

        vm.prank(address(router));
        engine.syncAndFetchGhost(MARKET_A);
        (, uint256 committedAfterSync) = engine.getCancelOrderState(MARKET_A, orderId);
        assertEq(committedAfterSync, exactRefund, "committed preview should match exact after sync");
    }

    function test_marketScopedOrderStorageRejectsWrongMarketOnCancel() external {
        vm.prank(alice);
        bytes32 orderId = engine.submitStream(MARKET_A, true, 120, 1_200e18);

        uint256 claimedFromWrongMarket = engine.claimTokens(MARKET_B, orderId);
        assertEq(claimedFromWrongMarket, 0, "claim on wrong market should return zero");

        vm.startPrank(alice);
        vm.expectRevert(TwapEngine.OrderDoesNotExist.selector);
        engine.cancelOrder(MARKET_B, orderId);
        vm.stopPrank();
    }

    function test_clearAuctionPullsPaymentThenPaysOut() external {
        uint256 amountIn = 1_200e18;
        uint256 duration = 120;
        uint256 clearAmount = 100e18;
        uint256 expectedPayment = 200e18; // spot=2e18, no discount

        vm.prank(alice);
        engine.submitStream(MARKET_A, true, duration, amountIn);
        uint256 startEpoch = _nextEpoch(block.timestamp);
        vm.warp(startEpoch + 60);

        uint256 solverToken0Before = token0.balanceOf(solver);
        uint256 solverToken1Before = token1.balanceOf(solver);

        vm.prank(solver);
        engine.clearAuction(MARKET_A, true, clearAmount, 0);

        assertEq(token0.balanceOf(solver), solverToken0Before + clearAmount, "solver token0 payout mismatch");
        assertEq(token1.balanceOf(solver), solverToken1Before - expectedPayment, "solver token1 payment mismatch");
    }

    function test_cancelOrderRefundsOnlyUnsoldPrincipalAndClaimsAccruedProceeds() external {
        uint256 amountIn = 1_200e18;
        uint256 duration = 120;

        vm.prank(alice);
        bytes32 orderId = engine.submitStream(MARKET_A, true, duration, amountIn);
        vm.prank(alice);
        engine.submitStream(MARKET_A, true, duration, amountIn);
        uint256 startEpoch = _nextEpoch(block.timestamp);

        vm.warp(startEpoch + 30);
        vm.prank(alice);
        (uint256 refund, uint256 earnings) = engine.cancelOrder(MARKET_A, orderId);

        assertApproxEqAbs(earnings, 300e18, 1, "cancel should include accrued proceeds");
        assertEq(refund, 900e18, "refund mismatch");
    }

    function test_forceSettleIsRouterOnly() external {
        vm.expectRevert(TwapEngine.UnauthorizedRouter.selector);
        engine.forceSettle(MARKET_A, true);
    }
}

contract TwapEngineFuzzTest is TwapEngineBaseTest {
    function testFuzz_cancelRefundNeverExceedsDeposit(uint96 rawAmount, uint8 rawIntervals, uint16 rawElapsed)
        external
    {
        uint256 durationIntervals = bound(uint256(rawIntervals), 1, 30);
        uint256 duration = durationIntervals * INTERVAL;
        uint256 amountIn = bound(uint256(rawAmount), duration, 1_000_000e18);

        vm.prank(alice);
        bytes32 orderId = engine.submitStream(MARKET_A, true, duration, amountIn);
        uint256 startEpoch = _nextEpoch(block.timestamp);

        uint256 elapsed = bound(uint256(rawElapsed), 0, duration - 1);
        vm.warp(startEpoch + elapsed);

        vm.prank(alice);
        (uint256 refund,) = engine.cancelOrder(MARKET_A, orderId);
        assertLe(refund, amountIn, "refund exceeds deposit");
    }

    function testFuzz_takeGhostIsBoundedByInputAndInventory(
        uint96 rawAmount,
        uint8 rawIntervals,
        uint16 rawElapsed,
        uint128 rawAmountIn,
        uint256 rawSpotPrice
    ) external {
        uint256 durationIntervals = bound(uint256(rawIntervals), 1, 20);
        uint256 duration = durationIntervals * INTERVAL;
        uint256 amountIn = bound(uint256(rawAmount), duration, 1_000_000e18);
        uint256 spotPrice = bound(rawSpotPrice, 1e9, 1e27);

        // oneForZero stream accrues token1 ghost
        vm.prank(alice);
        engine.submitStream(MARKET_A, false, duration, amountIn);
        uint256 startEpoch = _nextEpoch(block.timestamp);

        uint256 elapsed = bound(uint256(rawElapsed), 0, duration);
        vm.warp(startEpoch + elapsed);

        vm.prank(address(router));
        (, uint256 ghost1Before) = engine.syncAndFetchGhost(MARKET_A);

        uint256 takerAmountIn = bound(uint256(rawAmountIn), 1, 1_000_000e18);
        vm.prank(address(router));
        (uint256 filledOut, uint256 inputConsumed) = engine.takeGhost(MARKET_A, true, takerAmountIn, spotPrice);

        assertLe(filledOut, ghost1Before, "fill exceeds available ghost");
        assertLe(inputConsumed, takerAmountIn, "input consumed exceeds taker budget");
    }
}
