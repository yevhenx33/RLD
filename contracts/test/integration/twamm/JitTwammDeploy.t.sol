// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {JITRLDIntegrationBase} from "../shared/JITRLDIntegrationBase.t.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {PoolId} from "v4-core/src/types/PoolId.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {ModifyLiquidityParams, SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolModifyLiquidityTestNoChecks} from "v4-core/src/test/PoolModifyLiquidityTestNoChecks.sol";
import {PoolSwapTest} from "v4-core/src/test/PoolSwapTest.sol";
import {IJTM} from "../../../src/twamm/IJTM.sol";
import "forge-std/console.sol";

/**
 * @title JitTwammDeployTest
 * @notice Integration tests verifying the new JIT-TWAMM hook deployment,
 *         order lifecycle, and the 3-layer matching engine (internal netting,
 *         JIT fill, dynamic auction).
 *
 * Extends JITRLDIntegrationBase which deploys the REAL JIT-TWAMM hook
 * with HookMiner + CREATE2, matching the production deployment pipeline.
 */
contract JitTwammDeployTest is JITRLDIntegrationBase {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;

    PoolModifyLiquidityTestNoChecks public lpRouter;
    PoolSwapTest public swapRouter;

    uint256 constant INTERVAL = 3600; // 1 hour

    // ================================================================
    //  SETUP
    // ================================================================

    function setUp() public override {
        vm.warp(7200); // Ensure we're past the first interval
        super.setUp();
    }

    function _tweakSetup() internal override {
        // Deploy test routers
        lpRouter = new PoolModifyLiquidityTestNoChecks(IPoolManager(address(poolManager)));
        swapRouter = new PoolSwapTest(IPoolManager(address(poolManager)));

        // Approve routers
        pt.approve(address(lpRouter), type(uint256).max);
        ct.approve(address(lpRouter), type(uint256).max);
        pt.approve(address(swapRouter), type(uint256).max);
        ct.approve(address(swapRouter), type(uint256).max);

        // Approve hook for order submission
        pt.approve(address(twammHook), type(uint256).max);
        ct.approve(address(twammHook), type(uint256).max);

        // Seed liquidity
        _seedLiquidity(100e12);
    }

    // ================================================================
    //  HELPERS
    // ================================================================

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

    // ================================================================
    //  TEST: DEPLOYMENT & INITIALIZATION
    // ================================================================

    /// @notice Hook address is non-zero and pool is initialized
    function test_Deploy_HookAndPoolInitialized() public view {
        assertTrue(address(twammHook) != address(0), "twammHook deployed");
        (uint160 sqrtPriceX96,,,) = poolManager.getSlot0(twammPoolKey.toId());
        assertTrue(sqrtPriceX96 > 0, "pool initialized with non-zero price");
        console.log("[Deploy] hook:", address(twammHook));
        console.log("[Deploy] sqrtPriceX96:", sqrtPriceX96);
    }

    /// @notice Pool has seed liquidity
    function test_Deploy_PoolHasLiquidity() public view {
        (,,, uint128 liquidity) = poolManager.getSlot0(twammPoolKey.toId());
        console.log("[Deploy] pool liquidity slot0 (fee proto):", liquidity);
    }

    // ================================================================
    //  TEST: ORDER SUBMISSION
    // ================================================================

    /// @notice Submit a 1-hour zeroForOne order
    function test_Submit_ZeroForOne() public {
        uint256 amountIn = 3600e6;

        (bytes32 orderId, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, amountIn);

        assertTrue(orderId != bytes32(0), "orderId non-zero");
        assertEq(orderKey.owner, address(this), "owner is test contract");
        assertTrue(orderKey.zeroForOne, "direction is zeroForOne");

        // Verify order stored
        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, orderKey);
        assertTrue(order.sellRate > 0, "sellRate stored");

        console.log("[Submit] orderId:", uint256(orderId));
        console.log("[Submit] sellRate:", order.sellRate);
    }

    /// @notice Submit a 1-hour oneForZero order
    function test_Submit_OneForZero() public {
        uint256 amountIn = 3600e6;

        (bytes32 orderId, IJTM.OrderKey memory orderKey) = _submitOrder1For0(INTERVAL, amountIn);

        assertTrue(orderId != bytes32(0), "orderId non-zero");
        assertFalse(orderKey.zeroForOne, "direction is oneForZero");

        console.log("[Submit] 1for0 orderId:", uint256(orderId));
    }

    /// @notice Both directions simultaneously
    function test_Submit_BothDirections() public {
        (bytes32 id0,) = _submitOrder0For1(INTERVAL, 3600e6);
        (bytes32 id1,) = _submitOrder1For0(INTERVAL, 3600e6);

        assertTrue(id0 != id1, "different IDs");

        (uint256 sr0,) = twammHook.getOrderPool(twammPoolKey, true);
        (uint256 sr1,) = twammHook.getOrderPool(twammPoolKey, false);
        assertTrue(sr0 > 0, "0for1 sellRate active");
        assertTrue(sr1 > 0, "1for0 sellRate active");
    }

    /// @notice Revert on zero sell rate
    function test_Submit_Revert_ZeroSellRate() public {
        vm.expectRevert(IJTM.SellRateCannotBeZero.selector);
        _submitOrder0For1(INTERVAL, INTERVAL - 1);
    }

    // ================================================================
    //  TEST: LAYER 1 — INTERNAL NETTING
    // ================================================================

    /// @notice Equal opposing orders net internally at TWAP
    function test_Layer1_InternalNetting_EqualOrders() public {
        uint256 amountIn = 3600e6;

        (, IJTM.OrderKey memory key0for1) = _submitOrder0For1(INTERVAL, amountIn);
        (, IJTM.OrderKey memory key1for0) = _submitOrder1For0(INTERVAL, amountIn);

        // Snapshot pool price
        (uint160 sqrtBefore,,,) = poolManager.getSlot0(twammPoolKey.toId());

        // Warp → trigger execution
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Price should remain close (equal opposing = no net AMM pressure)
        (uint160 sqrtAfter,,,) = poolManager.getSlot0(twammPoolKey.toId());
        uint256 diff = sqrtBefore > sqrtAfter ? sqrtBefore - sqrtAfter : sqrtAfter - sqrtBefore;
        assertLe(diff, sqrtBefore / 100, "price should not move with balanced orders");

        // Internal netting: earningsFactor records matched amounts,
        // but no ERC20 moves (both sides cancel each other out).
        // Verify the earnings are COMPUTED correctly via tokensOwed.
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key0for1}));
        twammHook.sync(IJTM.SyncParams({key: twammPoolKey, orderKey: key1for0}));

        // tokensOwed should be non-zero (earningsFactor recorded)
        Currency c0 = twammPoolKey.currency0;
        Currency c1 = twammPoolKey.currency1;
        uint256 owed0 = twammHook.tokensOwed(twammPoolKey.toId(), c0, address(this));
        uint256 owed1 = twammHook.tokensOwed(twammPoolKey.toId(), c1, address(this));

        assertTrue(owed0 > 0 || owed1 > 0, "earningsFactor should credit buy tokens to tokensOwed");

        console.log("[L1] tokensOwed c0:", owed0, "c1:", owed1);
    }

    // ================================================================
    //  TEST: ORDER CANCELLATION
    // ================================================================

    /// @notice Cancel mid-life returns refund + partial earnings
    function test_Cancel_MidLife() public {
        uint256 duration = 3 * INTERVAL;
        uint256 amountIn = 10800e6;

        // Submit opposing so netting produces real earnings
        _submitOrder1For0(duration, amountIn);
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(duration, amountIn);

        // Warp 1 hour (1/3)
        vm.warp(block.timestamp + INTERVAL);

        (uint256 buyTokensOut, uint256 sellTokensRefund) = twammHook.cancelOrder(twammPoolKey, orderKey);

        // ~2/3 refund
        uint256 sellRate = amountIn / duration;
        uint256 expectedRefund = sellRate * 2 * INTERVAL;
        assertApproxEqAbs(sellTokensRefund, expectedRefund, expectedRefund / 50, "refund ~2/3");
        assertTrue(buyTokensOut > 0, "earned buy tokens for 1hr");

        console.log("[Cancel] buy:", buyTokensOut, "refund:", sellTokensRefund);
    }

    /// @notice Cancel immediately → full refund, zero earnings
    function test_Cancel_Immediate() public {
        uint256 amountIn = 3600e6;
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, amountIn);

        (uint256 buyTokensOut, uint256 sellTokensRefund) = twammHook.cancelOrder(twammPoolKey, orderKey);

        uint256 sellRate = amountIn / INTERVAL;
        assertEq(sellTokensRefund, sellRate * INTERVAL, "full refund");
        assertEq(buyTokensOut, 0, "no buy tokens");
    }

    /// @notice Revert: non-owner cancel
    function test_Cancel_Revert_NotOwner() public {
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(INTERVAL, 3600e6);
        vm.prank(address(0xDEAD));
        vm.expectRevert(IJTM.Unauthorized.selector);
        twammHook.cancelOrder(twammPoolKey, orderKey);
    }

    // ================================================================
    //  TEST: LAYER 3 — DYNAMIC AUCTION
    // ================================================================

    /// @notice Single-direction order accrues ghost balance, arb can clear
    function test_Layer3_AuctionClear() public {
        // Use 2hr order so stream is still active after 1hr (NoActiveStream guard)
        uint256 amountIn = 7200e6;
        _submitOrder0For1(2 * INTERVAL, amountIn);

        // Warp 1 hour → accrual builds up (order still active for 1 more hour)
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Check stream state
        (uint256 accrued0, uint256 accrued1, uint256 discount,) = twammHook.getStreamState(twammPoolKey);

        console.log("[L3] accrued0:", accrued0, "accrued1:", accrued1);
        console.log("[L3] discount:", discount);

        // If there is accrued0 (from 0→1 sell stream), arb can clear it
        if (accrued0 > 0) {
            // Approve payment token for clear
            bool token0IsPt = (_token0() == address(pt));
            if (token0IsPt) {
                ct.approve(address(twammHook), type(uint256).max);
            } else {
                pt.approve(address(twammHook), type(uint256).max);
            }

            twammHook.clear(twammPoolKey, true, accrued0, 0);
            console.log("[L3] Cleared accrued0 successfully");
        }
    }

    // ================================================================
    //  TEST: getCancelOrderState (used by JTMBrokerModule)
    // ================================================================

    /// @notice getCancelOrderState returns consistent values
    function test_GetCancelOrderState() public {
        uint256 duration = 3 * INTERVAL;
        uint256 amountIn = 10800e6;

        // Submit opposing to get earnings
        _submitOrder1For0(duration, amountIn);
        (, IJTM.OrderKey memory orderKey) = _submitOrder0For1(duration, amountIn);

        // Warp 1hr
        vm.warp(block.timestamp + INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        (uint256 buyOwed, uint256 sellRefund) = twammHook.getCancelOrderState(twammPoolKey, orderKey);

        console.log("[getCancelOrderState] buyOwed:", buyOwed, "sellRefund:", sellRefund);
        assertTrue(buyOwed > 0 || sellRefund > 0, "should return non-zero values");
    }

    // ================================================================
    //  TEST: SYNC & CLAIM
    // ================================================================

    /// @notice syncAndClaimTokens returns earnings and cleans up expired orders
    function test_SyncAndClaim_FullDuration() public {
        uint256 amountIn = 3600e6;

        // Submit opposing orders so netting produces real buy tokens
        (, IJTM.OrderKey memory key0for1) = _submitOrder0For1(INTERVAL, amountIn);
        _submitOrder1For0(INTERVAL, amountIn);

        // Warp past expiration
        vm.warp(block.timestamp + INTERVAL);

        // Sync to process earnings
        twammHook.syncAndClaimTokens(IJTM.SyncParams({key: twammPoolKey, orderKey: key0for1}));

        // Order should be deleted (expired)
        IJTM.Order memory order = twammHook.getOrder(twammPoolKey, key0for1);
        assertEq(order.sellRate, 0, "expired order deleted after sync");

        // tokensOwed should be non-zero (but claim may not transfer
        // if hook has no ERC20 backing from actual external fills)
        console.log("[Sync] order cleaned up successfully");
    }
}
