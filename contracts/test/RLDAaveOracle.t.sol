// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/modules/oracles/RLDAaveOracle.sol";

contract RLDAaveOracleTest is Test {
    RLDAaveOracle oracle;

    // Aave Pool on Mainnet
    address constant POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;

    uint256 constant RATE_CAP = 1e27; // 100% APY
    uint256 constant K_SCALAR = 100;
    uint256 constant MIN_PRICE = 1e14; // $0.0001 or 0.01 cents

    function setUp() public {
        oracle = new RLDAaveOracle();
    }

    /*//////////////////////////////////////////////////////////////
                                HELPERS
    //////////////////////////////////////////////////////////////*/

    // Mocks the Aave V3 Pool return data for USDC
    function _mockPoolRate(uint128 variableBorrowRate) internal {
        // We only care about currentVariableBorrowRate (index 4 in struct)
        // ReserveData is a large struct, we need to return it fully packed
        IAavePool.ReserveData memory data;
        data.currentVariableBorrowRate = variableBorrowRate;

        // Mock the call
        vm.mockCall(
            POOL,
            abi.encodeWithSelector(IAavePool.getReserveData.selector, USDC),
            abi.encode(data)
        );
    }

    /*//////////////////////////////////////////////////////////////
                            INTEGRATION TESTS
    //////////////////////////////////////////////////////////////*/

    // Uses actual Mainnet Fork to verify address correctness & integration
    function testFork_RealTimeData() public {
        string memory rpcUrl = vm.envString("MAINNET_RPC_URL");
        vm.createSelectFork(rpcUrl);
        // addresses are hardcoded in contract, so new deployment works on fork
        RLDAaveOracle realOracle = new RLDAaveOracle();

        uint256 price = realOracle.getIndexPrice(POOL, USDC);

        console.log("--- RLD Oracle Live Data ---");
        console.log("Spot Index Price (WAD):", price);

        // Basic sanity checks on live data
        assertTrue(price >= MIN_PRICE, "Price below floor");
        assertTrue(price <= 100 * 1e18, "Price above cap");
    }

    function testFork_USDT_RealTimeData() public {
        string memory rpcUrl = vm.envString("MAINNET_RPC_URL");
        vm.createSelectFork(rpcUrl);
        
        address USDT = 0xdAC17F958D2ee523a2206206994597C13D831ec7;
        RLDAaveOracle usdtOracle = new RLDAaveOracle();

        uint256 price = usdtOracle.getIndexPrice(POOL, USDT);

        // Fetch raw data for debugging/verification
        IAavePool.ReserveData memory data = IAavePool(POOL).getReserveData(USDT);
        uint256 rawRate = data.currentVariableBorrowRate;

        console.log("--- RLD USDT Oracle Live Data ---");
        console.log("Raw Aave Rate (RAY):", rawRate);
        console.log("Spot Index Price (WAD):", price);

        // Basic sanity checks on live data
        assertTrue(price >= MIN_PRICE, "Price below floor");
        assertTrue(price <= 100 * 1e18, "Price above cap");
    }

    /*//////////////////////////////////////////////////////////////
                            UNIT / MOCK TESTS
    //////////////////////////////////////////////////////////////*/

    function test_SpecificRates() public {
        console.log("\n--- Unit Test: Specific Rates ---");

        // 1. Nominal Case: 5% Rate
        // 5% = 0.05 * 1e27 = 5e25
        uint128 rate = 5e25; 
        _mockPoolRate(rate);

        uint256 price = oracle.getIndexPrice(POOL, USDC);
        console.log("Scenario 1: 5% APY");
        console.log("Input Rate (RAY):", rate);
        console.log("Output Price (WAD):", price);
        console.log("Expected Price (WAD): 5000000000000000000 (5.00 ETH/USD approx logic equivalent)");

        // Expected: (5e25 * 100) / 1e9 = 500e25 / 1e9 = 500e16 = 5e18 ($5.00)
        assertEq(price, 5e18, "5% rate should be $5.00");

        // 2. High Case: 50% Rate
        // 50% = 50e25
        rate = 50e25;
        _mockPoolRate(rate);
        price = oracle.getIndexPrice(POOL, USDC);
        console.log("\nScenario 2: 50% APY");
        console.log("Input Rate (RAY):", rate);
        console.log("Output Price (WAD):", price);
        
        // Expected: (50e25 * 100) / 1e9 = 50e18 ($50.00)
        assertEq(price, 50e18, "50% rate should be $50.00");
    }

    function test_CapFunctionality() public {
        console.log("\n--- Unit Test: Cap Functionality ---");
        // Rate: 200% (2e27)
        // Should be capped at 100% (1e27) -> $100.00 (100e18)
        uint128 rate = 2e27;
        _mockPoolRate(rate);
        
        uint256 price = oracle.getIndexPrice(POOL, USDC);
        console.log("Input Rate (RAY):", rate, "(200% APY)");
        console.log("Output Price (WAD):", price);
        console.log("Expected Price (WAD): 100000000000000000000 ($100.00)");

        assertEq(price, 100e18, "Price should be capped at $100");
    }

    function test_FloorFunctionality() public {
        console.log("\n--- Unit Test: Floor Functionality ---");
        // Rate: 0%
        // Should be floor at $0.0001 (1e14)
        _mockPoolRate(0);
        uint256 price = oracle.getIndexPrice(POOL, USDC);
        console.log("Scenario 1: 0% APY");
        console.log("Input Rate (RAY): 0");
        console.log("Output Price (WAD):", price);
        console.log("Expected Floor:   100000000000000 ($0.0001)");
        
        assertEq(price, MIN_PRICE, "Price should be floor at 0 rate");

        // Rate: Extremely low but non-zero (1 wei in RAY)
        // 1 * 100 / 1e9 = 0 (integer division) -> Floor
        _mockPoolRate(1);
        price = oracle.getIndexPrice(POOL, USDC);
        
        console.log("\nScenario 2: 1 wei RAY (near 0%)");
        console.log("Input Rate (RAY): 1");
        console.log("Output Price (WAD):", price);
        console.log("Expected Floor:   100000000000000 ($0.0001)");

        assertEq(price, MIN_PRICE, "Tiny rate should hit floor");
    }

    /*//////////////////////////////////////////////////////////////
                                FUZZ TESTS
    //////////////////////////////////////////////////////////////*/

    // Test rates in the "normal" range [Floor Trigger ... Cap Trigger]
    // Floor Trigger: Rate * 100 / 1e9 >= 1e14  => Rate >= 1e21
    // Cap Trigger: Rate <= 1e27
    function testFuzz_CalculatesCorrectly(uint128 rate) public {
        // Bound rate to be within valid non-clamping range
        // Lower Bound: 1e21 (yields 1e14 exactly)
        // Upper Bound: 1e27 (yields 100e18 exactly)
        rate = uint128(bound(rate, 1e21, 1e27));
        _mockPoolRate(rate);

        uint256 price = oracle.getIndexPrice(POOL, USDC);
        uint256 expected = (uint256(rate) * K_SCALAR) / 1e9;

        assertEq(price, expected, "Fuzzed math mismatch");
    }

    // Test rates strictly above the cap
    function testFuzz_CapEnforcement(uint128 rate) public {
        // Bound rate to be > 1e27
        // Max uint128 is ~3.4e38, so there's plenty of room
        rate = uint128(bound(rate, 1e27 + 1, type(uint128).max));
        _mockPoolRate(rate);

        uint256 price = oracle.getIndexPrice(POOL, USDC);
        assertEq(price, 100e18, "Should satisfy cap for high rates");
    }

    // Test rates strictly below the floor logic range
    function testFuzz_FloorEnforcement(uint128 rate) public {
        // Bound rate to be < 1e21
        // (1e21 * 100)/1e9 = 100e12 = 1e14. Anything less triggers floor.
        rate = uint128(bound(rate, 0, 1e21 - 1));
        _mockPoolRate(rate);

        uint256 price = oracle.getIndexPrice(POOL, USDC);
        assertEq(price, MIN_PRICE, "Should satisfy floor for low rates");
    }

    /*//////////////////////////////////////////////////////////////
                                SECURITY PoCs
    //////////////////////////////////////////////////////////////*/

    // Quantifies how much the Spot Rate can be moved by a single large transaction (Flash Loan simulation)
    // Helps define thresholds for Symbiotic Oracle validation.
    function testPoC_FlashVolatility() public {
        // 1. Fork Mainnet
        string memory rpcUrl = vm.envString("MAINNET_RPC_URL");
        vm.createSelectFork(rpcUrl);
        RLDAaveOracle realOracle = new RLDAaveOracle();

        // 2. Snapshot Initial State
        IAavePool.ReserveData memory initialData = IAavePool(POOL).getReserveData(USDC);
        uint256 initialRate = initialData.currentVariableBorrowRate;
        uint256 initialPrice = realOracle.getIndexPrice(POOL, USDC);

        console.log("\n--- PoC: Flash Volatility Quantification ---");
        console.log("Initial Borrow Rate (RAY):", initialRate);
        console.log("Initial RLD Price (WAD):  ", initialPrice);

        // 3. Simulate "Attacker" with massive liquidity
        // We will act as a user who supplies independent collateral (e.g., WETH) and borrows USDC
        address whale = address(0x123);
        address weth = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
        
        // Deal Whale multiple assets to gain borrowing power without hitting single Supply Cap
        // Target: $1B+ Collateral
        
        // Deal Whale multiple assets to gain borrowing power
        // 1. WETH: 5,000 WETH (~$15M)
        deal(weth, whale, 5_000e18);
        
        // 2. wstETH: 200,000 wstETH (~$600M+) - Usually has higher caps or crucial for ecosystem
        address wsteth = 0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0;
        deal(wsteth, whale, 200_000e18);

        vm.startPrank(whale);
        
        // Supply WETH
        IERC20(weth).approve(POOL, type(uint256).max);
        IAavePoolExternal(POOL).supply(weth, 5_000e18, whale, 0);

        // Supply wstETH
        IERC20(wsteth).approve(POOL, type(uint256).max);
        IAavePoolExternal(POOL).supply(wsteth, 200_000e18, whale, 0);

        // 4. Borrow massive amount of USDC
        address aUsdc = initialData.aTokenAddress;
        uint256 availableLiquidity = IERC20(USDC).balanceOf(aUsdc);
        
        console.log("Available USDC Liquidity: ", availableLiquidity / 1e6, "M USDC");

        // Borrow 60% of available liquidity (Safer bet to avoid borrowing caps/collateral limits)
        // If we borrow 60%, utilization -> >60%. Rate should jump visibly.
        uint256 borrowAmount = (availableLiquidity * 60) / 100;
        
        IAavePoolExternal(POOL).borrow(USDC, borrowAmount, 2, 0, whale);
        
        vm.stopPrank();

        // 5. Check New Rate
        IAavePool.ReserveData memory finalData = IAavePool(POOL).getReserveData(USDC);
        uint256 finalRate = finalData.currentVariableBorrowRate;
        uint256 finalPrice = realOracle.getIndexPrice(POOL, USDC);

        console.log("--- AFTER Borrowing 99% of Pool ---");
        console.log("Total Borrowed:           ", borrowAmount / 1e6, "M USDC");
        console.log("Final Borrow Rate (RAY):  ", finalRate);
        console.log("Final RLD Price (WAD):    ", finalPrice);
        
        if (initialPrice > 0) {
            uint256 deltaPercent = ((finalPrice - initialPrice) * 100) / initialPrice;
            console.log("Price Increase:           ", deltaPercent, "%");
        }

        assertTrue(finalRate > initialRate, "Rate should increase with utilization");
    }
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IAavePoolExternal {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
    function borrow(address asset, uint256 amount, uint256 interestRateMode, uint16 referralCode, address onBehalfOf) external;
}
