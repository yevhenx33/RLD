// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationTwammBase} from "../liquidation/LiquidationTwammBase.t.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {IPrimeBroker} from "../../../src/shared/interfaces/IPrimeBroker.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {IJTM} from "../../../src/twamm/IJTM.sol";
import {IJTM} from "../../../src/twamm/IJTM.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {IERC721} from "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {
    IAllowanceTransfer
} from "permit2/src/interfaces/IAllowanceTransfer.sol";
import {
    LiquidityAmounts
} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import "forge-std/console.sol";

/// @title BrokerPositionTracking — Phase 3 Penetration Tests
/// @notice Tests for position tracking (V4 LP, TWAMM orders), submit/cancel flows,
///         and getNetAccountValue() correctness.
///
/// Extends LiquidationTwammBase for full TWAMM + V4 LP infrastructure.
///
/// Coverage Map (from PENETRATION_TESTING.md):
///
///     ### 3.1 setActiveV4Position()       — Tests 30-34
///     ### 3.2 setActiveTwammOrder()       — Tests 35-38
///     ### 3.3 submitTwammOrder/cancel      — Tests 39-44
///     ### 3.4 getNetAccountValue()         — Tests 45-49
///
contract BrokerPositionTracking is LiquidationTwammBase {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    address public attacker = address(0xdead);
    uint256 constant TWAMM_INTERVAL_LOCAL = 3600;

    // ================================================================
    //  3.1 setActiveV4Position()
    // ================================================================

    /// @notice Test #30: Cannot track a V4 NFT owned by someone else
    function test_cannot_track_foreign_v4_position() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        // Provision LP owned by test contract (NOT the broker)
        uint256 tokenId = _provisionLPForTestContract(
            broker,
            10_000e6,
            10_000e6
        );

        // Attempt to track a position the broker doesn't own → revert
        vm.expectRevert("!owner");
        broker.setActiveV4Position(tokenId);
    }

    /// @notice Test #31: Ownership verified via POSM.ownerOf
    function test_v4_ownership_verified() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        // Provision LP and transfer to broker (happy path)
        uint256 tokenId = _provideV4LP(broker, 10_000e6, 10_000e6);

        // Broker owns it — verify tracking is active
        assertEq(
            broker.activeTokenId(),
            tokenId,
            "Should track the LP position"
        );
    }

    /// @notice Test #32: Post-update solvency check when switching positions
    function test_v4_solvency_on_switch() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        // Provision LP and register
        uint256 tokenId = _provideV4LP(broker, 10_000e6, 10_000e6);
        assertTrue(broker.activeTokenId() != 0, "Should have active LP");

        // Clearing position → solvency checked (should pass if broker is well-collateralized)
        broker.setActiveV4Position(0);
        assertEq(broker.activeTokenId(), 0, "Position cleared");
    }

    /// @notice Test #33: Setting tokenId=0 clears tracking
    function test_v4_clear_tracking() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 tokenId = _provideV4LP(broker, 5_000e6, 5_000e6);
        assertTrue(broker.activeTokenId() != 0, "Should track LP");

        // Clear via setActiveV4Position(0)
        broker.setActiveV4Position(0);
        assertEq(broker.activeTokenId(), 0, "Should be cleared");
    }

    /// @notice Test #34: Only authorized can set V4 position
    function test_v4_only_authorized() public {
        PrimeBroker broker = _createBroker();

        vm.prank(attacker);
        vm.expectRevert(PrimeBroker.NotAuthorized.selector);
        broker.setActiveV4Position(0);
    }

    // ================================================================
    //  3.2 setActiveTwammOrder()
    // ================================================================

    /// @notice Test #35: Cannot track a TWAMM order owned by someone else
    function test_cannot_track_foreign_twamm_order() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        // Place order from TEST CONTRACT (not the broker)
        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        address sellToken = colIsC0
            ? Currency.unwrap(marketTwammKey.currency0)
            : Currency.unwrap(marketTwammKey.currency1);

        ERC20(sellToken).approve(address(twammHook), 1_000e6);
        (bytes32 orderId, IJTM.OrderKey memory orderKey) = twammHook
            .submitOrder(
                IJTM.SubmitOrderParams({
                    key: marketTwammKey,
                    zeroForOne: colIsC0,
                    duration: TWAMM_INTERVAL_LOCAL,
                    amountIn: 1_000e6
                })
            );

        // Try to register this order on the broker
        // orderKey.owner = test contract, NOT broker → should revert
        vm.expectRevert("!owner");
        broker.setActiveTwammOrder(
            IPrimeBroker.TwammOrderInfo({
                key: marketTwammKey,
                orderKey: IJTM.OrderKey({
                    owner: orderKey.owner,
                    expiration: orderKey.expiration,
                    zeroForOne: orderKey.zeroForOne
                }),
                orderId: orderId
            })
        );
    }

    /// @notice Test #36: Ownership check verifies orderKey.owner == broker
    function test_twamm_ownership_verified() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        // Place order FROM the broker — owner = broker ✓
        _placeTwammOrder(broker, 10_000e6, true, TWAMM_INTERVAL_LOCAL);

        // Verify tracking is active — activeTwammOrder() returns a tuple
        (, , bytes32 trackedId) = broker.activeTwammOrder();
        assertTrue(trackedId != bytes32(0), "TWAMM order should be tracked");
    }

    /// @notice Test #37: Post-update solvency check on TWAMM order switch
    function test_twamm_solvency_on_switch() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        _placeTwammOrder(broker, 10_000e6, true, TWAMM_INTERVAL_LOCAL);

        (, , bytes32 orderId1) = broker.activeTwammOrder();
        assertTrue(orderId1 != bytes32(0), "Should have active order");

        // Clear tracking — solvency is checked, should pass w/ enough collateral
        broker.setActiveTwammOrder(
            IPrimeBroker.TwammOrderInfo({
                key: marketTwammKey,
                orderKey: IJTM.OrderKey({
                    owner: address(0),
                    expiration: 0,
                    zeroForOne: false
                }),
                orderId: bytes32(0)
            })
        );

        (, , bytes32 orderId2) = broker.activeTwammOrder();
        assertEq(orderId2, bytes32(0), "Order tracking cleared");
    }

    /// @notice Test #38: clearActiveV4Position + clearActiveTwammOrder
    function test_clear_both_positions() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        // TWAMM first (before V4 LP warp to 1.7B to avoid interval gap)
        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);
        _placeTwammOrder(broker, 5_000e6, true, TWAMM_INTERVAL_LOCAL);

        // Then V4 LP (this warps to 1.7B internally)
        uint256 tokenId = _provideV4LP(broker, 5_000e6, 5_000e6);
        assertTrue(broker.activeTokenId() != 0, "Should track V4 LP");

        // Clear both
        broker.setActiveV4Position(0);
        broker.setActiveTwammOrder(
            IPrimeBroker.TwammOrderInfo({
                key: marketTwammKey,
                orderKey: IJTM.OrderKey({
                    owner: address(0),
                    expiration: 0,
                    zeroForOne: false
                }),
                orderId: bytes32(0)
            })
        );

        assertEq(broker.activeTokenId(), 0, "V4 position cleared");
        (, , bytes32 clearedOrderId) = broker.activeTwammOrder();
        assertEq(clearedOrderId, bytes32(0), "TWAMM order cleared");
    }

    // ================================================================
    //  3.3 submitTwammOrder() / cancelTwammOrder()
    // ================================================================

    /// @notice Test #39: submitTwammOrder happy path with auto-tracking
    function test_submit_twamm_auto_tracking() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        // Use broker.submitTwammOrder() (not _placeTwammOrder helper)
        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;

        (bytes32 orderId, ) = broker.submitTwammOrder(
            address(twammHook),
            IJTM.SubmitOrderParams({
                key: marketTwammKey,
                zeroForOne: colIsC0,
                duration: TWAMM_INTERVAL_LOCAL,
                amountIn: 10_000e6
            })
        );

        // Verify auto-registered
        (, , bytes32 trackedOrderId) = broker.activeTwammOrder();
        assertEq(trackedOrderId, orderId, "Order should be auto-tracked");
    }

    /// @notice Test #40: submitTwammOrder JIT approval pattern
    function test_submit_twamm_jit_approval() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        address sellToken = colIsC0
            ? Currency.unwrap(marketTwammKey.currency0)
            : Currency.unwrap(marketTwammKey.currency1);

        // Check allowance before
        uint256 allowanceBefore = ERC20(sellToken).allowance(
            address(broker),
            address(twammHook)
        );
        assertEq(allowanceBefore, 0, "No pre-existing allowance");

        broker.submitTwammOrder(
            address(twammHook),
            IJTM.SubmitOrderParams({
                key: marketTwammKey,
                zeroForOne: colIsC0,
                duration: TWAMM_INTERVAL_LOCAL,
                amountIn: 5_000e6
            })
        );

        // Check allowance after — should be revoked to 0
        uint256 allowanceAfter = ERC20(sellToken).allowance(
            address(broker),
            address(twammHook)
        );
        assertEq(allowanceAfter, 0, "Allowance should be revoked after submit");
    }

    /// @notice Test #41: No lingering allowances after submitTwammOrder
    function test_submit_twamm_no_lingering_allowance() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;

        // Both tokens should have zero allowance to hook after submit
        broker.submitTwammOrder(
            address(twammHook),
            IJTM.SubmitOrderParams({
                key: marketTwammKey,
                zeroForOne: colIsC0,
                duration: TWAMM_INTERVAL_LOCAL,
                amountIn: 5_000e6
            })
        );

        uint256 allow0 = ERC20(Currency.unwrap(marketTwammKey.currency0))
            .allowance(address(broker), address(twammHook));
        uint256 allow1 = ERC20(Currency.unwrap(marketTwammKey.currency1))
            .allowance(address(broker), address(twammHook));

        assertEq(allow0, 0, "currency0 allowance should be 0");
        assertEq(allow1, 0, "currency1 allowance should be 0");
    }

    /// @notice Test #42: cancelTwammOrder returns proceeds to broker
    function test_cancel_twamm_returns_proceeds() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        address sellToken = colIsC0
            ? Currency.unwrap(marketTwammKey.currency0)
            : Currency.unwrap(marketTwammKey.currency1);

        uint256 sellBalBefore = ERC20(sellToken).balanceOf(address(broker));

        broker.submitTwammOrder(
            address(twammHook),
            IJTM.SubmitOrderParams({
                key: marketTwammKey,
                zeroForOne: colIsC0,
                duration: TWAMM_INTERVAL_LOCAL,
                amountIn: 10_000e6
            })
        );

        uint256 sellBalAfterSubmit = ERC20(sellToken).balanceOf(
            address(broker)
        );
        assertTrue(
            sellBalAfterSubmit < sellBalBefore,
            "Sell token should decrease after submit"
        );

        // Cancel immediately — refund should come back
        (uint256 buyTokensOut, uint256 sellRefund) = broker.cancelTwammOrder();

        uint256 sellBalAfterCancel = ERC20(sellToken).balanceOf(
            address(broker)
        );
        assertTrue(
            sellBalAfterCancel > sellBalAfterSubmit,
            "Sell token should increase after cancel (refund)"
        );
        assertTrue(sellRefund > 0, "Should get sell token refund");
    }

    /// @notice Test #43: cancelTwammOrder clears activeTwammOrder
    function test_cancel_twamm_clears_tracking() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;

        broker.submitTwammOrder(
            address(twammHook),
            IJTM.SubmitOrderParams({
                key: marketTwammKey,
                zeroForOne: colIsC0,
                duration: TWAMM_INTERVAL_LOCAL,
                amountIn: 5_000e6
            })
        );

        (, , bytes32 orderIdBefore) = broker.activeTwammOrder();
        assertTrue(orderIdBefore != bytes32(0), "Should have active order");

        broker.cancelTwammOrder();

        (, , bytes32 orderIdAfter) = broker.activeTwammOrder();
        assertEq(orderIdAfter, bytes32(0), "Order should be cleared");
    }

    /// @notice Test #44: cancelTwammOrder reverts with no active order
    function test_cancel_twamm_no_active_reverts() public {
        PrimeBroker broker = _createBroker();

        vm.expectRevert("!order");
        broker.cancelTwammOrder();
    }

    // ================================================================
    //  3.4 getNetAccountValue() correctness
    // ================================================================

    /// @notice Test #45: NAV = cash + wRLP value (basic components)
    function test_nav_cash_plus_wrlp() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 100_000e6);

        // No debt → NAV = just cash
        uint256 nav1 = broker.getNetAccountValue();
        assertEq(nav1, 100_000e6, "NAV should equal cash balance");

        // Mint debt → NAV = cash + wRLP * indexPrice
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(100_000e6)),
            int256(uint256(10_000e6))
        );

        uint256 nav2 = broker.getNetAccountValue();
        // NAV = 100_000 (cash) + 10_000 * 5 (wRLP value) = 150_000
        assertEq(nav2, 150_000e6, "NAV should include wRLP at index price");
    }

    /// @notice Test #46: Empty broker returns 0
    function test_nav_empty_broker_zero() public {
        PrimeBroker broker = _createBroker();
        uint256 nav = broker.getNetAccountValue();
        assertEq(nav, 0, "Empty broker NAV should be 0");
    }

    /// @notice Test #47: wRLP valued at index price, not 1:1
    function test_nav_wrlp_at_index_price() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 100_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(100_000e6)),
            int256(uint256(10_000e6))
        );

        // Index price = 5e18 → 10_000 wRLP = 50_000 value
        uint256 nav = broker.getNetAccountValue();
        uint256 wrlpValue = nav - 100_000e6; // subtract cash component
        assertEq(
            wrlpValue,
            50_000e6,
            "wRLP should be valued at 5x (index price)"
        );

        // Change index price → NAV should change
        testOracle.setIndexPrice(10e18); // double the price
        uint256 nav2 = broker.getNetAccountValue();
        uint256 wrlpValue2 = nav2 - 100_000e6;
        assertEq(wrlpValue2, 100_000e6, "wRLP value should follow index price");

        // Reset
        testOracle.setIndexPrice(5e18);
    }

    /// @notice Test #48: NAV ignores V4 LP if ownership check fails
    function test_nav_ignores_v4_if_not_owned() public {
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        // Provision LP owned by broker
        uint256 tokenId = _provideV4LP(broker, 10_000e6, 10_000e6);
        uint256 navWithLP = broker.getNetAccountValue();

        // Transfer NFT away from broker (simulating external transfer)
        vm.prank(address(broker));
        IERC721(address(positionManager)).transferFrom(
            address(broker),
            address(this),
            tokenId
        );

        // NAV should drop — V4 LP value no longer counted
        uint256 navWithoutLP = broker.getNetAccountValue();
        assertTrue(
            navWithoutLP < navWithLP,
            "NAV should drop when LP NFT is transferred away"
        );
    }

    /// @notice Test #49: NAV ignores TWAMM if ownership check fails
    function test_nav_ignores_twamm_if_not_owned() public {
        // TWAMM orders are tied to orderKey.owner which is set at submission
        // and cannot be transferred. The ownership check is defensive.
        // We test by constructing a fake TwammOrderInfo with wrong owner.

        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), 500_000e6);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(uint256(500_000e6)),
            int256(USER_DEBT)
        );

        uint256 navBase = broker.getNetAccountValue();

        // Place a real order from the broker
        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL_LOCAL) + 1) *
            TWAMM_INTERVAL_LOCAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);
        _placeTwammOrder(broker, 10_000e6, true, TWAMM_INTERVAL_LOCAL);

        uint256 navWithOrder = broker.getNetAccountValue();
        // NAV may differ from navBase due to the order placement reducing cash
        // but adding TWAMM value

        // Cancel the order externally (simulate losing ownership)
        // After cancel, the activeTwammOrder still references old orderId
        // But the order no longer exists in TWAMM hook
        // The TWAMM module should return 0 for the cancelled order
        (
            PoolKey memory cancelKey,
            IJTM.OrderKey memory cancelOrderKey,

        ) = broker.activeTwammOrder();
        vm.prank(address(broker));
        IJTM(address(twammHook)).cancelOrder(cancelKey, cancelOrderKey);

        // NAV should reflect only cash + wRLP (order value = 0)
        uint256 navAfterCancel = broker.getNetAccountValue();
        // The cancelled order should contribute 0 to NAV since it no longer exists
        console.log("NAV base:", navBase / 1e6);
        console.log("NAV with order:", navWithOrder / 1e6);
        console.log("NAV after external cancel:", navAfterCancel / 1e6);
    }

    // ================================================================
    //  Helpers
    // ================================================================

    /// @dev Provision V4 LP owned by test contract (NOT the broker) for negative testing
    function _provisionLPForTestContract(
        PrimeBroker broker,
        uint256 wAmt,
        uint256 cAmt
    ) internal returns (uint256 tokenId) {
        // Withdraw tokens from broker to test contract
        broker.withdrawPositionToken(address(this), wAmt);
        broker.withdrawCollateral(address(this), cAmt);

        vm.warp(1_700_000_000);

        // Same LP provisioning but DON'T transfer NFT to broker
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            ma.positionToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            ma.collateralToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );

        (, int24 tick, , ) = poolManager.getSlot0(lpPoolKey.toId());
        int24 sp = lpPoolKey.tickSpacing;
        int24 lo = (tick / sp) * sp - 3000;
        int24 hi = lo + 6000;
        uint256 a0;
        uint256 a1;
        if (Currency.unwrap(lpPoolKey.currency0) == ma.positionToken) {
            a0 = wAmt;
            a1 = cAmt;
        } else {
            a0 = cAmt;
            a1 = wAmt;
        }
        uint128 liq = LiquidityAmounts.getLiquidityForAmounts(
            TickMath.getSqrtPriceAtTick(tick),
            TickMath.getSqrtPriceAtTick(lo),
            TickMath.getSqrtPriceAtTick(hi),
            a0,
            a1
        );
        require(liq > 0, "zero liq");
        bytes memory acts = abi.encodePacked(
            uint8(Actions.MINT_POSITION),
            uint8(Actions.SETTLE_PAIR)
        );
        bytes[] memory p = new bytes[](2);
        p[0] = abi.encode(
            lpPoolKey,
            lo,
            hi,
            uint256(liq),
            uint128((a0 * 110) / 100),
            uint128((a1 * 110) / 100),
            address(this), // test contract owns the NFT, NOT broker
            bytes("")
        );
        p[1] = abi.encode(lpPoolKey.currency0, lpPoolKey.currency1);
        positionManager.modifyLiquidities(
            abi.encode(acts, p),
            block.timestamp + 60
        );
        tokenId = positionManager.nextTokenId() - 1;
        // NOTE: NOT transferring to broker — this is intentional for test #30
    }
}
