// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {RLDCore} from "../../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../../src/rld/core/RLDMarketFactory.sol";

/**
 * @title CRITICAL-001 Fix Verification Tests
 * @notice Tests to verify factory front-running vulnerability is fixed
 */
contract CRITICAL001FixTest is Test {
    address deployer = makeAddr("deployer");
    address attacker = makeAddr("attacker");
    
    // Mock addresses for factory constructor
    address poolManager = makeAddr("poolManager");
    address positionTokenImpl = makeAddr("positionTokenImpl");
    address primeBrokerImpl = makeAddr("primeBrokerImpl");
    address v4Oracle = makeAddr("v4Oracle");
    address fundingModel = makeAddr("fundingModel");
    address twamm = makeAddr("twamm");
    address metadataRenderer = makeAddr("metadataRenderer");
    uint32 fundingPeriod = 30 days;
    
    function setUp() public {
        vm.label(deployer, "Deployer");
        vm.label(attacker, "Attacker");
    }
    
    /* ============================================================================ */
    /*                           VULNERABILITY TESTS (BEFORE FIX)                   */
    /* ============================================================================ */
    
    /**
     * @notice This test would pass BEFORE the fix (demonstrating the vulnerability)
     * @dev With old setFactory() function, attacker could front-run and become factory
     */
    function test_BEFORE_FIX_AttackerCouldFrontRun() public {
        // This test demonstrates what WOULD happen with vulnerable code
        // (Cannot actually test since setFactory() is removed)
        
        // BEFORE FIX:
        // 1. Deploy RLDCore
        // RLDCore core = new RLDCore();
        
        // 2. Attacker sees setFactory() tx in mempool
        // 3. Attacker front-runs with higher gas
        // vm.prank(attacker);
        // core.setFactory(attacker);  // SUCCESS - attacker is now factory!
        
        // 4. Legitimate factory tx fails
        // vm.prank(deployer);
        // vm.expectRevert("Unauthorized");
        // core.setFactory(legitimateFactory);
        
        // Result: Attacker controls all market creation ❌
    }
    
    /* ============================================================================ */
    /*                            FIX VERIFICATION TESTS                            */
    /* ============================================================================ */
    
    /**
     * @notice Test 1: Factory must be set in constructor
     */
    function test_FactorySetInConstructor() public {
        vm.startPrank(deployer);
        
        // Deploy factory
        RLDMarketFactory factory = _deployFactory();
        
        // Deploy core with factory address
        RLDCore core = new RLDCore(address(factory), address(poolManager), address(0));
        
        // Verify factory is set
        assertEq(core.factory(), address(factory), "Factory not set correctly");
        
        vm.stopPrank();
    }
    
    /**
     * @notice Test 2: Factory address cannot be zero
     */
    function test_RevertsIfFactoryIsZero() public {
        vm.expectRevert("Invalid factory");
        new RLDCore(address(0), address(poolManager), address(0));
    }
    
    /**
     * @notice Test 3: Factory is immutable (no setFactory function exists)
     */
    function test_FactoryIsImmutable() public {
        vm.startPrank(deployer);
        
        RLDMarketFactory factory = _deployFactory();
        RLDCore core = new RLDCore(address(factory), address(poolManager), address(0));
        
        // Verify factory is set
        address originalFactory = core.factory();
        assertEq(originalFactory, address(factory));
        
        // Try to call setFactory (should not exist)
        // This would fail at compile time, but we can verify via low-level call
        (bool success, ) = address(core).call(
            abi.encodeWithSignature("setFactory(address)", makeAddr("newFactory"))
        );
        
        // Function doesn't exist, so call fails
        assertFalse(success, "setFactory should not exist");
        
        // Factory unchanged
        assertEq(core.factory(), originalFactory, "Factory should be immutable");
        
        vm.stopPrank();
    }
    
    /**
     * @notice Test 4: Atomic deployment works correctly
     */
    function test_AtomicDeploymentWorks() public {
        vm.startPrank(deployer);
        
        // Step 1: Deploy factory with CORE = address(0)
        RLDMarketFactory factory = _deployFactory();
        assertEq(factory.CORE(), address(0), "Factory CORE should be zero initially");
        
        // Step 2: Deploy core with factory address
        RLDCore core = new RLDCore(address(factory), address(poolManager), address(0));
        assertEq(core.factory(), address(factory), "Core factory should be set");
        
        // Step 3: Initialize factory's CORE
        factory.initializeCore(address(core));
        assertEq(factory.CORE(), address(core), "Factory CORE should be set");
        
        // Verify bidirectional link
        assertEq(core.factory(), address(factory), "Core -> Factory link broken");
        assertEq(factory.CORE(), address(core), "Factory -> Core link broken");
        
        vm.stopPrank();
    }
    
    /**
     * @notice Test 5: Only deployer can initialize CORE
     */
    function test_OnlyDeployerCanInitializeCore() public {
        vm.startPrank(deployer);
        RLDMarketFactory factory = _deployFactory();
        RLDCore core = new RLDCore(address(factory), address(poolManager), address(0));
        vm.stopPrank();
        
        // Attacker tries to initialize
        vm.prank(attacker);
        vm.expectRevert("Not deployer");
        factory.initializeCore(address(core));
        
        // Verify CORE still zero
        assertEq(factory.CORE(), address(0), "CORE should still be zero");
    }
    
    /**
     * @notice Test 6: CORE can only be initialized once
     */
    function test_CoreCanOnlyBeInitializedOnce() public {
        vm.startPrank(deployer);
        
        RLDMarketFactory factory = _deployFactory();
        RLDCore core1 = new RLDCore(address(factory), address(poolManager), address(0));
        RLDCore core2 = new RLDCore(address(factory), address(poolManager), address(0));
        
        // First initialization succeeds
        factory.initializeCore(address(core1));
        assertEq(factory.CORE(), address(core1));
        
        // Second initialization fails
        vm.expectRevert("Already initialized");
        factory.initializeCore(address(core2));
        
        // CORE unchanged
        assertEq(factory.CORE(), address(core1), "CORE should not change");
        
        vm.stopPrank();
    }
    
    /**
     * @notice Test 7: Cannot initialize with zero address
     */
    function test_CannotInitializeWithZeroAddress() public {
        vm.startPrank(deployer);
        
        RLDMarketFactory factory = _deployFactory();
        
        vm.expectRevert("Invalid core");
        factory.initializeCore(address(0));
        
        vm.stopPrank();
    }
    
    /**
     * @notice Test 8: Cannot create markets before CORE is initialized
     */
    function test_CannotCreateMarketsBeforeCoreInitialized() public {
        vm.startPrank(deployer);
        
        RLDMarketFactory factory = _deployFactory();
        
        // Try to create market before initializing CORE
        RLDMarketFactory.DeployParams memory params = _getDefaultParams();
        
        vm.expectRevert("Core not initialized");
        factory.createMarket(params);
        
        vm.stopPrank();
    }
    
    /**
     * @notice Test 9: No front-running window exists
     */
    function test_NoFrontRunningWindow() public {
        // Simulate deployment in single transaction
        vm.startPrank(deployer);
        
        // All three steps in same transaction
        RLDMarketFactory factory = _deployFactory();
        RLDCore core = new RLDCore(address(factory), address(poolManager), address(0));
        factory.initializeCore(address(core));
        
        vm.stopPrank();
        
        // Attacker cannot interfere at any point
        // - Cannot set factory in Core (no setFactory function)
        // - Cannot initialize factory's CORE (only deployer)
        // - Cannot create markets (only owner)
        
        // Verify deployment is secure
        assertEq(core.factory(), address(factory));
        assertEq(factory.CORE(), address(core));
    }
    
    /* ============================================================================ */
    /*                                   HELPERS                                    */
    /* ============================================================================ */
    
    function _deployFactory() internal returns (RLDMarketFactory) {
        return new RLDMarketFactory(
            poolManager,
            positionTokenImpl,
            primeBrokerImpl,
            v4Oracle,
            fundingModel,
            twamm,
            metadataRenderer,
            fundingPeriod
        );
    }
    
    function _getDefaultParams() internal pure returns (RLDMarketFactory.DeployParams memory) {
        return RLDMarketFactory.DeployParams({
            underlyingPool: address(0x123),
            underlyingToken: address(0x456),
            collateralToken: address(0x789),
            curator: address(0xABC),
            positionTokenName: "Test wRLP",
            positionTokenSymbol: "wRLP",
            minColRatio: 1.2e18,
            maintenanceMargin: 1.1e18,
            liquidationCloseFactor: 0.5e18,
            liquidationModule: address(0xDEF),
            liquidationParams: bytes32(0),
            spotOracle: address(0x111),
            rateOracle: address(0x222),
            oraclePeriod: 3600,
            poolFee: 3000,
            tickSpacing: 60
        });
    }
}
