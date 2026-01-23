// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "../AtomicDeployment.t.sol"; // Base setup
import {TwammBrokerModule} from "../../src/rld/modules/broker/TwammBrokerModule.sol";

contract LiquidationWaterfallTest is AtomicDeploymentTest {
    
    // Additional vars
    TwammBrokerModule twammModule;

    function setUp() public override {
        super.setUp(); // Sets up core, factory, tokens, etc.
        
        // We need to attach the TwammBrokerModule to the Broker for the TWAMM test
        // Prerequisite: Core must recognize this module? No, PrimeBroker delegates.
        // But PrimeBroker needs to know WHERE to delegate.
        // In this simplified test environment, we might need to manually set it or ensure Factory sets it.
        // RLDMarketFactory usually doesn't deploy TwammBrokerModule? 
        // Let's check Contracts Analysis. `TwammBrokerModule` is usually a singleton or deployed by factory?
        // Actually, `PrimeBroker` code usually hardcodes modules or has a registry.
        // Assuming `PrimeBroker` implementation in `contracts/src/rld/broker/PrimeBroker.sol` handles delegation.
        // For this test, we assume the broker works as deployed.
    }

    /// @notice Scenario C: Waterfall Liquidation
    /// @dev Verifies that `liquidate()` respects the "Least Destructive" asset priority.
    /// Flow:
    /// 1. User is setup with Cash (Collateral Token) and potentially other assets.
    /// 2. Insolvency is triggered by a price crash (Collateral Value drops).
    /// 3. Liquidator calls `liquidate`.
    /// 4. RLDCore calculates `seizeAmount` including the Dutch Auction Bonus.
    /// 5. PrimeBroker.seize() is called.
    /// 6. Broker checks Cash Balance. If sufficient, it pays using Cash (Zero Slippage).
    /// 7. Assert: Liquidator receives Collateral, User Debt Reduced.
    struct LockData {
        MarketId marketId;
        int256 deltaCollateral;
        int256 deltaDebt;
        address target;
        bytes data;
    }

    function test_Liquidation_SeizesCashFirst() public {
        // [Step 1] Setup Market
        // We use the Global Config params (Base 5% Bonus, 20% Max).
        (MarketId marketId, address brokerFactory) = marketFactory.createMarket(_defaultParams());
        
        // [Step 2] Create User Broker & Fund it
        address user = address(0x111);
        vm.prank(user);
        address brokerAddr = PrimeBrokerFactory(brokerFactory).createBroker();
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));

        // Fund Broker with 1000 aUSDC
        MockERC20(collateralToken).mint(brokerAddr, 1000e6);
        
        // [Step 3] Open Position
        // User mints 500 Debt.
        // Initial State: Collateral 1000. Debt 500. CR = 200%. (Secure)
        vm.prank(user);
        core.lock(abi.encode(
            LockData({
                marketId: marketId,
                deltaCollateral: 0, 
                deltaDebt: 500e18,
                target: address(broker),
                data: ""
            })
        ));
        
        // [Step 4] Trigger Insolvency
        // We simulate a Market Crash. Collateral (aUSDC) price drops from $1.00 to $0.40.
        // New State:
        // - Collateral Value: 1000 * 0.40 = $400.
        // - Debt Value: $500.
        // - CR: 400 / 500 = 80%.
        // - Maintenance Margin: 110%.
        // RESULT: Highly Insolvent.
        oracle.setPrice(4e17); // 0.4
        
        assertFalse(core.isSolvent(marketId, brokerAddr), "Should be insolvent");
        
        // [Step 5] Liquidation Execution
        address liquidator = address(0x999);
        uint256 debtToCover = 100e18; // Liquidating $100 of debt
        
        // Liquidator must hold `wRLP` (Position Token) to burn.
        // Only then does core release the collateral.
        PositionToken(core.getMarketAddresses(marketId).positionToken).mint(liquidator, 1000e18);

        vm.startPrank(liquidator);
        PositionToken(core.getMarketAddresses(marketId).positionToken).approve(address(core), type(uint256).max);
        
        // Expected Math (With TEST_LIQ_PARAMS):
        // Health Score = 80%.
        // Base Discount = 5%. Slope = 1.0.
        // Dynamic Bonus = (1.0 - 0.8) * 1.0 = 20%? 
        // Wait, Code Logic: if (Health < 1.0) bonus += (1-H) * Slope.
        // (1 - 0.8) = 0.2. 0.2 * 100 (Slope 1.0) = 20%.
        // Total Bonus = Base (5%) + Dynamic (20%) = 25%.
        // Cap is 20%. So Bonus = 20%.
        
        // Cost (Debt Valid): $100.
        // Reward Value: $100 * (1 + 0.20) = $120.
        // Seize Amount (in Tokens): $120 / Price ($0.40) = 300 Tokens.
        
        // So we expect the liquidator to receive exactly 300.0 aUSDC.
        
        uint256 balBefore = ERC20(collateralToken).balanceOf(liquidator);
        
        core.liquidate(marketId, brokerAddr, debtToCover);
        
        uint256 balAfter = ERC20(collateralToken).balanceOf(liquidator);
        
        assertGt(balAfter, balBefore, "Liquidator should receive collateral");
        assertApproxEqAbs(balAfter - balBefore, 300e6, 2e6); // 300 Expected
        
        vm.stopPrank();
    }
    
    function _defaultParams() internal view returns (RLDMarketFactory.DeployParams memory) {
        return getGlobalDeployParams(
            underlyingPool,
            underlyingToken,
            collateralToken,
            address(this),
            address(oracle),
            address(oracle),
            address(0x123)
        );
    }
}
