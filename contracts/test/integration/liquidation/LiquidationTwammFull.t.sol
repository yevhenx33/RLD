// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationTwammBase} from "./LiquidationTwammBase.t.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import "forge-std/console.sol";

/// @title Tier 7: Full Stack TWAMM Liquidation Tests
/// @dev Tests with ALL asset types: Cash + wRLP + TWAMM + V4 LP
///      a-variants: No clearing (ghost lost)
///      b-variants: With clearing (only auction discount lost)
contract LiquidationTwammFull is LiquidationTwammBase {
    // ================================================================
    //  T22: Full stack, NOT underwater, NO CLEAR
    // ================================================================
    function test_T22_FullStack_NotUnderwater() public {
        console.log("=== T22: Full Stack (not UW), NO clear ===");
        (PrimeBroker broker,) = _setupBrokerTwammCascade(20_000e6, 3_000e6, 60_000e6, 3_000e6, 15_000e6);
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) == ma.collateralToken;
        (uint256 a0, uint256 a1,,) = twammHook.getStreamState(marketTwammKey);
        console.log("  Ghost (LOST):", (colIsC0 ? a0 : a1) / 1e6);

        _setOraclePrice(20e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 debtVal = FullMath.mulDiv(USER_DEBT, 20e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", debtVal / 1e6);
        assertFalse(core.isSolvent(marketId, address(broker)), "T22: insolvent");
        assertGt(nav, debtVal, "T22: NOT underwater");

        uint256 preLiq = ERC20(ma.collateralToken).balanceOf(liquidator);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT / 2, 0);

        uint256 postCash = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 postWRLP = ERC20(ma.positionToken).balanceOf(address(broker));
        uint256 liqGain = ERC20(ma.collateralToken).balanceOf(liquidator) - preLiq;
        console.log("  Post: cash:", postCash / 1e6, "wRLP:", postWRLP / 1e6);
        console.log("  Liq gained:", liqGain / 1e6);

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  T22b: Full stack, NOT underwater, WITH CLEAR
    //
    //  Clearing boosts NAV from ~155k to ~240k.
    //  Broker stays SOLVENT -> no liquidation possible!
    // ================================================================
    function test_T22b_FullStack_NotUnderwater_Cleared() public {
        console.log("=== T22b: Full Stack (not UW), WITH clear ===");
        (PrimeBroker broker,) = _setupBrokerTwammCascade(20_000e6, 3_000e6, 60_000e6, 3_000e6, 15_000e6);
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) == ma.collateralToken;
        (uint256 a0, uint256 a1,,) = twammHook.getStreamState(marketTwammKey);
        console.log("  Ghost before clear:", (colIsC0 ? a0 : a1) / 1e6);

        _clearTwammAuction(colIsC0, type(uint256).max);

        _setOraclePrice(15e18);
        uint256 nav = broker.getNetAccountValue();
        console.log("  NAV after clear:", nav / 1e6);
        console.log("  vs T22(no clear): NAV was 155k");

        bool solvent = core.isSolvent(marketId, address(broker));
        console.log("  Solvent?", solvent);
        assertTrue(solvent, "T22b: clearing made broker SOLVENT");

        console.log("  Result: clearing PREVENTED liquidation entirely");
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  T23: Full stack, UNDERWATER, NO CLEAR
    // ================================================================
    function test_T23_FullStack_Underwater() public {
        console.log("=== T23: Full Stack (UW), NO clear ===");
        (PrimeBroker broker,) = _setupBrokerTwammCascade(20_000e6, 3_000e6, 60_000e6, 3_000e6, 15_000e6);
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) == ma.collateralToken;
        (uint256 a0, uint256 a1,,) = twammHook.getStreamState(marketTwammKey);
        console.log("  Ghost (LOST):", (colIsC0 ? a0 : a1) / 1e6);

        _setOraclePrice(30e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 debtVal = FullMath.mulDiv(USER_DEBT, 30e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", debtVal / 1e6);
        assertFalse(core.isSolvent(marketId, address(broker)), "T23: insolvent");
        assertLt(nav, debtVal, "T23: must be underwater");

        uint256 preLiq = ERC20(ma.collateralToken).balanceOf(liquidator);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);

        uint256 postCash = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 postWRLP = ERC20(ma.positionToken).balanceOf(address(broker));
        uint256 liqGain = ERC20(ma.collateralToken).balanceOf(liquidator) - preLiq;
        console.log("  Post: cash:", postCash / 1e6, "wRLP:", postWRLP / 1e6);
        console.log("  Liq gained:", liqGain / 1e6);

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // ================================================================
    //  T23b: Full stack, UNDERWATER, WITH CLEAR
    //
    //  Clearing boosts NAV from ~184k to ~298k.
    //  Broker stays SOLVENT -> no liquidation possible!
    // ================================================================
    function test_T23b_FullStack_Underwater_Cleared() public {
        console.log("=== T23b: Full Stack (UW), WITH clear ===");
        (PrimeBroker broker,) = _setupBrokerTwammCascade(20_000e6, 3_000e6, 60_000e6, 3_000e6, 15_000e6);
        vm.warp(block.timestamp + TWAMM_INTERVAL / 2);
        twammHook.executeJTMOrders(marketTwammKey);

        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) == ma.collateralToken;
        (uint256 a0, uint256 a1,,) = twammHook.getStreamState(marketTwammKey);
        console.log("  Ghost before clear:", (colIsC0 ? a0 : a1) / 1e6);

        _clearTwammAuction(colIsC0, type(uint256).max);

        _setOraclePrice(20e18);
        uint256 nav = broker.getNetAccountValue();
        console.log("  NAV after clear:", nav / 1e6);
        console.log("  vs T23(no clear): NAV was 184k");

        bool solvent = core.isSolvent(marketId, address(broker));
        console.log("  Solvent?", solvent);
        assertTrue(solvent, "T23b: clearing made broker SOLVENT");

        console.log("  Result: clearing PREVENTED liquidation entirely");
        _setOraclePrice(INDEX_PRICE_WAD);
    }
}
