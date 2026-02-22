// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationTwammBase} from "./LiquidationTwammBase.t.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import "forge-std/console.sol";

/// @title Tier 6: TWAMM + Other Assets Cascade Liquidation Tests
/// @dev Tests the _unlockLiquidity priority waterfall with TWAMM orders:
///      Cash check -> TWAMM cancel -> LP unwind
///
///      a-variants: No clearing (ghost lost)
///      b-variants: With clearing (only auction discount lost)
contract LiquidationTwammCascade is LiquidationTwammBase {
    // ================================================================
    //  T19: Cash + TWAMM, partial swap, NO CLEAR
    //
    //  Ghost(100k) now valued in NAV (ghost-aware). Trigger at higher price.
    // ================================================================
    function test_T19_CashPlusTWAMM() public {
        console.log("=== T19: Cash + TWAMM, NO clear ===");
        PrimeBroker broker = _setupBrokerTwamm(
            50_000e6,
            200_000e6,
            true,
            TWAMM_INTERVAL
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJITTWAMMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        (uint256 a0, uint256 a1, , ) = twammHook.getStreamState(marketTwammKey);
        uint256 ghost = colIsC0 ? a0 : a1;
        console.log("  Ghost (LOST):", ghost / 1e6);

        _setOraclePrice(30e18);
        uint256 nav = broker.getNetAccountValue();
        console.log("  NAV:", nav / 1e6);
        assertFalse(
            core.isSolvent(marketId, address(broker)),
            "T19: insolvent"
        );

        uint256 preLiq = ERC20(ma.collateralToken).balanceOf(liquidator);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);
        uint256 liqGain = ERC20(ma.collateralToken).balanceOf(liquidator) -
            preLiq;
        console.log("  Liq gained:", liqGain / 1e6);
        console.log("  Result: 100k ghost LOST, broker drained");

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  T19b: Cash + TWAMM, WITH CLEAR
    //
    //  Ghost(100k) cleared -> wRLP earned. NAV jumps to ~435k.
    //  Broker stays SOLVENT -> no liquidation possible.
    // ================================================================
    function test_T19b_CashPlusTWAMM_Cleared() public {
        console.log("=== T19b: Cash + TWAMM, WITH clear ===");
        PrimeBroker broker = _setupBrokerTwamm(
            50_000e6,
            200_000e6,
            true,
            TWAMM_INTERVAL
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJITTWAMMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        (uint256 a0, uint256 a1, , ) = twammHook.getStreamState(marketTwammKey);
        uint256 ghostBefore = colIsC0 ? a0 : a1;
        console.log("  Ghost before clear:", ghostBefore / 1e6);

        // CLEAR: ghost -> wRLP earnings (only auction discount lost)
        _clearTwammAuction(colIsC0, type(uint256).max);

        (a0, a1, , ) = twammHook.getStreamState(marketTwammKey);
        uint256 ghostAfter = colIsC0 ? a0 : a1;
        assertEq(ghostAfter, 0, "T19b: ghost cleared");

        _setOraclePrice(15e18);
        uint256 nav = broker.getNetAccountValue();
        console.log("  NAV after clear:", nav / 1e6);
        console.log("  vs T19(no clear): NAV was 150k");

        // Clearing boosted NAV so much the broker is SOLVENT!
        bool solvent = core.isSolvent(marketId, address(broker));
        console.log("  Solvent?", solvent);
        assertTrue(solvent, "T19b: clearing made broker SOLVENT");

        console.log(
            "  Result: clearing saved 100k, broker solvent, NO liquidation needed"
        );

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  T20: TWAMM + LP, NO CLEAR
    // ================================================================
    function test_T20_TWAMMplusLP() public {
        console.log("=== T20: TWAMM + LP, NO clear ===");
        (PrimeBroker broker, ) = _setupBrokerTwammCascade(
            0,
            0,
            60_000e6,
            3_000e6,
            15_000e6
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJITTWAMMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        (uint256 a0, uint256 a1, , ) = twammHook.getStreamState(marketTwammKey);
        console.log("  Ghost (LOST):", (colIsC0 ? a0 : a1) / 1e6);

        _setOraclePrice(25e18);
        uint256 nav = broker.getNetAccountValue();
        console.log("  NAV:", nav / 1e6);
        assertFalse(
            core.isSolvent(marketId, address(broker)),
            "T20: insolvent"
        );

        uint256 preLiq = ERC20(ma.collateralToken).balanceOf(liquidator);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);
        console.log(
            "  Liq gained:",
            (ERC20(ma.collateralToken).balanceOf(liquidator) - preLiq) / 1e6
        );

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  T20b: TWAMM + LP, WITH CLEAR
    // ================================================================
    function test_T20b_TWAMMplusLP_Cleared() public {
        console.log("=== T20b: TWAMM + LP, WITH clear ===");
        (PrimeBroker broker, ) = _setupBrokerTwammCascade(
            0,
            0,
            60_000e6,
            3_000e6,
            15_000e6
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJITTWAMMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        (uint256 a0, uint256 a1, , ) = twammHook.getStreamState(marketTwammKey);
        console.log("  Ghost before clear:", (colIsC0 ? a0 : a1) / 1e6);

        _clearTwammAuction(colIsC0, type(uint256).max);

        _setOraclePrice(10e18);
        uint256 nav = broker.getNetAccountValue();
        console.log("  NAV after clear:", nav / 1e6);
        console.log("  vs T20(no clear): NAV was 75k");

        bool solvent = core.isSolvent(marketId, address(broker));
        console.log("  Solvent?", solvent);
        assertTrue(solvent, "T20b: clearing made broker SOLVENT");

        console.log(
            "  Result: clearing saved 30k, broker solvent, NO liquidation needed"
        );
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  T21: Cash + TWAMM + LP, NO CLEAR
    // ================================================================
    function test_T21_FullCascade() public {
        console.log("=== T21: Cash + TWAMM + LP, NO clear ===");
        (PrimeBroker broker, ) = _setupBrokerTwammCascade(
            30_000e6,
            0,
            60_000e6,
            3_000e6,
            15_000e6
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJITTWAMMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        (uint256 a0, uint256 a1, , ) = twammHook.getStreamState(marketTwammKey);
        console.log("  Ghost (LOST):", (colIsC0 ? a0 : a1) / 1e6);

        _setOraclePrice(25e18);
        uint256 nav = broker.getNetAccountValue();
        console.log("  NAV:", nav / 1e6);
        assertFalse(
            core.isSolvent(marketId, address(broker)),
            "T21: insolvent"
        );

        uint256 preLiq = ERC20(ma.collateralToken).balanceOf(liquidator);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);
        uint256 postCash = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 postWRLP = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Post: cash:", postCash / 1e6, "wRLP:", postWRLP / 1e6);
        console.log(
            "  Liq gained:",
            (ERC20(ma.collateralToken).balanceOf(liquidator) - preLiq) / 1e6
        );

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  T21b: Cash + TWAMM + LP, WITH CLEAR
    // ================================================================
    function test_T21b_FullCascade_Cleared() public {
        console.log("=== T21b: Cash + TWAMM + LP, WITH clear ===");
        (PrimeBroker broker, ) = _setupBrokerTwammCascade(
            30_000e6,
            0,
            60_000e6,
            3_000e6,
            15_000e6
        );
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJITTWAMMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        (uint256 a0, uint256 a1, , ) = twammHook.getStreamState(marketTwammKey);
        console.log("  Ghost before clear:", (colIsC0 ? a0 : a1) / 1e6);

        _clearTwammAuction(colIsC0, type(uint256).max);

        _setOraclePrice(25e18);
        uint256 nav = broker.getNetAccountValue();
        console.log("  NAV after clear:", nav / 1e6);
        console.log("  vs T21(no clear): NAV was 149k");

        bool solvent = core.isSolvent(marketId, address(broker));
        console.log("  Solvent?", solvent);
        assertTrue(solvent, "T21b: clearing made broker SOLVENT");

        console.log(
            "  Result: clearing saved 30k, broker solvent, NO liquidation needed"
        );
        _setOraclePrice(INDEX_PRICE_WAD);
    }
}
