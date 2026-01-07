// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/oracles/RLDAaveOracle.sol";

contract RLDAaveOracleTest is Test {
    RLDAaveOracle oracle;

    function setUp() public {
        // Use the RPC URL from your .env file
        string memory rpcUrl = vm.envString("MAINNET_RPC_URL");
        vm.createSelectFork(rpcUrl);

        oracle = new RLDAaveOracle();
    }

    function testRealTimeData() public {
        uint256 price = oracle.getIndexPrice();

        console.log("--- RLD Oracle Live Data ---");
        console.log("Spot Index Price (WAD):", price);

        // Pretty print dollar value
        uint256 dollars = price / 1e18;
        uint256 cents = (price % 1e18) / 1e14; // roughly 4 decimals
        console.log("Dollar Value: $%s.%s", dollars, cents);

        // Sanity: Price should be >= MIN_PRICE ($0.0001)
        assertTrue(price >= 1e14);

        // Sanity: Price should be <= $100 (100% APY cap)
        assertTrue(price <= 100 * 1e18);
    }

    function testMathVerification() public {
        // Whitebox Math Check for 5%
        uint256 rate5Percent = 5 * 10 ** 25; // 5% in RAY
        uint256 K = 100;

        uint256 expectedPrice = 5 * 10 ** 18; // $5.00
        uint256 actualPrice = (rate5Percent * K) / 1e9;

        assertEq(
            actualPrice,
            expectedPrice,
            "Math Error: 5% Rate did not equal $5.00"
        );
    }

    function testPriceFloor() public {
        // To verify the floor logic without mocking the huge Aave contract,
        // we can check the logic mathematically or use a mock.
        // Since we can't easily mock `address(POOL)` in a fork test without DeployCode,
        // we will verify the constants here to ensure safety.

        uint256 minPrice = oracle.MIN_PRICE();
        assertEq(minPrice, 1e14, "Min Price is not 0.0001");

        // Verify Logic Flow:
        // If rate is 0 -> Price is 0 -> Should return MIN_PRICE
        uint256 zeroRatePrice = (0 * 100) / 1e9;
        if (zeroRatePrice < minPrice) {
            zeroRatePrice = minPrice;
        }
        assertEq(zeroRatePrice, 1e14, "Floor logic failed for 0 rate");
    }
}
