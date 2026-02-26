// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationBase} from "./LiquidationBase.t.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import "forge-std/console.sol";

/// @title Tier 2: Multi-Asset Cascade Liquidation Tests
/// @dev T4-T7. Multiple asset types seized in priority order.
contract LiquidationCascade is LiquidationBase {
    // T4: Cash + wRLP. 50k cash, 5k wRLP. Price->9.
    function test_T4_CashPlusWRLP() public {
        console.log("=== T4: Cash + wRLP ===");
        (PrimeBroker broker,) = _setupBroker(50_000e6, 5_000e6, 0, 0);
        _liquidate(broker, 0, 9e18, false);
    }

    // T5: wRLP + LP. 10k cash, 3k wRLP, LP(5k+25k). Price->8.5.
    // Not underwater -> 50% close. wRLP covers principal, LP partial.
    function test_T5_WRLPplusLP() public {
        console.log("=== T5: wRLP + LP ===");
        (PrimeBroker broker, uint256 tokenId) = _setupBroker(10_000e6, 3_000e6, 5_000e6, 25_000e6);
        _liquidate(broker, tokenId, 8.5e18, true);
    }

    // T6: Full cascade, not underwater. 20k cash, 3k wRLP, LP(3k+15k).
    // Price->7.5. 50% close factor.
    function test_T6_FullCascade_NotUnderwater() public {
        console.log("=== T6: Full Cascade (not underwater) ===");
        (PrimeBroker broker, uint256 tokenId) = _setupBroker(20_000e6, 3_000e6, 3_000e6, 15_000e6);
        _liquidate(broker, tokenId, 7.5e18, true);
    }

    // T7: Full cascade, deeply underwater. Same setup, price->10.
    function test_T7_FullCascade_Underwater() public {
        console.log("=== T7: Full Cascade (underwater) ===");
        (PrimeBroker broker, uint256 tokenId) = _setupBroker(20_000e6, 3_000e6, 3_000e6, 15_000e6);
        _liquidate(broker, tokenId, 10e18, true);
    }
}
