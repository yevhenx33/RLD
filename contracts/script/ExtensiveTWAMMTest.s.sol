// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {IJTM} from "../src/twamm/IJTM.sol";
import {JTM} from "../src/twamm/JTM.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title ExtensiveTWAMMTest
/// @notice Comprehensive test suite for TWAMM orders with various configurations
contract ExtensiveTWAMMTest is Script {
    JTM public twamm;
    PoolKey public poolKey;

    address public token0;
    address public token1;

    struct OrderResult {
        bytes32 orderId;
        uint256 expiration;
        uint256 sellRate;
        uint256 buyTokensOwed;
        uint256 sellTokensRefund;
        bool passed;
    }

    OrderResult[] public results;

    function run() external {
        // Load environment
        token0 = vm.envAddress("TOKEN0");
        token1 = vm.envAddress("TOKEN1");
        address twammHook = vm.envAddress("TWAMM_HOOK");
        twamm = JTM(payable(twammHook));

        // Setup pool key
        poolKey = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: 500,
            tickSpacing: 5,
            hooks: IHooks(twammHook)
        });

        console.log("====== EXTENSIVE TWAMM TEST SUITE ======");
        console.log("Token0:", token0);
        console.log("Token1:", token1);
        console.log("TWAMM:", twammHook);
        console.log("");

        // Run all test scenarios
        uint256 userKey = vm.envUint("USER_C_KEY");
        vm.startBroadcast(userKey);

        // Approve TWAMM for max spending
        IERC20(token0).approve(twammHook, type(uint256).max);
        IERC20(token1).approve(twammHook, type(uint256).max);

        vm.stopBroadcast();

        // Test 1: Small order, short duration (1 hour)
        _testOrder("Small/Short", 1000e6, 3600, true, userKey);

        // Test 2: Medium order, medium duration (2 hours)
        _testOrder("Medium/Medium", 5000e6, 7200, true, userKey);

        // Test 3: Large order, long duration (4 hours)
        _testOrder("Large/Long", 10000e6, 14400, true, userKey);

        // Warp time past ALL expirations
        console.log("");
        console.log("=== WARPING TIME PAST ALL EXPIRATIONS ===");
        uint256 maxExpiration = results[results.length - 1].expiration;
        uint256 targetTime = maxExpiration + 7200; // 2 hours past last expiration
        vm.warp(targetTime);
        console.log("New timestamp:", block.timestamp);

        // Trigger execution
        console.log("Triggering executeJTMOrders...");
        vm.broadcast(userKey);
        twamm.executeJTMOrders(poolKey);

        // Query all orders
        console.log("");
        console.log("=== QUERYING ALL EXPIRED ORDERS ===");
        _queryAllOrders(userKey);

        // Print summary
        _printSummary();
    }

    function _testOrder(string memory name, uint256 amount, uint256 duration, bool zeroForOne, uint256 userKey)
        internal
    {
        console.log("");
        console.log("--- Test:", name, "---");
        console.log("Amount:", amount / 1e6, "tokens");
        console.log("Duration:", duration / 60, "minutes");
        console.log("Direction:", zeroForOne ? "0->1" : "1->0");

        address user = vm.addr(userKey);
        address sellToken = zeroForOne ? token0 : token1;

        uint256 balanceBefore = IERC20(sellToken).balanceOf(user);
        console.log("Balance before:", balanceBefore);

        if (balanceBefore < amount) {
            console.log("SKIP: Insufficient balance");
            return;
        }

        vm.broadcast(userKey);
        (bytes32 orderId, IJTM.OrderKey memory orderKey) = twamm.submitOrder(
            IJTM.SubmitOrderParams({key: poolKey, zeroForOne: zeroForOne, amountIn: amount, duration: duration})
        );

        console.log("Order ID:", vm.toString(orderId));
        console.log("Expiration:", orderKey.expiration);

        // Get order details
        IJTM.Order memory order = twamm.getOrder(poolKey, orderKey);
        console.log("Sell Rate:", order.sellRate);

        results.push(
            OrderResult({
                orderId: orderId,
                expiration: orderKey.expiration,
                sellRate: order.sellRate,
                buyTokensOwed: 0,
                sellTokensRefund: 0,
                passed: false
            })
        );
    }

    function _queryAllOrders(uint256 userKey) internal {
        address user = vm.addr(userKey);

        for (uint256 i = 0; i < results.length; i++) {
            console.log("");
            console.log("--- Order", i + 1, "---");

            IJTM.OrderKey memory orderKey =
                IJTM.OrderKey({owner: user, expiration: uint160(results[i].expiration), zeroForOne: true, nonce: 0});

            (uint256 buyOwed, uint256 sellRefund) = twamm.getCancelOrderState(poolKey, orderKey);

            results[i].buyTokensOwed = buyOwed;
            results[i].sellTokensRefund = sellRefund;

            console.log("Expiration:", results[i].expiration);
            console.log("Sell Rate:", results[i].sellRate);
            console.log("buyTokensOwed:", buyOwed);
            console.log("sellTokensRefund:", sellRefund);

            // Verify: expired order should have sellTokensRefund = 0
            bool refundCorrect = (sellRefund == 0);
            // Verify: expired order should have buyTokensOwed > 0
            bool earningsCorrect = (buyOwed > 0);

            results[i].passed = refundCorrect && earningsCorrect;

            console.log("Refund correct (should be 0):", refundCorrect ? "PASS" : "FAIL");
            console.log("Earnings correct (should be > 0):", earningsCorrect ? "PASS" : "FAIL");
        }
    }

    function _printSummary() internal view {
        console.log("");
        console.log("====== TEST SUMMARY ======");

        uint256 passed = 0;
        uint256 failed = 0;

        for (uint256 i = 0; i < results.length; i++) {
            if (results[i].passed) {
                passed++;
            } else {
                failed++;
            }
        }

        console.log("Total orders:", results.length);
        console.log("Passed:", passed);
        console.log("Failed:", failed);

        if (failed == 0) {
            console.log("");
            console.log(">>> ALL TESTS PASSED <<<");
        } else {
            console.log("");
            console.log(">>> SOME TESTS FAILED <<<");
        }
    }
}
