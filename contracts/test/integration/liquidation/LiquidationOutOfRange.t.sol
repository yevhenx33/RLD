// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationBase} from "./LiquidationBase.t.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import "forge-std/console.sol";

/// @title Tier 4: Out-of-Range LP Liquidation Tests
/// @dev T12-T15. LP positions entirely above or below current tick.
///      Tests that seize pipeline handles single-token LP unwinds correctly.
contract LiquidationOutOfRange is LiquidationBase {
    // T12: Pure OOR LP above tick (token0/wRLP only).
    function test_T12_OOR_LP_Above() public {
        console.log("=== T12: OOR LP Above (token0 only) ===");
        (PrimeBroker broker, uint256 tokenId) = _setupBrokerOOR(
            0,
            0,
            5_000e6,
            true
        );
        _liquidate(broker, tokenId, 15e18, true);
    }

    // T13: Pure OOR LP below tick (token1/collateral only).
    function test_T13_OOR_LP_Below() public {
        console.log("=== T13: OOR LP Below (token1 only) ===");
        (PrimeBroker broker, uint256 tokenId) = _setupBrokerOOR(
            0,
            0,
            10_000e6,
            false
        );
        _liquidate(broker, tokenId, 30e18, true);
    }

    // T14: Cash + OOR LP above. Price->15.
    function test_T14_Cash_Plus_OOR_LP_Above() public {
        console.log("=== T14: Cash + OOR LP Above ===");
        (PrimeBroker broker, uint256 tokenId) = _setupBrokerOOR(
            20_000e6,
            0,
            5_000e6,
            true
        );
        _liquidate(broker, tokenId, 15e18, true);
    }

    // T15: Cash + OOR LP below. Price->15.
    function test_T15_Cash_Plus_OOR_LP_Below() public {
        console.log("=== T15: Cash + OOR LP Below ===");
        (PrimeBroker broker, uint256 tokenId) = _setupBrokerOOR(
            20_000e6,
            0,
            10_000e6,
            false
        );
        _liquidate(broker, tokenId, 15e18, true);
    }
}
