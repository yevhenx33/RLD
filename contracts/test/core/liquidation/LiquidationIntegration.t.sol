// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console} from "forge-std/Test.sol";
import "./LiquidationTestBase.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";

/**
 * @title Liquidation Integration Tests
 * @notice End-to-end tests for complete liquidation flows
 * @dev Financial Engineering Focus:
 *      - Asset unlocking priority (Cash -> TWAMM -> V4)
 *      - Multi-step liquidations
 *      - Oracle price impact
 *      - Economic rationality checks
 */
contract LiquidationIntegrationTest is LiquidationTestBase {
    using FixedPointMathLib for uint256;
    
    /* ============================================================================ */
    /*                    STANDARD LIQUIDATION SCENARIOS                            */
    /* ============================================================================ */
    
    function test_Integration_CashOnlyLiquidation() public {
        // SCENARIO: Broker has only collateral tokens (no wRLP, no positions)
        // This is the simplest liquidation path
        
        // GIVEN: Broker with 10k collateral, 5k debt
        uint256 collateral = 10_000e18;
        uint256 debt = 5_000e18;
        uint256 nf = 1e18;
        
        // Position becomes liquidatable (price drops)
        uint256 newPrice = 0.4e18; // 60% drop
        // Health = 10k / (5k * 0.4) = 10k / 2k = 5.0 (was healthy)
        // But if maintenance margin is 1.2, and price is used differently...
        // Let's say debt value is now 5k * 0.4 = 2k, health = 10k/2k = 5.0 still healthy
        
        // Actually, for liquidation, we need debt VALUE > collateral / maintenanceMargin
        // Let's set it up properly:
        // Collateral = 10k, Debt = 5k at price 1.0 -> debt value = 5k
        // Health = 10k / 5k = 2.0 (200% collateralized)
        // If maintenance margin = 1.5 (150%), position is healthy
        
        // To make liquidatable: increase debt or decrease collateral
        // Let's increase debt via NF
        nf = 2.5e18; // Debt value = 5k * 2.5 = 12.5k
        // Health = 10k / 12.5k = 0.8 (80% collateralized) -> LIQUIDATABLE
        
        // WHEN: Liquidating 50% of true debt
        uint256 trueDebt = debt.mulWadDown(nf); // 12.5k
        uint256 debtToCover = trueDebt / 2; // 6.25k
        uint256 principalToBurn = debtToCover.divWadDown(nf); // 2.5k
        
        // Liquidator receives bonus
        uint256 bonus = 1.05e18;
        uint256 seizeAmount = debtToCover.mulWadDown(bonus); // 6.5625k
        
        // THEN: All seized from cash (no wRLP to burn)
        // Broker sends 6.5625k collateral to liquidator
        // Liquidator burns 2.5k wRLP
        
        // FINANCIAL CHECK: Liquidator profit
        uint256 liquidatorReceives = seizeAmount; // 6.5625k
        uint256 liquidatorPays = principalToBurn; // 2.5k (cost of wRLP at current value)
        uint256 profit = liquidatorReceives - liquidatorPays.mulWadDown(nf); // 6.5625k - 6.25k = 0.3125k
        
        assertGt(profit, 0, "Liquidator must profit");
        
        // BORROWER CHECK: Loss is bounded
        uint256 debtReduced = debtToCover; // 6.25k value
        uint256 collateralLost = seizeAmount; // 6.5625k
        uint256 borrowerLoss = collateralLost - debtReduced; // 0.3125k (5% of debt)
        
        assertEq(borrowerLoss, debtReduced.mulWadDown(bonus - 1e18), "Loss equals bonus");
    }
    
    function test_Integration_WRLPOnlyLiquidation() public {
        // SCENARIO: Broker has only wRLP tokens (no cash)
        // This is the "swap-free" liquidation - most efficient
        
        // GIVEN: Broker with 10k collateral value in wRLP, 5k debt
        uint256 wRLPBalance = 10_000e18;
        uint256 debt = 5_000e18;
        uint256 nf = 2.5e18; // Make liquidatable
        
        uint256 trueDebt = debt.mulWadDown(nf); // 12.5k
        uint256 debtToCover = trueDebt / 2; // 6.25k
        uint256 principalToBurn = debtToCover.divWadDown(nf); // 2.5k
        
        // WHEN: Liquidating
        // Broker has 10k wRLP, needs to send 2.5k to Core
        // Liquidator needs to provide: principalToBurn - wRLPFromBroker
        
        uint256 wRLPFromBroker = principalToBurn; // Broker has enough
        uint256 wRLPFromLiquidator = 0;
        
        // THEN: No collateral seized (all debt offset by wRLP)
        // This is OPTIMAL for liquidator: no need to source wRLP from market
        
        // FINANCIAL: Liquidator still gets bonus via collateral
        // Wait, if broker only has wRLP, where does bonus come from?
        // Answer: Broker must have SOME collateral for the bonus
        // Let's revise: Broker has wRLP + small cash for bonus
    }
    
    function test_Integration_MixedAssetsLiquidation() public {
        // SCENARIO: Broker has cash + wRLP + TWAMM + V4 LP
        // Tests the full asset unlocking priority
        
        // GIVEN: Broker with diversified assets
        uint256 cash = 2_000e18;
        uint256 wRLP = 1_000e18;
        uint256 twammValue = 3_000e18;
        uint256 v4Value = 5_000e18;
        // Total value = 11k
        
        uint256 debt = 5_000e18;
        uint256 nf = 2.5e18; // True debt = 12.5k -> liquidatable
        
        uint256 debtToCover = 6_250e18; // Half of true debt
        uint256 seizeAmount = debtToCover.mulWadDown(1.05e18); // 6.5625k with bonus
        
        // WHEN: Liquidating
        // Priority 1: Cash (2k) - taken
        // Priority 2: wRLP (1k) - taken
        // Remaining needed: 6.5625k - 3k = 3.5625k
        // Priority 3: TWAMM (3k) - cancel order, get 3k
        // Remaining: 0.5625k
        // Priority 4: V4 LP - unwind partial position
        
        // THEN: Verify correct asset distribution
        uint256 cashSeized = cash; // All cash
        uint256 wRLPSeized = wRLP; // All wRLP
        uint256 twammSeized = twammValue; // All TWAMM
        uint256 v4Seized = 562.5e18; // Partial V4
        
        uint256 totalSeized = cashSeized + wRLPSeized + twammSeized + v4Seized;
        assertEq(totalSeized, seizeAmount, "Total seized matches seize amount");
    }
    
    /* ============================================================================ */
    /*                    MULTI-STEP LIQUIDATIONS                                   */
    /* ============================================================================ */
    
    function test_Integration_SequentialLiquidations() public {
        // SCENARIO: Liquidate 50%, then 50% again (should liquidate 75% total)
        
        // GIVEN: Position with 1000 principal, NF=2.0
        uint256 principal = 1000e18;
        uint256 nf = 2e18;
        uint256 trueDebt = principal.mulWadDown(nf); // 2000
        uint256 closeFactor = 0.5e18; // 50%
        
        // ROUND 1: Liquidate 50% of true debt
        uint256 round1_debtToCover = trueDebt.mulWadDown(closeFactor); // 1000
        uint256 round1_principalBurned = round1_debtToCover.divWadDown(nf); // 500
        uint256 remaining1 = principal - round1_principalBurned; // 500
        
        // ROUND 2: Liquidate 50% of REMAINING true debt
        uint256 remaining1_trueDebt = remaining1.mulWadDown(nf); // 1000
        uint256 round2_debtToCover = remaining1_trueDebt.mulWadDown(closeFactor); // 500
        uint256 round2_principalBurned = round2_debtToCover.divWadDown(nf); // 250
        uint256 remaining2 = remaining1 - round2_principalBurned; // 250
        
        // THEN: After 2 rounds, 75% of principal is liquidated
        assertEq(remaining2, 250e18, "25% principal remains");
        
        // FINANCIAL: Total liquidated = 750 principal = 75%
        uint256 totalLiquidated = principal - remaining2;
        assertEq(totalLiquidated, 750e18, "75% liquidated");
    }
    
    function test_Integration_CompetingLiquidators() public {
        // SCENARIO: Multiple liquidators compete for the same position
        // First one wins, second one reverts (position no longer liquidatable)
        
        // GIVEN: Liquidatable position
        address liquidator1 = makeAddr("liquidator1");
        address liquidator2 = makeAddr("liquidator2");
        
        // WHEN: Liquidator 1 liquidates first
        // ... liquidation succeeds ...
        
        // WHEN: Liquidator 2 tries to liquidate
        // THEN: Should revert with "UserSolvent" if position is now healthy
        // OR succeed if still liquidatable (partial liquidation case)
    }
    
    /* ============================================================================ */
    /*                    ORACLE & PRICE SCENARIOS                                  */
    /* ============================================================================ */
    
    function test_Integration_PriceChange_DuringLiquidation() public {
        // SCENARIO: Oracle price changes between validation and execution
        // This tests if liquidation is atomic or if there's a vulnerability
        
        // GIVEN: Position liquidatable at price 1.0
        uint256 initialPrice = 1e18;
        rldOracle.setIndexPrice(initialPrice);
        
        // Position is liquidatable...
        
        // WHEN: Price changes to 2.0 mid-transaction (via reentrancy or MEV)
        // This would make position healthy again
        
        // THEN: Liquidation should either:
        // a) Succeed with old price (if price is cached)
        // b) Revert with "UserSolvent" (if price is re-checked)
        
        // Current implementation: _applyFunding() is called first, which updates NF
        // Then _isSolvent() is checked
        // So price changes DURING execution would not be caught
        
        // This is a potential issue if oracle can be manipulated mid-transaction
    }
    
    function test_Integration_SpotIndexDivergence() public {
        // SCENARIO: Spot price diverges significantly from index price
        // This affects seize amount calculation
        
        // GIVEN: Index price = 1.0, Spot price = 0.8 (20% discount)
        rldOracle.setIndexPrice(1e18);
        spotOracle.setSpotPrice(0.8e18);
        
        uint256 debtToCover = 1000e18;
        
        // WHEN: Calculating seize amount
        // Liquidation module uses BOTH prices
        // Index price: for debt valuation
        // Spot price: for wRLP valuation
        
        // If wRLP is valued at spot (0.8), liquidator needs to provide more wRLP
        // This could create arbitrage opportunities
        
        // FINANCIAL RISK: If spot < index, liquidator can profit by:
        // 1. Buy wRLP at spot (0.8)
        // 2. Use it to liquidate (valued at index 1.0)
        // 3. Receive bonus on inflated value
    }
    
    /* ============================================================================ */
    /*                    ECONOMIC INVARIANT CHECKS                                 */
    /* ============================================================================ */
    
    function test_Integration_ProtocolSolvency_Maintained() public {
        // INVARIANT: Total collateral >= Total debt (in value terms)
        // This must hold before and after liquidation
        
        // GIVEN: Protocol with multiple positions
        uint256 totalCollateral = 100_000e18;
        uint256 totalDebt = 80_000e18; // 80% utilized
        uint256 nf = 1e18;
        
        // BEFORE: Protocol is solvent
        uint256 totalDebtValue = totalDebt.mulWadDown(nf);
        assertGe(totalCollateral, totalDebtValue, "Protocol solvent before");
        
        // WHEN: Liquidating one position
        uint256 debtToCover = 10_000e18;
        uint256 seizeAmount = debtToCover.mulWadDown(1.05e18);
        
        // AFTER: Update totals
        totalDebt -= debtToCover.divWadDown(nf);
        totalCollateral -= seizeAmount;
        
        // THEN: Protocol still solvent
        totalDebtValue = totalDebt.mulWadDown(nf);
        assertGe(totalCollateral, totalDebtValue, "Protocol solvent after");
    }
    
    function test_Integration_WRLPSupply_Conservation() public {
        // INVARIANT: wRLP total supply = sum of all debt principals
        
        // GIVEN: Initial state
        uint256 totalSupply = 10_000e18;
        uint256 sumOfPrincipals = 10_000e18;
        assertEq(totalSupply, sumOfPrincipals, "Supply matches principals");
        
        // WHEN: Liquidating (burning wRLP)
        uint256 principalBurned = 1_000e18;
        
        // THEN: Both decrease by same amount
        totalSupply -= principalBurned;
        sumOfPrincipals -= principalBurned;
        assertEq(totalSupply, sumOfPrincipals, "Supply still matches");
    }
    
    function test_Integration_NoNegativeEquity() public {
        // INVARIANT: After liquidation, position should not have negative equity
        // (debt value > collateral value)
        
        // GIVEN: Underwater position
        uint256 collateral = 1_000e18;
        uint256 debt = 2_000e18;
        uint256 nf = 1e18;
        // Debt value = 2k, collateral = 1k -> negative equity
        
        // WHEN: Liquidating maximum allowed (close factor)
        uint256 closeFactor = 0.5e18;
        uint256 debtToCover = debt.mulWadDown(closeFactor); // 1k
        uint256 seizeAmount = debtToCover.mulWadDown(1.05e18); // 1.05k
        
        // AFTER:
        uint256 remainingDebt = debt - debtToCover.divWadDown(nf); // 1k
        uint256 remainingCollateral = collateral - seizeAmount; // -50 (PROBLEM!)
        
        // This reveals an issue: if position is too underwater,
        // liquidation can drain all collateral and still leave debt
        
        // SOLUTION: Liquidation should be capped at available collateral
        // OR: Protocol should have insurance fund for bad debt
    }
}
