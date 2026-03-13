#!/usr/bin/env python3
"""
Fix JitTwammParanoid.t.sol test regressions from deferred-start sellRate.

Pattern: after submit, sellRateCurrent is 0 until the next epoch crossing.
Tests that assert sellRate, accrual, or earnings immediately after submit
need _activateOrders() call inserted.
"""
import re

FILE = "/home/ubuntu/RLD/contracts/test/integration/twamm/JitTwammParanoid.t.sol"

with open(FILE, 'r') as f:
    content = f.read()

# ─── Fix 1: test_Submit_MultipleOrdersSameDirection ───
# After submitting 3 orders, asserts sellRate == sum immediately
# Fix: add _activateOrders() before the assertion
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);
        _submitOrder0For1(INTERVAL + INTERVAL, amountIn); // diff expiry
        _submitOrder0For1(INTERVAL + INTERVAL + INTERVAL, amountIn); // diff expiry

        (uint256 aggRate, ) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(aggRate, expectedAgg, "aggregate sellRate is sum");''',
    '''_submitOrder0For1(INTERVAL, amountIn);
        _submitOrder0For1(INTERVAL + INTERVAL, amountIn); // diff expiry
        _submitOrder0For1(INTERVAL + INTERVAL + INTERVAL, amountIn); // diff expiry

        _activateOrders();
        (uint256 aggRate, ) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(aggRate, expectedAgg, "aggregate sellRate is sum");'''
)

# ─── Fix 2: test_Cancel_SellRateRemoved ───
# Submits then asserts sellRate > 0 before cancel
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        (uint256 rateBefore, ) = twammHook.getStreamPool(twammPoolKey, true);
        assertTrue(rateBefore > 0, "sellRate active before cancel");''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        (uint256 rateBefore, ) = twammHook.getStreamPool(twammPoolKey, true);
        assertTrue(rateBefore > 0, "sellRate active before cancel");'''
)

# ─── Fix 3: test_Cancel_MidLife_PartialRefund ───
# Submits, warps 1 INTERVAL, cancels. With deferred start, order starts at nextEpoch
# so warping 1 INTERVAL lands exactly at startEpoch. Need to warp 2*INTERVAL.
content = content.replace(
    '''_submitOrder1For0(duration, amountIn); // opposing
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(
            duration,
            amountIn
        );

        vm.warp(block.timestamp + INTERVAL); // 1/3 elapsed''',
    '''_submitOrder1For0(duration, amountIn); // opposing
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(
            duration,
            amountIn
        );

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL); // 1/3 elapsed (from startEpoch)'''
)

# ─── Fix 4: test_Cancel_MidLife_EarnsWithOpposing ───
content = content.replace(
    '''_submitOrder1For0(duration, amountIn); // opposing
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(
            duration,
            amountIn
        );

        vm.warp(block.timestamp + INTERVAL); // mid-life

        (uint256 buyOut''',
    '''_submitOrder1For0(duration, amountIn); // opposing
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(
            duration,
            amountIn
        );

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL); // mid-life (from startEpoch)

        (uint256 buyOut'''
)

# ─── Fix 5: test_Cancel_Revert_OrderExpired ───
# Warps past expiration then cancels. With deferred start, we also need epoch crossing.
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + 2 * INTERVAL); // past expiry''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + 2 * INTERVAL); // past expiry'''
)

# ─── Fix 6: test_Engine_AccruesCorrectAmount ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL / 2);''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL / 2);'''
)

# ─── Fix 7: test_Engine_CrossEpoch_SubtractsExpired ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL + 1); // past 1st epoch
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 rateFinal, ) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(rateFinal, 0, "expired sellRate removed");''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders(); // activate the deferred sellRate
        vm.warp(block.timestamp + INTERVAL + 1); // past expiry
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 rateFinal, ) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(rateFinal, 0, "expired sellRate removed");'''
)

# ─── Fix 8: test_Engine_MultipleEpochsAtOnce ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + 3 * INTERVAL + 1);''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + 3 * INTERVAL + 1);'''
)

# ─── Fix 9: test_Sync_CreditsEarnings_Proper ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL); // one epoch
        twammHook.executeJTMOrders(twammPoolKey);

        uint256 earnings = twammHook.sync(''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL); // one epoch from startEpoch
        twammHook.executeJTMOrders(twammPoolKey);

        uint256 earnings = twammHook.sync('''
)

# ─── Fix 10: test_Sync_IdempotentDoubleSync ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        uint256 e1 = twammHook.sync(''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        uint256 e1 = twammHook.sync('''
)

# ─── Fix 11: test_Sync_AccumulatesAcrossMultiplePeriods ───
content = content.replace(
    '''_submitOrder0For1(3 * INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL);''',
    '''_submitOrder0For1(3 * INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL);'''
)

# ─── Fix 12: test_SyncAndClaim_ExpiresOrder ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.syncAndClaimTokens(''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.syncAndClaimTokens('''
)

# ─── Fix 13: test_SyncAndClaim_NoDoubleSubtract_Regression ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL + 1);

        twammHook.syncAndClaimTokens(''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);

        twammHook.syncAndClaimTokens('''
)

# ─── Fix 14: test_ClaimTokens_TransfersOwed ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: orderKey})
        );

        PoolId pid = twammPoolKey.toId();
        Currency buyCurrency = twammPoolKey.currency1;
        uint256 owed = twammHook.tokensOwed(pid, buyCurrency, address(this));
        assertTrue(owed > 0, "has tokens owed");''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: orderKey})
        );

        PoolId pid = twammPoolKey.toId();
        Currency buyCurrency = twammPoolKey.currency1;
        uint256 owed = twammHook.tokensOwed(pid, buyCurrency, address(this));
        assertTrue(owed > 0, "has tokens owed");'''
)

# ─── Fix 15: test_View_GetStreamState_IncludesPending ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL / 2);
        (uint256 a0, , , ) = twammHook.getStreamState(twammPoolKey);
        assertTrue(a0 > 0, "pending accrual in view");''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL / 2);
        (uint256 a0, , , ) = twammHook.getStreamState(twammPoolKey);
        assertTrue(a0 > 0, "pending accrual in view");'''
)

# ─── Fix 16: test_L1_NoOpposingFlow_NoNetting ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 a0, , , ) = twammHook.getStreamState(twammPoolKey);
        assertTrue(a0 > 0, "ghost balance builds with no opposing");''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 a0, , , ) = twammHook.getStreamState(twammPoolKey);
        assertTrue(a0 > 0, "ghost balance builds with no opposing");'''
)

# ─── Fix 17: test_L1_BothStreamsEarnCorrectToken ───
content = content.replace(
    '''_submitOrder1For0(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL + 1);''',
    '''_submitOrder1For0(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);'''
)

# ─── Fix 18: test_L1_Netting_AsymmetricAmounts ───
content = content.replace(
    '''_submitOrder1For0(INTERVAL, smallAmt);

        vm.warp(block.timestamp + INTERVAL + 1);''',
    '''_submitOrder1For0(INTERVAL, smallAmt);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);'''
)

# ─── Fix 19: test_L1_UnequalOrders_Leftover ───
content = content.replace(
    '''_submitOrder1For0(INTERVAL, smallAmt);

        vm.warp(block.timestamp + INTERVAL / 2);''',
    '''_submitOrder1For0(INTERVAL, smallAmt);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL / 2);'''
)

# ─── Fix 20: test_L1_Netting_AcrossMultipleEpochs ───
content = content.replace(
    '''_submitOrder1For0(3 * INTERVAL, amountIn);

        vm.warp(block.timestamp + 3 * INTERVAL + 1);''',
    '''_submitOrder1For0(3 * INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + 3 * INTERVAL + 1);'''
)

# ─── Fix 21: test_L1_NetBeforeEpoch_Regression ───
content = content.replace(
    '''_submitOrder1For0(INTERVAL, amountIn);

        // Advance to boundary — netting should have happened
        vm.warp(block.timestamp + INTERVAL);''',
    '''_submitOrder1For0(INTERVAL, amountIn);

        _activateOrders();
        // Advance to boundary — netting should have happened
        vm.warp(block.timestamp + INTERVAL);'''
)

# ─── Fix 22: test_L2_FillDecreasesAccrued ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 ghostBefore, , , ) = twammHook.getStreamState(twammPoolKey);
        assertTrue(ghostBefore > 0, "has ghost before swap");''',
    '''_submitOrder0For1(INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 ghostBefore, , , ) = twammHook.getStreamState(twammPoolKey);
        assertTrue(ghostBefore > 0, "has ghost before swap");'''
)

# ─── Fix 23: test_Ported_UnbalancedZeroForOne_10x ───
content = content.replace(
    '''_submitOrder1For0(INTERVAL, smallAmt);

        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: largeKey})
        );
        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: smallKey})
        );

        PoolId pid = twammPoolKey.toId();
        Currency c1 = twammPoolKey.currency1;
        uint256 largeOwed = twammHook.tokensOwed(pid, c1, address(this));

        assertTrue(largeOwed > 0, "unbalanced produces earnings");''',
    '''_submitOrder1For0(INTERVAL, smallAmt);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: largeKey})
        );
        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: smallKey})
        );

        PoolId pid = twammPoolKey.toId();
        Currency c1 = twammPoolKey.currency1;
        uint256 largeOwed = twammHook.tokensOwed(pid, c1, address(this));

        assertTrue(largeOwed > 0, "unbalanced produces earnings");'''
)

# ─── Fix 24: test_Ported_UnbalancedOneForZero_10x ───
content = content.replace(
    '''_submitOrder0For1(INTERVAL, smallAmt);

        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: largeKey})
        );
        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: smallKey})
        );

        PoolId pid = twammPoolKey.toId();
        Currency c0 = twammPoolKey.currency0;
        uint256 largeOwed = twammHook.tokensOwed(pid, c0, address(this));
        assertTrue(largeOwed > 0, "large 1for0 earns");''',
    '''_submitOrder0For1(INTERVAL, smallAmt);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: largeKey})
        );
        twammHook.sync(
            IJTM.SyncParams({key: twammPoolKey, orderKey: smallKey})
        );

        PoolId pid = twammPoolKey.toId();
        Currency c0 = twammPoolKey.currency0;
        uint256 largeOwed = twammHook.tokensOwed(pid, c0, address(this));
        assertTrue(largeOwed > 0, "large 1for0 earns");'''
)

# ─── Fix 25: test_Stress_10Epochs_SingleOrder ───
content = content.replace(
    '''_submitOrder0For1(10 * INTERVAL, amountIn);

        vm.warp(block.timestamp + 10 * INTERVAL + 1);''',
    '''_submitOrder0For1(10 * INTERVAL, amountIn);

        _activateOrders();
        vm.warp(block.timestamp + 10 * INTERVAL + 1);'''
)

# ─── Fix 26: test_Ported_DifferentExpirations ───
content = content.replace(
    '''_submitOrder0For1(2 * INTERVAL, amt2);

        vm.warp(block.timestamp + INTERVAL + 1);''',
    '''_submitOrder0For1(2 * INTERVAL, amt2);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);'''
)

# ─── Fix 27: test_Stress_10Actors_SameEpoch ───
# This has a loop with _submitAs and then warps
content = content.replace(
    '''vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 r0, ) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(r0, 0, "no residual 0for1");''',
    '''_activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 r0, ) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(r0, 0, "no residual 0for1");'''
)

# ─── Fix 28: Overlap tests ───
# test_Overlap_A1hr_B1hr_30minOffset
content = content.replace(
    '''vm.warp(block.timestamp + INTERVAL / 2); // 30 min later
        (, IJTM.OrderKey memory bKey) = _submitOrder0For1(''',
    '''_activateOrders();
        vm.warp(block.timestamp + INTERVAL / 2); // 30 min later
        (, IJTM.OrderKey memory bKey) = _submitOrder0For1('''
)

# test_Overlap_A3hr_B1hr_AtHour2
content = content.replace(
    '''vm.warp(block.timestamp + 2 * INTERVAL); // 2 hours later
        (, IJTM.OrderKey memory bKey) = _submitOrder0For1(''',
    '''_activateOrders();
        vm.warp(block.timestamp + 2 * INTERVAL); // 2 hours later
        (, IJTM.OrderKey memory bKey) = _submitOrder0For1('''
)

# test_Overlap_SameUserTwoOrders_DiffExpiry
content = content.replace(
    '''_submitOrder0For1(2 * INTERVAL, amt2);

        vm.warp(block.timestamp + INTERVAL + 1); // past short expiry
        twammHook.executeJTMOrders(twammPoolKey);

        IJTM.Order memory shortOrder = twammHook.getOrder(
            twammPoolKey,
            shortKey
        );
        assertEq(shortOrder.sellRate, 0, "short order deleted");''',
    '''_submitOrder0For1(2 * INTERVAL, amt2);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1); // past short expiry
        twammHook.executeJTMOrders(twammPoolKey);

        IJTM.Order memory shortOrder = twammHook.getOrder(
            twammPoolKey,
            shortKey
        );
        assertEq(shortOrder.sellRate, 0, "short order deleted");'''
)

# test_Overlap_CancelDuringOverlap
content = content.replace(
    '''_submitOrder0For1(2 * INTERVAL, amt2);

        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.cancelOrder(twammPoolKey, shortKey);

        // After cancel, only long order remains
        vm.warp(block.timestamp + 2 * INTERVAL);''',
    '''_submitOrder0For1(2 * INTERVAL, amt2);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.cancelOrder(twammPoolKey, shortKey);

        // After cancel, only long order remains
        vm.warp(block.timestamp + 2 * INTERVAL);'''
)

# ─── Fix 29: test_FullCycle_NoFundsLost_AllParticipantsWhole ───
content = content.replace(
    '''_submitOrder1For0(INTERVAL, opAmount);

        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 a0, , , ) = twammHook.getStreamState(twammPoolKey);
        assertTrue(a0 > 0, "accrued0 > 0");''',
    '''_submitOrder1For0(INTERVAL, opAmount);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 a0, , , ) = twammHook.getStreamState(twammPoolKey);
        assertTrue(a0 > 0, "accrued0 > 0");'''
)

# ─── Fix 30: test_AsymmetricOpposingStreams_ConservationHolds ───
content = content.replace(
    '''_submitOrder1For0(INTERVAL, smallSide);

        vm.warp(block.timestamp + INTERVAL + 1);''',
    '''_submitOrder1For0(INTERVAL, smallSide);

        _activateOrders();
        vm.warp(block.timestamp + INTERVAL + 1);'''
)

# ─── Fix 31: test_GapConvergence_ImbalancedFlow_AuctionFills ───
content = content.replace(
    '''_submitOrder0For1(3 * INTERVAL, largeAmount);

        // After 1 epoch, ghost0 should be building
        vm.warp(block.timestamp + INTERVAL);''',
    '''_submitOrder0For1(3 * INTERVAL, largeAmount);

        _activateOrders();
        // After 1 epoch, ghost0 should be building
        vm.warp(block.timestamp + INTERVAL);'''
)

# ─── Fix 32: test_Cancel_MidLife (in separate file) ───
# This will be handled separately

with open(FILE, 'w') as f:
    f.write(content)

print(f"File patched. Size: {len(content)} bytes")
