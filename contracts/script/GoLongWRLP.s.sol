// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";

/**
 * @title SimpleSwapRouter
 * @notice Minimal swap router for testing V4 pool swaps
 */
contract SimpleSwapRouter {
    using CurrencySettler for Currency;
    using SafeTransferLib for ERC20;

    IPoolManager public immutable manager;

    struct CallbackData {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    constructor(IPoolManager _manager) {
        manager = _manager;
    }

    function swap(PoolKey memory key, SwapParams memory params) external payable returns (BalanceDelta) {
        return abi.decode(manager.unlock(abi.encode(CallbackData(msg.sender, key, params))), (BalanceDelta));
    }

    function unlockCallback(bytes calldata rawData) external returns (bytes memory) {
        require(msg.sender == address(manager), "Not PM");

        CallbackData memory data = abi.decode(rawData, (CallbackData));

        BalanceDelta delta = manager.swap(data.key, data.params, new bytes(0));

        // Settle the swap
        if (data.params.zeroForOne) {
            // Pay in currency0, receive currency1
            data.key.currency0.settle(manager, data.sender, uint256(int256(-delta.amount0())), false);
            data.key.currency1.take(manager, data.sender, uint256(int256(delta.amount1())), false);
        } else {
            // Pay in currency1, receive currency0
            data.key.currency1.settle(manager, data.sender, uint256(int256(-delta.amount1())), false);
            data.key.currency0.take(manager, data.sender, uint256(int256(delta.amount0())), false);
        }

        return abi.encode(delta);
    }
}

/**
 * @title GoLongWRLP
 * @notice Script for User B to buy wRLP from V4 pool by swapping waUSDC
 * @dev Uses a simple swap router for direct V4 pool swaps
 */
contract GoLongWRLP is Script {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    // Mainnet addresses
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    // Pool params
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    function run() external {
        // Read from environment
        address waUSDC = vm.envAddress("WAUSDC");
        address positionToken = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");
        uint256 swapAmount = vm.envUint("SWAP_AMOUNT");

        // Use different private key for User B
        uint256 userBKey = vm.envUint("USER_B_PRIVATE_KEY");
        address userB = vm.addr(userBKey);

        console.log("=== Go Long wRLP ===");
        console.log("User B:", userB);
        console.log("Swap amount:", swapAmount / 1e6, "waUSDC");

        // Build pool key
        (address currency0Addr, address currency1Addr) =
            waUSDC < positionToken ? (waUSDC, positionToken) : (positionToken, waUSDC);

        bool waUsdcIsCurrency0 = waUSDC < positionToken;

        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twammHook)
        });

        // Get current state
        IPoolManager pm = IPoolManager(V4_POOL_MANAGER);
        (, int24 tickBefore,,) = pm.getSlot0(poolKey.toId());

        uint256 wrlpBalanceBefore = ERC20(positionToken).balanceOf(userB);
        uint256 waUsdcBalanceBefore = ERC20(waUSDC).balanceOf(userB);

        console.log("");
        console.log("=== BEFORE SWAP ===");
        console.log("User B waUSDC:", waUsdcBalanceBefore / 1e6);
        console.log("User B wRLP:", wrlpBalanceBefore / 1e6);
        console.log("Pool tick:", tickBefore);

        // Determine swap direction
        // We want: waUSDC → wRLP
        bool zeroForOne = waUsdcIsCurrency0;

        vm.startBroadcast(userBKey);

        // 1. Deploy simple swap router
        SimpleSwapRouter router = new SimpleSwapRouter(pm);
        console.log("Deployed SwapRouter:", address(router));

        // 2. Approve the router to spend waUSDC
        ERC20(waUSDC).approve(address(router), type(uint256).max);

        // Also approve PoolManager to pull tokens during settle
        ERC20(waUSDC).approve(V4_POOL_MANAGER, type(uint256).max);
        console.log("Approved tokens");

        // 3. Build swap params
        SwapParams memory params = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: int256(swapAmount), // Positive = exact input
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740  // MIN_SQRT_PRICE + 1
                : 1461446703485210103287273052203988822378723970341 // MAX_SQRT_PRICE - 1
        });

        // 4. Execute swap
        console.log("Executing swap...");
        BalanceDelta delta = router.swap(poolKey, params);
        console.log("Swap executed");

        vm.stopBroadcast();

        // Verify
        (, int24 tickAfter,,) = pm.getSlot0(poolKey.toId());
        uint256 wrlpBalanceAfter = ERC20(positionToken).balanceOf(userB);
        uint256 waUsdcBalanceAfter = ERC20(waUSDC).balanceOf(userB);

        console.log("");
        console.log("=== AFTER SWAP ===");
        console.log("User B waUSDC:", waUsdcBalanceAfter / 1e6);
        console.log("User B wRLP:", wrlpBalanceAfter / 1e6);
        console.log("Pool tick:", tickAfter);
        console.log("");
        console.log("=== SUMMARY ===");
        console.log("waUSDC spent:", (waUsdcBalanceBefore - waUsdcBalanceAfter) / 1e6);
        console.log("wRLP received:", (wrlpBalanceAfter - wrlpBalanceBefore) / 1e6);
        console.log("Tick change:", int256(tickAfter) - int256(tickBefore));

        if (wrlpBalanceAfter > wrlpBalanceBefore) {
            console.log("");
            console.log("=== SUCCESS ===");
            console.log("User B is now LONG wRLP!");
        } else {
            console.log("FAILED: No wRLP received");
        }
    }
}
