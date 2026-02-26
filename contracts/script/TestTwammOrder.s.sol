// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {IJTM} from "../src/twamm/IJTM.sol";

/**
 * @title TestTwammOrder
 * @notice Test script to place a TWAMM order to buy wRLP with waUSDC
 * @dev User C places a gradual buy order that executes over time
 */
contract TestTwammOrder is Script {
    // Pool configuration from DeployWrappedMarket
    // NOTE: Must match the poolFee used in DeployWrappedMarket.s.sol (500, not DYNAMIC_FEE_FLAG)
    uint24 constant FEE = 500;
    int24 constant TICK_SPACING = 5;

    function run() external {
        // Load from environment
        address waUSDC = vm.envAddress("WAUSDC");
        address positionToken = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");
        uint256 amountIn = vm.envUint("AMOUNT_IN");
        uint256 durationSeconds = vm.envUint("DURATION_SECONDS");

        uint256 privateKey = vm.envUint("USER_C_PRIVATE_KEY");
        address userC = vm.addr(privateKey);

        console.log("=== TWAMM Order Test ===");
        console.log("User C:", userC);
        console.log("waUSDC:", waUSDC);
        console.log("wRLP:", positionToken);
        console.log("TWAMM Hook:", twammHook);
        console.log("Amount In:", amountIn / 1e6, "waUSDC");
        console.log("Duration:", durationSeconds / 3600, "hours");
        console.log("");

        // Check User C's waUSDC balance
        uint256 balance = ERC20(waUSDC).balanceOf(userC);
        console.log("User C waUSDC balance:", balance / 1e6);
        require(balance >= amountIn, "Insufficient waUSDC balance");

        vm.startBroadcast(privateKey);

        // Step 1: Approve TWAMM hook to spend waUSDC
        console.log("Approving TWAMM hook...");
        ERC20(waUSDC).approve(twammHook, amountIn);

        // Step 2: Construct PoolKey
        // Tokens must be sorted by address
        (address currency0, address currency1) =
            waUSDC < positionToken ? (waUSDC, positionToken) : (positionToken, waUSDC);

        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(currency0),
            currency1: Currency.wrap(currency1),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twammHook)
        });

        // Step 3: Determine zeroForOne
        // We want to sell waUSDC and buy wRLP
        // zeroForOne = true means selling token0 for token1
        bool zeroForOne = (waUSDC == currency0);

        console.log("Currency0:", currency0);
        console.log("Currency1:", currency1);
        console.log("zeroForOne (selling waUSDC):", zeroForOne);

        // Step 4: Submit TWAMM order
        console.log("");
        console.log("Submitting TWAMM order...");

        IJTM.SubmitOrderParams memory orderParams = IJTM.SubmitOrderParams({
            key: poolKey, zeroForOne: zeroForOne, duration: durationSeconds, amountIn: amountIn
        });

        (bytes32 orderId, IJTM.OrderKey memory orderKey) = IJTM(twammHook).submitOrder(orderParams);

        console.log("");
        console.log("=== ORDER CREATED ===");
        console.log("Order ID:", vm.toString(orderId));
        console.log("Owner:", orderKey.owner);
        console.log("Expiration:", orderKey.expiration);
        console.log("zeroForOne:", orderKey.zeroForOne);

        // Step 5: Verify order exists
        IJTM.Order memory order = IJTM(twammHook).getOrder(poolKey, orderKey);
        console.log("");
        console.log("=== ORDER STATE ===");
        console.log("Sell Rate:", order.sellRate / 1e18, "(scaled)");
        console.log("Earnings Factor Last:", order.earningsFactorLast);

        require(order.sellRate > 0, "Order not created - sellRate is 0");

        vm.stopBroadcast();

        console.log("");
        console.log("=== SUCCESS ===");
        console.log("TWAMM order successfully created!");
        console.log("The order will gradually buy wRLP over", durationSeconds / 3600, "hours");
    }
}
