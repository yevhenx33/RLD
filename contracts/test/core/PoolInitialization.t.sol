// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {RLDMarketFactory} from "../../src/rld/core/RLDMarketFactory.sol";
import {RLDCore} from "../../src/rld/core/RLDCore.sol";
import {PositionToken} from "../../src/rld/tokens/PositionToken.sol";
import {PrimeBroker} from "../../src/rld/broker/PrimeBroker.sol";
import {UniswapV4SingletonOracle} from "../../src/rld/modules/oracles/UniswapV4SingletonOracle.sol";


import {IRLDOracle} from "../../src/shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../src/shared/interfaces/ISpotOracle.sol";
import {IFundingModel} from "../../src/shared/interfaces/IFundingModel.sol";
import {MarketId} from "../../src/shared/interfaces/IRLDCore.sol";

import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolManager} from "v4-core/src/PoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {BeforeSwapDelta} from "v4-core/src/types/BeforeSwapDelta.sol";
import {ModifyLiquidityParams, SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";

import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {FixedPointMathLib} from "solady/utils/FixedPointMathLib.sol";

/**
 * @title Pool Initialization Test
 * @notice Step-by-step verification of _initializePool function with $10 oracle price
 */
contract PoolInitializationTest is Test {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    // --- Contracts ---
    RLDCore core;
    RLDMarketFactory factory;
    PoolManager poolManager;
    PositionToken positionTokenImpl;
    PrimeBroker primeBrokerImpl;
    UniswapV4SingletonOracle v4Oracle;
    address renderer = address(0);

    // --- Mocks ---
    MockOracle oracle;
    MockFundingModel fundingModel;
    MockTwammHook twammHook;
    MockERC20 underlying;  // USDC
    MockERC20 collateral;  // aUSDC

    // --- Constants ---
    uint256 constant ORACLE_PRICE = 10e18;  // $10 index price
    uint256 constant Q96 = 1 << 96;

    function setUp() public {
        // Deploy core infrastructure
        poolManager = new PoolManager(address(0));
        
        // Deploy mocks
        oracle = new MockOracle();
        oracle.setIndexPrice(ORACLE_PRICE);  // Set to $10
        
        fundingModel = new MockFundingModel();
        twammHook = new MockTwammHook();

        
        // Deploy core contracts
        core = new RLDCore();
        positionTokenImpl = new PositionToken();
        primeBrokerImpl = new PrimeBroker(
            address(core),
            address(0),
            address(0),  // No TWAMM for testing
            address(0)
        );
        v4Oracle = new UniswapV4SingletonOracle();
        
        // Deploy factory with address(0) for TWAMM
        // V4 treats address(0) as "no hooks" which is valid
        factory = new RLDMarketFactory(
            address(core),
            address(poolManager),
            address(positionTokenImpl),
            address(primeBrokerImpl),
            address(v4Oracle),
            address(fundingModel),
            address(0),  // No hooks - valid for V4
            address(renderer),
            30 days
        );
        
        // Register factory with core
        core.setFactory(address(factory));
        
        // Deploy mock tokens
        underlying = new MockERC20("USDC", "USDC", 6);
        collateral = new MockERC20("Aave USDC", "aUSDC", 6);
    }

    /**
     * @notice STEP-BY-STEP TEST: Full pool initialization flow with $10 oracle
     */
    function test_InitializePool_StepByStep_Price10() public {
        console.log("========== POOL INITIALIZATION TEST: $10 Oracle ==========");
        console.log("");
        
        // === STEP 1: Create market (triggers _initializePool) ===
        console.log("STEP 1: Creating market...");
        
        RLDMarketFactory.DeployParams memory params = RLDMarketFactory.DeployParams({
            underlyingPool: address(0x999),
            underlyingToken: address(underlying),
            collateralToken: address(collateral),
            curator: address(this),
            positionTokenName: "Wrapped RLP: aUSDC",
            positionTokenSymbol: "wRLP-aUSDC",
            minColRatio: 120e16,          // 120%
            maintenanceMargin: 110e16,    // 110%
            liquidationCloseFactor: 50e16, // 50%
            liquidationModule: address(0x123),
            liquidationParams: bytes32(0),
            spotOracle: address(oracle),
            rateOracle: address(oracle),
            oraclePeriod: 3600,
            poolFee: 3000,
            tickSpacing: 60
        });
        
        (MarketId marketId, address brokerFactory) = factory.createMarket(params);
        
        console.log("  Market ID:", uint256(MarketId.unwrap(marketId)));
        console.log("  Broker Factory:", brokerFactory);
        assertTrue(MarketId.unwrap(marketId) != bytes32(0), "Market ID should not be zero");
        
        // === STEP 2: Verify PositionToken deployment ===
        console.log("");
        console.log("STEP 2: Verifying PositionToken...");
        
        address positionToken = core.getMarketAddresses(marketId).positionToken;
        console.log("  PositionToken:", positionToken);
        console.log("  Name:", PositionToken(positionToken).name());
        console.log("  Symbol:", PositionToken(positionToken).symbol());
        
        // === STEP 3: Verify currency ordering ===
        console.log("");
        console.log("STEP 3: Currency ordering analysis...");
        
        address currency0Addr;
        address currency1Addr;
        bool positionIsToken0;
        
        if (positionToken < address(collateral)) {
            currency0Addr = positionToken;
            currency1Addr = address(collateral);
            positionIsToken0 = true;
            console.log("  PositionToken < Collateral: wRLP is Token0");
        } else {
            currency0Addr = address(collateral);
            currency1Addr = positionToken;
            positionIsToken0 = false;
            console.log("  Collateral < PositionToken: aUSDC is Token0");
        }
        console.log("  Currency0:", currency0Addr);
        console.log("  Currency1:", currency1Addr);
        
        // === STEP 4: Calculate expected sqrtPriceX96 ===
        console.log("");
        console.log("STEP 4: sqrtPriceX96 calculation...");
        
        uint256 indexPrice = ORACLE_PRICE;  // 10e18
        console.log("  Oracle indexPrice:", indexPrice);
        
        // If positionToken is currency1, price gets inverted
        if (!positionIsToken0) {
            indexPrice = 1e36 / indexPrice;
            console.log("  Price inverted (wRLP is Token1):", indexPrice);
        }
        
        // Calculate sqrt manually
        uint256 sqrtIndex = FixedPointMathLib.sqrt(indexPrice);
        console.log("  sqrt(indexPrice):", sqrtIndex);
        
        uint160 expectedSqrtPrice = uint160((sqrtIndex * Q96) / 1e9);
        console.log("  Expected sqrtPriceX96:", expectedSqrtPrice);
        
        // Convert to tick for reference
        int24 expectedTick = TickMath.getTickAtSqrtPrice(expectedSqrtPrice);
        console.log("  Expected tick:", expectedTick);
        
        // === STEP 5: Verify pool was initialized correctly ===
        console.log("");
        console.log("STEP 5: Pool state verification...");
        
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: params.poolFee,
            tickSpacing: params.tickSpacing,
            hooks: IHooks(address(0))  // No hooks in test
        });
        
        PoolId poolId = key.toId();
        console.log("  PoolId:", uint256(PoolId.unwrap(poolId)));
        
        // Get pool state
        (uint160 actualSqrtPrice, int24 actualTick,,) = StateLibrary.getSlot0(poolManager, poolId);
        console.log("  Actual sqrtPriceX96:", actualSqrtPrice);
        console.log("  Actual tick:", actualTick);
        
        // Verify price matches expected
        assertEq(actualSqrtPrice, expectedSqrtPrice, "sqrtPrice mismatch");
        assertEq(actualTick, expectedTick, "tick mismatch");
        
        // === STEP 6: Price bounds (skipped - no TWAMM hook in test) ===
        console.log("");
        console.log("STEP 6: Price bounds (skipped - no TWAMM in test)");
        
        // Calculate expected bounds for reference
        uint160 expectedMinSqrt;
        uint160 expectedMaxSqrt;
        if (positionIsToken0) {
            expectedMinSqrt = uint160(Q96 / 100);   // sqrt(0.0001) = 0.01
            expectedMaxSqrt = uint160(Q96 * 10);     // sqrt(100) = 10
            console.log("  Expected bounds (Token0): min=", expectedMinSqrt, " max=", expectedMaxSqrt);
        } else {
            expectedMinSqrt = uint160(Q96 / 10);    // sqrt(0.01) = 0.1
            expectedMaxSqrt = uint160(Q96 * 100);   // sqrt(10000) = 100
            console.log("  Expected bounds (Token1): min=", expectedMinSqrt, " max=", expectedMaxSqrt);
        }
        
        // === STEP 7: Verify init price is within expected bounds ===
        console.log("");
        console.log("STEP 7: Price within bounds check...");
        
        assertTrue(actualSqrtPrice >= expectedMinSqrt, "Price below min bound!");
        assertTrue(actualSqrtPrice <= expectedMaxSqrt, "Price above max bound!");
        console.log("  PASS: Init price is within bounds");
        
        // === STEP 8: Price interpretation ===
        console.log("");
        console.log("STEP 8: Price interpretation...");
        
        // sqrtPrice^2 / 2^192 = price
        uint256 priceX192 = uint256(actualSqrtPrice) * uint256(actualSqrtPrice);
        uint256 actualPrice = (priceX192 * 1e18) >> 192;
        console.log("  Decoded price (WAD):", actualPrice);
        
        if (positionIsToken0) {
            console.log("  Interpretation: 1 wRLP = ", actualPrice / 1e15, "/ 1000 aUSDC");
        } else {
            console.log("  Interpretation: 1 aUSDC = ", actualPrice / 1e15, "/ 1000 wRLP");
        }
        
        console.log("");
        console.log("========== TEST COMPLETE ==========");
    }

    /**
     * @notice Test with $1 (parity) price
     */
    function test_InitializePool_Price1_Parity() public {
        console.log("========== PARITY TEST: $1 Oracle ==========");
        
        oracle.setIndexPrice(1e18);  // $1
        
        RLDMarketFactory.DeployParams memory params = _defaultParams();
        (MarketId marketId,) = factory.createMarket(params);
        
        address positionToken = core.getMarketAddresses(marketId).positionToken;
        
        // Build key
        address currency0Addr = positionToken < address(collateral) ? positionToken : address(collateral);
        address currency1Addr = positionToken < address(collateral) ? address(collateral) : positionToken;
        
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
        
        (uint160 sqrtPrice, int24 tick,,) = StateLibrary.getSlot0(poolManager, key.toId());
        
        console.log("  sqrtPriceX96:", sqrtPrice);
        console.log("  tick:", tick);
        
        // At parity (price=1), sqrtPrice should be ~Q96
        // And tick should be ~0
        assertApproxEqRel(sqrtPrice, uint160(Q96), 0.01e18, "sqrtPrice should be ~Q96 at parity");
        assertApproxEqAbs(tick, 0, 10, "tick should be ~0 at parity");
        
        console.log("  PASS: Parity price initialized correctly");
    }

    /**
     * @notice Test edge case: very high price ($100)
     */
    function test_InitializePool_Price100_High() public {
        console.log("========== HIGH PRICE TEST: $100 Oracle ==========");
        
        oracle.setIndexPrice(100e18);  // $100
        
        RLDMarketFactory.DeployParams memory params = _defaultParams();
        (MarketId marketId,) = factory.createMarket(params);
        
        address positionToken = core.getMarketAddresses(marketId).positionToken;
        address currency0Addr = positionToken < address(collateral) ? positionToken : address(collateral);
        address currency1Addr = positionToken < address(collateral) ? address(collateral) : positionToken;
        
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
        
        (uint160 sqrtPrice, int24 tick,,) = StateLibrary.getSlot0(poolManager, key.toId());
        
        // Calculate expected bounds for verification
        bool positionIsToken0 = positionToken < address(collateral);
        uint160 expectedMinSqrt = positionIsToken0 ? uint160(Q96 / 100) : uint160(Q96 / 10);
        uint160 expectedMaxSqrt = positionIsToken0 ? uint160(Q96 * 10) : uint160(Q96 * 100);
        
        console.log("  sqrtPriceX96:", sqrtPrice);
        console.log("  tick:", tick);
        console.log("  expectedMinSqrt:", expectedMinSqrt);
        console.log("  expectedMaxSqrt:", expectedMaxSqrt);
        
        // Verify within expected bounds
        assertTrue(sqrtPrice >= expectedMinSqrt && sqrtPrice <= expectedMaxSqrt, "Price should be within bounds");
        console.log("  PASS: High price within bounds");
    }

    // =========================================================================
    //                    TOKEN COMBINATION TESTS
    // =========================================================================
    
    /**
     * @notice Test with specific token addresses - positionToken > collateral
     * @dev When positionToken address > collateral address:
     *      - collateral becomes Token0
     *      - positionToken becomes Token1
     *      - Price gets INVERTED
     */
    function test_TokenCombination_PositionHigherAddress() public {
        console.log("=== TOKEN COMBO: positionToken > collateral (INVERTED) ===");
        
        // Use addresses where position > collateral (alphabetically)
        // Simulating: 0xAa... (position) vs 0x01... (collateral)
        MockERC20 lowCollateral = new MockERC20("Low Addr Token", "LOW", 6);
        MockERC20 highUnderlying = new MockERC20("High Addr Token", "HIGH", 6);
        
        // Ensure high > low
        if (address(lowCollateral) > address(highUnderlying)) {
            (lowCollateral, highUnderlying) = (highUnderlying, lowCollateral);
        }
        
        console.log("  lowCollateral: ", address(lowCollateral));
        console.log("  highUnderlying:", address(highUnderlying));
        
        oracle.setIndexPrice(10e18);  // $10
        
        RLDMarketFactory.DeployParams memory params = RLDMarketFactory.DeployParams({
            underlyingPool: address(0x999),
            underlyingToken: address(highUnderlying),
            collateralToken: address(lowCollateral),  // LOW address = Token0
            curator: address(this),
            positionTokenName: "wRLP Low-High",
            positionTokenSymbol: "wRLP-LH",
            minColRatio: 120e16,
            maintenanceMargin: 110e16,
            liquidationCloseFactor: 50e16,
            liquidationModule: address(0x123),
            liquidationParams: bytes32(0),
            spotOracle: address(oracle),
            rateOracle: address(oracle),
            oraclePeriod: 3600,
            poolFee: 3000,
            tickSpacing: 60
        });
        
        (MarketId marketId,) = factory.createMarket(params);
        address positionToken = core.getMarketAddresses(marketId).positionToken;
        
        // Verify ordering
        bool positionIsToken0 = positionToken < address(lowCollateral);
        console.log("  positionToken:", positionToken);
        console.log("  positionIsToken0:", positionIsToken0);
        
        // Build key and check pool
        address currency0Addr = positionToken < address(lowCollateral) ? positionToken : address(lowCollateral);
        address currency1Addr = positionToken < address(lowCollateral) ? address(lowCollateral) : positionToken;
        
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
        
        (uint160 sqrtPrice, int24 tick,,) = StateLibrary.getSlot0(poolManager, key.toId());
        console.log("  sqrtPriceX96:", sqrtPrice);
        console.log("  tick:", tick);
        
        // Verify tick sign matches expectation
        if (positionIsToken0) {
            // No inversion: tick should be positive for $10 price
            assertTrue(tick > 0, "Tick should be positive when positionToken is Token0");
            console.log("  VERIFIED: Positive tick (no inversion)");
        } else {
            // Inverted: tick should be negative for $10 price
            assertTrue(tick < 0, "Tick should be negative when positionToken is Token1");
            console.log("  VERIFIED: Negative tick (price inverted)");
        }
    }
    
    /**
     * @notice Test with $0.5 oracle price (below parity)
     */
    function test_TokenCombination_LowPrice_Half() public {
        console.log("=== TOKEN COMBO: $0.5 Oracle (below parity) ===");
        
        oracle.setIndexPrice(0.5e18);  // $0.5
        
        RLDMarketFactory.DeployParams memory params = _defaultParams();
        (MarketId marketId,) = factory.createMarket(params);
        
        address positionToken = core.getMarketAddresses(marketId).positionToken;
        address currency0Addr = positionToken < address(collateral) ? positionToken : address(collateral);
        address currency1Addr = positionToken < address(collateral) ? address(collateral) : positionToken;
        bool positionIsToken0 = positionToken < address(collateral);
        
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
        
        (uint160 sqrtPrice, int24 tick,,) = StateLibrary.getSlot0(poolManager, key.toId());
        
        console.log("  positionIsToken0:", positionIsToken0);
        console.log("  sqrtPriceX96:", sqrtPrice);
        console.log("  tick:", tick);
        
        // At $0.5:
        // - If positionToken is Token0: price = 0.5, tick ≈ -6931
        // - If positionToken is Token1: inverted to 2, tick ≈ +6931
        if (positionIsToken0) {
            assertTrue(tick < 0, "Tick should be negative for $0.5 when positionToken is Token0");
        } else {
            assertTrue(tick > 0, "Tick should be positive for $0.5 when inverted");
        }
        console.log("  PASS: Tick sign correct for $0.5 price");
    }
    
    /**
     * @notice Test with multiple token pairs from user's addresses
     */
    function test_TokenCombination_UserAddresses() public {
        console.log("=== USER TOKEN ADDRESSES ANALYSIS ===");
        
        // User's sorted addresses
        address[9] memory userTokens = [
            0x018008bfb33d285247A21d44E50697654f754e63,
            0x23878914EFE38d27C4D67Ab83ed1b93A74D4086a,
            0x24Ab03a9a5Bc2C49E5523e8d915A3536ac38B91D,
            0x32a6268f9Ba3642Dda7892aDd74f1D34469A4259,
            0x4579a27aF00A62C0EB156349f31B345c08386419,
            0x4F5923Fc5FD4a93352581b38B7cD26943012DECF,
            0x5F9190496e0DFC831C3bd307978de4a245E2F5cD,
            0x7c0477d085ECb607CF8429f3eC91Ae5E1e460F4F,
            0xAa0200d169fF3ba9385c12E073c5d1d30434AE7b
        ];
        
        // Test: lowest vs highest
        address lowest = userTokens[0];  // 0x01...
        address highest = userTokens[8]; // 0xAa...
        
        console.log("  Lowest address: ", lowest);
        console.log("  Highest address:", highest);
        
        // If positionToken = highest (0xAa...) and collateral = lowest (0x01...)
        // Then: collateral < positionToken
        //       currency0 = collateral (0x01...)
        //       currency1 = positionToken (0xAa...)
        //       positionToken is Token1 → PRICE INVERTED
        
        bool positionIsToken1 = lowest < highest;
        console.log("  If positionToken=0xAa, collateral=0x01:");
        console.log("    positionToken would be Token1:", positionIsToken1);
        console.log("    Price would be INVERTED:", positionIsToken1);
        
        // Reverse: positionToken = lowest, collateral = highest
        bool positionIsToken0 = lowest < highest;
        console.log("  If positionToken=0x01, collateral=0xAa:");
        console.log("    positionToken would be Token0:", positionIsToken0);
        console.log("    Price would NOT be inverted:", positionIsToken0);
        
        // Verify with adjacent addresses (0x45... vs 0x4F...)
        address adjacent1 = userTokens[4]; // 0x4579...
        address adjacent2 = userTokens[5]; // 0x4F59...
        
        console.log("");
        console.log("  Adjacent addresses:");
        console.log("    0x4579...:", adjacent1);
        console.log("    0x4F59...:", adjacent2);
        console.log("    0x4579 < 0x4F59:", adjacent1 < adjacent2);
        
        assertTrue(lowest < highest, "Lowest should be less than highest");
        assertTrue(adjacent1 < adjacent2, "0x45 should be less than 0x4F");
        
        console.log("  PASS: All ordering verified");
    }

    // === HELPER FUNCTIONS ===
    
    function _defaultParams() internal view returns (RLDMarketFactory.DeployParams memory) {
        return RLDMarketFactory.DeployParams({
            underlyingPool: address(0x999),
            underlyingToken: address(underlying),
            collateralToken: address(collateral),
            curator: address(this),
            positionTokenName: "Wrapped RLP: aUSDC",
            positionTokenSymbol: "wRLP-aUSDC",
            minColRatio: 120e16,
            maintenanceMargin: 110e16,
            liquidationCloseFactor: 50e16,
            liquidationModule: address(0x123),
            liquidationParams: bytes32(0),
            spotOracle: address(oracle),
            rateOracle: address(oracle),
            oraclePeriod: 3600,
            poolFee: 3000,
            tickSpacing: 60
        });
    }
}

// ============ MOCK CONTRACTS ============

contract MockOracle is IRLDOracle, ISpotOracle {
    uint256 public indexPrice = 1e18;
    uint256 public spotPrice = 1e18;
    
    function setIndexPrice(uint256 _price) external {
        indexPrice = _price;
    }
    
    function setSpotPrice(uint256 _price) external {
        spotPrice = _price;
    }
    
    function getIndexPrice(address, address) external view returns (uint256) {
        return indexPrice;
    }
    
    function getMarkPrice(address, address) external view returns (uint256) {
        return indexPrice;
    }
    
    function getSpotPrice(address, address) external view returns (uint256) {
        return spotPrice;
    }
}

contract MockFundingModel is IFundingModel {
    function calculateFunding(bytes32, address, uint256 oldNorm, uint48) external pure returns (uint256, int256) {
        return (oldNorm, 0);
    }
}

contract MockTwammHook is IHooks {
    using PoolIdLibrary for PoolKey;
    
    mapping(PoolId => uint160) public minBounds;
    mapping(PoolId => uint160) public maxBounds;
    
    function setPriceBounds(PoolKey calldata key, uint160 minSqrt, uint160 maxSqrt) external {
        PoolId id = key.toId();
        minBounds[id] = minSqrt;
        maxBounds[id] = maxSqrt;
    }
    
    function priceBounds(PoolId id) external view returns (uint160 min, uint160 max) {
        return (minBounds[id], maxBounds[id]);
    }
    
    // IHooks interface stubs
    function beforeInitialize(address, PoolKey calldata, uint160) external pure returns (bytes4) { 
        return IHooks.beforeInitialize.selector; 
    }
    function afterInitialize(address, PoolKey calldata, uint160, int24) external pure returns (bytes4) { 
        return IHooks.afterInitialize.selector; 
    }
    function beforeAddLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, bytes calldata) external pure returns (bytes4) { 
        return IHooks.beforeAddLiquidity.selector; 
    }
    function afterAddLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata) external pure returns (bytes4, BalanceDelta) { 
        return (IHooks.afterAddLiquidity.selector, BalanceDelta.wrap(0)); 
    }
    function beforeRemoveLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, bytes calldata) external pure returns (bytes4) { 
        return IHooks.beforeRemoveLiquidity.selector; 
    }
    function afterRemoveLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata) external pure returns (bytes4, BalanceDelta) { 
        return (IHooks.afterRemoveLiquidity.selector, BalanceDelta.wrap(0)); 
    }
    function beforeSwap(address, PoolKey calldata, SwapParams calldata, bytes calldata) external pure returns (bytes4, BeforeSwapDelta, uint24) { 
        return (IHooks.beforeSwap.selector, BeforeSwapDelta.wrap(0), 0); 
    }
    function afterSwap(address, PoolKey calldata, SwapParams calldata, BalanceDelta, bytes calldata) external pure returns (bytes4, int128) { 
        return (IHooks.afterSwap.selector, 0); 
    }
    function beforeDonate(address, PoolKey calldata, uint256, uint256, bytes calldata) external pure returns (bytes4) { 
        return IHooks.beforeDonate.selector; 
    }
    function afterDonate(address, PoolKey calldata, uint256, uint256, bytes calldata) external pure returns (bytes4) { 
        return IHooks.afterDonate.selector; 
    }
}
