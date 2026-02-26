// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationBase} from "./LiquidationBase.t.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import "forge-std/console.sol";

/// @title Tier 3: Edge-Case Liquidation Tests
/// @dev T8-T11. Close factor, slippage, sequential liquidations.
contract LiquidationEdgeCases is LiquidationBase {
    // T8: Close factor enforcement. Solvent but insolvent, NOT underwater.
    // 60% reverts, 50% succeeds.
    function test_T8_CloseFactorReverts() public {
        console.log("=== T8: Close Factor Enforcement ===");
        (PrimeBroker broker,) = _setupBroker(50_000e6, 5_000e6, 0, 0);

        _setOraclePrice(9e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 dv = FullMath.mulDiv(USER_DEBT, 9e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", dv / 1e6);
        assertFalse(core.isSolvent(marketId, address(broker)), "must be insolvent");
        assertTrue(nav >= dv, "must NOT be underwater");

        vm.prank(liquidator);
        vm.expectRevert(IRLDCore.CloseFactorExceeded.selector);
        core.liquidate(marketId, address(broker), 6_000e6, 0);

        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), 5_000e6, 0);
        console.log("  50% liquidation succeeded");
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // T9: Close factor bypass when underwater. 100% allowed.
    function test_T9_CloseFactorBypassUnderwater() public {
        console.log("=== T9: Close Factor Bypass (Underwater) ===");
        (PrimeBroker broker,) = _setupBroker(80_000e6, 0, 0, 0);

        _setOraclePrice(9e18);
        uint256 nav = broker.getNetAccountValue();
        uint256 dv = FullMath.mulDiv(USER_DEBT, 9e18, 1e18);
        console.log("  NAV:", nav / 1e6, "debtVal:", dv / 1e6);
        assertTrue(nav < dv, "must be underwater");

        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), USER_DEBT, 0);
        console.log("  100% liquidation succeeded");
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // T10: Slippage protection. minCollateralOut=100k reverts, 0 succeeds.
    function test_T10_SlippageProtectionReverts() public {
        console.log("=== T10: Slippage Protection ===");
        (PrimeBroker broker,) = _setupBroker(50_000e6, 5_000e6, 0, 0);

        _setOraclePrice(9e18);
        assertFalse(core.isSolvent(marketId, address(broker)), "must be insolvent");

        vm.prank(liquidator);
        vm.expectRevert("Slippage: collateral below minimum");
        core.liquidate(marketId, address(broker), 5_000e6, 100_000e6);

        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), 5_000e6, 0);
        console.log("  Slippage: high min reverted, zero min passed");
        _setOraclePrice(INDEX_PRICE_WAD);
    }

    // T11: Sequential liquidation. Liquidate twice on same position.
    function test_T11_SequentialLiquidation() public {
        console.log("=== T11: Sequential Liquidation ===");
        (PrimeBroker broker,) = _setupBroker(50_000e6, 5_000e6, 0, 0);

        _setOraclePrice(9e18);
        assertFalse(core.isSolvent(marketId, address(broker)), "must be insolvent");

        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), 5_000e6, 0);

        IRLDCore.Position memory pos1 = core.getPosition(marketId, address(broker));
        console.log("  After 1st liq, principal:", uint256(pos1.debtPrincipal) / 1e6);

        bool stillInsolvent = !core.isSolvent(marketId, address(broker));
        console.log("  Still insolvent:", stillInsolvent);

        if (stillInsolvent) {
            uint256 remaining = uint256(pos1.debtPrincipal);
            uint256 dtc2 = remaining / 2;
            vm.prank(liquidator);
            core.liquidate(marketId, address(broker), dtc2, 0);

            IRLDCore.Position memory pos2 = core.getPosition(marketId, address(broker));
            console.log("  After 2nd liq, principal:", uint256(pos2.debtPrincipal) / 1e6);
            assertTrue(pos2.debtPrincipal < pos1.debtPrincipal, "debt must decrease");
        }
        _setOraclePrice(INDEX_PRICE_WAD);
    }
}
