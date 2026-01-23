// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {DutchLiquidationModule} from "../../src/rld/modules/liquidation/DutchLiquidationModule.sol";
import {ILiquidationModule} from "../../src/shared/interfaces/ILiquidationModule.sol";
import {IRLDCore} from "../../src/shared/interfaces/IRLDCore.sol";
import {FixedPointMath} from "../../src/shared/libraries/FixedPointMath.sol";

contract DutchAuctionMathTest is Test {
    using FixedPointMath for uint256;

    DutchLiquidationModule module;

    function setUp() public {
        module = new DutchLiquidationModule();
    }

    /// @notice Params packed as: [Base 16b][Max 16b][Slope 16b]
    /// Example: Base 500 (5%), Max 2000 (20%), Slope 100 (1.0x)
    function _packParams(uint16 base, uint16 max, uint16 slope) internal pure returns (bytes32) {
        uint256 p = uint256(base) | (uint256(max) << 16) | (uint256(slope) << 32);
        return bytes32(p);
    }

    function test_CalculateSeizeAmount_SolventUser_BaseDiscount() public {
        // Solvent User (Health Score > 1.0)
        // Should pay ONLY Base Discount
        
        // Debt = 100. Collateral = 200. Maintenance = 1.1.
        // DebtVal = 100. ColVal = 200. HS = 200 / (100 * 1.1) = 1.81 (Solvent)
        
        uint256 debtToCover = 10e18;
        uint256 userCollateral = 200e18;
        uint256 userDebt = 100e18;
        
        ILiquidationModule.PriceData memory pd = ILiquidationModule.PriceData({
            indexPrice: 1e18, 
            spotPrice: 1e18,
            normalizationFactor: 1e18
        });
        
        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            minColRatio: 0, 
            maintenanceMargin: 1.1e18,
            liquidationCloseFactor: 0,
            fundingPeriod: 30 days,
            liquidationParams: bytes32(0),
            brokerVerifier: address(0)
        });

        // Base 500 bps (5%)
        bytes32 params = _packParams(500, 2000, 100);

        (uint256 bonus, uint256 seize) = module.calculateSeizeAmount(
            debtToCover, userCollateral, userDebt, pd, config, params
        );

        // Expected Bonus = Base = 0.05e18
        assertEq(bonus, 0.05e18);
        
        // Expected Seize = Debt * (1 + Bonus) / Price
        // 10 * 1.05 / 1 = 10.5
        assertEq(seize, 10.5e18);
    }

    function test_CalculateSeizeAmount_InsolventUser_DynamicDiscount() public {
        // Insolvent User (Health Score < 1.0)
        // Should pay Base + Slope * (1 - HS)

        // Debt = 100. Collateral = 100. Maintenance = 1.1.
        // DebtVal = 100 * 1 = 100. ColVal = 100.
        // HS = 100 / (100 * 1.1) = 1 / 1.1 = 0.9090...e18
        
        uint256 debtToCover = 10e18;
        uint256 userCollateral = 100e18;
        uint256 userDebt = 100e18;

        ILiquidationModule.PriceData memory pd = ILiquidationModule.PriceData({
            indexPrice: 1e18, spotPrice: 1e18, normalizationFactor: 1e18
        });
        
        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            minColRatio: 0, maintenanceMargin: 1.1e18, liquidationCloseFactor: 0, fundingPeriod: 30 days, liquidationParams: bytes32(0), brokerVerifier: address(0)
        });

        // Slope 100 (1.0x). Base 0.
        bytes32 params = _packParams(0, 5000, 100);

        (uint256 bonus, uint256 seize) = module.calculateSeizeAmount(
            debtToCover, userCollateral, userDebt, pd, config, params
        );
        
        // Calc Expected Logic
        uint256 hs = uint256(100e18).divWad(uint256(110e18)); // ~0.909
        uint256 insolvency = 1e18 - hs; // ~0.0909
        uint256 expectedBonus = insolvency; // Slope is 1.0

        assertApproxEqAbs(bonus, expectedBonus, 1e14); // Allow minor rounding
    }

    /// @notice Fuzz test: Bonus should NEVER exceed Max Discount
    function testFuzz_BonusCap(uint256 collateral, uint256 debt) public {
        // Bounds for realism
        collateral = bound(collateral, 1e18, 1000000e18);
        debt = bound(debt, 1e18, 1000000e18);
        
        ILiquidationModule.PriceData memory pd = ILiquidationModule.PriceData({
            indexPrice: 1e18, spotPrice: 1e18, normalizationFactor: 1e18
        });
        
        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            minColRatio: 0, maintenanceMargin: 1.1e18, liquidationCloseFactor: 0, fundingPeriod: 30 days, liquidationParams: bytes32(0), brokerVerifier: address(0)
        });

        // Max Discount 10% (1000 bps)
        bytes32 params = _packParams(500, 1000, 500); // 5% base, 10% max, 5.0x slope

        (uint256 bonus, ) = module.calculateSeizeAmount(
            1e18, collateral, debt, pd, config, params
        );

        assertLe(bonus, 0.10e18, "Bonus exceeded Max Discount");
    }
}
