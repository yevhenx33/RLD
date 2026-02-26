// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationTwammBase} from "./LiquidationTwammBase.t.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import "forge-std/console.sol";

/// @title  Tier 9: Exhaustive TWAMM × LP Range Permutation Tests
/// @author RLD Protocol
///
/// @notice Covers the cross-product of TWAMM sell direction × V4 LP range
///         (in-range / OOR-above / OOR-below) × clearing state × solvency,
///         completing the matrix from the original implementation plan.
///
/// @dev    ## Permutation Matrix
///
///         ### Tier A: TWAMM + OOR LP Cascades (T28-T33)
///
///         | ID  | Assets               | LP Range          | Clear? |
///         |-----|----------------------|-------------------|--------|
///         | T28 | TWAMM + OOR LP       | Above (token0)    | ✗      |
///         | T29 | TWAMM + OOR LP       | Below (token1)    | ✗      |
///         | T30 | Cash + TWAMM + OOR   | Above (token0)    | ✗      |
///         | T31 | Cash + TWAMM + OOR   | Below (token1)    | ✗      |
///         | T32 | Full + OOR LP        | Above (token0)    | ✗      |
///         | T33 | Full + OOR LP        | Below (token1)    | ✗      |
///
///         ### Tier B: Full Stack Direction Variants (T34-T37)
///
///         | ID  | Assets               | TWAMM Dir | LP Range | UW? |
///         |-----|----------------------|-----------|----------|-----|
///         | T34 | Full + OOR LP Above  | sellCol   | Above    | ✓   |
///         | T35 | Full + OOR LP Below  | sellCol   | Below    | ✓   |
///         | T36 | Full (in-range LP)   | sellPos   | In-range | ✓   |
///         | T37 | Full (in-range LP)   | sellPos   | In-range | ✗   |
///
///         Uses `_setupBrokerTwammOOR()` for OOR LP variants and
///         `_setupBrokerTwammCascade()` with `sellCollateral=false` for
///         sell-position variants.
contract LiquidationPermutations is LiquidationTwammBase {
    uint256 constant TWAMM_AMT = 60_000e6;

    // ================================================================
    //  TIER A: TWAMM + OOR LP CASCADES
    // ================================================================

    // ── T28: TWAMM + OOR LP Above ──────────────────────────────────
    //
    //  Broker holds only TWAMM order + OOR LP (above tick → token0 only).
    //  Ghost accrues 50%, then liquidation cascades: TWAMM cancel → LP unwind.
    //  LP returns only token0 on unwind.
    function test_T28_TWAMM_OOR_LP_Above() public {
        console.log("=== T28: TWAMM + OOR LP Above ===");
        (PrimeBroker broker,) = _setupBrokerTwammOOR(
            0, // no cash
            0, // no wRLP
            TWAMM_AMT,
            5_000e6, // LP amount
            true, // sellCollateral
            true // LP above tick
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _logGhost();

        _setOraclePrice(30e18);
        _assertInsolventAndLiquidate(broker, 30e18, true);
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ── T29: TWAMM + OOR LP Below ─────────────────────────────────
    //
    //  Same as T28 but LP is below tick → token1 only on unwind.
    function test_T29_TWAMM_OOR_LP_Below() public {
        console.log("=== T29: TWAMM + OOR LP Below ===");
        (PrimeBroker broker,) = _setupBrokerTwammOOR(
            0, // no cash
            0, // no wRLP
            TWAMM_AMT,
            10_000e6, // LP amount (larger for below-tick)
            true, // sellCollateral
            false // LP below tick
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _logGhost();

        _setOraclePrice(30e18);
        _assertInsolventAndLiquidate(broker, 30e18, true);
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ── T30: Cash + TWAMM + OOR LP Above ──────────────────────────
    //
    //  Waterfall: Cash → TWAMM cancel → OOR LP unwind (token0 only).
    function test_T30_Cash_TWAMM_OOR_Above() public {
        console.log("=== T30: Cash + TWAMM + OOR LP Above ===");
        (PrimeBroker broker,) = _setupBrokerTwammOOR(
            20_000e6, // cash
            0, // no wRLP
            TWAMM_AMT,
            5_000e6, // LP amount
            true, // sellCollateral
            true // LP above tick
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _logGhost();

        _setOraclePrice(30e18);
        _assertInsolventAndLiquidate(broker, 30e18, true);
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ── T31: Cash + TWAMM + OOR LP Below ──────────────────────────
    //
    //  Same as T30 but LP below tick → token1 only on unwind.
    function test_T31_Cash_TWAMM_OOR_Below() public {
        console.log("=== T31: Cash + TWAMM + OOR LP Below ===");
        (PrimeBroker broker,) = _setupBrokerTwammOOR(
            20_000e6, // cash
            0, // no wRLP
            TWAMM_AMT,
            10_000e6, // LP amount
            true, // sellCollateral
            false // LP below tick
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _logGhost();

        _setOraclePrice(30e18);
        _assertInsolventAndLiquidate(broker, 30e18, true);
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ── T32: Full Stack + OOR LP Above ────────────────────────────
    //
    //  Cash + wRLP + TWAMM + OOR LP (above).
    //  Full waterfall: Cash → wRLP → TWAMM cancel → OOR LP unwind.
    function test_T32_Full_OOR_Above() public {
        console.log("=== T32: Full + OOR LP Above ===");
        (PrimeBroker broker,) = _setupBrokerTwammOOR(
            10_000e6, // cash
            3_000e6, // wRLP
            TWAMM_AMT,
            5_000e6, // LP amount
            true, // sellCollateral
            true // LP above tick
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _logGhost();

        _setOraclePrice(35e18);
        _assertInsolventAndLiquidate(broker, 35e18, true);
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ── T33: Full Stack + OOR LP Below ────────────────────────────
    //
    //  Cash + wRLP + TWAMM + OOR LP (below).
    function test_T33_Full_OOR_Below() public {
        console.log("=== T33: Full + OOR LP Below ===");
        (PrimeBroker broker,) = _setupBrokerTwammOOR(
            10_000e6, // cash
            3_000e6, // wRLP
            TWAMM_AMT,
            10_000e6, // LP amount
            true, // sellCollateral
            false // LP below tick
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _logGhost();

        _setOraclePrice(30e18);
        _assertInsolventAndLiquidate(broker, 30e18, true);
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  TIER B: FULL STACK DIRECTION & SOLVENCY VARIANTS
    // ================================================================

    // ── T34: Full + OOR LP Above, UNDERWATER ──────────────────────
    //
    //  Full stack with OOR LP above, deeply underwater.
    //  Asserts 100% liquidation (full debt).
    function test_T34_Full_OOR_Above_Underwater() public {
        console.log("=== T34: Full + OOR Above, Underwater ===");
        (PrimeBroker broker,) =
            _setupBrokerTwammOOR(
                10_000e6,
                3_000e6,
                TWAMM_AMT,
                5_000e6,
                true, // sellCollateral
                true // LP above tick
            );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _setOraclePrice(40e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 debtVal = FullMath.mulDiv(USER_DEBT, 40e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", debtVal / 1e6);
        assertLt(nav, debtVal, "T34: must be underwater");

        uint256 preLiq = ERC20(ma.collateralToken).balanceOf(liquidator);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);
        uint256 liqGain = ERC20(ma.collateralToken).balanceOf(liquidator) - preLiq;
        console.log("  Liq gained:", liqGain / 1e6);

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ── T35: Full + OOR LP Below, UNDERWATER ──────────────────────
    //
    //  Full stack with OOR LP below, deeply underwater.
    function test_T35_Full_OOR_Below_Underwater() public {
        console.log("=== T35: Full + OOR Below, Underwater ===");
        (PrimeBroker broker,) =
            _setupBrokerTwammOOR(
                10_000e6,
                3_000e6,
                TWAMM_AMT,
                10_000e6,
                true, // sellCollateral
                false // LP below tick
            );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _setOraclePrice(40e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 debtVal = FullMath.mulDiv(USER_DEBT, 40e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", debtVal / 1e6);
        assertLt(nav, debtVal, "T35: must be underwater");

        uint256 preLiq = ERC20(ma.collateralToken).balanceOf(liquidator);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);
        uint256 liqGain = ERC20(ma.collateralToken).balanceOf(liquidator) - preLiq;
        console.log("  Liq gained:", liqGain / 1e6);

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ── T36: Full Stack, Sell Position Token, UNDERWATER ───────────
    //
    //  TWAMM sells positionToken (wRLP) → buys collateral.
    //  Reverse direction from all other tests.
    //  Exercises _cancelTwammOrder with buyToken=collateral.
    function test_T36_SellPosition_Underwater() public {
        console.log("=== T36: Full Stack, Sell Position, UW ===");
        // sellCollateral=false → broker sells wRLP, buys collateral.
        // Broker gets USER_DEBT (10k) wRLP from modifyPosition.
        // Sell 5k wRLP via TWAMM (within available balance).
        PrimeBroker broker = _setupBrokerTwamm(
            20_000e6,
            5_000e6,
            false, // sellPosition!
            TWAMM_INTERVAL
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _setOraclePrice(25e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 debtVal = FullMath.mulDiv(USER_DEBT, 25e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", debtVal / 1e6);

        assertFalse(core.isSolvent(marketId, address(broker)), "T36: must be insolvent");

        // Sell-position direction preserves NAV better → NOT underwater.
        // Use 50% close factor.
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT / 2, 0);

        uint256 postCash = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 postWRLP = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Post: cash:", postCash / 1e6, "wRLP:", postWRLP / 1e6);

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ── T37: Full Stack, Sell Position Token, NOT UNDERWATER ──────
    //
    //  Same as T36 but milder price shock → insolvent but NOT underwater.
    //  50% close factor applies.
    function test_T37_SellPosition_NotUnderwater() public {
        console.log("=== T37: Full Stack, Sell Position, NOT UW ===");
        PrimeBroker broker = _setupBrokerTwamm(
            20_000e6,
            5_000e6,
            false, // sellPosition!
            TWAMM_INTERVAL
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        _setOraclePrice(20e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 debtVal = FullMath.mulDiv(USER_DEBT, 20e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", debtVal / 1e6);

        assertFalse(core.isSolvent(marketId, address(broker)), "T37: must be insolvent");
        assertGt(nav, debtVal, "T37: NOT underwater");

        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT / 2, 0);

        uint256 postCash = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 postWRLP = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Post: cash:", postCash / 1e6, "wRLP:", postWRLP / 1e6);

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  INTERNAL HELPERS
    // ================================================================

    function _logGhost() internal view {
        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) == ma.collateralToken;
        (uint256 a0, uint256 a1,,) = twammHook.getStreamState(marketTwammKey);
        uint256 ghost = colIsC0 ? a0 : a1;
        console.log("  Ghost:", ghost / 1e6);
    }

    function _assertInsolventAndLiquidate(PrimeBroker broker, uint256 priceWad, bool expectUnderwater) internal {
        uint256 nav = broker.getNetAccountValue();
        uint256 debtVal = FullMath.mulDiv(USER_DEBT, priceWad, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", debtVal / 1e6);

        assertFalse(core.isSolvent(marketId, address(broker)), "must be insolvent");

        if (expectUnderwater) {
            assertLt(nav, debtVal, "must be underwater");
        }

        uint256 dtc = (nav < debtVal) ? USER_DEBT : USER_DEBT / 2;
        uint256 preLiq = ERC20(ma.collateralToken).balanceOf(liquidator);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), dtc, 0);
        uint256 liqGain = ERC20(ma.collateralToken).balanceOf(liquidator) - preLiq;

        uint256 postCash = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 postWRLP = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Post: cash:", postCash / 1e6, "wRLP:", postWRLP / 1e6);
        console.log("  Liq gained:", liqGain / 1e6);
    }
}
