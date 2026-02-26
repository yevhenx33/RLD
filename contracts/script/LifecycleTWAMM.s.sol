// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {IJTM} from "../src/twamm/IJTM.sol";

/**
 * @title LifecycleTWAMM
 * @notice TWAMM order submission with explicit, pre-sorted currency inputs
 * @dev Expects TOKEN0 < TOKEN1 to be verified by caller (shell script)
 */
contract LifecycleTWAMM is Script {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    function run() external {
        console.log("====== LIFECYCLE TWAMM ======");

        // 1. Read SORTED currency addresses from environment
        address token0 = vm.envAddress("TOKEN0");
        address token1 = vm.envAddress("TOKEN1");
        address hook = vm.envAddress("TWAMM_HOOK");
        uint256 orderAmount = vm.envUint("ORDER_AMOUNT");
        uint256 durationSeconds = vm.envUint("DURATION_SECONDS");
        bool zeroForOne = vm.envBool("ZERO_FOR_ONE");
        uint256 userKey = vm.envUint("TWAMM_USER_KEY");

        address user = vm.addr(userKey);

        // 2. CRITICAL: Verify currencies are sorted correctly
        require(token0 < token1, "FATAL: TOKEN0 must be < TOKEN1");

        console.log("Token0:", token0);
        console.log("Token1:", token1);
        console.log("Hook (TWAMM):", hook);
        console.log("Order amount (raw):", orderAmount);
        console.log("Duration (seconds):", durationSeconds);
        console.log("zeroForOne:", zeroForOne);
        console.log("User:", user);

        // 3. Build pool key
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(hook)
        });

        IPoolManager pm = IPoolManager(V4_POOL_MANAGER);
        PoolId poolId = poolKey.toId();

        // 4. Get pool state
        (uint160 sqrtPriceX96, int24 tick,,) = pm.getSlot0(poolId);

        console.log("");
        console.log("=== POOL STATE ===");
        console.log("Current tick:", tick);
        console.log("sqrtPriceX96:", sqrtPriceX96);

        // 5. Check user balances before
        uint256 user0Before = ERC20(token0).balanceOf(user);
        uint256 user1Before = ERC20(token1).balanceOf(user);

        console.log("");
        console.log("=== USER BALANCES BEFORE ===");
        console.log("Token0:", user0Before);
        console.log("Token1:", user1Before);

        // Verify user has enough of the input token
        address inputToken = zeroForOne ? token0 : token1;
        uint256 inputBalance = zeroForOne ? user0Before : user1Before;
        require(inputBalance >= orderAmount, "Insufficient balance for TWAMM order");

        // 6. Submit TWAMM order using SubmitOrderParams
        vm.startBroadcast(userKey);

        // Approve TWAMM hook to spend tokens
        ERC20(inputToken).approve(hook, orderAmount);
        console.log("Approved input token to TWAMM hook");

        // Build order params using the simpler SubmitOrderParams struct
        IJTM.SubmitOrderParams memory orderParams = IJTM.SubmitOrderParams({
            key: poolKey, zeroForOne: zeroForOne, duration: durationSeconds, amountIn: orderAmount
        });

        console.log("Submitting TWAMM order...");

        // Submit the order - returns orderId and orderKey
        (bytes32 orderId, IJTM.OrderKey memory orderKey) = IJTM(hook).submitOrder(orderParams);

        vm.stopBroadcast();

        console.log("");
        console.log("=== ORDER SUBMITTED ===");
        console.log("Order ID:", vm.toString(orderId));
        console.log("Expiration:", orderKey.expiration);

        // 7. Verify order exists by querying state
        IJTM.Order memory order = IJTM(hook).getOrder(poolKey, orderKey);

        console.log("");
        console.log("=== ORDER STATE ===");
        console.log("Sell rate:", order.sellRate);

        require(order.sellRate > 0, "Order not found - submission failed!");

        // 8. Check user balances after
        uint256 user0After = ERC20(token0).balanceOf(user);
        uint256 user1After = ERC20(token1).balanceOf(user);

        console.log("");
        console.log("=== USER BALANCES AFTER ===");
        console.log("Token0:", user0After);
        console.log("Token1:", user1After);

        // Calculate changes
        int256 change0 = int256(user0After) - int256(user0Before);
        int256 change1 = int256(user1After) - int256(user1Before);

        console.log("");
        console.log("=== BALANCE CHANGES ===");
        console.log("Token0 change:", change0);
        console.log("Token1 change:", change1);

        // Verify order locked the tokens
        if (zeroForOne) {
            require(change0 < 0, "Expected to spend token0");
            console.log("SUCCESS: Token0 locked in TWAMM order");
        } else {
            require(change1 < 0, "Expected to spend token1");
            console.log("SUCCESS: Token1 locked in TWAMM order");
        }
    }
}
