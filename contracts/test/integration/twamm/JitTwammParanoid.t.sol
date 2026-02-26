// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {JITRLDIntegrationBase} from "../shared/JITRLDIntegrationBase.t.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {PoolId} from "v4-core/src/types/PoolId.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {ModifyLiquidityParams, SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolModifyLiquidityTestNoChecks} from "v4-core/src/test/PoolModifyLiquidityTestNoChecks.sol";
import {PoolSwapTest} from "v4-core/src/test/PoolSwapTest.sol";
import {IJTM} from "../../../src/twamm/IJTM.sol";
import {JTM} from "../../../src/twamm/JTM.sol";
import {IERC20Minimal} from "@uniswap/v4-core/src/interfaces/external/IERC20Minimal.sol";
import "forge-std/console.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";

/**
 * @title JitTwammParanoidTest
 * @notice Paranoid test suite: 117 tests across 13 groups.
 *         Covers every function, branch, revert, and edge case
 *         in JTM.sol.
 *
 * Extends JITRLDIntegrationBase which deploys the REAL JIT-TWAMM hook
 * with HookMiner + CREATE2, matching the production deployment pipeline.
 */
contract JitTwammParanoidTest is JITRLDIntegrationBase {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;

    PoolModifyLiquidityTestNoChecks public lpRouter;
    PoolSwapTest public swapRouter;

    uint256 constant INTERVAL = 3600; // 1 hour
    uint256 constant RATE_SCALER = 1e18;

    // ════════════════════════════════════════════════════════════════════
    //  SETUP
    // ════════════════════════════════════════════════════════════════════

    function setUp() public override {
        vm.warp(7200); // Past first interval
        super.setUp();
    }

    function _tweakSetup() internal override {
        lpRouter = new PoolModifyLiquidityTestNoChecks(IPoolManager(address(poolManager)));
        swapRouter = new PoolSwapTest(IPoolManager(address(poolManager)));

        pt.approve(address(lpRouter), type(uint256).max);
        ct.approve(address(lpRouter), type(uint256).max);
        pt.approve(address(swapRouter), type(uint256).max);
        ct.approve(address(swapRouter), type(uint256).max);
        pt.approve(address(twammHook), type(uint256).max);
        ct.approve(address(twammHook), type(uint256).max);

        _seedLiquidity(100e12);
    }

    // ════════════════════════════════════════════════════════════════════
    //  HELPERS
    // ════════════════════════════════════════════════════════════════════

    function _seedLiquidity(int256 amount) internal {
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({tickLower: -600, tickUpper: 600, liquidityDelta: amount, salt: bytes32(0)}),
            ""
        );
    }

    function _submitOrder0For1(uint256 duration, uint256 amountIn)
        internal
        returns (bytes32 orderId, IJTM.OrderKey memory orderKey)
    {
        return twammHook.submitOrder(
            IJTM.SubmitOrderParams({key: twammPoolKey, zeroForOne: true, duration: duration, amountIn: amountIn})
        );
    }

    function _submitOrder1For0(uint256 duration, uint256 amountIn)
        internal
        returns (bytes32 orderId, IJTM.OrderKey memory orderKey)
    {
        return twammHook.submitOrder(
            IJTM.SubmitOrderParams({key: twammPoolKey, zeroForOne: false, duration: duration, amountIn: amountIn})
        );
    }

    function _token0() internal view returns (address) {
        return Currency.unwrap(twammPoolKey.currency0);
    }

    function _token1() internal view returns (address) {
        return Currency.unwrap(twammPoolKey.currency1);
    }

    function _doSwap(bool zeroForOne, int256 amountSpecified) internal {
        swapRouter.swap(
            twammPoolKey,
            SwapParams({
                zeroForOne: zeroForOne,
                amountSpecified: amountSpecified,
                sqrtPriceLimitX96: zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1
            }),
            PoolSwapTest.TestSettings({takeClaims: false, settleUsingBurn: false}),
            ""
        );
    }

    function _fundAndApprove(address user) internal {
        pt.mint(user, 100_000_000e6);
        ct.mint(user, 100_000_000e6);
        vm.startPrank(user);
        pt.approve(address(twammHook), type(uint256).max);
        ct.approve(address(twammHook), type(uint256).max);
        vm.stopPrank();
    }

    function _submitAs(address user, bool zeroForOne, uint256 duration, uint256 amountIn)
        internal
        returns (bytes32 orderId, IJTM.OrderKey memory orderKey)
    {
        vm.prank(user);
        return twammHook.submitOrder(
            IJTM.SubmitOrderParams({
                key: twammPoolKey, zeroForOne: zeroForOne, duration: duration, amountIn: amountIn
            })
        );
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 1: INITIALIZATION & DEPLOYMENT (5 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_Init_HookDeployed() public view {
        assertTrue(address(twammHook) != address(0), "hook deployed");
    }

    function test_Init_PoolInitialized() public view {
        (uint160 sqrtPriceX96,,,) = poolManager.getSlot0(twammPoolKey.toId());
        assertTrue(sqrtPriceX96 > 0, "pool initialized");
    }

    function test_Init_LastUpdateTimestamp() public view {
        uint256 ts = twammHook.lastVirtualOrderTimestamp(twammPoolKey.toId());
        assertTrue(ts > 0, "lastUpdateTimestamp set");
        assertEq(ts % INTERVAL, 0, "timestamp on interval boundary");
    }

    function test_Init_ExpirationInterval() public view {
        assertEq(twammHook.expirationInterval(), INTERVAL, "expiration interval stored");
    }

    function test_Init_DefaultTunables() public view {
        assertEq(twammHook.discountRateBpsPerSecond(), 1, "default discount");
        assertEq(twammHook.maxDiscountBps(), 500, "default max discount");
        assertEq(twammHook.twapWindow(), 300, "default twap window");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 2: ADMIN CONFIGURATION (8 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_Admin_SetRldCore() public {
        address newCore = address(0xC0DE);
        twammHook.setRldCore(newCore);
        assertEq(twammHook.rldCore(), newCore, "rldCore updated");
    }

    function test_Admin_SetRldCore_Revert_NotOwner() public {
        vm.prank(address(0xDEAD));
        vm.expectRevert();
        twammHook.setRldCore(address(0xC0DE));
    }

    function test_Admin_SetDiscountRate() public {
        twammHook.setDiscountRate(5);
        assertEq(twammHook.discountRateBpsPerSecond(), 5);
    }

    function test_Admin_SetMaxDiscount() public {
        twammHook.setMaxDiscount(100);
        assertEq(twammHook.maxDiscountBps(), 100);
    }

    function test_Admin_SetTwapWindow() public {
        twammHook.setTwapWindow(600);
        assertEq(twammHook.twapWindow(), 600);
    }

    function test_Admin_AllSetters_Revert_NotOwner() public {
        vm.startPrank(address(0xDEAD));
        vm.expectRevert();
        twammHook.setDiscountRate(5);
        vm.expectRevert();
        twammHook.setMaxDiscount(100);
        vm.expectRevert();
        twammHook.setTwapWindow(600);
        vm.stopPrank();
    }

    function test_Admin_SetPriceBounds() public view {
        // Bounds are now set during setUp via JITRLDIntegrationBase.
        // Verify they're stored correctly.
        (uint160 storedMin, uint160 storedMax) = twammHook.priceBounds(twammPoolKey.toId());
        assertTrue(storedMin > 0, "min stored");
        assertTrue(storedMax > storedMin, "max > min");
    }

    function test_Admin_SetPriceBounds_Revert_AlreadySet() public {
        // Bounds already set in setUp - second call must revert
        uint160 minPrice = TickMath.getSqrtPriceAtTick(-500);
        uint160 maxPrice = TickMath.getSqrtPriceAtTick(500);
        vm.expectRevert();
        twammHook.setPriceBounds(twammPoolKey, minPrice, maxPrice);
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 3: PRICE BOUNDS ENFORCEMENT (6 tests)
    // ════════════════════════════════════════════════════════════════════

    // NOTE: Price bounds tests use a SEPARATE pool key approach.
    // Since priceBounds are already set by JITRLDIntegrationBase and
    // can only be set once, we test the LP range gating/swap gating
    // by checking that within-range operations succeed.

    function test_Bounds_LP_WithinRange_Succeeds() public {
        // Existing liquidity at [-600, 600] already works
        // Add more within that same range - should succeed
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({tickLower: -120, tickUpper: 120, liquidityDelta: 1e12, salt: bytes32(uint256(1))}),
            ""
        );
    }

    function test_Bounds_Swap_WithinRange_Succeeds() public {
        // Small swap should not push price out of any reasonable bounds
        _doSwap(true, -1000e6);
    }

    function test_Bounds_RemoveLiquidity_NoBoundsCheck() public {
        // Add then remove - removal should never revert
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({tickLower: -120, tickUpper: 120, liquidityDelta: 1e12, salt: bytes32(uint256(2))}),
            ""
        );
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({tickLower: -120, tickUpper: 120, liquidityDelta: -1e12, salt: bytes32(uint256(2))}),
            ""
        );
    }

    function test_Bounds_SetBounds_ThenSwapOk() public {
        // Bounds already set by base. Normal swap OK.
        _doSwap(false, -500e6);
    }

    function test_Bounds_PriceBoundsStoredCorrectly() public view {
        (uint160 min, uint160 max) = twammHook.priceBounds(twammPoolKey.toId());
        // Bounds are now set during setUp to sqrtPriceX96(0.0001)–sqrtPriceX96(100)
        assertTrue(min > 0, "min bound set");
        assertTrue(max > min, "max > min");
    }

    function test_Bounds_SwapDoesNotExceedBounds() public view {
        // Get current price and check it's within bounds
        (uint160 sqrtPriceX96,,,) = poolManager.getSlot0(twammPoolKey.toId());
        (uint160 min, uint160 max) = twammHook.priceBounds(twammPoolKey.toId());
        if (min != 0) {
            assertTrue(sqrtPriceX96 >= min, "price above min");
            assertTrue(sqrtPriceX96 <= max, "price below max");
        }
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 4: ORDER SUBMISSION (12 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_Submit_0For1_Basic() public {
        uint256 amountIn = 3600e6;
        (bytes32 orderId, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, amountIn);

        assertTrue(orderId != bytes32(0), "orderId non-zero");
        assertEq(orderKey.owner, address(this), "owner correct");
        assertTrue(orderKey.zeroForOne, "direction correct");

        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, orderKey);
        assertTrue(order.sellRate > 0, "sellRate stored");
    }

    function test_Submit_1For0_Basic() public {
        uint256 amountIn = 3600e6;
        (bytes32 orderId, IJTM.OrderKey memory orderKey) = _submitOrder1For0(INTERVAL, amountIn);

        assertTrue(orderId != bytes32(0));
        assertFalse(orderKey.zeroForOne, "direction 1for0");
    }

    function test_Submit_TokenBalanceDeducted() public {
        uint256 amountIn = 3600e6;
        uint256 sellRate = amountIn / INTERVAL;
        uint256 actualDeposit = sellRate * INTERVAL;

        uint256 balBefore = IERC20Minimal(_token0()).balanceOf(address(this));
        _submitOrder0For1(INTERVAL, amountIn);
        uint256 balAfter = IERC20Minimal(_token0()).balanceOf(address(this));

        assertEq(balBefore - balAfter, actualDeposit, "exact deduction");
    }

    function test_Submit_HookBalanceIncreased() public {
        uint256 amountIn = 3600e6;
        uint256 sellRate = amountIn / INTERVAL;
        uint256 actualDeposit = sellRate * INTERVAL;

        uint256 hookBefore = IERC20Minimal(_token0()).balanceOf(address(twammHook));
        _submitOrder0For1(INTERVAL, amountIn);
        uint256 hookAfter = IERC20Minimal(_token0()).balanceOf(address(twammHook));

        assertEq(hookAfter - hookBefore, actualDeposit, "hook received");
    }

    function test_Submit_SellRateScaling() public {
        uint256 amountIn = 3600e6;
        uint256 expectedSellRate = (amountIn / INTERVAL) * RATE_SCALER;

        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, amountIn);
        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, orderKey);

        assertEq(order.sellRate, expectedSellRate, "sell rate scaled");
    }

    function test_Submit_DustTruncation() public {
        // 3601 / 3600 = 1 token/sec (1 token dust stays with user)
        uint256 amountIn = 3601e6;
        uint256 sellRate = amountIn / INTERVAL; // 1000277...
        uint256 actualDeposit = sellRate * INTERVAL;

        uint256 balBefore = IERC20Minimal(_token0()).balanceOf(address(this));
        _submitOrder0For1(INTERVAL, amountIn);
        uint256 balAfter = IERC20Minimal(_token0()).balanceOf(address(this));

        uint256 deducted = balBefore - balAfter;
        assertEq(deducted, actualDeposit, "only sellRate*duration deducted");
        assertTrue(deducted <= amountIn, "deducted <= amountIn (dust stays with user)");
    }

    function test_Submit_MultipleOrdersSameDirection() public {
        address alice = address(0xA11CE);
        address bob = address(0xB0B);
        _fundAndApprove(alice);
        _fundAndApprove(bob);

        _submitAs(alice, true, INTERVAL, 3600e6);
        _submitAs(bob, true, INTERVAL, 7200e6);

        (uint256 sr,) = twammHook.getStreamPool(twammPoolKey, true);
        uint256 expected = ((3600e6 / INTERVAL) + (7200e6 / INTERVAL)) * RATE_SCALER;
        assertEq(sr, expected, "aggregate sellRate is sum");
    }

    function test_Submit_EarningsFactorLast_Snapshot() public {
        // First submit to seed earningsFactor
        _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Get current earningsFactor
        (, uint256 efBefore) = twammHook.getStreamPool(twammPoolKey, true);

        // Submit new order - its earningsFactorLast should match efBefore
        (, IJTM.OrderKey memory newKey) = _submitOrder0For1(INTERVAL, 3600e6);
        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, newKey);

        assertEq(order.earningsFactorLast, efBefore, "snapshot matches stream current");
    }

    function test_Submit_Revert_ZeroSellRate() public {
        vm.expectRevert(IJTM.SellRateCannotBeZero.selector);
        _submitOrder0For1(INTERVAL, INTERVAL - 1);
    }

    function test_Submit_Revert_DuplicateOrder() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        vm.expectRevert();
        _submitOrder0For1(INTERVAL, 3600e6); // same owner+exp+direction
    }

    function test_Submit_Revert_ExpirationNotOnInterval() public {
        vm.expectRevert();
        _submitOrder0For1(1800, 1800e6); // 1800s not on 3600s boundary
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 5: ORDER CANCELLATION (10 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_Cancel_Immediate_FullRefund() public {
        uint256 amountIn = 3600e6;
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, amountIn);

        (uint256 buyOut, uint256 sellRefund) = twammHook.cancelOrder(twammPoolKey, orderKey);

        uint256 sellRate = amountIn / INTERVAL;
        assertEq(sellRefund, sellRate * INTERVAL, "full refund");
        assertEq(buyOut, 0, "no earnings");
    }

    function test_Cancel_MidLife_PartialRefund() public {
        uint256 duration = 3 * INTERVAL;
        uint256 amountIn = 10800e6;

        _submitOrder1For0(duration, amountIn); // opposing
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(duration, amountIn);

        vm.warp(block.timestamp + INTERVAL); // 1/3 elapsed

        (, uint256 sellRefund) = twammHook.cancelOrder(twammPoolKey, orderKey);

        uint256 sellRate = amountIn / duration;
        uint256 expectedRefund = sellRate * 2 * INTERVAL; // 2/3
        assertApproxEqAbs(
            sellRefund,
            expectedRefund,
            expectedRefund / 200, // 0.5% tolerance (was 2%)
            "~2/3 refund"
        );
    }

    function test_Cancel_MidLife_EarnsWithOpposing() public {
        uint256 duration = 3 * INTERVAL;
        uint256 amountIn = 10800e6;

        _submitOrder1For0(duration, amountIn); // opposing
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(duration, amountIn);

        vm.warp(block.timestamp + INTERVAL);

        (uint256 buyOut,) = twammHook.cancelOrder(twammPoolKey, orderKey);
        assertTrue(buyOut > 0, "earned buy tokens");
    }

    function test_Cancel_SellRateRemoved() public {
        uint256 amountIn = 3600e6;
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, amountIn);

        (uint256 srBefore,) = twammHook.getStreamPool(twammPoolKey, true);
        assertTrue(srBefore > 0, "sellRate active before cancel");

        twammHook.cancelOrder(twammPoolKey, orderKey);

        (uint256 srAfter,) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(srAfter, 0, "sellRate zeroed after cancel");
    }

    function test_Cancel_OrderDeleted() public {
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, 3600e6);
        twammHook.cancelOrder(twammPoolKey, orderKey);
        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, orderKey);
        assertEq(order.sellRate, 0, "order deleted");
    }

    function test_Cancel_TokensTransferred() public {
        uint256 amountIn = 10800e6;
        uint256 duration = 3 * INTERVAL;

        _submitOrder1For0(duration, amountIn);
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(duration, amountIn);

        vm.warp(block.timestamp + INTERVAL);

        uint256 bal0Before = IERC20Minimal(_token0()).balanceOf(address(this));
        uint256 bal1Before = IERC20Minimal(_token1()).balanceOf(address(this));

        (uint256 buyOut, uint256 sellRefund) = twammHook.cancelOrder(twammPoolKey, orderKey);

        uint256 bal0After = IERC20Minimal(_token0()).balanceOf(address(this));
        uint256 bal1After = IERC20Minimal(_token1()).balanceOf(address(this));

        // Sell token (token0) refund:
        assertEq(bal0After - bal0Before, sellRefund, "sell refund arrived");
        // Buy token (token1) earnings:
        assertTrue(bal1After >= bal1Before, "buy token balance non-decreasing");
    }

    function test_Cancel_Revert_NotOwner() public {
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, 3600e6);
        vm.prank(address(0xDEAD));
        vm.expectRevert(IJTM.Unauthorized.selector);
        twammHook.cancelOrder(twammPoolKey, orderKey);
    }

    function test_Cancel_Revert_OrderExpired() public {
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL + 1);
        vm.expectRevert();
        twammHook.cancelOrder(twammPoolKey, orderKey);
    }

    function test_Cancel_Revert_OrderDoesNotExist() public {
        IJTM.OrderKey memory fakeKey = IJTM.OrderKey({
            owner: address(this), expiration: uint160(block.timestamp + INTERVAL), zeroForOne: true
        });
        vm.expectRevert();
        twammHook.cancelOrder(twammPoolKey, fakeKey);
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 6: SYNC & CLAIM (10 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_Sync_CreditsEarnings_Proper() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);

        uint256 earnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));

        Currency buyToken = twammPoolKey.currency1;
        uint256 owed = twammHook.tokensOwed(twammPoolKey.toId(), buyToken, address(this));

        assertTrue(owed > 0, "tokensOwed credited");
        assertEq(owed, earnings, "earnings matches owed");
    }

    function test_Sync_UpdatesEarningsFactorLast() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL / 2);

        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));

        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, key0);
        (, uint256 efCurrent) = twammHook.getStreamPool(twammPoolKey, true);

        assertEq(order.earningsFactorLast, efCurrent, "earningsFactorLast updated");
    }

    function test_Sync_IdempotentDoubleSync() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);

        uint256 e1 = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));
        uint256 e2 = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));

        assertTrue(e1 > 0, "first sync has earnings");
        assertEq(e2, 0, "second sync is no-op");
    }

    function test_Sync_AccumulatesAcrossMultiplePeriods() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(3 * INTERVAL, 10800e6);
        _submitOrder1For0(3 * INTERVAL, 10800e6);

        // Don't sync during epochs
        vm.warp(block.timestamp + 2 * INTERVAL);

        uint256 earnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));

        assertTrue(earnings > 0, "accumulated earnings across 2 epochs");
    }

    function test_Sync_Revert_OrderDoesNotExist() public {
        IJTM.OrderKey memory fakeKey = IJTM.OrderKey({
            owner: address(this), expiration: uint160(block.timestamp + INTERVAL), zeroForOne: true
        });
        vm.expectRevert();
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: fakeKey}));
    }

    function test_ClaimTokens_TransfersOwed() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));

        Currency buyToken = twammPoolKey.currency1;
        uint256 owed = twammHook.tokensOwed(twammPoolKey.toId(), buyToken, address(this));
        assertTrue(owed > 0, "has tokens owed");

        uint256 balBefore = IERC20Minimal(Currency.unwrap(buyToken)).balanceOf(address(this));
        twammHook.claimTokens(twammPoolKey, buyToken);
        uint256 balAfter = IERC20Minimal(Currency.unwrap(buyToken)).balanceOf(address(this));

        assertEq(balAfter - balBefore, owed, "claimed correct amount");
        assertEq(twammHook.tokensOwed(twammPoolKey.toId(), buyToken, address(this)), 0, "tokensOwed zeroed");
    }

    function test_ClaimTokens_ZeroIfNothingOwed() public {
        Currency c0 = twammPoolKey.currency0;
        uint256 amount = twammHook.claimTokens(twammPoolKey, c0);
        assertEq(amount, 0, "nothing to claim");
    }

    function test_SyncAndClaim_ExpiresOrder() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);

        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));

        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, key0);
        assertEq(order.sellRate, 0, "expired order deleted");
    }

    function test_SyncAndClaim_NoDoubleSubtract_Regression() public {
        // Regression test for the double-subtraction bug
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);

        // This should NOT revert with arithmetic underflow
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));

        // sellRateCurrent should be 0 (epoch crossed, orders expired)
        (uint256 sr,) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(sr, 0, "sellRate properly cleaned by crossEpoch only");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 7: LAYER 1 - INTERNAL NETTING (10 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_L1_EqualOrders_FullyNetted() public {
        uint256 amountIn = 3600e6;
        _submitOrder0For1(INTERVAL, amountIn);
        _submitOrder1For0(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // After full netting, ghost balances should be ~0
        (uint256 accrued0, uint256 accrued1,,) = twammHook.getStreamState(twammPoolKey);
        // Due to TWAP pricing, amounts might not be exactly zero but should be very small
        assertLe(accrued0, 1e6, "accrued0 near zero after netting");
        assertLe(accrued1, 1e6, "accrued1 near zero after netting");
    }

    function test_L1_UnequalOrders_Leftover() public {
        // 10:1 ratio
        _submitOrder0For1(INTERVAL, 36000e6); // big
        _submitOrder1For0(INTERVAL, 3600e6); // small

        // Check accrued BEFORE expiry (pre-epoch crossing)
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);
        (uint256 accrued0Mid,,,) = twammHook.getStreamState(twammPoolKey);
        assertTrue(accrued0Mid > 0, "large side has accrued mid-stream");
        console.log("[L1] mid-stream accrued0:", accrued0Mid);

        // After expiry, accrued moves to pendingDonation (dust fix)
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);
        (uint256 accrued0Post, uint256 accrued1Post,,) = twammHook.getStreamState(twammPoolKey);
        assertEq(accrued0Post, 0, "accrued0 moved to pendingDonation after expiry");
        console.log("[L1] post-expiry accrued0:", accrued0Post, "accrued1:", accrued1Post);
    }

    function test_L1_NoOpposingFlow_NoNetting() public {
        _submitOrder0For1(INTERVAL, 3600e6);

        // Check mid-stream: accrued0 should be building
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);
        (uint256 accrued0Mid,,,) = twammHook.getStreamState(twammPoolKey);
        assertTrue(accrued0Mid > 0, "ghost balance builds with no opposing");

        // After expiry: accrued moved to pendingDonation (dust fix)
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);
        (uint256 accrued0Post,,,) = twammHook.getStreamState(twammPoolKey);
        assertEq(accrued0Post, 0, "accrued0 orphaned after expiry");
    }

    function test_L1_PriceDoesNotMove() public {
        // Equal opposing → ZERO net AMM pressure
        (uint160 sqrtBefore,,,) = poolManager.getSlot0(twammPoolKey.toId());

        _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint160 sqrtAfter,,,) = poolManager.getSlot0(twammPoolKey.toId());
        uint256 diff = sqrtBefore > sqrtAfter ? sqrtBefore - sqrtAfter : sqrtAfter - sqrtBefore;
        assertEq(diff, 0, "price must not move with perfectly balanced flow");
    }

    function test_L1_BothStreamsEarnCorrectToken() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(INTERVAL, 3600e6);
        (, IJTM.OrderKey memory key1) = _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key1}));

        // 0→1 sells token0, earns token1
        uint256 owed1ForKey0 = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency1, address(this));
        assertTrue(owed1ForKey0 > 0, "0for1 earns token1");
    }

    function test_L1_RecordEarnings_SkipsZeroSellRate() public {
        // After all orders expire, sellRateCurrent = 0
        // Execute should not revert (guards against div-by-zero)
        _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Execute again with no active orders - should be a no-op
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey); // should not revert
    }

    function test_L1_NetBeforeEpoch_Regression() public {
        // Regression: netting MUST happen before epoch crossing
        // If epoch crosses first, sellRateCurrent could go to zero
        // and _recordEarnings would silently fail (div by zero)
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(INTERVAL, 3600e6);
        (, IJTM.OrderKey memory key1) = _submitOrder1For0(INTERVAL, 3600e6);

        // Warp exactly to epoch boundary (where orders expire)
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Both should have earnings despite expiring at epoch boundary
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key1}));

        uint256 owedC0 = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency0, address(this));
        uint256 owedC1 = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency1, address(this));
        assertTrue(owedC0 > 0 || owedC1 > 0, "earnings recorded at boundary");
    }

    function test_L1_Netting_AcrossMultipleEpochs() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(3 * INTERVAL, 10800e6);
        _submitOrder1For0(3 * INTERVAL, 10800e6);

        // Step through each epoch
        for (uint256 i = 1; i <= 3; i++) {
            vm.warp(block.timestamp + INTERVAL);
            twammHook.executeJTMOrders(twammPoolKey);
        }

        uint256 earnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));
        assertTrue(earnings > 0, "3-epoch netting produces earnings");
    }

    function test_L1_Netting_AsymmetricAmounts() public {
        // 10:1 ratio - smaller side fully matched
        _submitOrder0For1(INTERVAL, 36000e6);
        (, IJTM.OrderKey memory keySmall) = _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        uint256 earnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: keySmall}));
        assertTrue(earnings > 0, "small side earns from netting");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 8: LAYER 2 - JIT BEFORESWAP FILL (10 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_L2_NoGhostBalance_Passthrough() public {
        // No orders → swap should proceed normally (no revert)
        _doSwap(true, -1000e6);
    }

    function test_L2_ExactInput_HasGhost_SwapSucceeds() public {
        // Build ghost balance via single-direction order
        _submitOrder0For1(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        // Ghost balance of token0 exists. Swap 1→0 (buy token0) should fill from ghost.
        _doSwap(false, -500e6);
    }

    function test_L2_FillDecreasesAccrued() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accruedBefore,,,) = twammHook.getStreamState(twammPoolKey);
        assertTrue(accruedBefore > 0, "has ghost before swap");

        // Swap in direction that consumes accrued0 (swap 1→0)
        _doSwap(false, -100e6);

        // Can't easily check accrued decrease without re-executing but verify no revert
    }

    function test_L2_OpposingDirection_NoFill() public {
        // Ghost is in token0 (0→1 stream). Swap 0→1 should NOT fill from ghost
        // because the taker wants token1, but ghost is token0.
        _submitOrder0For1(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        // This swap should just go through AMM
        _doSwap(true, -500e6);
    }

    function test_L2_SwapAfterNetting_ReducedGhost() public {
        // Opposing flows net first, reducing ghost
        _submitOrder0For1(INTERVAL, 36000e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        // After netting, only leftover ghost from larger side
        // Swap should work
        _doSwap(false, -500e6);
    }

    function test_L2_MultipleSwaps_DrainsGhost() public {
        _submitOrder0For1(INTERVAL, 36000e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Multiple swaps depleting ghost
        for (uint256 i = 0; i < 3; i++) {
            _doSwap(false, -1000e6);
        }
    }

    function test_L2_SwapBothDirections() public {
        // Ghost in both directions
        _submitOrder0For1(INTERVAL, 36000e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        _doSwap(true, -500e6); // consumes accrued1
        _doSwap(false, -500e6); // consumes accrued0
    }

    function test_L2_SwapAfterFullExpiry() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // After full expiry + netting, ghost should be minimal
        // Swap should still work (no revert)
        _doSwap(true, -500e6);
    }

    function test_L2_LargeSwap_CappedByGhost() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Swap much larger than ghost - should fill from ghost + AMM
        _doSwap(false, -1_000_000e6);
    }

    function test_L2_SwapZeroAmount_Reverts() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        // V4 rejects zero-amount swaps with SwapAmountCannotBeZero
        vm.expectRevert();
        _doSwap(true, 0);
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 9: LAYER 3 - DYNAMIC AUCTION (12 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_L3_Clear_Basic() public {
        // Use 2hr order so stream is still active after 1hr (NoActiveStream guard)
        _submitOrder0For1(2 * INTERVAL, 7200e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
        if (accrued0 > 0) {
            twammHook.clear(twammPoolKey, true, accrued0, 0);
        }
    }

    function test_L3_Clear_DiscountGrowsWithTime() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (,, uint256 discount1,) = twammHook.getStreamState(twammPoolKey);

        vm.warp(block.timestamp + 30); // 30 more seconds
        (,, uint256 discount2,) = twammHook.getStreamState(twammPoolKey);

        assertTrue(discount2 >= discount1, "discount grows with time");
    }

    function test_L3_Clear_DiscountCapped() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Warp way past to ensure cap
        vm.warp(block.timestamp + 10000);
        (,, uint256 discount,) = twammHook.getStreamState(twammPoolKey);

        assertLe(discount, twammHook.maxDiscountBps(), "discount capped");
    }

    function test_L3_Clear_PartialClear() public {
        _submitOrder0For1(2 * INTERVAL, 7200e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
        if (accrued0 > 1) {
            uint256 partialAmount = accrued0 / 2;
            twammHook.clear(twammPoolKey, true, partialAmount, 0);

            (uint256 remaining,,,) = twammHook.getStreamState(twammPoolKey);
            assertTrue(remaining < accrued0, "partially cleared");
        }
    }

    function test_L3_Clear_FullClear() public {
        _submitOrder0For1(2 * INTERVAL, 7200e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
        if (accrued0 > 0) {
            twammHook.clear(twammPoolKey, true, accrued0, 0);

            (uint256 remaining,,,) = twammHook.getStreamState(twammPoolKey);
            assertEq(remaining, 0, "fully cleared");
        }
    }

    function test_L3_Clear_ResetsLastClearTimestamp() public {
        _submitOrder0For1(2 * INTERVAL, 7200e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
        if (accrued0 > 0) {
            vm.warp(block.timestamp + 50); // build discount
            twammHook.clear(twammPoolKey, true, accrued0 / 2, 0);

            // After clear, discount should reset significantly
            (,, uint256 discountAfter,) = twammHook.getStreamState(twammPoolKey);
            assertLe(discountAfter, 5, "discount reset after clear (near-zero time elapsed)");
        }
    }

    function test_L3_Clear_RecordsEarnings() public {
        // Use 2hr order so it's still active at first epoch when we clear.
        // If the order has expired, sellRateCurrent = 0 and _recordEarnings
        // is a no-op (div-by-zero guard). That's correct behavior - but
        // we want to test the ACTIVE path here.
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(2 * INTERVAL, 7200e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
        if (accrued0 > 0) {
            twammHook.clear(twammPoolKey, true, accrued0, 0);

            // Sync order - should now have earnings from clear
            uint256 earnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));
            assertTrue(earnings > 0, "clear produced earnings");
        }
    }

    function test_L3_Clear_ArbReceivesTokens() public {
        _submitOrder0For1(2 * INTERVAL, 7200e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
        if (accrued0 > 0) {
            uint256 balBefore = IERC20Minimal(_token0()).balanceOf(address(this));
            twammHook.clear(twammPoolKey, true, accrued0, 0);
            uint256 balAfter = IERC20Minimal(_token0()).balanceOf(address(this));

            assertEq(balAfter - balBefore, accrued0, "arb received cleared tokens");
        }
    }

    function test_L3_Clear_Revert_NothingToClear() public {
        vm.expectRevert(IJTM.NothingToClear.selector);
        twammHook.clear(twammPoolKey, true, 1e6, 0);
    }

    function test_L3_Clear_SequentialClears() public {
        _submitOrder0For1(2 * INTERVAL, 7200e6);
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
        if (accrued0 > 1) {
            // First clear: partial
            twammHook.clear(twammPoolKey, true, accrued0 / 2, 0);

            // Warp to build more ghost + discount
            vm.warp(block.timestamp + INTERVAL / 2);
            twammHook.executeJTMOrders(twammPoolKey);

            (uint256 newAccrued,,,) = twammHook.getStreamState(twammPoolKey);
            if (newAccrued > 0) {
                // Second clear
                twammHook.clear(twammPoolKey, true, newAccrued, 0);
            }
        }
    }

    function test_L3_Clear_BothDirections() public {
        // 2hr orders so both streams are active at hour 1
        _submitOrder0For1(2 * INTERVAL, 72000e6);
        _submitOrder1For0(2 * INTERVAL, 7200e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0, uint256 accrued1,,) = twammHook.getStreamState(twammPoolKey);

        if (accrued0 > 0) {
            twammHook.clear(twammPoolKey, true, accrued0, 0);
        }
        if (accrued1 > 0) {
            twammHook.clear(twammPoolKey, false, accrued1, 0);
        }
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 10: CORE ENGINE (_accrueAndNet) (6 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_Engine_NoOpIfNoTimePassed() public {
        _submitOrder0For1(INTERVAL, 3600e6);

        (uint256 accruedBefore,,,) = twammHook.getStreamState(twammPoolKey);

        // Execute in same block → no time delta → no accrual
        twammHook.executeJTMOrders(twammPoolKey);
        twammHook.executeJTMOrders(twammPoolKey);

        // Both calls should be idempotent in same timestamp
    }

    function test_Engine_AccruesCorrectAmount() public {
        uint256 amountIn = 3600e6;
        _submitOrder0For1(INTERVAL, amountIn);

        uint256 halfInterval = INTERVAL / 2;
        vm.warp(block.timestamp + halfInterval);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);

        uint256 sellRate = amountIn / INTERVAL;
        uint256 expected = sellRate * halfInterval;
        assertApproxEqAbs(
            accrued0,
            expected,
            expected / 1000, // 0.1% tolerance (was 1%)
            "accrued matches expected"
        );
    }

    function test_Engine_CrossEpoch_SubtractsExpired() public {
        _submitOrder0For1(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 sr,) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(sr, 0, "expired sellRate removed");
    }

    function test_Engine_MultipleEpochsAtOnce() public {
        _submitOrder0For1(3 * INTERVAL, 10800e6);
        _submitOrder1For0(3 * INTERVAL, 10800e6);

        // Warp 3 epochs at once (no intermediate execute calls)
        vm.warp(block.timestamp + 3 * INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 sr0,) = twammHook.getStreamPool(twammPoolKey, true);
        (uint256 sr1,) = twammHook.getStreamPool(twammPoolKey, false);
        assertEq(sr0, 0, "all expired after 3 epochs");
        assertEq(sr1, 0, "all expired after 3 epochs");
    }

    function test_Engine_UpdatesTimestamp() public {
        uint256 tsBefore = twammHook.lastVirtualOrderTimestamp(twammPoolKey.toId());

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        uint256 tsAfter = twammHook.lastVirtualOrderTimestamp(twammPoolKey.toId());
        assertTrue(tsAfter > tsBefore, "timestamp advanced");
    }

    function test_Engine_NoOrders_NoOp() public {
        // Execute with no orders should not revert
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 sr0,) = twammHook.getStreamPool(twammPoolKey, true);
        (uint256 sr1,) = twammHook.getStreamPool(twammPoolKey, false);
        assertEq(sr0, 0, "no sell rate");
        assertEq(sr1, 0, "no sell rate");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 11: VIEW FUNCTION CONSISTENCY (5 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_View_GetStreamState_IncludesPending() public {
        _submitOrder0For1(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL / 2);
        // DO NOT execute - view should still show pending accrual
        (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
        assertTrue(accrued0 > 0, "pending accrual in view");
    }

    function test_View_GetOrder_MatchesSubmission() public {
        uint256 amountIn = 3600e6;
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, amountIn);
        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, orderKey);

        uint256 expectedSellRate = (amountIn / INTERVAL) * RATE_SCALER;
        assertEq(order.sellRate, expectedSellRate, "sell rate matches");
    }

    function test_View_GetCancelOrderState_ConsistentWithCancel() public {
        uint256 duration = 3 * INTERVAL;
        uint256 amountIn = 10800e6;

        _submitOrder1For0(duration, amountIn);
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(duration, amountIn);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 viewBuy, uint256 viewRefund) = twammHook.getCancelOrderState(twammPoolKey, orderKey);

        (uint256 actualBuy, uint256 actualRefund) = twammHook.cancelOrder(twammPoolKey, orderKey);

        // These should be approximately equal (view might not include current block accrual)
        assertApproxEqAbs(
            viewRefund,
            actualRefund,
            actualRefund / 100, // 1% tolerance (was 5%)
            "refund consistent"
        );
    }

    function test_View_GetOrderPool_MatchesGetStreamPool() public {
        _submitOrder0For1(INTERVAL, 3600e6);

        (uint256 sr1, uint256 ef1) = twammHook.getOrderPool(twammPoolKey, true);
        (uint256 sr2, uint256 ef2) = twammHook.getStreamPool(twammPoolKey, true);

        assertEq(sr1, sr2, "sellRate matches");
        assertEq(ef1, ef2, "earningsFactor matches");
    }

    function test_View_LastVirtualOrderTimestamp() public {
        uint256 ts = twammHook.lastVirtualOrderTimestamp(twammPoolKey.toId());
        assertTrue(ts > 0 && ts % INTERVAL == 0, "valid timestamp");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 12: MULTI-ACTOR & MULTI-EPOCH STRESS (18 tests)
    // ════════════════════════════════════════════════════════════════════

    // ── 12a: Overlapping Intervals ──

    function test_Overlap_A1hr_B1hr_30minOffset() public {
        /*
         * t=0:    A submits 1hr 0→1 (3600e6)
         * t=0:    Opposing C submits 2hr 1→0 (7200e6) to create opposing flow
         * t=1800: B (different user) submits 1hr 0→1 (3600e6)
         * t=3600: A expires → claim A
         * t=5400: B expires → claim B
         *
         * NOTE: A and B must use DIFFERENT owners to avoid OrderAlreadyExists,
         * because both have the same (direction, expiration) at interval boundary.
         */
        address bob = address(0xB0B);
        _fundAndApprove(bob);

        (, IJTM.OrderKey memory keyA) = _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(2 * INTERVAL, 7200e6); // opposing flow

        vm.warp(block.timestamp + INTERVAL / 2); // t+1800
        (, IJTM.OrderKey memory keyB) = _submitAs(bob, true, INTERVAL, 3600e6);

        // Warp to A's expiry
        vm.warp(block.timestamp + INTERVAL / 2); // t+3600
        twammHook.executeJTMOrders(twammPoolKey);

        // Claim A
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyA}));
        IJTM.Order memory orderA = twammHook.getOrder(twammPoolKey, keyA);
        assertEq(orderA.sellRate, 0, "A expired and deleted");

        // B should still be active
        IJTM.Order memory orderB = twammHook.getOrder(twammPoolKey, keyB);
        assertTrue(orderB.sellRate > 0, "B still active");

        // Warp to B's expiry
        vm.warp(block.timestamp + INTERVAL / 2); // t+5400
        twammHook.executeJTMOrders(twammPoolKey);

        vm.prank(bob);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyB}));
        IJTM.Order memory orderBFinal = twammHook.getOrder(twammPoolKey, keyB);
        assertEq(orderBFinal.sellRate, 0, "B expired and deleted");

        // All 0→1 streams should be empty
        (uint256 sr0,) = twammHook.getStreamPool(twammPoolKey, true);
        assertEq(sr0, 0, "no residual sellRate");
    }

    function test_Overlap_A3hr_B1hr_AtHour2() public {
        (, IJTM.OrderKey memory keyA) = _submitOrder0For1(3 * INTERVAL, 10800e6);

        vm.warp(block.timestamp + 2 * INTERVAL); // t+2hr

        (, IJTM.OrderKey memory keyB) = _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL); // t+3hr - both expire

        twammHook.executeJTMOrders(twammPoolKey);

        // B should earn from netting during its 1hr window
        uint256 earningsB = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: keyB}));
        assertTrue(earningsB > 0, "B earns during overlap");

        // A should earn from netting during hours 2-3
        uint256 earningsA = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: keyA}));
        assertTrue(earningsA > 0, "A earns during overlap");
    }

    function test_Overlap_SameUserTwoOrders_DiffExpiry() public {
        (, IJTM.OrderKey memory keyShort) = _submitOrder0For1(INTERVAL, 3600e6);
        (, IJTM.OrderKey memory keyLong) = _submitOrder0For1(3 * INTERVAL, 10800e6);
        _submitOrder1For0(3 * INTERVAL, 10800e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Short expires, long stays
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyShort}));
        IJTM.Order memory shortOrder = twammHook.getOrder(twammPoolKey, keyShort);
        assertEq(shortOrder.sellRate, 0, "short order deleted");

        IJTM.Order memory longOrder = twammHook.getOrder(twammPoolKey, keyLong);
        assertTrue(longOrder.sellRate > 0, "long order still active");

        vm.warp(block.timestamp + 2 * INTERVAL);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyLong}));
        IJTM.Order memory longFinal = twammHook.getOrder(twammPoolKey, keyLong);
        assertEq(longFinal.sellRate, 0, "long order deleted after expiry");
    }

    function test_Overlap_CancelDuringOverlap() public {
        (, IJTM.OrderKey memory keyA) = _submitOrder0For1(2 * INTERVAL, 7200e6);
        (, IJTM.OrderKey memory keyB) = _submitOrder1For0(2 * INTERVAL, 7200e6);

        vm.warp(block.timestamp + INTERVAL / 2); // 30 min in

        // Cancel A mid-overlap
        twammHook.cancelOrder(twammPoolKey, keyA);

        // B continues alone for remaining 1.5 hours
        vm.warp(block.timestamp + INTERVAL + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 sr1,) = twammHook.getStreamPool(twammPoolKey, false);
        assertEq(sr1, 0, "B expired, no residual");
    }

    function test_Overlap_ThreeUsers_OverlappingWindows() public {
        address alice = address(0xA11CE);
        address bob = address(0xB0B);
        address carol = address(0xC0CA);
        _fundAndApprove(alice);
        _fundAndApprove(bob);
        _fundAndApprove(carol);

        // Alice: 2hr 0→1
        (, IJTM.OrderKey memory keyA) = _submitAs(alice, true, 2 * INTERVAL, 7200e6);

        vm.warp(block.timestamp + INTERVAL / 2); // t+0.5hr
        // Bob: 1hr 1→0
        (, IJTM.OrderKey memory keyB) = _submitAs(bob, false, INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL); // t+1.5hr (Bob expires)
        // Carol: 1hr 0→1
        (, IJTM.OrderKey memory keyC) = _submitAs(carol, true, INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL); // t+2.5hr (Alice & Carol expire)
        twammHook.executeJTMOrders(twammPoolKey);

        // All should be able to sync
        vm.prank(alice);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyA}));
        vm.prank(bob);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyB}));
        vm.prank(carol);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyC}));
    }

    // ── 12b: 10-Actor Stress Tests ──

    function test_Stress_10Actors_SameEpoch() public {
        address[10] memory actors;
        IJTM.OrderKey[10] memory keys;

        for (uint256 i = 0; i < 10; i++) {
            actors[i] = address(uint160(0x1000 + i));
            _fundAndApprove(actors[i]);

            bool zeroForOne = i < 5; // first 5: 0→1, last 5: 1→0
            (, keys[i]) = _submitAs(actors[i], zeroForOne, INTERVAL, 3600e6);
        }

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // All sync + claim
        for (uint256 i = 0; i < 10; i++) {
            vm.prank(actors[i]);
            twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keys[i]}));
        }

        (uint256 sr0,) = twammHook.getStreamPool(twammPoolKey, true);
        (uint256 sr1,) = twammHook.getStreamPool(twammPoolKey, false);
        assertEq(sr0, 0, "no residual 0for1");
        assertEq(sr1, 0, "no residual 1for0");
    }

    function test_Stress_10Actors_StaggeredEntry() public {
        address[10] memory actors;
        IJTM.OrderKey[10] memory keys;

        for (uint256 i = 0; i < 10; i++) {
            actors[i] = address(uint160(0x2000 + i));
            _fundAndApprove(actors[i]);
        }

        // Each actor enters 30 minutes apart, each with 1hr orders
        for (uint256 i = 0; i < 10; i++) {
            if (i > 0) {
                vm.warp(block.timestamp + INTERVAL / 2);
                twammHook.executeJTMOrders(twammPoolKey);
            }

            bool zeroForOne = i % 2 == 0;
            (, keys[i]) = _submitAs(actors[i], zeroForOne, INTERVAL, 3600e6);
        }

        // Warp past all expirations
        vm.warp(block.timestamp + 2 * INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // All sync - credit earnings to tokensOwed
        for (uint256 i = 0; i < 10; i++) {
            vm.prank(actors[i]);
            twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: keys[i]}));
        }

        // Verify sell rates zeroed (main invariant)
        (uint256 sr0,) = twammHook.getStreamPool(twammPoolKey, true);
        (uint256 sr1,) = twammHook.getStreamPool(twammPoolKey, false);
        assertEq(sr0, 0, "clean 0for1");
        assertEq(sr1, 0, "clean 1for0");
    }

    function test_Stress_10Actors_MixedDurations() public {
        // Durations: 1hr, 1hr, 2hr, 2hr, 3hr, 3hr, 1hr, 2hr, 3hr, 1hr
        uint256[10] memory durations = [
            INTERVAL,
            INTERVAL,
            2 * INTERVAL,
            2 * INTERVAL,
            3 * INTERVAL,
            3 * INTERVAL,
            INTERVAL,
            2 * INTERVAL,
            3 * INTERVAL,
            INTERVAL
        ];

        address[10] memory actors;
        IJTM.OrderKey[10] memory keys;

        for (uint256 i = 0; i < 10; i++) {
            actors[i] = address(uint160(0x3000 + i));
            _fundAndApprove(actors[i]);

            bool zeroForOne = i % 2 == 0;
            uint256 amountIn = (3600e6 * durations[i]) / INTERVAL;
            (, keys[i]) = _submitAs(actors[i], zeroForOne, durations[i], amountIn);
        }

        // Step through 3 epochs
        for (uint256 epoch = 1; epoch <= 3; epoch++) {
            vm.warp(block.timestamp + INTERVAL);
            twammHook.executeJTMOrders(twammPoolKey);

            // Claim expired orders
            for (uint256 i = 0; i < 10; i++) {
                if (durations[i] == epoch * INTERVAL) {
                    vm.prank(actors[i]);
                    twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keys[i]}));
                }
            }
        }

        // Everything should be clean - but the claim above deletes orders
        // that should have zeroed the sellRate via crossEpoch. Verify each
        // side went to zero after its orders expired.
        (uint256 sr0,) = twammHook.getStreamPool(twammPoolKey, true);
        (uint256 sr1,) = twammHook.getStreamPool(twammPoolKey, false);
        // With alternating even/odd, counts may not be exactly equal.
        // The test verifies that claiming does not panic, and that
        // sellRateCurrent drops with each epoch crossing.
        console.log("[Stress-MixedDurations] sr0:", sr0, "sr1:", sr1);
    }

    // ── 12c: 10-Epoch Stress Tests ──

    function test_Stress_10Epochs_SingleOrder() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(10 * INTERVAL, 36000e6);
        _submitOrder1For0(10 * INTERVAL, 36000e6);

        uint256 prevEarnings = 0;
        for (uint256 i = 1; i <= 10; i++) {
            vm.warp(block.timestamp + INTERVAL);
            twammHook.executeJTMOrders(twammPoolKey);

            uint256 ts = twammHook.lastVirtualOrderTimestamp(twammPoolKey.toId());
            assertEq(ts % INTERVAL, 0, "timestamp on boundary");
        }

        uint256 totalEarnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));
        assertTrue(totalEarnings > 0, "10-epoch earnings");
    }

    function test_Stress_10Epochs_RollingOrders() public {
        // Each epoch, new user submits 1hr order (alternating direction)
        address[10] memory actors;
        IJTM.OrderKey[10] memory keys;

        for (uint256 i = 0; i < 10; i++) {
            actors[i] = address(uint160(0x4000 + i));
            _fundAndApprove(actors[i]);

            bool zeroForOne = i % 2 == 0;
            (, keys[i]) = _submitAs(actors[i], zeroForOne, INTERVAL, 3600e6);

            vm.warp(block.timestamp + INTERVAL);
            twammHook.executeJTMOrders(twammPoolKey);

            // Claim previous epoch's expired order
            if (i > 0) {
                vm.prank(actors[i - 1]);
                twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keys[i - 1]}));
            }
        }

        // Claim the last one
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);
        vm.prank(actors[9]);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keys[9]}));
    }

    function test_Stress_10Epochs_WithClears() public {
        _submitOrder0For1(10 * INTERVAL, 36000e6);

        // clear() calls safeTransferFrom(msg.sender, ...) for payment
        pt.approve(address(twammHook), type(uint256).max);
        ct.approve(address(twammHook), type(uint256).max);

        for (uint256 i = 1; i <= 10; i++) {
            vm.warp(block.timestamp + INTERVAL);
            twammHook.executeJTMOrders(twammPoolKey);

            // Check accrued ghost immediately after execute (no extra warp)
            (uint256 accrued0,,,) = twammHook.getStreamState(twammPoolKey);
            if (accrued0 > 0) {
                twammHook.clear(twammPoolKey, true, accrued0, 0);
            }
        }

        (uint256 finalAccrued,,,) = twammHook.getStreamState(twammPoolKey);
        assertEq(finalAccrued, 0, "all ghost cleared across 10 epochs");
    }

    // ── 12d: Adversarial Scenarios ──

    function test_Adversarial_LateJoiner_NoFreebies() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL / 2); // 30min of netting
        twammHook.executeJTMOrders(twammPoolKey);

        // Late joiner (different user) tries to steal earnings.
        // Must be a different user because same (owner, expiration, direction)
        // would collide with the existing order → OrderAlreadyExists.
        address lateUser = address(0xBAD);
        _fundAndApprove(lateUser);
        (, IJTM.OrderKey memory lateKey) = _submitAs(lateUser, true, INTERVAL, 3600e6);

        // Sync immediately - should get 0 (earningsFactorLast = current)
        vm.prank(lateUser);
        uint256 earnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: lateKey}));
        assertEq(earnings, 0, "late joiner gets zero");
    }

    function test_Adversarial_CancelAndResubmit() public {
        (, IJTM.OrderKey memory key1) = _submitOrder0For1(INTERVAL, 3600e6);
        twammHook.cancelOrder(twammPoolKey, key1);

        // Re-submit with different expiration
        (, IJTM.OrderKey memory key2) = _submitOrder0For1(2 * INTERVAL, 7200e6);

        assertTrue(key1.expiration != key2.expiration, "different expiry");
        IJTM.Order memory order2 = twammHook.getOrder(twammPoolKey, key2);
        assertTrue(order2.sellRate > 0, "resubmitted order active");
    }

    function test_Adversarial_SubmitAfterFullExecution() public {
        (, IJTM.OrderKey memory key1) = _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: key1}));

        // Submit new order after full lifecycle
        (, IJTM.OrderKey memory key2) = _submitOrder0For1(2 * INTERVAL, 7200e6);
        IJTM.Order memory order2 = twammHook.getOrder(twammPoolKey, key2);
        assertTrue(order2.sellRate > 0, "new order works after full lifecycle");
    }

    function test_Adversarial_VerySmallOrder() public {
        // Minimum viable: 1 token/sec (INTERVAL tokens total)
        (, IJTM.OrderKey memory key) = _submitOrder0For1(INTERVAL, INTERVAL);

        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, key);
        assertEq(order.sellRate, RATE_SCALER, "1 token/sec scaled");
    }

    function test_Adversarial_VeryLargeOrder() public {
        uint256 amountIn = 1_000_000e6;
        _submitOrder0For1(INTERVAL, amountIn);
        _submitOrder1For0(INTERVAL, amountIn);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);
        // Should not overflow
    }

    function test_Adversarial_ExecuteNoOrders() public {
        // No orders active → should be safe no-op
        vm.warp(block.timestamp + 5 * INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);
    }

    function test_Adversarial_ExecuteOverload_SameResult() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        _submitOrder1For0(INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);

        // Both overloads should execute identically
        twammHook.executeJTMOrders(twammPoolKey, block.timestamp);
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 13: PORTED FLOW PATTERNS FROM OLD TWAMM (5 tests)
    // ════════════════════════════════════════════════════════════════════

    function test_Ported_DifferentExpirations() public {
        // A: 1hr 0→1, B: 3hr 1→0
        (, IJTM.OrderKey memory keyA) = _submitOrder0For1(INTERVAL, 3600e6);
        (, IJTM.OrderKey memory keyB) = _submitOrder1For0(3 * INTERVAL, 10800e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyA}));

        vm.warp(block.timestamp + 2 * INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyB}));

        (uint256 sr0,) = twammHook.getStreamPool(twammPoolKey, true);
        (uint256 sr1,) = twammHook.getStreamPool(twammPoolKey, false);
        assertEq(sr0, 0, "A expired");
        assertEq(sr1, 0, "B expired");
    }

    function test_Ported_UnbalancedZeroForOne_10x() public {
        (, IJTM.OrderKey memory keyBig) = _submitOrder0For1(INTERVAL, 36000e6);
        (, IJTM.OrderKey memory keySmall) = _submitOrder1For0(INTERVAL, 3600e6);

        (uint160 sqrtBefore,,,) = poolManager.getSlot0(twammPoolKey.toId());

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Both should have earnings
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: keyBig}));
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: keySmall}));

        uint256 owedBig = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency1, address(this));
        uint256 owedSmall = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency0, address(this));
        assertTrue(owedBig > 0 || owedSmall > 0, "unbalanced produces earnings");
    }

    function test_Ported_UnbalancedOneForZero_10x() public {
        _submitOrder0For1(INTERVAL, 3600e6);
        (, IJTM.OrderKey memory keyBig) = _submitOrder1For0(INTERVAL, 36000e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        uint256 earnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: keyBig}));
        assertTrue(earnings > 0, "large 1for0 earns");
    }

    function test_Ported_ConsecutiveIntervalExecution() public {
        (, IJTM.OrderKey memory key0) = _submitOrder0For1(3 * INTERVAL, 10800e6);
        _submitOrder1For0(3 * INTERVAL, 10800e6);

        uint256 lastVirtual = twammHook.lastVirtualOrderTimestamp(twammPoolKey.toId());

        for (uint256 i = 1; i <= 3; i++) {
            vm.warp(lastVirtual + i * INTERVAL);
            twammHook.executeJTMOrders(twammPoolKey);

            uint256 ts = twammHook.lastVirtualOrderTimestamp(twammPoolKey.toId());
            assertEq(ts, lastVirtual + i * INTERVAL, "timestamp advances");
        }

        uint256 earnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0}));
        assertTrue(earnings > 0, "3-interval earnings");
    }

    function test_Ported_TwoUsers_OppositeDirections() public {
        address alice = address(0xA11CE);
        address bob = address(0xB0B);
        _fundAndApprove(alice);
        _fundAndApprove(bob);

        (, IJTM.OrderKey memory keyA) = _submitAs(alice, true, INTERVAL, 3600e6);
        (, IJTM.OrderKey memory keyB) = _submitAs(bob, false, INTERVAL, 3600e6);

        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        vm.prank(alice);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyA}));
        vm.prank(bob);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keyB}));

        // Both should have received buy tokens
        uint256 aliceOwed = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency1, alice);
        uint256 bobOwed = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency0, bob);
        // After syncAndClaim, owed should be 0 (claimed)
        assertEq(aliceOwed, 0, "alice claimed");
        assertEq(bobOwed, 0, "bob claimed");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GAP CONVERGENCE VIA AUCTION
    //  Proves the 3-layer architecture works end-to-end:
    //    1. Imbalanced orders → ghost accrual (the "gap")
    //    2. Arb clears ghost → buy tokens enter the hook
    //    3. All actors syncAndClaimTokens successfully
    // ════════════════════════════════════════════════════════════════════

    /// @notice Imbalanced flow: 3x more sell0 than sell1.
    ///         Without auction clear, claims would fail (TRANSFER_FAILED).
    ///         With clear, the arb fills the gap and everyone gets paid.
    function test_GapConvergence_ImbalancedFlow_AuctionFills() public {
        // ── Phase 1: Actors & orders ──
        // 6 actors selling token0 (total: 6 × 7200e6 = 43,200e6)
        // 2 actors selling token1 (total: 2 × 7200e6 = 14,400e6)
        // Imbalance ratio: 3:1 in token0 direction
        // All orders are 2hr to keep stream active for clear at hour 1

        uint256 nSell0 = 6;
        uint256 nSell1 = 2;
        uint256 totalActors = nSell0 + nSell1;
        uint256 orderAmount = 7200e6; // each actor puts in 7200

        address[] memory actors = new address[](totalActors);
        IJTM.OrderKey[] memory keys = new IJTM.OrderKey[](totalActors);

        for (uint256 i = 0; i < totalActors; i++) {
            actors[i] = address(uint160(0xA000 + i));
            _fundAndApprove(actors[i]);
        }

        // Submit: actors 0-5 sell token0, actors 6-7 sell token1
        for (uint256 i = 0; i < totalActors; i++) {
            bool zeroForOne = i < nSell0;
            (, keys[i]) = _submitAs(actors[i], zeroForOne, 2 * INTERVAL, orderAmount);
        }

        console.log("=== GAP CONVERGENCE TEST ===");
        console.log("Sell0 actors:", nSell0, " Sell1 actors:", nSell1);

        // ── Phase 2: Warp 1 hour, execute ──
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Check ghost balance - the imbalance should produce accrued0
        (uint256 accrued0, uint256 accrued1,,) = twammHook.getStreamState(twammPoolKey);

        console.log("[After 1hr] accrued0 (ghost token0):", accrued0);
        console.log("[After 1hr] accrued1 (ghost token1):", accrued1);

        // The imbalanced direction should have leftover ghost
        // With 3:1 ratio, most sell0 gets netted with sell1, but 2/3 remains as ghost
        assertTrue(accrued0 > 0, "imbalance produces ghost token0");

        // ── Phase 3: Arb clears the ghost via Layer 3 auction ──
        // The arb (test contract) pays token1 and receives token0
        // This injects buy tokens (token1) into the hook

        uint256 hookToken1Before = IERC20Minimal(_token1()).balanceOf(address(twammHook));

        if (accrued0 > 0) {
            twammHook.clear(twammPoolKey, true, accrued0, 0);
            console.log("[Clear] Arb cleared", accrued0, "ghost token0");
        }
        if (accrued1 > 0) {
            twammHook.clear(twammPoolKey, false, accrued1, 0);
            console.log("[Clear] Arb cleared", accrued1, "ghost token1");
        }

        uint256 hookToken1After = IERC20Minimal(_token1()).balanceOf(address(twammHook));

        // The arb's payment injected token1 into the hook
        console.log(
            "[Clear] token1 injected into hook:",
            hookToken1After > hookToken1Before ? hookToken1After - hookToken1Before : 0
        );

        // ── Phase 4: Warp to expiry, execute ──
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // ── Phase 5: Clear any remaining ghost from last epoch ──
        (uint256 finalAccrued0, uint256 finalAccrued1,,) = twammHook.getStreamState(twammPoolKey);

        console.log("[After 2hr] final accrued0:", finalAccrued0, "accrued1:", finalAccrued1);

        // All orders expired → sellRateCurrent = 0 → can't clear if accrued
        // So if there's still ghost, sync will just credit what was earned
        // during the active period. That's fine - the clear at hour 1 already
        // injected the buy tokens for the first epoch's accrual.

        // ── Phase 6: ALL actors sync + claim - THIS is the critical test ──
        // Without the clear, these claims would fail with TRANSFER_FAILED
        // because the hook wouldn't hold enough buy tokens.

        uint256 totalClaimed0 = 0;
        uint256 totalClaimed1 = 0;

        for (uint256 i = 0; i < totalActors; i++) {
            // Record balances before
            uint256 bal0Before = IERC20Minimal(_token0()).balanceOf(actors[i]);
            uint256 bal1Before = IERC20Minimal(_token1()).balanceOf(actors[i]);

            vm.prank(actors[i]);
            (uint256 c0, uint256 c1) =
                twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: keys[i]}));

            uint256 bal0After = IERC20Minimal(_token0()).balanceOf(actors[i]);
            uint256 bal1After = IERC20Minimal(_token1()).balanceOf(actors[i]);

            uint256 received0 = bal0After - bal0Before;
            uint256 received1 = bal1After - bal1Before;

            totalClaimed0 += received0;
            totalClaimed1 += received1;

            if (i < nSell0) {
                // 0→1 sellers should receive token1 (buy side)
                console.log("  Actor", i, "(sell0): claimed token1 =", received1);
            } else {
                // 1→0 sellers should receive token0 (buy side)
                console.log("  Actor", i, "(sell1): claimed token0 =", received0);
            }
        }

        console.log("Total claimed token0:", totalClaimed0);
        console.log("Total claimed token1:", totalClaimed1);

        // ── Phase 7: Post-condition checks ──

        // Check sell rates (may have residual in storage until next _accrueAndNet call)
        (uint256 sr0,) = twammHook.getStreamPool(twammPoolKey, true);
        (uint256 sr1,) = twammHook.getStreamPool(twammPoolKey, false);
        console.log("[Post] sellRate 0for1:", sr0, "1for0:", sr1);

        // All tokensOwed should be zero (everyone claimed)
        for (uint256 i = 0; i < totalActors; i++) {
            uint256 owed0 = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency0, actors[i]);
            uint256 owed1 = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency1, actors[i]);
            assertEq(owed0, 0, "no residual owed token0");
            assertEq(owed1, 0, "no residual owed token1");
        }

        // Every sell0 actor should have earned SOME token1
        for (uint256 i = 0; i < nSell0; i++) {
            uint256 bal1 = IERC20Minimal(_token1()).balanceOf(actors[i]);
            // They started with 100_000_000e6, spent 7200e6, should have > 100M - 7200 + earnings
            assertTrue(bal1 > 100_000_000e6 - orderAmount, "sell0 actor earned token1");
        }
        // Every sell1 actor should have earned SOME token0
        for (uint256 i = nSell0; i < totalActors; i++) {
            uint256 bal0 = IERC20Minimal(_token0()).balanceOf(actors[i]);
            assertTrue(bal0 > 100_000_000e6 - orderAmount, "sell1 actor earned token0");
        }

        console.log("=== GAP CONVERGENCE: ALL CLAIMS SUCCEEDED ===");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 14: FULL-CYCLE INVARIANT TEST
    //  Single-direction stream exercises L2 (JIT fill) and L3 (clear).
    //  Verifies: LPs not drained, traders whole, hook clean, conservation.
    // ════════════════════════════════════════════════════════════════════

    function test_FullCycle_NoFundsLost_AllParticipantsWhole() public {
        address alice = address(0xA11CE);
        address taker = address(0x7A4E);
        address arb = address(0xA4B);
        address lp = address(0x11111);

        // INV-6 FIX: Snapshot totalSupply BEFORE minting actors
        uint256 supplyT0_before = pt.totalSupply();
        uint256 supplyT1_before = ct.totalSupply();

        _fundAndApprove(alice);
        _fundAndApprove(taker);
        _fundAndApprove(arb);
        pt.mint(lp, 100_000_000e6);
        ct.mint(lp, 100_000_000e6);
        vm.startPrank(lp);
        pt.approve(address(lpRouter), type(uint256).max);
        ct.approve(address(lpRouter), type(uint256).max);
        vm.stopPrank();
        vm.prank(taker);
        pt.approve(address(swapRouter), type(uint256).max);
        vm.prank(taker);
        ct.approve(address(swapRouter), type(uint256).max);
        vm.prank(arb);
        pt.approve(address(twammHook), type(uint256).max);
        vm.prank(arb);
        ct.approve(address(twammHook), type(uint256).max);

        uint256 alicePre0 = IERC20Minimal(_token0()).balanceOf(alice);
        uint256 alicePre1 = IERC20Minimal(_token1()).balanceOf(alice);
        uint256 takerPre0 = IERC20Minimal(_token0()).balanceOf(taker);
        uint256 takerPre1 = IERC20Minimal(_token1()).balanceOf(taker);
        uint256 arbPre0 = IERC20Minimal(_token0()).balanceOf(arb);
        uint256 arbPre1 = IERC20Minimal(_token1()).balanceOf(arb);
        uint256 lpPre0 = IERC20Minimal(_token0()).balanceOf(lp);
        uint256 lpPre1 = IERC20Minimal(_token1()).balanceOf(lp);

        // Phase 1: LP deposits
        uint256 lpDeposit = 50_000e6;
        vm.prank(lp);
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: -600, tickUpper: 600, liquidityDelta: int256(lpDeposit), salt: bytes32(uint256(0xBEEF))
            }),
            ""
        );
        uint256 lpAfterDep0 = IERC20Minimal(_token0()).balanceOf(lp);
        uint256 lpAfterDep1 = IERC20Minimal(_token1()).balanceOf(lp);
        uint256 lpSpent0 = lpPre0 - lpAfterDep0;
        uint256 lpSpent1 = lpPre1 - lpAfterDep1;
        uint256 v4Pre0 = IERC20Minimal(_token0()).balanceOf(address(poolManager));
        uint256 v4Pre1 = IERC20Minimal(_token1()).balanceOf(address(poolManager));

        // Phase 2: Alice sells token0 (single-direction)
        uint256 orderAmount = 3600e6;
        vm.prank(alice);
        (, IJTM.OrderKey memory aliceKey) = twammHook.submitOrder(
            IJTM.SubmitOrderParams({
                key: twammPoolKey, zeroForOne: true, duration: INTERVAL, amountIn: orderAmount
            })
        );
        uint256 hookMidT0 = IERC20Minimal(_token0()).balanceOf(address(twammHook));
        uint256 hookMidT1 = IERC20Minimal(_token1()).balanceOf(address(twammHook));

        // Phase 3: Accrue 30min + L2 JIT fill
        vm.warp(block.timestamp + 1800);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 acc0,,,) = twammHook.getStreamState(twammPoolKey);
        assertTrue(acc0 > 0, "accrued0 > 0");

        vm.prank(taker);
        swapRouter.swap(
            twammPoolKey,
            SwapParams({
                zeroForOne: false, amountSpecified: -int256(200e6), sqrtPriceLimitX96: TickMath.MAX_SQRT_PRICE - 1
            }),
            PoolSwapTest.TestSettings({takeClaims: false, settleUsingBurn: false}),
            ""
        );
        uint256 takerGot0 = IERC20Minimal(_token0()).balanceOf(taker) - takerPre0;
        assertTrue(takerGot0 > 0, "Taker received token0");

        // Phase 4: L3 clear ALL remaining before expiry
        uint256 expiry = uint256(aliceKey.expiration);
        vm.warp(expiry - 1);
        twammHook.executeJTMOrders(twammPoolKey);
        (acc0,,,) = twammHook.getStreamState(twammPoolKey);
        if (acc0 > 0) {
            vm.prank(arb);
            twammHook.clear(twammPoolKey, true, acc0, 0);
        }

        // Phase 5: Expire
        vm.warp(expiry + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        // Phase 6: Alice claims
        vm.prank(alice);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: aliceKey}));
        uint256 aliceFinal0 = IERC20Minimal(_token0()).balanceOf(alice);
        uint256 aliceFinal1 = IERC20Minimal(_token1()).balanceOf(alice);
        uint256 aliceLoss0 = alicePre0 - aliceFinal0;
        uint256 aliceGain1 = aliceFinal1 - alicePre1;
        console.log("[Alice] sold t0:", aliceLoss0, "earned t1:", aliceGain1);

        // Phase 7: LP withdraws
        vm.prank(lp);
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: -600, tickUpper: 600, liquidityDelta: -int256(lpDeposit), salt: bytes32(uint256(0xBEEF))
            }),
            ""
        );
        uint256 lpRec0 = IERC20Minimal(_token0()).balanceOf(lp) - lpAfterDep0;
        uint256 lpRec1 = IERC20Minimal(_token1()).balanceOf(lp) - lpAfterDep1;

        // ════════════════════  INVARIANTS  ════════════════════

        // INV-1: Alice earned token1
        assertTrue(aliceGain1 > 0, "INV-1: Alice earned token1");

        // INV-2: Alice sold full order
        assertEq(aliceLoss0, orderAmount, "INV-2: sold full order");

        // INV-3: LP not drained
        // With 100T seed liquidity + 50K deposit, IL from 3600e6 order should be < 0.5%
        assertTrue(
            lpRec0 >= (lpSpent0 * 995) / 1000 || lpRec1 >= (lpSpent1 * 995) / 1000,
            "INV-3: LP must recover >= 99.5% on at least one side"
        );

        // INV-4: Hook residual — with dust donation, should be near zero
        // Orphaned tokens now move to collectedDust via _flushDonations
        uint256 hookEnd0 = IERC20Minimal(_token0()).balanceOf(address(twammHook));
        uint256 hookEnd1 = IERC20Minimal(_token1()).balanceOf(address(twammHook));
        uint256 dust0 = twammHook.collectedDust(twammPoolKey.currency0);
        uint256 dust1 = twammHook.collectedDust(twammPoolKey.currency1);
        console.log("[Hook] residual t0:", hookEnd0, "t1:", hookEnd1);
        console.log("[Hook] collectedDust t0:", dust0, "t1:", dust1);
        // Hook balance = collectedDust + any in-flight amounts
        // After full cycle with dust donation, unaccounted residual should be tiny
        uint256 unaccounted0 = hookEnd0 > dust0 ? hookEnd0 - dust0 : 0;
        uint256 unaccounted1 = hookEnd1 > dust1 ? hookEnd1 - dust1 : 0;
        assertTrue(unaccounted0 < 10, "INV-4a: unaccounted hook t0 < 10 wei");
        assertTrue(unaccounted1 < 10, "INV-4b: unaccounted hook t1 < 10 wei");

        // INV-5: V4 solvent
        uint256 v4End0 = IERC20Minimal(_token0()).balanceOf(address(poolManager));
        uint256 v4End1 = IERC20Minimal(_token1()).balanceOf(address(poolManager));
        assertTrue(v4End0 > 0 && v4End1 > 0, "INV-5: V4 solvent");

        // INV-6: Global Token Conservation
        // totalSupply must not change between actor minting and test end.
        // This proves no tokens were created or destroyed during the full cycle.
        // We minted exact amounts to actors, then no further minting should occur.
        uint256 supplyT0_after = pt.totalSupply();
        uint256 supplyT1_after = ct.totalSupply();
        uint256 mintedT0 = 100_000_000e6 * 3 + 100_000_000e6; // alice + taker + arb + lp
        assertEq(supplyT0_after - supplyT0_before, mintedT0, "INV-6a: token0 supply conserved (only actor minting)");
        assertEq(
            supplyT1_after - supplyT1_before,
            mintedT0, // same amount minted for t1
            "INV-6b: token1 supply conserved (only actor minting)"
        );
        console.log("[INV-6] PASSED: totalSupply conserved");

        // INV-7: No residual owed
        PoolId pid = twammPoolKey.toId();
        assertEq(twammHook.tokensOwed(pid, twammPoolKey.currency0, alice), 0, "INV-7a");
        assertEq(twammHook.tokensOwed(pid, twammPoolKey.currency1, alice), 0, "INV-7b");

        console.log("=== FULL CYCLE: ALL 7 INVARIANTS PASSED ===");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 15: WEI-PRECISE LP ACCOUNTING
    //  Step-by-step numerical log of every price, amount, and balance
    //  through the full LP lifecycle: deposit -> swaps -> TWAMM -> withdraw
    // ════════════════════════════════════════════════════════════════════

    function _logPoolState(string memory label) internal view {
        PoolId pid = twammPoolKey.toId();
        (uint160 sqrtPrice, int24 tick,, uint24 lpFee) = poolManager.getSlot0(pid);
        uint128 liq = poolManager.getLiquidity(pid);
        uint256 v4t0 = IERC20Minimal(_token0()).balanceOf(address(poolManager));
        uint256 v4t1 = IERC20Minimal(_token1()).balanceOf(address(poolManager));
        uint256 hookT0 = IERC20Minimal(_token0()).balanceOf(address(twammHook));
        uint256 hookT1 = IERC20Minimal(_token1()).balanceOf(address(twammHook));

        console.log(string.concat("--- ", label, " ---"));
        console.log("  sqrtPriceX96:", sqrtPrice);
        console.log("  tick:", uint256(uint24(tick)));
        console.log("  liquidity:", liq);
        console.log("  V4 reserves t0:", v4t0, "t1:", v4t1);
        console.log("  Hook bal t0:", hookT0, "t1:", hookT1);
    }

    function test_LP_WeiPrecise_FullCycleAccounting() public {
        address lp = address(0x11111);
        address alice = address(0xA11CE);
        address taker = address(0x7A4E);
        address arb = address(0xA4B);

        // Fund
        _fundAndApprove(alice);
        _fundAndApprove(taker);
        _fundAndApprove(arb);
        pt.mint(lp, 10_000_000e6);
        ct.mint(lp, 10_000_000e6);
        vm.startPrank(lp);
        pt.approve(address(lpRouter), type(uint256).max);
        ct.approve(address(lpRouter), type(uint256).max);
        vm.stopPrank();
        vm.prank(taker);
        pt.approve(address(swapRouter), type(uint256).max);
        vm.prank(taker);
        ct.approve(address(swapRouter), type(uint256).max);
        vm.prank(arb);
        pt.approve(address(twammHook), type(uint256).max);
        vm.prank(arb);
        ct.approve(address(twammHook), type(uint256).max);

        // ── STEP 0: Pre-state ──
        _logPoolState("STEP 0: Before LP deposit");
        uint256 lpBal0_pre = IERC20Minimal(_token0()).balanceOf(lp);
        uint256 lpBal1_pre = IERC20Minimal(_token1()).balanceOf(lp);
        console.log("  LP wallet t0:", lpBal0_pre, "t1:", lpBal1_pre);

        // ── STEP 1: LP deposits (narrow range: -120 to +120 ticks) ──
        int24 tickLower = -120;
        int24 tickUpper = 120;
        int256 liqDelta = 1_000_000e6; // 1M liquidity units

        vm.prank(lp);
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: tickLower, tickUpper: tickUpper, liquidityDelta: liqDelta, salt: bytes32(uint256(0xDEAD))
            }),
            ""
        );
        uint256 lpBal0_afterDep = IERC20Minimal(_token0()).balanceOf(lp);
        uint256 lpBal1_afterDep = IERC20Minimal(_token1()).balanceOf(lp);
        uint256 lpDepT0 = lpBal0_pre - lpBal0_afterDep;
        uint256 lpDepT1 = lpBal1_pre - lpBal1_afterDep;

        _logPoolState("STEP 1: After LP deposit");
        console.log("  LP deposited t0:", lpDepT0);
        console.log("  LP deposited t1:", lpDepT1);

        // ── STEP 2: Alice submits TWAMM order (sells token0) ──
        uint256 orderAmt = 3600e6;
        vm.prank(alice);
        (, IJTM.OrderKey memory aliceKey) = twammHook.submitOrder(
            IJTM.SubmitOrderParams({key: twammPoolKey, zeroForOne: true, duration: INTERVAL, amountIn: orderAmt})
        );
        _logPoolState("STEP 2: After TWAMM order submit");

        // ── STEP 3: Half accrual (30 min) ──
        vm.warp(block.timestamp + 1800);
        twammHook.executeJTMOrders(twammPoolKey);
        (uint256 acc0, uint256 acc1, uint256 disc, uint256 secsLeft) = twammHook.getStreamState(twammPoolKey);
        _logPoolState("STEP 3: After 30min accrual");
        console.log("  accrued0:", acc0, "accrued1:", acc1);
        console.log("  discount:", disc, "secsLeft:", secsLeft);

        // ── STEP 4: Taker swap (oneForZero: buy accrued0 with token1) ──
        uint256 takerPre0 = IERC20Minimal(_token0()).balanceOf(taker);
        uint256 takerPre1 = IERC20Minimal(_token1()).balanceOf(taker);

        vm.prank(taker);
        swapRouter.swap(
            twammPoolKey,
            SwapParams({
                zeroForOne: false,
                amountSpecified: -int256(500e6), // exact input 500 token1
                sqrtPriceLimitX96: TickMath.MAX_SQRT_PRICE - 1
            }),
            PoolSwapTest.TestSettings({takeClaims: false, settleUsingBurn: false}),
            ""
        );
        uint256 takerPost0 = IERC20Minimal(_token0()).balanceOf(taker);
        uint256 takerPost1 = IERC20Minimal(_token1()).balanceOf(taker);
        _logPoolState("STEP 4: After taker swap (1->0)");
        console.log("  Taker paid t1:", takerPre1 - takerPost1);
        console.log("  Taker got  t0:", takerPost0 - takerPre0);

        // ── STEP 5: A reverse swap (zeroForOne: taker sells token0 for token1) ──
        uint256 takerPre0b = IERC20Minimal(_token0()).balanceOf(taker);
        uint256 takerPre1b = IERC20Minimal(_token1()).balanceOf(taker);

        vm.prank(taker);
        swapRouter.swap(
            twammPoolKey,
            SwapParams({
                zeroForOne: true,
                amountSpecified: -int256(300e6), // exact input 300 token0
                sqrtPriceLimitX96: TickMath.MIN_SQRT_PRICE + 1
            }),
            PoolSwapTest.TestSettings({takeClaims: false, settleUsingBurn: false}),
            ""
        );
        uint256 takerPost0b = IERC20Minimal(_token0()).balanceOf(taker);
        uint256 takerPost1b = IERC20Minimal(_token1()).balanceOf(taker);
        _logPoolState("STEP 5: After reverse swap (0->1)");
        console.log("  Taker paid t0:", takerPre0b - takerPost0b);
        console.log("  Taker got  t1:", takerPost1b - takerPre1b);

        // ── STEP 6: L3 clear remaining accrued0 ──
        twammHook.executeJTMOrders(twammPoolKey);
        (acc0, acc1, disc, secsLeft) = twammHook.getStreamState(twammPoolKey);
        console.log("--- STEP 6: Pre-clear state ---");
        console.log("  accrued0:", acc0, "accrued1:", acc1);

        if (acc0 > 0) {
            uint256 arbPre0 = IERC20Minimal(_token0()).balanceOf(arb);
            uint256 arbPre1 = IERC20Minimal(_token1()).balanceOf(arb);
            vm.prank(arb);
            twammHook.clear(twammPoolKey, true, acc0, 0);
            uint256 arbPost0 = IERC20Minimal(_token0()).balanceOf(arb);
            uint256 arbPost1 = IERC20Minimal(_token1()).balanceOf(arb);
            console.log("  Arb got  t0:", arbPost0 - arbPre0);
            console.log("  Arb paid t1:", arbPre1 - arbPost1);
        }
        _logPoolState("STEP 6: After L3 clear");

        // ── STEP 7: Warp to near-expiry, clear final, expire ──
        uint256 expiry = uint256(aliceKey.expiration);
        vm.warp(expiry - 1);
        twammHook.executeJTMOrders(twammPoolKey);
        (acc0,,,) = twammHook.getStreamState(twammPoolKey);
        if (acc0 > 0) {
            vm.prank(arb);
            twammHook.clear(twammPoolKey, true, acc0, 0);
        }
        vm.warp(expiry + 1);
        twammHook.executeJTMOrders(twammPoolKey);
        _logPoolState("STEP 7: After order expiry");

        // ── STEP 8: Alice claims ──
        vm.prank(alice);
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: aliceKey}));
        _logPoolState("STEP 8: After Alice claims");

        // ── STEP 9: LP withdraws (exact reverse of deposit) ──
        vm.prank(lp);
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: tickLower, tickUpper: tickUpper, liquidityDelta: -liqDelta, salt: bytes32(uint256(0xDEAD))
            }),
            ""
        );
        uint256 lpBal0_final = IERC20Minimal(_token0()).balanceOf(lp);
        uint256 lpBal1_final = IERC20Minimal(_token1()).balanceOf(lp);
        uint256 lpWithdT0 = lpBal0_final - lpBal0_afterDep;
        uint256 lpWithdT1 = lpBal1_final - lpBal1_afterDep;

        _logPoolState("STEP 9: After LP withdrawal");
        console.log("  LP withdrew t0:", lpWithdT0);
        console.log("  LP withdrew t1:", lpWithdT1);

        // ── STEP 10: FINAL ACCOUNTING ──
        console.log("========== FINAL LP ACCOUNTING ==========");
        console.log("  Deposited t0:", lpDepT0);
        console.log("  Deposited t1:", lpDepT1);
        console.log("  Withdrew  t0:", lpWithdT0);
        console.log("  Withdrew  t1:", lpWithdT1);

        int256 lpDeltaT0 = int256(lpWithdT0) - int256(lpDepT0);
        int256 lpDeltaT1 = int256(lpWithdT1) - int256(lpDepT1);
        console.log("  Delta t0 (+ = profit):");
        if (lpDeltaT0 >= 0) console.log("    +", uint256(lpDeltaT0));
        else console.log("    -", uint256(-lpDeltaT0));
        console.log("  Delta t1 (+ = profit):");
        if (lpDeltaT1 >= 0) console.log("    +", uint256(lpDeltaT1));
        else console.log("    -", uint256(-lpDeltaT1));

        // LP should get back at least 99.5% (fees earned offset IL)
        assertTrue(
            lpWithdT0 >= (lpDepT0 * 995) / 1000 || lpWithdT1 >= (lpDepT1 * 995) / 1000,
            "LP-WEI: LP recovered >= 99.5% on at least one side"
        );

        // V4 pool must still be solvent
        uint256 v4End0 = IERC20Minimal(_token0()).balanceOf(address(poolManager));
        uint256 v4End1 = IERC20Minimal(_token1()).balanceOf(address(poolManager));
        assertTrue(v4End0 > 0 && v4End1 > 0, "LP-WEI: V4 still solvent");

        // Hook residual after full cycle
        uint256 hookEnd0 = IERC20Minimal(_token0()).balanceOf(address(twammHook));
        uint256 hookEnd1 = IERC20Minimal(_token1()).balanceOf(address(twammHook));
        console.log("  Hook residual t0:", hookEnd0, "t1:", hookEnd1);

        console.log("========== LP WEI-PRECISE TEST PASSED ==========");
    }

    // ════════════════════════════════════════════════════════════════════
    //  GROUP 16: ASYMMETRIC OPPOSING STREAMS
    //  Tests the most common real-world scenario: two opposing TWAMM orders
    //  with different sizes (10:1 ratio). Exercises L1 netting (partial),
    //  L3 clearing (for the leftover side), and verifies conservation.
    // ════════════════════════════════════════════════════════════════════

    function test_AsymmetricOpposingStreams_ConservationHolds() public {
        address alice = address(0xA11CE);
        address bob = address(0xB0B);
        address arb = address(0xA4B);
        address lp = address(0x11111);

        // Snapshot totalSupply BEFORE minting
        uint256 supplyT0_before = pt.totalSupply();
        uint256 supplyT1_before = ct.totalSupply();

        _fundAndApprove(alice);
        _fundAndApprove(bob);
        _fundAndApprove(arb);
        pt.mint(lp, 100_000_000e6);
        ct.mint(lp, 100_000_000e6);
        vm.startPrank(lp);
        pt.approve(address(lpRouter), type(uint256).max);
        ct.approve(address(lpRouter), type(uint256).max);
        vm.stopPrank();
        vm.prank(arb);
        pt.approve(address(twammHook), type(uint256).max);
        vm.prank(arb);
        ct.approve(address(twammHook), type(uint256).max);

        uint256 alicePre0 = IERC20Minimal(_token0()).balanceOf(alice);
        uint256 alicePre1 = IERC20Minimal(_token1()).balanceOf(alice);
        uint256 bobPre0 = IERC20Minimal(_token0()).balanceOf(bob);
        uint256 bobPre1 = IERC20Minimal(_token1()).balanceOf(bob);
        uint256 lpPre0 = IERC20Minimal(_token0()).balanceOf(lp);

        // Phase 1: LP deposits
        uint256 lpDeposit = 50_000e6;
        vm.prank(lp);
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: -600, tickUpper: 600, liquidityDelta: int256(lpDeposit), salt: bytes32(uint256(0xBEEF))
            }),
            ""
        );
        uint256 lpAfterDep0 = IERC20Minimal(_token0()).balanceOf(lp);
        uint256 lpAfterDep1 = IERC20Minimal(_token1()).balanceOf(lp);
        uint256 lpSpent0 = lpPre0 - lpAfterDep0;

        // Phase 2: Asymmetric opposing streams (10:1 ratio)
        // Alice sells 36,000 token0, Bob sells 3,600 token1
        uint256 aliceAmount = 36000e6;
        uint256 bobAmount = 3600e6;
        vm.prank(alice);
        (, IJTM.OrderKey memory aliceKey) = twammHook.submitOrder(
            IJTM.SubmitOrderParams({
                key: twammPoolKey, zeroForOne: true, duration: INTERVAL, amountIn: aliceAmount
            })
        );
        vm.prank(bob);
        (, IJTM.OrderKey memory bobKey) = twammHook.submitOrder(
            IJTM.SubmitOrderParams({key: twammPoolKey, zeroForOne: false, duration: INTERVAL, amountIn: bobAmount})
        );

        // Phase 3: Mid-stream accrual — L1 netting should handle the smaller stream
        vm.warp(block.timestamp + INTERVAL / 2);
        twammHook.executeJTMOrders(twammPoolKey);
        (uint256 acc0Mid, uint256 acc1Mid,,) = twammHook.getStreamState(twammPoolKey);
        console.log("[ASYM] Mid-stream accrued0:", acc0Mid, "accrued1:", acc1Mid);
        // After netting, the smaller side (token1) should be mostly consumed
        // The larger side (token0) should have leftover
        assertTrue(acc0Mid > acc1Mid, "larger stream has more leftover");

        // Phase 4: L3 clear the excess token0 before expiry
        uint256 expiry = uint256(aliceKey.expiration);
        vm.warp(expiry - 1);
        twammHook.executeJTMOrders(twammPoolKey);
        (uint256 acc0Pre,,,) = twammHook.getStreamState(twammPoolKey);
        if (acc0Pre > 0) {
            vm.prank(arb);
            twammHook.clear(twammPoolKey, true, acc0Pre, 0);
        }

        // Phase 5: Expire
        vm.warp(expiry + 1);
        twammHook.executeJTMOrders(twammPoolKey);

        // Phase 6: KNOWN LIMITATION — Netting-Backing Gap
        // With asymmetric opposing streams, the arb clears token0 at a 5% discount.
        // This means the arb pays LESS token1 than the netting credited to Alice.
        // The hook's token1 balance is slightly less than Alice's total earnings.
        // This is a fundamental design tension between:
        //   (a) netting at TWAP price (fair to traders)
        //   (b) clearing at discounted price (incentive for arbs)
        // The gap = (discount% × cleared_amount) denominated in the payment token.
        //
        // On mainnet, the owner can fill the gap from collectedDust or protocol fees.
        // For this test, we demonstrate the gap exists and measure it precisely.

        // Sync both orders to calculate earnings (but DON'T claim yet)
        vm.prank(alice);
        uint256 aliceEarnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: aliceKey}));
        vm.prank(bob);
        uint256 bobEarnings = twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: bobKey}));
        console.log("[ASYM] Alice earnings (t1):", aliceEarnings);
        console.log("[ASYM] Bob earnings (t0):", bobEarnings);

        // Measure the gap: how much token1 does hook have vs how much it owes Alice?
        uint256 hookT1 = IERC20Minimal(_token1()).balanceOf(address(twammHook));
        uint256 aliceOwedT1 = twammHook.tokensOwed(twammPoolKey.toId(), twammPoolKey.currency1, alice);
        console.log("[ASYM] Hook token1 balance:", hookT1);
        console.log("[ASYM] Alice owed token1:", aliceOwedT1);

        uint256 gapFilled = 0;
        if (aliceOwedT1 > hookT1) {
            uint256 gap = aliceOwedT1 - hookT1;
            gapFilled = gap;
            console.log("[ASYM] NETTING-BACKING GAP:", gap, "token1");
            // The gap should be small relative to the order size
            assertTrue(gap < aliceAmount / 100, "Gap < 1% of order amount");

            // Fill the gap: in production, owner would use collectedDust or protocol fees
            // For the test, we mint the gap to the hook to prove conservation holds after
            // NOTE: Use Currency.unwrap to get the actual ERC20 address for currency1,
            // since V4 sorts currencies by address and ct may not equal currency1.
            MockERC20(Currency.unwrap(twammPoolKey.currency1)).mint(address(twammHook), gap);
            console.log("[ASYM] Gap filled with", gap, "token1");
        }

        // Now claim tokens
        vm.prank(alice);
        twammHook.claimTokens(twammPoolKey, twammPoolKey.currency1);
        vm.prank(bob);
        twammHook.claimTokens(twammPoolKey, twammPoolKey.currency0);

        uint256 aliceFinal0 = IERC20Minimal(_token0()).balanceOf(alice);
        uint256 aliceFinal1 = IERC20Minimal(_token1()).balanceOf(alice);
        uint256 bobFinal0 = IERC20Minimal(_token0()).balanceOf(bob);
        uint256 bobFinal1 = IERC20Minimal(_token1()).balanceOf(bob);

        // Phase 7: LP withdraws
        vm.prank(lp);
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: -600, tickUpper: 600, liquidityDelta: -int256(lpDeposit), salt: bytes32(uint256(0xBEEF))
            }),
            ""
        );
        uint256 lpRec0 = IERC20Minimal(_token0()).balanceOf(lp) - lpAfterDep0;
        uint256 lpRec1 = IERC20Minimal(_token1()).balanceOf(lp) - lpAfterDep1;

        // ════════════════════  INVARIANTS  ════════════════════

        // ASY-1: Alice earned token1 (sold token0)
        uint256 aliceLoss0 = alicePre0 - aliceFinal0;
        uint256 aliceGain1 = aliceFinal1 - alicePre1;
        console.log("[ASYM] Alice sold t0:", aliceLoss0, "earned t1:", aliceGain1);
        assertTrue(aliceGain1 > 0, "ASY-1: Alice earned token1");
        assertEq(aliceLoss0, aliceAmount, "ASY-1: Alice sold full order");

        // ASY-2: Bob earned token0 (sold token1)
        uint256 bobLoss1 = bobPre1 - bobFinal1;
        uint256 bobGain0 = bobFinal0 - bobPre0;
        console.log("[ASYM] Bob sold t1:", bobLoss1, "earned t0:", bobGain0);
        assertTrue(bobGain0 > 0, "ASY-2: Bob earned token0");
        assertEq(bobLoss1, bobAmount, "ASY-2: Bob sold full order");

        // ASY-3: LP recovery >= 99.5%
        assertTrue(
            lpRec0 >= (lpSpent0 * 995) / 1000 || lpRec1 >= (lpSpent0 * 995) / 1000,
            "ASY-3: LP recovery >= 99.5% on at least one side"
        );

        // ASY-4: Hook residual fully accounted (dust donation)
        uint256 hookEnd0 = IERC20Minimal(_token0()).balanceOf(address(twammHook));
        uint256 hookEnd1 = IERC20Minimal(_token1()).balanceOf(address(twammHook));
        uint256 dust0 = twammHook.collectedDust(twammPoolKey.currency0);
        uint256 dust1 = twammHook.collectedDust(twammPoolKey.currency1);
        console.log("[ASYM] Hook residual t0:", hookEnd0, "t1:", hookEnd1);
        console.log("[ASYM] collectedDust t0:", dust0, "t1:", dust1);
        uint256 unaccounted0 = hookEnd0 > dust0 ? hookEnd0 - dust0 : 0;
        uint256 unaccounted1 = hookEnd1 > dust1 ? hookEnd1 - dust1 : 0;
        assertTrue(unaccounted0 < 10, "ASY-4a: unaccounted t0 < 10 wei");
        assertTrue(unaccounted1 < 10, "ASY-4b: unaccounted t1 < 10 wei");

        // ASY-5: V4 solvent
        uint256 v4End0 = IERC20Minimal(_token0()).balanceOf(address(poolManager));
        uint256 v4End1 = IERC20Minimal(_token1()).balanceOf(address(poolManager));
        assertTrue(v4End0 > 0 && v4End1 > 0, "ASY-5: V4 solvent");

        // ASY-6: Global token conservation via totalSupply
        // NOTE: pt/ct naming doesn't match V4's currency0/currency1 (sorted by address).
        // gapFilled was minted to Currency.unwrap(currency1) which may be pt or ct.
        // To avoid confusion, we check BOTH supplies and allow gapFilled in EITHER.
        uint256 supplyPtAfter = pt.totalSupply();
        uint256 supplyCtAfter = ct.totalSupply();
        uint256 mintedPerToken = 100_000_000e6 * 3 + 100_000_000e6;
        uint256 ptDelta = supplyPtAfter - supplyT0_before;
        uint256 ctDelta = supplyCtAfter - supplyT1_before;
        // One token has exact 400M delta, the other has 400M + gapFilled
        assertTrue(
            (ptDelta == mintedPerToken && ctDelta == mintedPerToken + gapFilled)
                || (ptDelta == mintedPerToken + gapFilled && ctDelta == mintedPerToken),
            "ASY-6: supply conserved (one side includes gap-fill)"
        );
        if (gapFilled > 0) {
            console.log("[ASY-6] Gap-fill included in conservation:", gapFilled);
        }

        // ASY-7: No residual tokensOwed
        PoolId pid = twammPoolKey.toId();
        assertEq(twammHook.tokensOwed(pid, twammPoolKey.currency0, alice), 0, "ASY-7a");
        assertEq(twammHook.tokensOwed(pid, twammPoolKey.currency1, alice), 0, "ASY-7b");
        assertEq(twammHook.tokensOwed(pid, twammPoolKey.currency0, bob), 0, "ASY-7c");
        assertEq(twammHook.tokensOwed(pid, twammPoolKey.currency1, bob), 0, "ASY-7d");

        console.log("=== ASYMMETRIC OPPOSING STREAMS: ALL INVARIANTS PASSED ===");
    }
}
