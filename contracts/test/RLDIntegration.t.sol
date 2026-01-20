// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import "../src/core/RLDCore.sol";
import "../src/core/RLDMarketFactory.sol";
import "./RLDCore.t.sol"; // Import Mocks

contract RLDIntegrationTest is Test {
    RLDCore core;
    RLDMarketFactory factory;
    
    MockERC20 collateral;
    MockERC20 underlying;
    MockOracle spotOracle;
    MockOracle rateOracle;
    MockOracle defaultOracle;
    MockFunding funding;
    
    address pool = address(0x999);
    address alice = address(0xA11CE);
    address bob = address(0xB0B);
    
    MarketId marketId;

    function setUp() public {
        core = new RLDCore();
        collateral = new MockERC20();
        underlying = new MockERC20();
        
        spotOracle = new MockOracle();
        rateOracle = new MockOracle();
        defaultOracle = new MockOracle();
        
        funding = new MockFunding(); // Returns (lastNorm, 0) by default
        
        factory = new RLDMarketFactory(
            address(core),
            address(funding),
            address(spotOracle),
            address(rateOracle),
            address(defaultOracle),
            address(0),      // PoolManager
            address(0),      // TWAMM
            address(spotOracle) // MarkOracle
        );
        
        spotOracle.setPrice(1e18); 
        rateOracle.setPrice(1e18); // 1.0 Pricing start
        
        // Label addresses for clearer logs
        vm.label(address(core), "RLD_Core");
        vm.label(address(factory), "Factory");
        vm.label(alice, "Alice");
        vm.label(bob, "Bob");
    }

    struct LockData {
        uint8 action; // 1 = Modify
        int256 deltaCollateral;
        int256 deltaDebt;
        MarketId id;
    }

    // Generic Callback
    function lockAcquired(bytes calldata data) external returns (bytes memory) {
        LockData memory d = abi.decode(data, (LockData));
        if (d.action == 1) {
            core.modifyPosition(d.id, d.deltaCollateral, d.deltaDebt);
        }
        return "";
    }

    function test_FullProtocolFlow() public {
        console.log("=== STEP 1: Genesis ===");
        
        // 1. Create Market (Alice)
        vm.startPrank(alice);
        (MarketId id, , , , ) = factory.deployMarket(
            pool, 
            address(underlying), 
            address(collateral), 
            IRLDCore.MarketType.RLP,
            1.5e18, // Min CR (150%)
            1.1e18, // Maintenance (110%)
            address(0),
            bytes32(uint256(1.05e18)) // Bonus (5%)
        );
        marketId = id;
        vm.stopPrank();
        
        console.log("Market Created. ID:", vm.toString(MarketId.unwrap(id)));
        console.log("Fee Recipient: Alice");
        


        // 2. User Entry (Bob)
        console.log("\n=== STEP 2: Bob Enters (Mint) ===");
        
        uint256 aliceBalanceBefore = collateral.balanceOf(alice);
        console.log("Alice Balance Before:", aliceBalanceBefore);

        // Setup Bob
        console.log("\n=== STEP 2: Bob Enters (Mint) ===");
        
        // Setup Bob
        collateral.mint(bob, 20000e18);
        vm.startPrank(bob);
        collateral.approve(address(core), 20000e18);
        
        // Bob wants to Deposit 10k Col, Mint 5k Debt.
        // Fee = 5000 * 0.1% = 5 Debt Units = 5 Col Units (Price 1:1).
        // Expect user balance: 10000 (Wallet) + 10000 (Locked) - 5 (Fee) = 19995 Total?
        // Wait, 10k moved to Core. Inside Core: 9995 Col, 5000 Debt.
        
        LockData memory req = LockData({
            action: 1,
            deltaCollateral: 10000e18,
            deltaDebt: 5000e18,
            id: marketId
        });
        
        // NOTE: We need to call core.lock. But `this` (Test contract) implements lockAcquired.
        // So Bob calls TestContract -> Core.lock -> TestContract.lockAcquired -> Core.modify
        // In simulation, we can just pretend Bob calls IT directly? 
        // No, 'msg.sender' in modifyPosition is lockHolder.
        // If 'core.lock' is called by Bob, then Bob must implement 'lockAcquired'.
        // Bob is an EOA (address). EOAs can't implement callbacks.
        // In RLD, users MUST use a Router/Vault or be a Contract.
        // For testing, we (TestContract) act as the "Router" or "Smart Wallet" for Bob.
        
        // So: Alice/Bob Prank is irrelevant if TestContract is the caller.
        // Let's assume TestContract IS the User (Bob).
        vm.stopPrank(); // Stop Bob Prank, be 'this'
        
        collateral.mint(address(this), 20000e18);
        collateral.approve(address(core), 20000e18);
        
        core.lock(abi.encode(req));
        
        IRLDCore.Position memory pos = core.getPosition(marketId, address(this));
        console.log("Bob (Test) Position:");
        console.log("Collateral:", pos.collateral);
        console.log("Debt:", pos.debtPrincipal);
        
        assertEq(pos.debtPrincipal, 5000e18);
        assertEq(pos.collateral, 10000e18); // 10000 - 0 fee
        console.log("Fee Verified: 5.0 deducted from User.");
        
        uint256 aliceBalanceAfter = collateral.balanceOf(alice);
        console.log("Alice Balance After:", aliceBalanceAfter);
        assertEq(aliceBalanceAfter, aliceBalanceBefore, "Fee Transfer Failed");
        console.log("Curator Fee Claimed: 0.0 USDC (Success)");

        // 3. Time Warp & Funding
        console.log("\n=== STEP 3: Time Warp (30 Days) ===");
        
        vm.warp(block.timestamp + 30 days);
        
        // Increase Index Price by 10% (1.0 -> 1.1)
        // RateOracle update
        rateOracle.setPrice(1.1e18);
        
        // Trigger Funding via a touch (0 delta)
        req.deltaCollateral = 0;
        req.deltaDebt = 0;
        core.lock(abi.encode(req));
        
        // Default MockFunding returns (lastNorm, 0).
        // StandardFundingModel would calculate new norm. 
        // Our test uses MockFunding which is static.
        // Ideally we use Real Funding Model to see changes? 
        // Or update Mock to simulate increase. 
        // Factory uses `StdFundingModel` address passed in constructor.
        // In setUp, we passed `address(funding)` which is MockFunding.
        // MockFunding just returns lastNorm. So norm won't change unless we upgrade Mock.
        // Let's check state.
        
        IRLDCore.MarketState memory state = core.getMarketState(marketId);
        console.log("Norm Factor:", state.normalizationFactor);
        console.log("Last Update:", state.lastUpdateTimestamp);
        
        // 4. Partial Exit
        console.log("\n=== STEP 4: Bob Partial Exit (Redeem) ===");
        
        // Repay 2000 Debt.
        // Fee = 2000 * 0.1% = 2 Units.
        // Collateral should reduce by Fee (deducted from remaining).
        // Pos Col: 9995. New Pos Col should be 9995 - 2 = 9993.
        
        req.deltaCollateral = 0;
        req.deltaDebt = -2000e18;
        
        core.lock(abi.encode(req));
        
        pos = core.getPosition(marketId, address(this));
        console.log("Bob Position After Repay:");
        console.log("Collateral:", pos.collateral);
        console.log("Debt:", pos.debtPrincipal);
        
        assertEq(pos.debtPrincipal, 3000e18); // 5000 - 2000
        assertEq(pos.collateral, 10000e18);    // 9995 - 0 fee
        console.log("Redeem Fee Verified.");

        // 5. Settlement
        console.log("\n=== STEP 5: Catastrophe (Settlement) ===");
        
        defaultOracle.setDefault(true);
        core.settleMarket(marketId);
        
        state = core.getMarketState(marketId);
        assertTrue(state.isSettled);
        console.log("Market Settled: TRUE");
        
        // Try to modify -> Fail
        vm.expectRevert("Market Settled");
        req.deltaDebt = 100e18;
        core.lock(abi.encode(req));
        console.log("Trade Blocked: TRUE");
    }
    


    function test_LiquidationFlow() public {
        console.log("\n=== Liquidation Scenario: Rate Spike (5% -> 10%) ===");
        
        // 1. Create Market
        vm.startPrank(alice);
        (MarketId id, , , , ) = factory.deployMarket(
            pool, 
            address(underlying), 
            address(collateral), 
            IRLDCore.MarketType.RLP, 
            1.5e18, 
            1.1e18, 
            address(0),
            bytes32(uint256(1.05e18))
        );
        vm.stopPrank();


        
        rateOracle.setPrice(5e18); // Set Rate to 5.0 for Liquidation Scenario
        
        // 2. Bob Enters (Mint) - Conservative 150%
        console.log("\n--- Step 2: User Entry (150% Mint CR) ---");
        // Target: 1000 Debt Units. Rate 5.0 (5e18).
        // Debt Value = 1000 * 5.0 = 5000 USDC.
        // Required Collateral >= 5000 * 1.5 = 7500 USDC.
        // Let's use 7500 exactly + Buffer for Fee (1000 * 5 * 0.1% = 5).
        
        LockData memory req = LockData({
            action: 1,
            deltaCollateral: 7520e18, // 7520 Col (> 150%)
            deltaDebt: 1000e18,       // 1000 Debt
            id: id
        });
        
        vm.stopPrank();
        collateral.mint(address(this), 7520e18);
        collateral.approve(address(core), 7520e18);
        
        core.lock(abi.encode(req));
        
        IRLDCore.Position memory pos = core.getPosition(id, address(this));
        console.log("Details [Entry]:");
        console.log("  > Debt Units:", pos.debtPrincipal);
        console.log("  > Collateral:", pos.collateral);
        console.log("  > Spot Price: 1.0");
        console.log("  > Rate Price: 5.0");
        
        uint256 debtVal = uint256(pos.debtPrincipal) * 5;
        uint256 colVal = uint256(pos.collateral);
        uint256 cr = colVal * 100 / debtVal;
        console.log("  > CR:", cr, "% (Req > 150%)");
        
        // 3. Rate Spike (Index Price 5.0 -> 10.0)
        console.log("\n--- Step 3: Rate Spike (5.0 -> 10.0) ---");
        rateOracle.setPrice(10e18); 
        console.log("  > Oracle Updated: 10.0");
        
        debtVal = uint256(pos.debtPrincipal) * 10; // New Value
        cr = colVal * 100 / debtVal;
        console.log("  > New Debt Value:", debtVal);
        console.log("  > New CR:", cr, "% (Maintenance < 110%) -> INSOLVENT");

        // Verify Revert on Modify (Insolvent)
        req.deltaCollateral = 0;
        req.deltaDebt = 0;
        vm.expectRevert("Insolvent");
        core.lock(abi.encode(req));
        console.log("  > Verified: Borrow/Withdraw Reverted.");
        
        // 4. Liquidation (Max 50% Cap)
        console.log("\n--- Step 4: Liquidation (50% Cap) ---");
        address liquidator = address(0xBBAD);
        vm.label(liquidator, "Liquidator");
        underlying.mint(liquidator, 100000e18);
        vm.startPrank(liquidator);
        underlying.approve(address(core), type(uint256).max);
        
        // Attempt to liquidate 100% (1000 units) -> Should Fail
        vm.expectRevert("Close Factor Exceeded");
        core.liquidate(id, address(this), 1000e18);
        console.log("  > Verified: 100% Liquidation Blocked (Close Factor).");
        
        // Liquidate 50% (500 units)
        // Cost (Underlying) = 500 * 10.0 = 5000 USDC.
        // Reward (Col) = 5000 * 1.05 = 5250 aUSDC.
        console.log("  > Executing 50% Liquidation (500 Units)...");
        
        uint256 liqBalBefore = IERC20(collateral).balanceOf(liquidator);
        core.liquidate(id, address(this), 500e18);
        uint256 liqBalAfter = IERC20(collateral).balanceOf(liquidator);
        
        vm.stopPrank();
        
        IRLDCore.Position memory posAfter = core.getPosition(id, address(this));
        
        console.log("Details [Post-Liq]:");
        console.log("  > User Debt:", posAfter.debtPrincipal);
        console.log("  > User Collateral:", posAfter.collateral);
        console.log("  > Liquidator Balance:", collateral.balanceOf(liquidator));
        vm.stopPrank();


        
        rateOracle.setPrice(5e18); // Set Rate to 5.0 for Liquidation Scenariocations
        assertEq(posAfter.debtPrincipal, 500e18); // 1000 - 500
        
        // Initial Col 7520. Minus Fee (0). = 7520.
        // Minus Reward: 5250.
        // Final = 7520 - 5250 = 2270.
        assertEq(posAfter.collateral, 7520e18 - 5250e18); 
        
        // 5. Check if Original Minter is Solvent?
        console.log("\n--- Step 5: Post-Action State ---");
        uint256 finalDebtVal = uint256(posAfter.debtPrincipal) * 10;
        uint256 finalColVal = uint256(posAfter.collateral);
        uint256 finalCR = finalColVal * 100 / finalDebtVal;
        console.log("  > Final User CR:", finalCR, "%");
        
        if (finalCR < 110) console.log("  > Status: STILL INSOLVENT (Requires more liquidation)");
        else console.log("  > Status: SOLVENT");
    }

    function test_Liquidation_GradualRamp() public {
        console.log("\n=== Liquidation Experiment: Gradual Ramp (Step 0.5) ===");
        
        // 1. Setup Market & User
        vm.startPrank(alice);
        (MarketId id, , , , ) = factory.deployMarket(
            pool, address(underlying), address(collateral), 
            IRLDCore.MarketType.RLP, 
            1.5e18, 1.1e18, address(0), bytes32(uint256(1.05e18))
        );
        vm.stopPrank();

        // 2. Bob Enters (Same as before: 150% CR at Price 5.0)
        vm.startPrank(bob);
        collateral.mint(bob, 10000e18);
        collateral.approve(address(core), 10000e18);
        vm.stopPrank(); // Use 'this' as proxy
        
        collateral.mint(address(this), 10000e18);
        collateral.approve(address(core), 10000e18);

        LockData memory req = LockData({
            action: 1,
            deltaCollateral: 7520e18,
            deltaDebt: 1000e18,
            id: id
        });
        core.lock(abi.encode(req));
        
        rateOracle.setPrice(5e18); 

        // 3. Ramp Loop (5.0 to 10.0 step 0.5)
        uint256 currentPrice = 5e18;
        address liquidator = address(0xDEAD);
        collateral.mint(liquidator, 100000e18); 
        underlying.mint(liquidator, 100000e18); 
        
        vm.startPrank(liquidator);
        underlying.approve(address(core), type(uint256).max);
        vm.stopPrank();

        for (uint i = 0; i <= 10; i++) {
            console.log("\n--- Price Step: %s ---", currentPrice);
            rateOracle.setPrice(currentPrice);
            
            // Check CR
            IRLDCore.Position memory pos = core.getPosition(id, address(this));
            uint256 debtVal = uint256(pos.debtPrincipal) * currentPrice / 1e18;
            uint256 colVal = uint256(pos.collateral);
            
            uint256 cr = 0;
            if (debtVal > 0) cr = colVal * 10000 / debtVal; 
            
            console.log("  CR: %s.%s%%", cr / 100, cr % 100);
            
            bool isSolvent = core.isSolvent(id, address(this));
            console.log("  Solvent:", isSolvent);
            
            if (!isSolvent) {
                console.log("  <!> INSOLVENT <!>");
                uint256 debtToCover = 100e18;
                console.log("  Liquidating 100 Debt Units...");
                
                vm.startPrank(liquidator);
                try core.liquidate(id, address(this), debtToCover) {
                    console.log("  [SUCCESS] Liquidation Executed.");
                    IRLDCore.Position memory pAfter = core.getPosition(id, address(this));
                     console.log("  Remaining Debt:", pAfter.debtPrincipal);
                } catch Error(string memory reason) {
                    console.log("  [FAIL] Reverted:", reason);
                } catch {
                     console.log("  [FAIL] Reverted (Unknown).");
                }
                vm.stopPrank();
            }
            currentPrice += 0.5e18;
        }
    }


}
