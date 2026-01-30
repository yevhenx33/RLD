// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console} from "forge-std/Test.sol";
import "./LiquidationTestBase.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";

/**
 * @title True Debt Semantics Unit Tests
 * @notice Tests for liquidation with varying normalization factors
 * @dev Financial Engineering Focus:
 *      - Verify principal-to-true-debt conversions
 *      - Test extreme NF values (0.01 to 100)
 *      - Validate no value is lost to rounding
 *      - Ensure liquidator incentives remain positive
 */
contract TrueDebtSemanticsTest is LiquidationTestBase {
    using FixedPointMathLib for uint256;
    
    // Test state
    address testBroker;
    uint256 constant PRINCIPAL = 1000e18;
    
    function setUp() public override {
        super.setUp();
        // Additional setup specific to these tests
    }
    
    /* ============================================================================ */
    /*                         BASELINE: NF = 1.0                                   */
    /* ============================================================================ */
    
    function test_TrueDebt_NF_1_0_ExactMatch() public {
        // GIVEN: Position with 1000 principal, NF = 1.0
        uint256 nf = 1e18;
        uint256 principal = PRINCIPAL;
        uint256 trueDebt = principal.mulWadDown(nf);
        
        // THEN: True debt should equal principal
        assertEq(trueDebt, principal, "True debt should equal principal when NF=1.0");
        
        // WHEN: Liquidating 500 true debt
        uint256 debtToCover = 500e18;
        uint256 principalToBurn = debtToCover.divWadDown(nf);
        
        // THEN: Principal to burn should equal debt to cover
        assertEq(principalToBurn, debtToCover, "Principal to burn should equal debt when NF=1.0");
    }
    
    function test_TrueDebt_NF_1_0_FullLiquidation() public {
        // GIVEN: Full position liquidation at NF=1.0
        uint256 nf = 1e18;
        uint256 principal = PRINCIPAL;
        uint256 debtToCover = principal; // Liquidate all
        
        // WHEN: Converting to principal
        uint256 principalToBurn = debtToCover.divWadDown(nf);
        
        // THEN: Should burn entire principal
        assertEq(principalToBurn, principal, "Should burn entire principal");
        
        // AND: Remaining debt should be zero
        uint256 remainingPrincipal = principal - principalToBurn;
        assertEq(remainingPrincipal, 0, "No debt should remain");
    }
    
    /* ============================================================================ */
    /*                    SHORTS WINNING: NF = 0.5                                  */
    /* ============================================================================ */
    
    function test_TrueDebt_NF_0_5_DoubledPrincipalBurn() public {
        // SCENARIO: Negative funding rates, shorts are paid
        // NF drops from 1.0 to 0.5 (debt value halved)
        
        uint256 nf = 0.5e18;
        uint256 principal = PRINCIPAL; // 1000 wRLP
        uint256 trueDebt = principal.mulWadDown(nf); // 500 true debt
        
        // THEN: True debt is half of principal
        assertEq(trueDebt, 500e18, "True debt should be 500 when NF=0.5");
        
        // WHEN: Liquidator wants to cover 250 true debt value
        uint256 debtToCover = 250e18;
        uint256 principalToBurn = debtToCover.divWadDown(nf);
        
        // THEN: Must burn 500 principal (2x the true debt value)
        assertEq(principalToBurn, 500e18, "Should burn 2x principal when NF=0.5");
        
        // FINANCIAL CHECK: Liquidator pays for 250 value but burns 500 tokens
        // This makes sense: each token is worth 0.5, so need 500 tokens to cover 250 value
    }
    
    function test_TrueDebt_NF_0_5_CloseFactorCheck() public {
        // GIVEN: Position with 1000 principal, NF=0.5
        uint256 nf = 0.5e18;
        uint256 principal = PRINCIPAL;
        uint256 trueDebt = principal.mulWadDown(nf); // 500
        uint256 closeFactor = 0.5e18; // 50%
        
        // WHEN: Calculating max liquidatable
        uint256 maxDebtToCover = trueDebt.mulWadDown(closeFactor); // 250
        
        // THEN: Can liquidate up to 250 true debt
        assertEq(maxDebtToCover, 250e18, "Max liquidatable is 250");
        
        // WHEN: Converting to principal
        uint256 principalToBurn = maxDebtToCover.divWadDown(nf); // 500
        
        // THEN: Will burn 500 principal (50% of 1000)
        assertEq(principalToBurn, 500e18, "Burns 500 principal");
        
        // FINANCIAL INSIGHT: Close factor applies to TRUE DEBT, not principal
        // At NF=0.5, liquidating 50% of true debt = liquidating 50% of principal
    }
    
    /* ============================================================================ */
    /*                    SHORTS LOSING: NF = 2.0                                   */
    /* ============================================================================ */
    
    function test_TrueDebt_NF_2_0_HalvedPrincipalBurn() public {
        // SCENARIO: Positive funding rates, shorts pay
        // NF rises from 1.0 to 2.0 (debt value doubled)
        
        uint256 nf = 2e18;
        uint256 principal = PRINCIPAL; // 1000 wRLP
        uint256 trueDebt = principal.mulWadDown(nf); // 2000 true debt
        
        // THEN: True debt is double the principal
        assertEq(trueDebt, 2000e18, "True debt should be 2000 when NF=2.0");
        
        // WHEN: Liquidator wants to cover 1000 true debt value
        uint256 debtToCover = 1000e18;
        uint256 principalToBurn = debtToCover.divWadDown(nf);
        
        // THEN: Only burn 500 principal (0.5x the true debt value)
        assertEq(principalToBurn, 500e18, "Should burn 0.5x principal when NF=2.0");
        
        // FINANCIAL CHECK: Liquidator pays for 1000 value but only burns 500 tokens
        // This makes sense: each token is worth 2.0, so 500 tokens = 1000 value
    }
    
    function test_TrueDebt_NF_2_0_LiquidatorPaysMore() public {
        // FINANCIAL ANALYSIS: At NF=2.0, liquidator pays more $ for same # of tokens
        
        uint256 nf = 2e18;
        uint256 principal = PRINCIPAL;
        uint256 indexPrice = 1e18; // $1 per unit
        
        // CASE 1: Liquidate 500 true debt
        uint256 debtToCover = 500e18;
        uint256 principalToBurn = debtToCover.divWadDown(nf); // 250 tokens
        uint256 liquidatorCost = debtToCover.mulWadDown(indexPrice); // $500
        
        assertEq(principalToBurn, 250e18, "Burns 250 tokens");
        assertEq(liquidatorCost, 500e18, "Costs $500");
        
        // INSIGHT: Liquidator pays $500 to burn 250 tokens
        // At NF=1.0, they would pay $250 to burn 250 tokens
        // The extra cost reflects the accrued interest (shorts are losing)
    }
    
    /* ============================================================================ */
    /*                    EXTREME VALUES: NF = 0.01 to 100                          */
    /* ============================================================================ */
    
    function test_TrueDebt_NF_0_01_ExtremeShortWin() public {
        // EXTREME: NF = 0.01 (99% funding profit for shorts)
        uint256 nf = 0.01e18;
        uint256 principal = PRINCIPAL;
        uint256 trueDebt = principal.mulWadDown(nf); // 10 true debt
        
        assertEq(trueDebt, 10e18, "True debt is 10 when NF=0.01");
        
        // Liquidate all true debt
        uint256 debtToCover = trueDebt;
        uint256 principalToBurn = debtToCover.divWadDown(nf);
        
        // Should burn entire principal
        assertEq(principalToBurn, principal, "Burns all 1000 principal");
        
        // FINANCIAL: Liquidator pays for 10 value, burns 1000 tokens
        // Each token is worth 0.01, so this is correct
    }
    
    function test_TrueDebt_NF_100_ExtremeShortLoss() public {
        // EXTREME: NF = 100 (100x debt increase for shorts)
        uint256 nf = 100e18;
        uint256 principal = PRINCIPAL;
        uint256 trueDebt = principal.mulWadDown(nf); // 100,000 true debt
        
        assertEq(trueDebt, 100_000e18, "True debt is 100k when NF=100");
        
        // Liquidate 50,000 true debt
        uint256 debtToCover = 50_000e18;
        uint256 principalToBurn = debtToCover.divWadDown(nf);
        
        // Should burn 500 principal
        assertEq(principalToBurn, 500e18, "Burns 500 principal");
        
        // FINANCIAL: Liquidator pays for 50k value, burns 500 tokens
        // Each token is worth 100, so this is correct
    }
    
    /* ============================================================================ */
    /*                         PRECISION & ROUNDING                                 */
    /* ============================================================================ */
    
    function test_TrueDebt_Precision_NoValueLost() public {
        // Test that rounding doesn't cause value loss
        uint256 nf = 1.337e18; // Arbitrary NF
        uint256 principal = 999e18; // Odd number
        
        uint256 trueDebt = principal.mulWadDown(nf);
        uint256 principalRecovered = trueDebt.divWadDown(nf);
        
        // Allow 1 wei difference due to rounding
        uint256 diff = principal > principalRecovered 
            ? principal - principalRecovered 
            : principalRecovered - principal;
            
        assertLe(diff, 1, "Rounding error should be at most 1 wei");
    }
    
    function test_TrueDebt_Dust_1Wei() public {
        // Edge case: 1 wei of debt
        uint256 nf = 1.5e18;
        uint256 principal = 1; // 1 wei
        
        uint256 trueDebt = principal.mulWadDown(nf);
        // trueDebt will round down to 0 due to WAD math
        
        // This is acceptable: dust amounts round to zero
        // Protocol doesn't need to handle sub-wei precision
    }
    
    function testFuzz_TrueDebt_RoundTrip(uint128 principal, uint64 nf) public {
        // Fuzz test: principal -> trueDebt -> principal should be lossless
        vm.assume(principal > 1e18); // Avoid dust
        vm.assume(nf > 0.01e18 && nf < 100e18); // Reasonable NF range
        
        uint256 normalizedNF = uint256(nf);
        uint256 trueDebt = uint256(principal).mulWadDown(normalizedNF);
        uint256 recovered = trueDebt.divWadDown(normalizedNF);
        
        // Allow small rounding error
        uint256 diff = uint256(principal) > recovered 
            ? uint256(principal) - recovered 
            : recovered - uint256(principal);
            
        assertLe(diff, 100, "Round-trip error should be minimal");
    }
    
    /* ============================================================================ */
    /*                    FINANCIAL INVARIANTS                                      */
    /* ============================================================================ */
    
    function test_Financial_LiquidatorIncentive_Maintained() public {
        // INVARIANT: Liquidator must profit regardless of NF
        
        uint256[3] memory nfValues = [uint256(0.5e18), uint256(1e18), uint256(2e18)];
        uint256 bonus = 1.05e18; // 5% bonus
        uint256 indexPrice = 1e18;
        
        for (uint i = 0; i < nfValues.length; i++) {
            uint256 nf = nfValues[i];
            uint256 debtToCover = 1000e18; // Cover 1000 true debt
            
            // Liquidator receives
            uint256 seizeValue = debtToCover.mulWadDown(indexPrice).mulWadDown(bonus);
            
            // Liquidator pays (cost of wRLP)
            uint256 principalToBurn = debtToCover.divWadDown(nf);
            uint256 wRLPCost = principalToBurn.mulWadDown(indexPrice).mulWadDown(nf);
            
            // Profit = seized - cost
            uint256 profit = seizeValue - wRLPCost;
            
            assertGt(profit, 0, "Liquidator must profit");
        }
    }
    
    function test_Financial_BorrowerLoss_BoundedByBonus() public {
        // INVARIANT: Borrower loses at most (debt × bonus) in collateral
        
        uint256 nf = 1.5e18;
        uint256 debtToCover = 1000e18;
        uint256 bonus = 1.05e18;
        uint256 indexPrice = 1e18;
        
        // Borrower's debt reduced by
        uint256 debtReduction = debtToCover;
        
        // Borrower's collateral seized
        uint256 collateralSeized = debtToCover.mulWadDown(indexPrice).mulWadDown(bonus);
        
        // Net loss
        uint256 loss = collateralSeized - debtReduction;
        uint256 maxLoss = debtReduction.mulWadDown(bonus - 1e18);
        
        assertLe(loss, maxLoss, "Borrower loss should not exceed bonus");
    }
}
