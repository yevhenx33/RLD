// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {StaticLiquidationModule} from "../src/modules/liquidation/StaticLiquidationModule.sol";
import {DutchLiquidationModule} from "../src/modules/liquidation/DutchLiquidationModule.sol";
import {ILiquidationModule} from "../src/interfaces/ILiquidationModule.sol";
import {IRLDCore} from "../src/interfaces/IRLDCore.sol";
import {FixedPointMath} from "../src/libraries/FixedPointMath.sol";

contract LiquidationModulesTest is Test {
    using FixedPointMath for uint256;

    StaticLiquidationModule staticModule;
    DutchLiquidationModule dutchModule;

    // Standard Inputs
    uint256 constant PRICE = 1e18;
    uint256 constant DEBT_TO_COVER = 100e18;
    
    // Config
    IRLDCore.MarketConfig config;
    ILiquidationModule.PriceData priceData;

    function setUp() public {
        staticModule = new StaticLiquidationModule();
        dutchModule = new DutchLiquidationModule();

        config = IRLDCore.MarketConfig({

            minColRatio: 1.5e18,

            maintenanceMargin: 1.1e18, // 110%
            liquidationCloseFactor: 0.5e18,
            liquidationParams: bytes32(0),

            brokerVerifier: address(0)
        });

        priceData = ILiquidationModule.PriceData({
            indexPrice: PRICE,
            spotPrice: PRICE,
            normalizationFactor: 1e18
        });
    }

    function test_StaticLiquidation() public {
        // Setup Params: Fixed Bonus 5% (1.05e18)
        bytes32 params = bytes32(uint256(1.05e18));

        // User Stats
        uint256 userCollateral = 200e18;
        uint256 userDebt = 150e18; 

        (uint256 bonus, uint256 totalSeized) = staticModule.calculateSeizeAmount(
            DEBT_TO_COVER, // 100
            userCollateral,
            userDebt,
            priceData,
            config,
            params
        );

        // Expected Cost = 100 * 1.0 * 1.0 = 100
        // Expected Reward = 100 * 1.05 = 105
        // Total Seized = 105 / 1.0 = 105
        assertEq(totalSeized, 105e18);
        assertEq(bonus, 5e18);
    }

    function test_DutchLiquidation_LowInsolvency() public {
        // Params: Base 2%, Max 10%, Slope 1.0
        // Base = 200 bps -> 0.02e18
        // Max = 1000 bps -> 0.10e18
        // Slope = 100 -> 1.0e18 (scaled 100)
        // Packed: 
        uint256 base = 200;
        uint256 max = 1000;
        uint256 slope = 100;
        uint256 packed = base | (max << 16) | (slope << 32);
        bytes32 params = bytes32(packed);

        // User Health = 0.99 (Slightly insolvent)
        // HS = Col / (Debt * MM)
        // 0.99 = Col / (100 * 1.1)
        // Col = 0.99 * 110 = 108.9
        
        uint256 userDebt = 100e18;
        uint256 userCollateral = 108.9e18; 
        
        // DEBT_TO_COVER = 50
        
        (uint256 bonus, uint256 totalSeized) = dutchModule.calculateSeizeAmount(
            50e18,
            userCollateral,
            userDebt,
            priceData,
            config,
            params
        );

        // HS Calculation Check:
        // ColVal = 108.9
        // DebtVal = 100
        // MM = 1.1
        // Denom = 110
        // HS = 108.9 / 110 = 0.99
        
        // Bonus Calc:
        // Base = 0.02
        // Insolvency = 1.0 - 0.99 = 0.01
        // Dynamic = 0.01 * 1.0 = 0.01
        // Total Bonus = 0.02 + 0.01 = 0.03 (3%)

        // Cost = 50
        // Reward = 50 * 1.03 = 51.5
        
        assertEq(totalSeized, 51.5e18);
        assertEq(bonus, 1.5e18);
    }

    function test_DutchLiquidation_HighInsolvency_Capped() public {
        // Params: Base 2%, Max 10%, Slope 2.0 (Steep)
        // Packed: 
        uint256 base = 200;
        uint256 max = 1000;
        uint256 slope = 200; // 2.0
        uint256 packed = base | (max << 16) | (slope << 32);
        bytes32 params = bytes32(packed);

        // User Health = 0.80 (Deeply insolvent)
        // HS = 0.80
        // Insolvency = 0.20
        
        uint256 userDebt = 100e18;
        uint256 userCollateral = 88e18; // 88 / 110 = 0.8
        
        (uint256 bonusCol, uint256 totalSeized) = dutchModule.calculateSeizeAmount(
            50e18,
            userCollateral,
            userDebt,
            priceData,
            config,
            params
        );

        // Bonus Calc:
        // Base = 0.02
        // Dynamic = 0.20 * 2.0 = 0.40
        // Raw Total = 0.42
        // Max = 0.10
        // Capped Bonus = 0.10

        // Cost = 50
        // Reward = 50 * 1.10 = 55
        
        assertEq(totalSeized, 55e18);
        assertEq(bonusCol, 5e18);
    }
}
