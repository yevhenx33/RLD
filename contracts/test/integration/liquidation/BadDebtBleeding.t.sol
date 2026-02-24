// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationBase} from "./LiquidationBase.t.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import "forge-std/console.sol";

/// @title Bad Debt NF Bleeding Tests
/// @notice Tests the gradual bad debt socialization via normalization factor bleeding.
/// @dev Creates underwater positions, triggers bad debt, warps time, and asserts NF behavior.
///
///     | ID  | Test                         | Scenario                                  |
///     |-----|------------------------------|-------------------------------------------|
///     | T40 | BadDebtRegistered            | Underwater liq → badDebt > 0, user clear  |
///     | T41 | NFBleedingOverTime           | Warp 1d, 7d, 14d → NF grows, badDebt→0   |
///     | T42 | StackingBadDebt              | Two bad debt events stack, fully resolve   |
///     | T43 | NoBadDebtWhenSolvent         | Insolvent but not underwater → no badDebt  |
///     | T44 | NoBleedingWhenNoBadDebt      | applyFunding with badDebt=0 → NF normal   |
///
contract BadDebtBleeding is LiquidationBase {
    // INDEX_PRICE_WAD = 5e18 (from LiquidationBase)
    // USER_DEBT = 10_000e6 (from LiquidationBase)
    // At setup: trueDebt = 10,000 × NF(1.0) × 5 = 50,000 waUSDC

    /* ====================================================================
       T40: Bad debt is registered after underwater liquidation
       ==================================================================== */

    /// @dev Setup: 80k cash, price→9 → debtVal=90k, NAV=80k → underwater by 10k.
    ///      After liquidation: seize capped at 80k, remaining debt → badDebt, user cleared.
    function test_T40_BadDebtRegistered() public {
        console.log("=== T40: Bad Debt Registration ===");

        // 80k cash keeps broker solvent at index=5 (NAV=80k > debtVal=50k)
        (PrimeBroker broker, ) = _setupBroker(80_000e6, 0, 0, 0);

        // Verify initial state: no bad debt
        IRLDCore.MarketState memory s0 = core.getMarketState(marketId);
        assertEq(s0.badDebt, 0, "no bad debt initially");
        console.log("  NF before:", s0.normalizationFactor);

        // Spike price → underwater
        _setOraclePrice(9e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 dv = FullMath.mulDiv(USER_DEBT, 9e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", dv / 1e6);
        assertTrue(nav < dv, "must be underwater");

        // Get position before
        IRLDCore.Position memory posBefore = core.getPosition(
            marketId,
            address(broker)
        );
        console.log(
            "  Principal before:",
            uint256(posBefore.debtPrincipal) / 1e6
        );

        // Liquidate — 100% allowed because underwater
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);

        // Verify: bad debt was registered
        IRLDCore.MarketState memory s1 = core.getMarketState(marketId);
        console.log("  Bad debt registered:", uint256(s1.badDebt) / 1e6);
        assertTrue(s1.badDebt > 0, "bad debt must be registered");

        // Verify: user's position is fully cleared
        IRLDCore.Position memory posAfter = core.getPosition(
            marketId,
            address(broker)
        );
        assertEq(posAfter.debtPrincipal, 0, "user position must be cleared");
        console.log("  User principal after: 0 (cleared)");

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    /* ====================================================================
       T41: NF bleeds bad debt over time via _applyFunding
       ==================================================================== */

    function test_T41_NFBleedingOverTime() public {
        console.log("=== T41: NF Bleeding Over Time ===");

        (PrimeBroker broker, ) = _setupBroker(80_000e6, 0, 0, 0);
        IRLDCore.MarketState memory s0 = core.getMarketState(marketId);
        uint128 nfOriginal = s0.normalizationFactor;

        // Create bad debt
        _setOraclePrice(9e18);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);

        IRLDCore.MarketState memory s1 = core.getMarketState(marketId);
        uint128 badDebtAfterLiq = s1.badDebt;
        console.log(
            "  Bad debt after liquidation:",
            uint256(badDebtAfterLiq) / 1e6
        );
        assertTrue(badDebtAfterLiq > 0, "bad debt must exist");

        // Reset oracle so funding model doesn't interfere drastically
        _setOraclePrice(INDEX_PRICE_WAD);

        // ── Warp 1 day, apply funding ──
        vm.warp(block.timestamp + 1 days);
        core.applyFunding(marketId);

        IRLDCore.MarketState memory s2 = core.getMarketState(marketId);
        console.log("  After 1 day:");
        console.log("    Bad debt:", uint256(s2.badDebt) / 1e6);
        console.log("    NF:", s2.normalizationFactor);
        assertTrue(
            s2.badDebt < badDebtAfterLiq,
            "bad debt must decrease after 1 day"
        );
        assertTrue(
            s2.normalizationFactor > s1.normalizationFactor,
            "NF must increase after 1 day"
        );

        // ── Warp 6 more days (total 7), apply funding ──
        vm.warp(block.timestamp + 6 days);
        core.applyFunding(marketId);

        IRLDCore.MarketState memory s3 = core.getMarketState(marketId);
        console.log("  After 7 days:");
        console.log("    Bad debt:", uint256(s3.badDebt) / 1e6);
        console.log("    NF:", s3.normalizationFactor);
        assertTrue(s3.badDebt < s2.badDebt, "bad debt must decrease further");

        // ── Warp 7 more days (total 14), apply funding — fully socialized ──
        vm.warp(block.timestamp + 7 days);
        core.applyFunding(marketId);

        IRLDCore.MarketState memory s4 = core.getMarketState(marketId);
        console.log("  After 14 days:");
        console.log("    Bad debt:", uint256(s4.badDebt));
        console.log("    NF:", s4.normalizationFactor);
        assertEq(s4.badDebt, 0, "bad debt must be fully socialized by day 14");

        // Verify NF increased from original
        uint256 nfIncrease = s4.normalizationFactor - nfOriginal;
        console.log("  Total NF increase:", nfIncrease);
        assertTrue(nfIncrease > 0, "NF must have increased");
    }

    /* ====================================================================
       T42: Stacking — second bad debt event during active bleeding
       ==================================================================== */

    function test_T42_StackingBadDebt() public {
        console.log("=== T42: Stacking Bad Debt ===");

        // Create two underwater positions (80k each, 10k debt each)
        (PrimeBroker broker1, ) = _setupBroker(80_000e6, 0, 0, 0);
        (PrimeBroker broker2, ) = _setupBroker(80_000e6, 0, 0, 0);

        // Spike price
        _setOraclePrice(9e18);

        // Liquidate broker1 → first bad debt event
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker1), USER_DEBT, 0);

        IRLDCore.MarketState memory s1 = core.getMarketState(marketId);
        uint128 badDebt1 = s1.badDebt;
        console.log(
            "  Bad debt after 1st liquidation:",
            uint256(badDebt1) / 1e6
        );

        // Reset oracle, warp 3 days, partial bleed
        _setOraclePrice(INDEX_PRICE_WAD);
        vm.warp(block.timestamp + 3 days);
        core.applyFunding(marketId);

        IRLDCore.MarketState memory s2 = core.getMarketState(marketId);
        console.log(
            "  Bad debt after 3 days bleeding:",
            uint256(s2.badDebt) / 1e6
        );
        assertTrue(s2.badDebt < badDebt1, "must have bled some");

        // Spike again, liquidate broker2 → stacking
        _setOraclePrice(9e18);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker2), USER_DEBT, 0);

        IRLDCore.MarketState memory s3 = core.getMarketState(marketId);
        console.log(
            "  Bad debt after 2nd liquidation (stacked):",
            uint256(s3.badDebt) / 1e6
        );
        assertTrue(
            s3.badDebt > s2.badDebt,
            "bad debt must increase after stacking"
        );

        // Reset oracle, warp 14 days → should fully socialize everything
        _setOraclePrice(INDEX_PRICE_WAD);
        vm.warp(block.timestamp + 14 days);
        core.applyFunding(marketId);

        IRLDCore.MarketState memory s4 = core.getMarketState(marketId);
        console.log("  Bad debt after 14 more days:", uint256(s4.badDebt));
        assertEq(
            s4.badDebt,
            0,
            "all stacked bad debt must be fully socialized"
        );
    }

    /* ====================================================================
       T43: No bad debt when position is NOT underwater
       ==================================================================== */

    function test_T43_NoBadDebtWhenSolvent() public {
        console.log("=== T43: No Bad Debt When Not Underwater ===");

        // Same setup as T8: 50k cash, 5k wRLP, 10k debt.
        // At index=9: debtVal=90k, NAV~95k → insolvent (below MM) but NOT underwater.
        (PrimeBroker broker, ) = _setupBroker(50_000e6, 5_000e6, 0, 0);

        _setOraclePrice(9e18);

        uint256 nav = broker.getNetAccountValue();
        uint256 dv = FullMath.mulDiv(USER_DEBT, 9e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", dv / 1e6);

        // Verify insolvent (below maintenance margin)
        assertFalse(
            core.isSolvent(marketId, address(broker)),
            "must be insolvent"
        );
        // But NOT underwater (NAV >= debtValue)
        assertTrue(nav >= dv, "must NOT be underwater");

        // Liquidate 50% (close factor enforced)
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), 5_000e6, 0);

        // Verify: NO bad debt
        IRLDCore.MarketState memory state = core.getMarketState(marketId);
        assertEq(state.badDebt, 0, "no bad debt when not underwater");
        console.log("  Bad debt: 0 (correct - not underwater)");

        _setOraclePrice(INDEX_PRICE_WAD);
    }

    /* ====================================================================
       T44: NF bleeding does nothing when badDebt is zero
       ==================================================================== */

    function test_T44_NoBleedingWhenNoBadDebt() public {
        console.log("=== T44: No Bleeding When No Bad Debt ===");

        IRLDCore.MarketState memory s0 = core.getMarketState(marketId);
        assertEq(s0.badDebt, 0, "no bad debt");

        // Warp 7 days and apply funding
        vm.warp(block.timestamp + 7 days);
        core.applyFunding(marketId);

        IRLDCore.MarketState memory s1 = core.getMarketState(marketId);
        assertEq(s1.badDebt, 0, "still no bad debt");

        console.log("  NF before:", s0.normalizationFactor);
        console.log("  NF after:", s1.normalizationFactor);
        console.log("  No bad debt bleeding occurred (correct)");
    }
}
