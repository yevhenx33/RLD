// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console} from "forge-std/Test.sol";
import {DutchLiquidationModule} from "../../../src/rld/modules/liquidation/DutchLiquidationModule.sol";
import {ILiquidationModule} from "../../../src/shared/interfaces/ILiquidationModule.sol";
import {IRLDCore} from "../../../src/shared/interfaces/IRLDCore.sol";

/// @notice Test file for liquidation module verification against reference data
/// @dev Note: This was originally written for StaticLiquidationModule
///      which has been removed. Now uses DutchLiquidationModule.
contract LiquidationVerificationTest is Test {
    DutchLiquidationModule module;
    
    struct ScenarioResult {
        uint256 baseDiscount;
        uint256 debtToCover;
        uint256 expectedBonus;
        uint256 expectedSeize;
        uint256 indexPrice;
        uint256 liquidationBonus;
        uint256 maintenanceMargin;
        uint256 maxDiscount;
        string name;
        uint256 normFactor;
        uint256 slope;
        uint256 spotPrice;
        string type_;
        uint256 userCollateral;
        uint256 userDebt;
    }

    struct ReferenceData {
        ScenarioResult[] static_scenarios;
        ScenarioResult[] fuzz_vectors;
    }

    function setUp() public {
        module = new DutchLiquidationModule();
    }

    function test_VerificationFromJSON() public {
        string memory root = vm.projectRoot();
        string memory path = string.concat(root, "/test/differential/data/liquidation.json");
        
        // Skip if file doesn't exist
        if (!vm.exists(path)) {
            console.log("Skipping: liquidation.json not found");
            return;
        }
        
        string memory json = vm.readFile(path);
        
        // 1. Verify Dutch Scenarios if present
        bytes memory rawDutch = vm.parseJson(json, ".dutch");
        if (rawDutch.length > 0) {
            ScenarioResult[] memory dutchResults = abi.decode(rawDutch, (ScenarioResult[]));
            
            console.log("--- Verified Dutch Scenarios ---");
            for (uint256 i = 0; i < dutchResults.length; i++) {
                _runScenario(dutchResults[i]);
            }
        }
    }

    function _runScenario(ScenarioResult memory s) internal view {
        // Mock Inputs
        ILiquidationModule.PriceData memory priceData = ILiquidationModule.PriceData({
            spotPrice: s.spotPrice,
            indexPrice: s.indexPrice,
            normalizationFactor: s.normFactor
        });

        // Pack Dutch params: base | (max << 16) | (slope << 32)
        uint256 base = s.baseDiscount / 1e14; // Convert from WAD to bps
        uint256 max = s.maxDiscount / 1e14;
        uint256 slope = s.slope / 1e16; // Convert from WAD to 100-scaled
        uint256 packed = base | (max << 16) | (slope << 32);
        bytes32 params = bytes32(packed);
        
        // Config with maintenance margin
        // MarketConfig uses uint64 for ratio fields and uint32 for fundingPeriod
        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            minColRatio: uint64(1.5e18),
            maintenanceMargin: uint64(s.maintenanceMargin),
            liquidationCloseFactor: uint64(0.5e18),
            fundingPeriod: uint32(30 days),
            liquidationParams: params,
            brokerVerifier: address(0)
        });
        
        (uint256 bonusCollateral, uint256 seizeAmount) = module.calculateSeizeAmount(
            s.debtToCover,
            s.userCollateral,
            s.userDebt,
            priceData,
            config,
            params
        );
        
        if (seizeAmount == 0) {
             console.log("--- DEBUG FAILURE ---");
             console.log("Scenario:", s.name);
             console.log("DebtToCover:", s.debtToCover);
             console.log("Norm:", s.normFactor);
             console.log("Index:", s.indexPrice);
             console.log("Spot:", s.spotPrice);
             console.log("Seize:", seizeAmount);
        }

        // Assert Seize Amount
        if (s.expectedSeize > 1e16) {
             assertApproxEqRel(seizeAmount, s.expectedSeize, 1e14, "Seize Amount Relative Deviation");
        } else {
             assertApproxEqAbs(seizeAmount, s.expectedSeize, 200, "Seize Amount Absolute Deviation");
        }

        // Assert Bonus Collateral
        if (s.expectedBonus > 1e16) {
             assertApproxEqRel(bonusCollateral, s.expectedBonus, 1e14, "Bonus Relative Deviation");
        } else {
             assertApproxEqAbs(bonusCollateral, s.expectedBonus, 200, "Bonus Absolute Deviation");
        }
    }
}
