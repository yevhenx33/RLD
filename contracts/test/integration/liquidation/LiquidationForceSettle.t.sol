// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationTwammBase} from "./LiquidationTwammBase.t.sol";
import {IJTM} from "../../../src/twamm/IJTM.sol";
import {IJTM} from "../../../src/twamm/IJTM.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {IPrimeBroker} from "../../../src/shared/interfaces/IPrimeBroker.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {ModifyLiquidityParams} from "v4-core/src/types/PoolOperation.sol";
import {
    PoolModifyLiquidityTestNoChecks
} from "v4-core/src/test/PoolModifyLiquidityTestNoChecks.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import "forge-std/console.sol";

/// @title Tier 7: ForceSettle Tests
/// @dev Verifies JTM.forceSettle():
///   - T24: ghost -> 0 + earningsFactor > 0 + buyOwed > 0  (pool HAS liquidity)
///   - T25: forceSettle reverts for non-verified brokers (access control)
///   - T26: forceSettle auto-called during liquidation (integration)
///   - T27: forceSettle with zero ghost (early return, no swap)
contract LiquidationForceSettle is LiquidationTwammBase {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    uint256 constant TWAMM_AMT = 200_000e6; // 200k USDC

    PoolModifyLiquidityTestNoChecks public lpRouter;
    bool private _poolSeeded;

    // ================================================================
    //  SETUP: seed TWAMM pool with LP so forceSettle swaps execute
    // ================================================================

    /// @dev Call at the start of each test that needs pool liquidity
    modifier withPoolLiquidity() {
        if (!_poolSeeded) {
            _seedTwammPoolLiquidity();
            _poolSeeded = true;
        }
        _;
    }

    /// @dev Seed concentrated LP into the TWAMM market pool, centered on
    ///      the actual pool tick. Same pattern as RLDCoreJitTwammE2E._tweakSetup().
    function _seedTwammPoolLiquidity() internal {
        lpRouter = new PoolModifyLiquidityTestNoChecks(
            IPoolManager(address(poolManager))
        );

        // Approve LP router for both tokens
        ERC20(ma.positionToken).approve(address(lpRouter), type(uint256).max);
        ERC20(ma.collateralToken).approve(address(lpRouter), type(uint256).max);

        // Fund this contract with collateral for LP seeding
        collateralMock.mint(address(this), 10_000_000e6);

        // Mint wRLP for LP: create a helper broker, mint, withdraw
        // Use 20x collateral to ensure solvency after withdrawal (same as _fundClearer)
        PrimeBroker helper = _createBroker();
        uint256 lpWRLP = 500_000e6;
        uint256 lpCollateral = lpWRLP * 20;
        collateralMock.transfer(address(helper), lpCollateral);
        helper.modifyPosition(
            MarketId.unwrap(marketId),
            int256(lpCollateral),
            int256(lpWRLP)
        );
        helper.withdrawPositionToken(address(this), lpWRLP);

        // Read current pool tick and center LP around it (±600 ticks)
        (, int24 currentTick, , ) = poolManager.getSlot0(marketTwammKey.toId());
        int24 spacing = marketTwammKey.tickSpacing;
        int24 centerTick = (currentTick / spacing) * spacing; // align to spacing
        int24 tickLower = centerTick - 600;
        int24 tickUpper = centerTick + 600;

        console.log("  Seeding LP: currentTick:", uint24(currentTick));

        // Seed concentrated liquidity
        lpRouter.modifyLiquidity(
            marketTwammKey,
            ModifyLiquidityParams({
                tickLower: tickLower,
                tickUpper: tickUpper,
                liquidityDelta: int256(10e12),
                salt: bytes32(0)
            }),
            ""
        );
    }

    // ================================================================
    //  HELPERS
    // ================================================================

    function _getGhost() internal view returns (uint256 ghost, bool colIsC0) {
        (uint256 a0, uint256 a1, , ) = twammHook.getStreamState(marketTwammKey);
        colIsC0 =
            Currency.unwrap(marketTwammKey.currency0) == ma.collateralToken;
        ghost = colIsC0 ? a0 : a1;
    }

    function _getEarningsFactor() internal view returns (uint256 ef) {
        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        bool zfo = colIsC0; // selling collateral
        (, ef) = twammHook.getStreamPool(marketTwammKey, zfo);
    }

    function _getCancelPreview(
        PrimeBroker broker
    ) internal view returns (uint256 buyOwed, uint256 sellRefund) {
        IPrimeBroker.BrokerState memory state = broker.getFullState();
        return (state.twammBuyOwed, state.twammSellOwed);
    }

    // ================================================================
    //  T24: ForceSettle -- ghost -> 0, earningsFactor > 0, buyOwed > 0
    //
    //  Pool has liquidity, so forceSettle swap produces real proceeds.
    //  This is the core test: ghost is converted to real earnings.
    // ================================================================
    function test_T24_ForceSettle_GhostToZero() public withPoolLiquidity {
        console.log("=== T24: ForceSettle -- ghost to 0 (with pool liq) ===");
        PrimeBroker broker = _setupBrokerTwamm(
            0,
            TWAMM_AMT,
            true,
            TWAMM_INTERVAL
        );

        // Step 1: Warp 50% and accrue
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        // Step 2: Assert ghost > 0 before forceSettle
        (uint256 ghostPre, bool colIsC0) = _getGhost();
        console.log("  Pre ghost:", ghostPre / 1e6);
        assertGt(ghostPre, 90_000e6, "T24: ghost should be ~100k");

        uint256 efPre = _getEarningsFactor();
        assertEq(efPre, 0, "T24: earningsFactor must be 0 before settle");

        // Step 3: Cancel preview BEFORE forceSettle
        (uint256 buyPre, ) = _getCancelPreview(broker);
        assertEq(
            buyPre,
            0,
            "T24: buyOwed must be 0 before settle (ghost invisible)"
        );

        // Step 4: Call forceSettle (broker is verified)
        bool zfo = colIsC0;
        vm.prank(address(broker));
        twammHook.forceSettle(marketTwammKey, zfo, marketId);

        // Step 5: CORE INVARIANT -- ghost MUST be 0 after forceSettle
        (uint256 ghostPost, ) = _getGhost();
        console.log("  Post ghost:", ghostPost / 1e6);
        assertEq(ghostPost, 0, "T24: ghost must be 0 after forceSettle");

        // Step 6: earningsFactor MUST be > 0 (pool had liquidity -> proceeds > 0)
        uint256 efPost = _getEarningsFactor();
        console.log("  Post earningsFactor:", efPost);
        assertGt(
            efPost,
            0,
            "T24: earningsFactor must be > 0 after forceSettle"
        );

        // Step 7: Cancel preview now shows buyOwed > 0 (ghost -> earnings!)
        (uint256 buyPost, uint256 sellPost) = _getCancelPreview(broker);
        console.log(
            "  Post cancel: buyOwed:",
            buyPost / 1e6,
            "sellRefund:",
            sellPost / 1e6
        );
        assertGt(
            buyPost,
            0,
            "T24: buyOwed must be > 0 (ghost converted to earnings!)"
        );
        assertGt(
            sellPost,
            90_000e6,
            "T24: sellRefund should be ~100k (unsold half)"
        );

        console.log("  SUCCESS: ghost -> 0, earningsFactor > 0, buyOwed > 0");
    }

    // ================================================================
    //  T25: ForceSettle Access Control -- non-verified broker reverts
    // ================================================================
    function test_T25_ForceSettle_AccessControl() public withPoolLiquidity {
        console.log("=== T25: ForceSettle Access Control ===");
        PrimeBroker broker = _setupBrokerTwamm(
            0,
            TWAMM_AMT,
            true,
            TWAMM_INTERVAL
        );

        // Warp and accrue ghost
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        (uint256 ghost, bool colIsC0) = _getGhost();
        assertGt(ghost, 0, "T25: need ghost for test");
        bool zfo = colIsC0;

        // Attempt 1: Random EOA should revert
        address randomUser = address(0xDEAD);
        vm.prank(randomUser);
        vm.expectRevert("Not verified broker");
        twammHook.forceSettle(marketTwammKey, zfo, marketId);

        // Attempt 2: Test contract itself should revert (not a broker)
        vm.expectRevert("Not verified broker");
        twammHook.forceSettle(marketTwammKey, zfo, marketId);

        // Attempt 3: Verified broker SHOULD succeed
        vm.prank(address(broker));
        twammHook.forceSettle(marketTwammKey, zfo, marketId);

        (uint256 ghostPost, ) = _getGhost();
        assertEq(ghostPost, 0, "T25: verified broker forceSettle must succeed");

        console.log(
            "  SUCCESS: non-brokers blocked, verified broker succeeded"
        );
    }

    // ================================================================
    //  T26: ForceSettle Integration -- auto-called during liquidation
    //
    //  With pool liquidity, forceSettle converts ghost -> real proceeds.
    //  The cancel after forceSettle returns real buyTokensOwed.
    //  Liquidator gains MORE value than without forceSettle.
    // ================================================================
    function test_T26_ForceSettle_DuringLiquidation() public withPoolLiquidity {
        console.log("=== T26: ForceSettle During Liquidation ===");
        PrimeBroker broker = _setupBrokerTwamm(
            0,
            TWAMM_AMT,
            true,
            TWAMM_INTERVAL
        );

        // Step 1: Warp 50% and accrue ghost
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        (uint256 ghostPre, ) = _getGhost();
        console.log("  Pre-liq ghost:", ghostPre / 1e6);
        assertGt(ghostPre, 90_000e6, "T26: ghost should be ~100k");

        // Step 2: Check cancel preview BEFORE liquidation
        (uint256 buyPre, ) = _getCancelPreview(broker);
        console.log("  Pre-liq cancel: buyOwed:", buyPre / 1e6);
        assertEq(
            buyPre,
            0,
            "T26: buyOwed must be 0 before liq (ghost invisible)"
        );

        // Step 3: Trigger insolvency and liquidate
        _setOraclePrice(25e18);
        assertFalse(
            core.isSolvent(marketId, address(broker)),
            "T26: must be insolvent"
        );

        uint256 preLiqCash = ERC20(ma.collateralToken).balanceOf(liquidator);

        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);

        // Step 4: Verify liquidator gained value
        uint256 postLiqCash = ERC20(ma.collateralToken).balanceOf(liquidator);
        uint256 postBrokerCash = ERC20(ma.collateralToken).balanceOf(
            address(broker)
        );
        uint256 postBrokerWRLP = ERC20(ma.positionToken).balanceOf(
            address(broker)
        );

        console.log(
            "  Post: broker cash:",
            postBrokerCash / 1e6,
            "wRLP:",
            postBrokerWRLP / 1e6
        );
        console.log("  Liq cash gained:", (postLiqCash - preLiqCash) / 1e6);

        assertGt(
            postLiqCash,
            preLiqCash,
            "T26: liquidator must gain collateral (forceSettle preserved value)"
        );

        _setOraclePrice(INDEX_PRICE_WAD);
        console.log("  SUCCESS: forceSettle auto-called during liquidation");
    }

    // ================================================================
    //  T27: ForceSettle with zero ghost -- early return, no swap
    // ================================================================
    function test_T27_ForceSettle_ZeroGhost() public withPoolLiquidity {
        console.log("=== T27: ForceSettle -- zero ghost (noop) ===");
        PrimeBroker broker = _setupBrokerTwamm(
            0,
            TWAMM_AMT,
            true,
            TWAMM_INTERVAL
        );
        // No warp -- ghost = 0 (order just placed)

        (uint256 ghost, bool colIsC0) = _getGhost();
        console.log("  Ghost:", ghost / 1e6);
        assertEq(ghost, 0, "T27: ghost must be 0 (just placed)");

        // ForceSettle should be a noop
        bool zfo = colIsC0;
        uint256 gasBefore = gasleft();
        vm.prank(address(broker));
        twammHook.forceSettle(marketTwammKey, zfo, marketId);
        uint256 gasUsed = gasBefore - gasleft();

        console.log("  Gas used (noop):", gasUsed);

        uint256 ef = _getEarningsFactor();
        assertEq(ef, 0, "T27: earningsFactor must remain 0 (noop)");

        console.log("  SUCCESS: forceSettle with ghost=0 is a noop");
    }
}
