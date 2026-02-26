// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationBase} from "./LiquidationBase.t.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import "forge-std/console.sol";

/// @title Tier 1: Single-Asset Liquidation Tests
/// @dev T1 (Pure Cash), T2 (Pure wRLP), T3 (Pure LP in-range)
contract LiquidationSingleAsset is LiquidationBase {
    // T1: Pure Cash. 80k cash, 0 wRLP, 10k debt. Price->9.
    function test_T1_PureCash() public {
        console.log("=== T1: Pure Cash ===");
        (PrimeBroker broker,) = _setupBroker(80_000e6, 0, 0, 0);
        _liquidate(broker, 0, 9e18, false);
    }

    // T2: Pure wRLP. 10k cash (min solvency), 10k wRLP, 10k debt.
    // Price->11 to cross mm threshold. P1 takes wRLP, P2 cash.
    function test_T2_PureWRLP() public {
        console.log("=== T2: Pure wRLP ===");
        (PrimeBroker broker,) = _setupBroker(10_000e6, 10_000e6, 0, 0);
        _liquidate(broker, 0, 11e18, false);
    }

    // T3: LP-Only. Large LP(10k+30k), targetCash=0.
    // Price->15. Unlock MUST unwind LP, sweep takes unwound cash.
    function test_T3_LPOnly() public {
        console.log("=== T3: LP Only ===");
        (PrimeBroker broker, uint256 tokenId) = _setupBroker(0, 0, 10_000e6, 30_000e6);
        _liquidate(broker, tokenId, 15e18, true);
    }
}
