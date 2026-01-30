// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console} from "forge-std/Test.sol";
import {RLDCore} from "../../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../../src/rld/core/RLDMarketFactory.sol";
import {IRLDCore, MarketId} from "../../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../../src/rld/broker/PrimeBroker.sol";
import {PositionToken} from "../../src/rld/tokens/PositionToken.sol";
import {UniswapV4SingletonOracle} from "../../src/rld/modules/oracles/UniswapV4SingletonOracle.sol";
import {StandardFundingModel} from "../../src/rld/modules/funding/StandardFundingModel.sol";
import {PoolManager} from "v4-core/src/PoolManager.sol";
import {GlobalTestConfig} from "../utils/GlobalTestConfig.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {MockOracle} from "../factory/unit/RLDMarketFactoryTest.t.sol";
import {MockFundingModel} from "../factory/unit/RLDMarketFactoryTest.t.sol";

/**
 * @title Curator Functionality Test Suite
 * @notice Comprehensive tests for curator functions: risk updates, debt cap, pool fees
 * @dev Tests cover:
 *      - proposeRiskUpdate() with validation
 *      - Auto-apply after 7-day timelock
 *      - cancelRiskUpdate()
 *      - Debt cap enforcement
 *      - updatePoolFee()
 */
contract CuratorFunctionalityTest is Test, GlobalTestConfig {
    // Core contracts
    RLDCore core;
    RLDMarketFactory factory;
    PoolManager poolManager;
    PositionToken positionTokenImpl;
    PrimeBroker primeBrokerImpl;
    UniswapV4SingletonOracle v4Oracle;
    StandardFundingModel fundingModel;
    
    // Mocks
    MockOracle oracle;
    MockERC20 underlying;
    MockERC20 collateral;
    
    // Test addresses
    address curator = makeAddr("curator");
    address notCurator = makeAddr("notCurator");
    address user = makeAddr("user");
    
    // Market ID for tests
    MarketId marketId;
    
    // Constants
    uint256 constant TIMELOCK = 7 days;
    
    function setUp() public {
        // Deploy infrastructure
        poolManager = new PoolManager(address(0));
        positionTokenImpl = createPositionTokenImpl();
        primeBrokerImpl = new PrimeBroker(
            address(0),
            address(0),
            address(0),
            address(0)
        );
        v4Oracle = new UniswapV4SingletonOracle();
        fundingModel = new StandardFundingModel();
        
        // Deploy mocks
        oracle = new MockOracle();
        oracle.setIndexPrice(10e18);
        underlying = new MockERC20("USDC", "USDC", 6);
        collateral = new MockERC20("aUSDC", "aUSDC", 6);
        
        // ATOMIC DEPLOYMENT PATTERN
        factory = new RLDMarketFactory(
            address(poolManager),
            address(positionTokenImpl),
            address(primeBrokerImpl),
            address(v4Oracle),
            address(fundingModel),
            address(0), // No TWAMM
            address(0x1), // Mock renderer
            30 days
        );
        
        core = new RLDCore(address(factory), address(poolManager), address(0));
        factory.initializeCore(address(core));
        
        // Create a market with curator set
        marketId = _createTestMarket();
    }
    
    /* ============================================================================ */
    /*                         PROPOSE RISK UPDATE TESTS                            */
    /* ============================================================================ */
    
    function test_ProposeRiskUpdate_Success() public {
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,     // minColRatio: 150%
            1.2e18,     // maintenanceMargin: 120%
            0.6e18,     // liquidationCloseFactor: 60%
            14 days,    // fundingPeriod: 14 days
            1000000e18, // debtCap: 1M tokens
            bytes32(uint256(0x123)) // liquidationParams
        );
        
        // Check pending update exists
        IRLDCore.PendingRiskUpdate memory pending = core.getPendingRiskUpdate(marketId);
        assertTrue(pending.pending, "Update should be pending");
        assertEq(pending.minColRatio, 1.5e18);
        assertEq(pending.maintenanceMargin, 1.2e18);
        assertEq(pending.liquidationCloseFactor, 0.6e18);
        assertEq(pending.fundingPeriod, 14 days);
        assertEq(pending.debtCap, 1000000e18);
        assertEq(pending.executeAt, block.timestamp + TIMELOCK);
    }
    
    function test_ProposeRiskUpdate_OnlyCurator() public {
        vm.prank(notCurator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.Unauthorized.selector));
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
    }
    
    function test_ProposeRiskUpdate_InvalidMinColRatio() public {
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "MinCol <= 100%"));
        core.proposeRiskUpdate(
            marketId,
            1e18,       // 100% - too low (must be > 100%)
            0.9e18,
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
    }
    
    function test_ProposeRiskUpdate_InvalidMaintenanceMargin() public {
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "Maintenance < 100%"));
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            0.99e18,    // 99% - too low (must be >= 100%)
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
    }
    
    function test_ProposeRiskUpdate_InvalidRiskConfig() public {
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "Risk Config Error"));
        core.proposeRiskUpdate(
            marketId,
            1.2e18,     // minColRatio: 120%
            1.2e18,     // maintenanceMargin: 120% (must be < minColRatio)
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
    }
    
    function test_ProposeRiskUpdate_InvalidCloseFactor() public {
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "Invalid CloseFactor"));
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            1.1e18,     // 110% - too high (must be <= 100%)
            30 days,
            0,
            bytes32(0)
        );
    }
    
    function test_ProposeRiskUpdate_InvalidFundingPeriod_TooLow() public {
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "Invalid period"));
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            12 hours,   // Too low (must be >= 1 day)
            0,
            bytes32(0)
        );
    }
    
    function test_ProposeRiskUpdate_InvalidFundingPeriod_TooHigh() public {
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "Invalid period"));
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            400 days,   // Too high (must be <= 365 days)
            0,
            bytes32(0)
        );
    }
    
    function test_ProposeRiskUpdate_EmitsEvent() public {
        uint48 expectedExecuteAt = uint48(block.timestamp + TIMELOCK);
        
        vm.expectEmit(true, false, false, true);
        emit IRLDCore.RiskUpdateProposed(
            marketId,
            1.5e18,
            1.2e18,
            0.6e18,
            14 days,
            1000000e18,
            bytes32(uint256(0x123)),
            expectedExecuteAt
        );
        
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.6e18,
            14 days,
            1000000e18,
            bytes32(uint256(0x123))
        );
    }
    
    /* ============================================================================ */
    /*                         AUTO-APPLY TIMELOCK TESTS                            */
    /* ============================================================================ */
    
    function test_AutoApply_BeforeTimelock_ReturnsOldConfig() public {
        // Get original config
        IRLDCore.MarketConfig memory originalConfig = core.getMarketConfig(marketId);
        
        // Propose update
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,     // New minColRatio: 150%
            1.2e18,
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
        
        // Before timelock expires, should return OLD config
        IRLDCore.MarketConfig memory currentConfig = core.getMarketConfig(marketId);
        assertEq(currentConfig.minColRatio, originalConfig.minColRatio, "Should return old config before timelock");
    }
    
    function test_AutoApply_AfterTimelock_ReturnsNewConfig() public {
        // Propose update
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,     // New minColRatio: 150%
            1.2e18,     // New maintenanceMargin: 120%
            0.6e18,     // New liquidationCloseFactor: 60%
            14 days,    // New fundingPeriod: 14 days
            1000000e18, // New debtCap: 1M
            bytes32(uint256(0x456))
        );
        
        // Warp past timelock
        vm.warp(block.timestamp + TIMELOCK + 1);
        
        // After timelock expires, should return NEW config
        IRLDCore.MarketConfig memory newConfig = core.getMarketConfig(marketId);
        assertEq(newConfig.minColRatio, 1.5e18, "Should auto-apply new minColRatio");
        assertEq(newConfig.maintenanceMargin, 1.2e18, "Should auto-apply new maintenanceMargin");
        assertEq(newConfig.liquidationCloseFactor, 0.6e18, "Should auto-apply new liquidationCloseFactor");
        assertEq(newConfig.fundingPeriod, 14 days, "Should auto-apply new fundingPeriod");
        assertEq(newConfig.debtCap, 1000000e18, "Should auto-apply new debtCap");
        assertEq(newConfig.liquidationParams, bytes32(uint256(0x456)), "Should auto-apply new liquidationParams");
    }
    
    function test_AutoApply_ExactlyAtTimelock() public {
        // Propose update
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
        
        // Warp to exactly timelock (not +1)
        vm.warp(block.timestamp + TIMELOCK);
        
        // Should apply at exactly executeAt timestamp
        IRLDCore.MarketConfig memory newConfig = core.getMarketConfig(marketId);
        assertEq(newConfig.minColRatio, 1.5e18, "Should apply at exact timelock");
    }
    
    function test_AutoApply_BrokerVerifierUnchanged() public {
        // Get original broker verifier
        IRLDCore.MarketConfig memory originalConfig = core.getMarketConfig(marketId);
        address originalBrokerVerifier = originalConfig.brokerVerifier;
        
        // Propose update (cannot change brokerVerifier)
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            14 days,
            0,
            bytes32(0)
        );
        
        // Warp past timelock
        vm.warp(block.timestamp + TIMELOCK + 1);
        
        // Broker verifier should be unchanged (immutable)
        IRLDCore.MarketConfig memory newConfig = core.getMarketConfig(marketId);
        assertEq(newConfig.brokerVerifier, originalBrokerVerifier, "BrokerVerifier should be immutable");
    }
    
    /* ============================================================================ */
    /*                         CANCEL RISK UPDATE TESTS                             */
    /* ============================================================================ */
    
    function test_CancelRiskUpdate_Success() public {
        // Propose update
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
        
        // Cancel update
        vm.prank(curator);
        core.cancelRiskUpdate(marketId);
        
        // Pending should be cleared
        IRLDCore.PendingRiskUpdate memory pending = core.getPendingRiskUpdate(marketId);
        assertFalse(pending.pending, "Pending should be cleared after cancel");
    }
    
    function test_CancelRiskUpdate_OnlyCurator() public {
        // Propose update
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
        
        // Non-curator tries to cancel
        vm.prank(notCurator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.Unauthorized.selector));
        core.cancelRiskUpdate(marketId);
    }
    
    function test_CancelRiskUpdate_NoPendingUpdate() public {
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "No pending update"));
        core.cancelRiskUpdate(marketId);
    }
    
    function test_CancelRiskUpdate_EmitsEvent() public {
        // Propose update
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
        
        // Expect event
        vm.expectEmit(true, false, false, false);
        emit IRLDCore.RiskUpdateCancelled(marketId);
        
        vm.prank(curator);
        core.cancelRiskUpdate(marketId);
    }
    
    function test_CancelRiskUpdate_BeforeTimelock_PreventsApply() public {
        // Get original config
        IRLDCore.MarketConfig memory originalConfig = core.getMarketConfig(marketId);
        
        // Propose update
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.5e18,
            30 days,
            0,
            bytes32(0)
        );
        
        // Cancel before timelock
        vm.prank(curator);
        core.cancelRiskUpdate(marketId);
        
        // Warp past original timelock
        vm.warp(block.timestamp + TIMELOCK + 1);
        
        // Config should still be original (update was cancelled)
        IRLDCore.MarketConfig memory currentConfig = core.getMarketConfig(marketId);
        assertEq(currentConfig.minColRatio, originalConfig.minColRatio, "Should use original after cancel");
    }
    
    /* ============================================================================ */
    /*                           UPDATE POOL FEE TESTS                              */
    /* ============================================================================ */
    
    function test_UpdatePoolFee_RevertsWithoutTWAMM() public {
        // Note: In this test setup, TWAMM is set to address(0)
        // This verifies the TWAMM configuration check works
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "TWAMM not configured"));
        core.updatePoolFee(marketId, 5000);
    }
    
    function test_UpdatePoolFee_OnlyCurator() public {
        vm.prank(notCurator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.Unauthorized.selector));
        core.updatePoolFee(marketId, 5000);
    }
    
    function test_UpdatePoolFee_FeeTooHigh_RevertsAfterTWAMMCheck() public {
        // Note: Fee validation happens after TWAMM check, so we'll get TWAMM error first
        // This test verifies access control is checked before TWAMM config
        vm.prank(curator);
        // Fee check happens after TWAMM check, so error is TWAMM not configured
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.InvalidParam.selector, "TWAMM not configured"));
        core.updatePoolFee(marketId, 1000001);
    }
    
    function test_UpdatePoolFee_InvalidMarket() public {
        MarketId invalidMarketId = MarketId.wrap(bytes32(uint256(0x999)));
        
        vm.prank(curator);
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.Unauthorized.selector));
        core.updatePoolFee(invalidMarketId, 5000);
    }
    
    /* ============================================================================ */
    /*                           GET PENDING UPDATE TESTS                           */
    /* ============================================================================ */
    
    function test_GetPendingRiskUpdate_NoPending() public view {
        IRLDCore.PendingRiskUpdate memory pending = core.getPendingRiskUpdate(marketId);
        assertFalse(pending.pending, "Should have no pending update initially");
    }
    
    function test_GetPendingRiskUpdate_WithPending() public {
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            1.5e18,
            1.2e18,
            0.6e18,
            14 days,
            500000e18,
            bytes32(uint256(0x789))
        );
        
        IRLDCore.PendingRiskUpdate memory pending = core.getPendingRiskUpdate(marketId);
        assertTrue(pending.pending);
        assertEq(pending.minColRatio, 1.5e18);
        assertEq(pending.maintenanceMargin, 1.2e18);
        assertEq(pending.liquidationCloseFactor, 0.6e18);
        assertEq(pending.fundingPeriod, 14 days);
        assertEq(pending.debtCap, 500000e18);
        assertEq(pending.liquidationParams, bytes32(uint256(0x789)));
        assertEq(pending.executeAt, block.timestamp + TIMELOCK);
    }
    
    /* ============================================================================ */
    /*                            FUZZ TESTS                                        */
    /* ============================================================================ */
    
    function testFuzz_ProposeRiskUpdate_ValidParams(
        uint64 minColRatioSeed,
        uint64 maintenanceMarginSeed,
        uint64 liquidationCloseFactorSeed,
        uint32 fundingPeriodSeed,
        uint128 debtCap
    ) public {
        // Use bound() to constrain to valid ranges (more efficient than vm.assume)
        uint64 minColRatio = uint64(bound(minColRatioSeed, 1.01e18, 10e18));
        uint64 maintenanceMargin = uint64(bound(maintenanceMarginSeed, 1e18, minColRatio - 1));
        uint64 liquidationCloseFactor = uint64(bound(liquidationCloseFactorSeed, 1, 1e18));
        uint32 fundingPeriod = uint32(bound(fundingPeriodSeed, 1 days, 365 days));
        
        vm.prank(curator);
        core.proposeRiskUpdate(
            marketId,
            minColRatio,
            maintenanceMargin,
            liquidationCloseFactor,
            fundingPeriod,
            debtCap,
            bytes32(0)
        );
        
        IRLDCore.PendingRiskUpdate memory pending = core.getPendingRiskUpdate(marketId);
        assertTrue(pending.pending);
        assertEq(pending.minColRatio, minColRatio);
        assertEq(pending.maintenanceMargin, maintenanceMargin);
        assertEq(pending.liquidationCloseFactor, liquidationCloseFactor);
        assertEq(pending.fundingPeriod, fundingPeriod);
        assertEq(pending.debtCap, debtCap);
    }
    
    /* ============================================================================ */
    /*                            HELPER FUNCTIONS                                  */
    /* ============================================================================ */
    
    function _createTestMarket() internal returns (MarketId) {
        RLDMarketFactory.DeployParams memory params = RLDMarketFactory.DeployParams({
            underlyingPool: address(0x999),
            underlyingToken: address(underlying),
            collateralToken: address(collateral),
            curator: curator,
            positionTokenName: "Wrapped RLP: aUSDC",
            positionTokenSymbol: "wRLPaUSDC",
            minColRatio: 1.2e18,
            maintenanceMargin: 1.1e18,
            liquidationCloseFactor: 0.5e18,
            liquidationModule: address(0x123),
            liquidationParams: bytes32(0),
            spotOracle: address(oracle),
            rateOracle: address(oracle),
            oraclePeriod: 3600,
            poolFee: 3000,
            tickSpacing: 60
        });
        
        (MarketId id, ) = factory.createMarket(params);
        return id;
    }
}
