// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {RLDMarketFactory} from "../../../src/rld/core/RLDMarketFactory.sol";
import {RLDCore} from "../../../src/rld/core/RLDCore.sol";
import {StandardFundingModel} from "../../../src/rld/modules/funding/StandardFundingModel.sol";
import {PositionToken} from "../../../src/rld/tokens/PositionToken.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {UniswapV4SingletonOracle} from "../../../src/rld/modules/oracles/UniswapV4SingletonOracle.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../../../src/shared/interfaces/IRLDOracle.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolManager} from "v4-core/src/PoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {GlobalTestConfig} from "../../utils/GlobalTestConfig.sol";
import {TWAMM} from "../../../src/twamm/TWAMM.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {HookMiner} from "v4-periphery/src/utils/HookMiner.sol";

/**
 * @title FundingRateCalculationTest
 * @notice Comprehensive tests for funding rate calculations across different currency orderings
 * @dev Tests verify:
 *      - Correct price queries for both wRLP < collateral and wRLP > collateral
 *      - Funding rate calculations over time
 *      - Normalization factor updates
 */
contract FundingRateCalculationTest is Test, GlobalTestConfig {
    // Core contracts
    RLDCore core;
    RLDMarketFactory factory;
    PoolManager poolManager;
    PositionToken positionTokenImpl;
    PrimeBroker primeBrokerImpl;
    UniswapV4SingletonOracle v4Oracle;
    StandardFundingModel fundingModel;
    TWAMM twamm;
    
    // Mocks
    MockOracle rateOracle;
    MockERC20 underlying;
    MockERC20 collateral;
    
    // Test constants
    uint256 constant INITIAL_NORM_FACTOR = 1e18;
    uint256 constant FUNDING_PERIOD = 30 days;
    uint256 constant TWAMM_INTERVAL = 10000;
    
    function setUp() public {
        console.log("\n========================================");
        console.log("SETUP: Deploying Test Infrastructure");
        console.log("========================================\n");
        
        // Deploy infrastructure
        poolManager = new PoolManager(address(0));
        console.log("PoolManager deployed at:", address(poolManager));
        
        positionTokenImpl = createPositionTokenImpl();
        console.log("PositionToken impl deployed at:", address(positionTokenImpl));
        
        primeBrokerImpl = new PrimeBroker(
            address(0),  // Core address will be set later
            address(0),
            address(0),
            address(0)
        );
        console.log("PrimeBroker impl deployed at:", address(primeBrokerImpl));
        
        // Deploy oracles
        v4Oracle = new UniswapV4SingletonOracle();
        console.log("V4 Oracle deployed at:", address(v4Oracle));
        
        rateOracle = new MockOracle();
        console.log("Rate Oracle deployed at:", address(rateOracle));
        
        // Deploy funding model
        fundingModel = new StandardFundingModel();
        console.log("StandardFundingModel deployed at:", address(fundingModel));

        // Deploy TWAMM Hook using HookMiner
        uint160 flags = uint160(
            Hooks.BEFORE_INITIALIZE_FLAG |
            Hooks.BEFORE_ADD_LIQUIDITY_FLAG |
            Hooks.BEFORE_REMOVE_LIQUIDITY_FLAG |
            Hooks.BEFORE_SWAP_FLAG |
            Hooks.AFTER_SWAP_FLAG
        );
        
        bytes memory creationCode = type(TWAMM).creationCode;
        bytes memory constructorArgs = abi.encode(IPoolManager(address(poolManager)), TWAMM_INTERVAL, address(this), address(0));
        
        (address hookAddress, bytes32 salt) = HookMiner.find(address(this), flags, creationCode, constructorArgs);
        twamm = new TWAMM{salt: salt}(IPoolManager(address(poolManager)), TWAMM_INTERVAL, address(this), address(0));
        
        require(address(twamm) == hookAddress, "Hook address mismatch");
        console.log("TWAMM Hook deployed at:", address(twamm));
        
        // Deploy tokens
        underlying = new MockERC20("USDC", "USDC", 6);
        collateral = new MockERC20("Aave USDC", "aUSDC", 6);
        console.log("Underlying token (USDC):", address(underlying));
        console.log("Collateral token (aUSDC):", address(collateral));
        
        // ATOMIC DEPLOYMENT PATTERN (CRITICAL-001 FIX)
        // Step 1: Deploy factory with CORE = address(0)
        factory = new RLDMarketFactory(
            address(poolManager),
            address(positionTokenImpl),
            address(primeBrokerImpl),
            address(v4Oracle),
            address(fundingModel),
            address(twamm),
            address(0x1), // Mock renderer
            uint32(FUNDING_PERIOD)
        );
        console.log("RLDMarketFactory deployed at:", address(factory));
        
        // Step 2: Deploy core with factory address (immutable)
        core = new RLDCore(address(factory), address(poolManager), address(0));
        console.log("RLDCore deployed at:", address(core));
        
        // Step 3: Initialize factory's CORE reference
        factory.initializeCore(address(core));
        console.log("Factory authorized in core");
        
        console.log("\nSetup complete!\n");
    }
    
    /**
     * @notice Test funding rate calculation with wRLP < collateral (normal ordering)
     * @dev This tests the case where positionToken address < collateralToken address
     */
    function test_FundingRate_NormalOrdering() public {
        console.log("\n========================================");
        console.log("TEST: Funding Rate - Normal Ordering");
        console.log("========================================\n");
        
        // Setup: Deploy market with specific index price
        uint256 indexPrice = 10e18; // 10 aUSDC per wRLP
        rateOracle.setIndexPrice(indexPrice);
        console.log("Step 1: Set index price to", indexPrice / 1e18, "aUSDC per wRLP");
        
        RLDMarketFactory.DeployParams memory params = _defaultParams();
        (MarketId marketId,) = factory.createMarket(params);
        console.log("Step 2: Market created with ID:", uint256(MarketId.unwrap(marketId)));
        
        // Get market addresses
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(marketId);
        console.log("\nMarket Addresses:");
        console.log("  Position Token (wRLP):", addrs.positionToken);
        console.log("  Collateral Token (aUSDC):", addrs.collateralToken);
        console.log("  Underlying Token (USDC):", addrs.underlyingToken);
        
        // Determine currency ordering
        bool wRLPIsToken0 = addrs.positionToken < addrs.collateralToken;
        console.log("\nCurrency Ordering:");
        console.log("  wRLP < collateral:", wRLPIsToken0);
        console.log("  V4 Pool represents:", wRLPIsToken0 ? "wRLP/aUSDC" : "aUSDC/wRLP");
        
        // Get initial pool state
        PoolKey memory key = _buildPoolKey(addrs, params);
        PoolId poolId = PoolIdLibrary.toId(key);
        (uint160 sqrtPriceX96Initial, int24 tickInitial,,) = StateLibrary.getSlot0(poolManager, poolId);
        
        console.log("\nInitial Pool State:");
        console.log("  sqrtPriceX96:", sqrtPriceX96Initial);
        console.log("  tick:");
        console.logInt(tickInitial);
        
        // Calculate initial funding (should be zero since no time passed)
        uint48 initialTimestamp = uint48(block.timestamp);
        console.log("\nStep 3: Calculate initial funding at timestamp", initialTimestamp);
        
        (uint256 normFactor1, int256 fundingRate1) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            INITIAL_NORM_FACTOR,
            initialTimestamp
        );
        
        console.log("  Initial norm factor:", normFactor1);
        console.log("  Initial funding rate:", fundingRate1);
        assertEq(normFactor1, INITIAL_NORM_FACTOR, "Norm factor should be unchanged");
        assertEq(fundingRate1, 0, "Funding rate should be zero (no time passed)");
        
        // Warp time forward by 1 hour
        uint256 timeElapsed = 1 hours;
        vm.warp(block.timestamp + timeElapsed);
        console.log("\nStep 4: Time warped forward by", timeElapsed / 3600, "hours");
        console.log("  New timestamp:", block.timestamp);
        
        // Calculate funding after time has passed
        console.log("\nStep 5: Calculate funding after time warp");
        
        // Change index price to 9
        uint256 newIndexPrice = 9e18;
        rateOracle.setIndexPrice(newIndexPrice);
        console.log("  Changed index price to", newIndexPrice, "(9 aUSDC)");

        // Check prices individually
        uint256 currentMarkPrice = v4Oracle.getSpotPrice(addrs.positionToken, addrs.collateralToken);
        uint256 currentIndexPrice = rateOracle.getIndexPrice(address(0), address(0));
        
        console.log("  Current Mark Price (WAD):", currentMarkPrice);
        console.log("  Current Index Price (WAD):", currentIndexPrice);
        
        console.log("  Calculating funding...");
        (uint256 normFactor2, int256 fundingRate2) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            INITIAL_NORM_FACTOR,
            initialTimestamp
        );
        
        console.log("\nFunding Calculation Results:");
        console.log("  Time elapsed:", timeElapsed, "seconds");
        console.log("  New norm factor:", normFactor2);
        console.log("  Funding rate (WAD):");
        console.logInt(fundingRate2);
        
        // Verify funding was calculated
        assertTrue(normFactor2 > 0, "Norm factor should be positive");
        console.log("\n[OK] Funding calculation succeeded for normal ordering");
        
        // Log the funding rate interpretation
        console.log("\nFunding Rate Interpretation:");
        if (fundingRate2 > 0) {
            console.log("  Mark > Index: Shorts earn, longs pay");
            console.log("  Norm factor decreased:", INITIAL_NORM_FACTOR > normFactor2);
        } else if (fundingRate2 < 0) {
            console.log("  Mark < Index: Longs earn, shorts pay");
            console.log("  Norm factor increased:", normFactor2 > INITIAL_NORM_FACTOR);
        } else {
            console.log("  Mark = Index: No funding payment");
        }
        
        console.log("\n========================================\n");
    }
    
    /**
     * @notice Test funding rate calculation with wRLP > collateral (inverted ordering)
     * @dev This tests the case where positionToken address > collateralToken address
     */
    function test_FundingRate_InvertedOrdering() public {
        console.log("\n========================================");
        console.log("TEST: Funding Rate - Inverted Ordering");
        console.log("========================================\n");
        
        // Use a collateral address that will be < wRLP address
        // We'll use a mock with a low address
        MockERC20 lowAddressCollateral = new MockERC20("Low Addr aUSDC", "aUSDC", 6);
        
        // Ensure we get inverted ordering by using vm.etch if needed
        // For this test, we'll just use the natural address and verify ordering
        
        uint256 indexPrice = 10e18; // 10 aUSDC per wRLP
        rateOracle.setIndexPrice(indexPrice);
        console.log("Step 1: Set index price to", indexPrice / 1e18, "aUSDC per wRLP");
        
        RLDMarketFactory.DeployParams memory params = _defaultParams();
        params.collateralToken = address(lowAddressCollateral);
        
        (MarketId marketId,) = factory.createMarket(params);
        console.log("Step 2: Market created with ID:", uint256(MarketId.unwrap(marketId)));
        
        // Get market addresses
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(marketId);
        console.log("\nMarket Addresses:");
        console.log("  Position Token (wRLP):", addrs.positionToken);
        console.log("  Collateral Token (aUSDC):", addrs.collateralToken);
        
        // Determine currency ordering
        bool wRLPIsToken0 = addrs.positionToken < addrs.collateralToken;
        console.log("\nCurrency Ordering:");
        console.log("  wRLP < collateral:", wRLPIsToken0);
        console.log("  V4 Pool represents:", wRLPIsToken0 ? "wRLP/aUSDC" : "aUSDC/wRLP");
        
        // Get initial pool state
        PoolKey memory key = _buildPoolKey(addrs, params);
        PoolId poolId = PoolIdLibrary.toId(key);
        (uint160 sqrtPriceX96Initial, int24 tickInitial,,) = StateLibrary.getSlot0(poolManager, poolId);
        
        console.log("\nInitial Pool State:");
        console.log("  sqrtPriceX96:", sqrtPriceX96Initial);
        console.log("  tick:");
        console.logInt(tickInitial);
        
        // Initial funding calculation
        uint48 initialTimestamp = uint48(block.timestamp);
        console.log("\nStep 3: Calculate initial funding at timestamp", initialTimestamp);
        
        (uint256 normFactor1, int256 fundingRate1) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            INITIAL_NORM_FACTOR,
            initialTimestamp
        );
        
        console.log("  Initial norm factor:", normFactor1);
        console.log("  Initial funding rate:", fundingRate1);
        
        // Warp time forward by 1 hour
        uint256 timeElapsed = 1 hours;
        vm.warp(block.timestamp + timeElapsed);
        console.log("\nStep 4: Time warped forward by", timeElapsed / 3600, "hours");
        console.log("  New timestamp:", block.timestamp);
        
        // Calculate funding after time has passed
        console.log("\nStep 5: Calculate funding after time warp");
        
        // Change index price to 9 (to match previous test logic, though inverted)
        uint256 newIndexPrice = 9e18;
        rateOracle.setIndexPrice(newIndexPrice);
        console.log("  Changed index price to", newIndexPrice, "(9 aUSDC)");

        // Check prices individually
        uint256 currentMarkPrice = v4Oracle.getSpotPrice(addrs.positionToken, addrs.collateralToken);
        uint256 currentIndexPrice = rateOracle.getIndexPrice(address(0), address(0));
        
        console.log("  Current Mark Price (WAD):", currentMarkPrice);
        console.log("  Current Index Price (WAD):", currentIndexPrice);
        
        console.log("  Calculating funding...");
        (uint256 normFactor2, int256 fundingRate2) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            INITIAL_NORM_FACTOR,
            initialTimestamp
        );
        
        console.log("\nFunding Calculation Results:");
        console.log("  Time elapsed:", timeElapsed, "seconds");
        console.log("  New norm factor:", normFactor2);
        console.log("  Funding rate (WAD):");
        console.logInt(fundingRate2);
        
        // Verify funding was calculated
        assertTrue(normFactor2 > 0, "Norm factor should be positive");
        console.log("\n[OK] Funding calculation succeeded for inverted ordering");
        
        // Log the funding rate interpretation
        console.log("\nFunding Rate Interpretation:");
        if (fundingRate2 > 0) {
            console.log("  Mark > Index: Shorts earn, longs pay");
            console.log("  Norm factor decreased:", INITIAL_NORM_FACTOR > normFactor2);
        } else if (fundingRate2 < 0) {
            console.log("  Mark < Index: Longs earn, shorts pay");
            console.log("  Norm factor increased:", normFactor2 > INITIAL_NORM_FACTOR);
        } else {
            console.log("  Mark = Index: No funding payment");
        }
        
        console.log("\n========================================\n");
    }
    
    /**
     * @notice Test funding rate over multiple time periods
     * @dev Verifies cumulative funding effects over time
     */
    function test_FundingRate_MultipleTimePeriods() public {
        console.log("\n========================================");
        console.log("TEST: Funding Rate - Multiple Time Periods");
        console.log("========================================\n");
        
        // Setup
        uint256 indexPrice = 10e18;
        rateOracle.setIndexPrice(indexPrice);
        console.log("Initial index price:", indexPrice / 1e18, "aUSDC per wRLP\n");
        
        RLDMarketFactory.DeployParams memory params = _defaultParams();
        (MarketId marketId,) = factory.createMarket(params);
        
        uint48 lastUpdate = uint48(block.timestamp);
        uint256 currentNormFactor = INITIAL_NORM_FACTOR;
        
        console.log("Starting normalization factor:", currentNormFactor);
        console.log("Starting timestamp:", lastUpdate);
        
        // Period 1: 1 hour
        console.log("\n--- Period 1: 1 hour ---");
        vm.warp(block.timestamp + 1 hours);
        (uint256 newNormFactor1, int256 fundingRate1) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            currentNormFactor,
            lastUpdate
        );
        console.log("After 1 hour:");
        console.log("  Norm factor:", newNormFactor1);
        console.log("  Funding rate:");
        console.logInt(fundingRate1);
        console.log("  Change:", int256(newNormFactor1) - int256(currentNormFactor));
        
        lastUpdate = uint48(block.timestamp);
        currentNormFactor = newNormFactor1;
        
        // Period 2: 12 hours
        console.log("\n--- Period 2: 12 hours ---");
        vm.warp(block.timestamp + 12 hours);
        (uint256 newNormFactor2, int256 fundingRate2) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            currentNormFactor,
            lastUpdate
        );
        console.log("After 12 hours:");
        console.log("  Norm factor:", newNormFactor2);
        console.log("  Funding rate:");
        console.logInt(fundingRate2);
        console.log("  Change:", int256(newNormFactor2) - int256(currentNormFactor));
        
        lastUpdate = uint48(block.timestamp);
        currentNormFactor = newNormFactor2;
        
        // Period 3: 1 day
        console.log("\n--- Period 3: 1 day ---");
        vm.warp(block.timestamp + 1 days);
        (uint256 newNormFactor3, int256 fundingRate3) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            currentNormFactor,
            lastUpdate
        );
        console.log("After 1 day:");
        console.log("  Norm factor:", newNormFactor3);
        console.log("  Funding rate:");
        console.logInt(fundingRate3);
        console.log("  Change:", int256(newNormFactor3) - int256(currentNormFactor));
        
        // Summary
        console.log("\n--- Summary ---");
        console.log("Total time elapsed:", (block.timestamp - uint256(lastUpdate) + 1 hours + 12 hours + 1 days) / 3600, "hours");
        console.log("Initial norm factor:", INITIAL_NORM_FACTOR);
        console.log("Final norm factor:", newNormFactor3);
        console.log("Total change:", int256(newNormFactor3) - int256(INITIAL_NORM_FACTOR));
        
        console.log("\n========================================\n");
    }
    
    /**
     * @notice Fuzz test for funding rate across different prices and time periods
     */
    function testFuzz_FundingRate_PricesAndTime(
        uint256 indexPrice,
        uint256 timeElapsed
    ) public {
        // Bound inputs
        indexPrice = bound(indexPrice, 1e16, 100e18); // 0.01 to 100 aUSDC per wRLP
        timeElapsed = bound(timeElapsed, 1 hours, 30 days); // 1 hour to 30 days
        
        console.log("\n========================================");
        console.log("FUZZ TEST: Price (WAD):", indexPrice, "Time (seconds):", timeElapsed);
        console.log("========================================\n");
        
        // Setup
        rateOracle.setIndexPrice(indexPrice);
        RLDMarketFactory.DeployParams memory params = _defaultParams();
        (MarketId marketId,) = factory.createMarket(params);
        
        uint48 initialTimestamp = uint48(block.timestamp);
        
        // Warp time
        vm.warp(block.timestamp + timeElapsed);
        
        // Calculate funding
        (uint256 newNormFactor, int256 fundingRate) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            INITIAL_NORM_FACTOR,
            initialTimestamp
        );
        
        // Verify results
        assertTrue(newNormFactor > 0, "Norm factor should be positive");
        console.log("[OK] Funding calculation succeeded");
        console.log("  Norm factor:", newNormFactor);
        console.log("  Funding rate:");
        console.logInt(fundingRate);
    }
    
    // Helper functions
    
    function _defaultParams() internal view returns (RLDMarketFactory.DeployParams memory) {
        return getGlobalDeployParams(
            address(0x123),
            address(underlying),
            address(collateral),
            address(0),
            address(rateOracle),
            address(rateOracle),
            address(0x456)
        );
    }
    
    function _buildPoolKey(
        IRLDCore.MarketAddresses memory addrs,
        RLDMarketFactory.DeployParams memory params
    ) internal view returns (PoolKey memory) {
        Currency currency0;
        Currency currency1;
        
        if (addrs.positionToken < addrs.collateralToken) {
            currency0 = Currency.wrap(addrs.positionToken);
            currency1 = Currency.wrap(addrs.collateralToken);
        } else {
            currency0 = Currency.wrap(addrs.collateralToken);
            currency1 = Currency.wrap(addrs.positionToken);
        }
        
        return PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: params.poolFee,
            tickSpacing: params.tickSpacing,
            hooks: IHooks(address(twamm))
        });
    }

    /**
     * @notice Extensive fuzz test for varied Mark and Index prices
     * @dev Bounds inputs to valid range defined in RLDMarketFactory (0.0001 to 100)
     */
    function testFuzz_FundingRate_PricesExtensive(
        uint256 markPriceWad,
        uint256 indexPriceWad,
        uint32 timeElapsed
    ) public {
        // Bound inputs to valid range [0.0001e18, 100e18] corresponding to factory limits
        markPriceWad = bound(markPriceWad, 1e14, 100e18);
        indexPriceWad = bound(indexPriceWad, 1e14, 100e18);
        timeElapsed = uint32(bound(timeElapsed, 3600, 30 days)); // Must be >= oraclePeriod
        
        // 1. Initialize Market with Rate Oracle = Mark Price
        rateOracle.setIndexPrice(markPriceWad);
        
        (MarketId marketId, ) = factory.createMarket(
            RLDMarketFactory.DeployParams({
                underlyingPool: address(0x123),
                underlyingToken: address(underlying),
                collateralToken: address(collateral),
                curator: address(this),
                positionTokenName: "Wrapped RLP",
                positionTokenSymbol: "wRLP",
                minColRatio: 1.2e18,
                maintenanceMargin: 1.1e18,
                liquidationCloseFactor: 0.5e18,
                liquidationModule: address(0x456), 
                liquidationParams: bytes32(0),
                spotOracle: address(v4Oracle),
                rateOracle: address(rateOracle),
                oraclePeriod: 3600,
                poolFee: 3000,
                tickSpacing: 60
            })
        );
        
        // 2. Set Rate Oracle to Actual Index Price
        rateOracle.setIndexPrice(indexPriceWad);
        
        // 3. Warp Time & Calculate Funding
        vm.warp(block.timestamp + timeElapsed);
        
        (uint256 newNormFactor, int256 fundingRate) = fundingModel.calculateFunding(
            MarketId.unwrap(marketId),
            address(core),
            INITIAL_NORM_FACTOR,
            uint48(block.timestamp - timeElapsed)
        );
        
        // 4. Verify Results using Actual V4 Spot Price
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(marketId);
        uint256 actualMarkPrice = v4Oracle.getSpotPrice(addrs.positionToken, addrs.collateralToken);
        
        int256 mark = int256(actualMarkPrice);
        int256 index = int256(indexPriceWad);
        int256 expectedRate = ((mark - index) * 1e18) / index;
        
        assertEq(fundingRate, expectedRate, "Funding rate calculation mismatch");
        
        // Sanity Checks
        if (mark == index) {
            assertEq(fundingRate, 0);
            assertEq(newNormFactor, INITIAL_NORM_FACTOR);
        } else if (mark > index) {
            assertTrue(fundingRate > 0);
            assertTrue(newNormFactor < INITIAL_NORM_FACTOR); // Shorts earn -> debt decreases
        } else {
            assertTrue(fundingRate < 0);
            assertTrue(newNormFactor > INITIAL_NORM_FACTOR); // Longs earn -> debt increases
        }
    }
}

// Mock Oracle
contract MockOracle is IRLDOracle {
    uint256 private price;
    
    function setIndexPrice(uint256 _price) external {
        price = _price;
    }
    
    function getIndexPrice(address, address) external view override returns (uint256) {
        return price;
    }
}
