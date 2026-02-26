// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IUnlockCallback} from "v4-core/src/interfaces/callback/IUnlockCallback.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";

/**
 * @title LifecycleSwapRouter
 * @notice Minimal router for lifecycle test swaps with explicit currency handling
 */
contract LifecycleSwapRouter is IUnlockCallback {
    using StateLibrary for IPoolManager;
    using CurrencySettler for Currency;

    IPoolManager public immutable poolManager;

    struct SwapCallbackData {
        PoolKey key;
        bool zeroForOne;
        int256 amountSpecified;
        address sender;
    }

    constructor(IPoolManager _pm) {
        poolManager = _pm;
    }

    function swap(PoolKey memory key, bool zeroForOne, int256 amountSpecified) external returns (BalanceDelta delta) {
        bytes memory result = poolManager.unlock(
            abi.encode(
                SwapCallbackData({
                    key: key, zeroForOne: zeroForOne, amountSpecified: amountSpecified, sender: msg.sender
                })
            )
        );
        delta = abi.decode(result, (BalanceDelta));
    }

    function unlockCallback(bytes calldata data) external override returns (bytes memory) {
        require(msg.sender == address(poolManager), "Not PM");

        SwapCallbackData memory cbData = abi.decode(data, (SwapCallbackData));

        // Execute the swap
        BalanceDelta delta = poolManager.swap(
            cbData.key,
            SwapParams({
                zeroForOne: cbData.zeroForOne,
                amountSpecified: cbData.amountSpecified,
                sqrtPriceLimitX96: cbData.zeroForOne
                    ? 4295128740  // MIN + 1
                    : 1461446703485210103287273052203988822378723970341 // MAX - 1
            }),
            ""
        );

        // Settle the swap using CurrencySettler pattern
        if (cbData.zeroForOne) {
            // Pay in currency0, receive currency1
            cbData.key.currency0.settle(poolManager, cbData.sender, uint256(int256(-delta.amount0())), false);
            cbData.key.currency1.take(poolManager, cbData.sender, uint256(int256(delta.amount1())), false);
        } else {
            // Pay in currency1, receive currency0
            cbData.key.currency1.settle(poolManager, cbData.sender, uint256(int256(-delta.amount1())), false);
            cbData.key.currency0.take(poolManager, cbData.sender, uint256(int256(delta.amount0())), false);
        }

        return abi.encode(delta);
    }
}

/**
 * @title LifecycleSwap
 * @notice Swap script with explicit, pre-sorted currency inputs
 * @dev Expects TOKEN0 < TOKEN1 to be verified by caller (shell script)
 */
contract LifecycleSwap is Script {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    function run() external {
        console.log("====== LIFECYCLE SWAP ======");

        // 1. Read SORTED currency addresses from environment
        address token0 = vm.envAddress("TOKEN0");
        address token1 = vm.envAddress("TOKEN1");
        address hook = vm.envAddress("TWAMM_HOOK");
        uint256 swapAmount = vm.envUint("SWAP_AMOUNT");
        bool zeroForOne = vm.envBool("ZERO_FOR_ONE");
        uint256 userKey = vm.envUint("SWAP_USER_KEY");

        address user = vm.addr(userKey);

        // 2. CRITICAL: Verify currencies are sorted correctly
        require(token0 < token1, "FATAL: TOKEN0 must be < TOKEN1");

        console.log("Token0:", token0);
        console.log("Token1:", token1);
        console.log("Hook:", hook);
        console.log("Swap amount (raw):", swapAmount);
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

        // 4. Get pool state before swap
        (uint160 sqrtPriceX96, int24 tickBefore,,) = pm.getSlot0(poolId);

        console.log("");
        console.log("=== POOL STATE BEFORE ===");
        console.log("Current tick:", tickBefore);
        console.log("sqrtPriceX96:", sqrtPriceX96);

        // 5. Check user balances before
        uint256 user0Before = ERC20(token0).balanceOf(user);
        uint256 user1Before = ERC20(token1).balanceOf(user);

        console.log("");
        console.log("=== USER BALANCES BEFORE ===");
        console.log("Token0:", user0Before);
        console.log("Token1:", user1Before);

        // Verify user has enough of the input token
        if (zeroForOne) {
            require(user0Before >= swapAmount, "Insufficient token0 balance");
        } else {
            require(user1Before >= swapAmount, "Insufficient token1 balance");
        }

        // 6. Execute swap
        vm.startBroadcast(userKey);

        // Deploy router
        LifecycleSwapRouter router = new LifecycleSwapRouter(pm);
        console.log("Deployed router:", address(router));

        // Approve router to pull tokens
        ERC20(token0).approve(address(router), type(uint256).max);
        ERC20(token1).approve(address(router), type(uint256).max);

        // Also approve pool manager for settlement
        ERC20(token0).approve(V4_POOL_MANAGER, type(uint256).max);
        ERC20(token1).approve(V4_POOL_MANAGER, type(uint256).max);

        console.log("Executing swap...");

        // NEGATIVE amountSpecified = exact input (we specify how much to spend)
        // POSITIVE amountSpecified = exact output (we specify how much to receive)
        BalanceDelta delta = router.swap(poolKey, zeroForOne, -int256(swapAmount));

        vm.stopBroadcast();

        // 7. Get pool state after swap
        (, int24 tickAfter,,) = pm.getSlot0(poolId);

        console.log("");
        console.log("=== POOL STATE AFTER ===");
        console.log("Current tick:", tickAfter);
        console.log("Tick delta:", int256(tickAfter) - int256(tickBefore));

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

        // Verify swap was successful
        if (zeroForOne) {
            // Sold token0, bought token1
            require(change0 < 0, "Expected to spend token0");
            require(change1 > 0, "Expected to receive token1");
            console.log("SUCCESS: Sold token0, received token1");
        } else {
            // Sold token1, bought token0
            require(change1 < 0, "Expected to spend token1");
            require(change0 > 0, "Expected to receive token0");
            console.log("SUCCESS: Sold token1, received token0");
        }
    }
}
