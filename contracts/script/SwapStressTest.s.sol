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
import {TickMath} from "v4-core/src/libraries/TickMath.sol";

/**
 * @title StressTestRouter
 * @notice Swap router that handles both directions and exact in/out
 */
contract StressTestRouter {
    using CurrencySettler for Currency;

    IPoolManager public immutable manager;

    struct CallbackData {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    constructor(IPoolManager _manager) {
        manager = _manager;
    }

    function swap(PoolKey memory key, SwapParams memory params) external returns (BalanceDelta) {
        return abi.decode(manager.unlock(abi.encode(CallbackData(msg.sender, key, params))), (BalanceDelta));
    }

    function unlockCallback(bytes calldata rawData) external returns (bytes memory) {
        require(msg.sender == address(manager), "Not PM");

        CallbackData memory data = abi.decode(rawData, (CallbackData));

        BalanceDelta delta = manager.swap(data.key, data.params, new bytes(0));

        // Settle the swap based on direction
        if (data.params.zeroForOne) {
            // currency0 in, currency1 out
            if (delta.amount0() < 0) {
                data.key.currency0.settle(manager, data.sender, uint256(-int256(delta.amount0())), false);
            }
            if (delta.amount1() > 0) {
                data.key.currency1.take(manager, data.sender, uint256(int256(delta.amount1())), false);
            }
        } else {
            // currency1 in, currency0 out
            if (delta.amount1() < 0) {
                data.key.currency1.settle(manager, data.sender, uint256(-int256(delta.amount1())), false);
            }
            if (delta.amount0() > 0) {
                data.key.currency0.take(manager, data.sender, uint256(int256(delta.amount0())), false);
            }
        }

        return abi.encode(delta);
    }
}

/**
 * @title SwapStressTest
 * @notice Execute 100 alternating swaps to stress test V4 pool + TWAMM hook
 * @dev Pattern: Buy/Sell x ExactIn/ExactOut (4-cycle repeat)
 */
contract SwapStressTest is Script {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    // Mainnet addresses
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    // Pool params
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    // Swap config
    uint256 constant NUM_SWAPS = 100;
    uint256 constant SWAP_SIZE = 100_000_000; // 100 tokens (6 decimals)

    function run() external {
        // Read from environment
        address waUSDC = vm.envAddress("WAUSDC");
        address wRLP = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");

        uint256 traderKey = vm.envUint("USER_B_PRIVATE_KEY");
        address trader = vm.addr(traderKey);

        console.log("=== 100-Swap Stress Test ===");
        console.log("Trader:", trader);
        console.log("Swap size:", SWAP_SIZE / 1e6, "tokens each");

        // Build pool key
        bool waUsdcIsCurrency0 = waUSDC < wRLP;
        (address currency0Addr, address currency1Addr) = waUsdcIsCurrency0 ? (waUSDC, wRLP) : (wRLP, waUSDC);

        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twammHook)
        });

        IPoolManager pm = IPoolManager(V4_POOL_MANAGER);
        (, int24 initialTick,,) = pm.getSlot0(poolKey.toId());

        uint256 initialWaUSDC = ERC20(waUSDC).balanceOf(trader);
        uint256 initialWRLP = ERC20(wRLP).balanceOf(trader);

        console.log("");
        console.log("=== INITIAL STATE ===");
        console.log("Tick:", initialTick);
        console.log("Trader waUSDC:", initialWaUSDC / 1e6);
        console.log("Trader wRLP:", initialWRLP / 1e6);

        vm.startBroadcast(traderKey);

        // Deploy router
        StressTestRouter router = new StressTestRouter(pm);
        console.log("Router deployed:", address(router));

        // Approve tokens
        ERC20(waUSDC).approve(address(router), type(uint256).max);
        ERC20(wRLP).approve(address(router), type(uint256).max);
        ERC20(waUSDC).approve(V4_POOL_MANAGER, type(uint256).max);
        ERC20(wRLP).approve(V4_POOL_MANAGER, type(uint256).max);

        console.log("");
        console.log("iter,direction,type,amountIn,amountOut,tick");

        // Execute 100 swaps
        for (uint256 i = 0; i < NUM_SWAPS; i++) {
            // Pattern:
            // i%4 == 0: Buy, Exact In
            // i%4 == 1: Sell, Exact In
            // i%4 == 2: Buy, Exact Out
            // i%4 == 3: Sell, Exact Out

            bool isBuy = (i % 2 == 0);
            bool isExactInput = ((i / 2) % 2 == 0);

            // For buy: waUSDC → wRLP
            // zeroForOne depends on token ordering
            bool zeroForOne = isBuy == waUsdcIsCurrency0;

            // amountSpecified:
            // positive = exact input
            // negative = exact output
            int256 amountSpecified;
            if (isExactInput) {
                amountSpecified = int256(SWAP_SIZE);
            } else {
                amountSpecified = -int256(SWAP_SIZE);
            }

            // Set price limit based on direction
            uint160 sqrtPriceLimit = zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1;

            SwapParams memory params = SwapParams({
                zeroForOne: zeroForOne, amountSpecified: amountSpecified, sqrtPriceLimitX96: sqrtPriceLimit
            });

            try router.swap(poolKey, params) returns (BalanceDelta delta) {
                (, int24 tickAfter,,) = pm.getSlot0(poolKey.toId());

                // Log in CSV format
                console.log(
                    string.concat(
                        vm.toString(i),
                        ",",
                        isBuy ? "BUY" : "SELL",
                        ",",
                        isExactInput ? "EXACT_IN" : "EXACT_OUT",
                        ",",
                        vm.toString(delta.amount0()),
                        ",",
                        vm.toString(delta.amount1()),
                        ",",
                        vm.toString(int256(tickAfter))
                    )
                );
            } catch Error(string memory reason) {
                console.log(string.concat(vm.toString(i), ",FAILED,", reason));
                break;
            } catch {
                console.log(string.concat(vm.toString(i), ",FAILED,unknown"));
                break;
            }
        }

        vm.stopBroadcast();

        // Final state
        (, int24 finalTick,,) = pm.getSlot0(poolKey.toId());
        uint256 finalWaUSDC = ERC20(waUSDC).balanceOf(trader);
        uint256 finalWRLP = ERC20(wRLP).balanceOf(trader);

        console.log("");
        console.log("=== FINAL STATE ===");
        console.log("Tick:", finalTick);
        console.log("Trader waUSDC:", finalWaUSDC / 1e6);
        console.log("Trader wRLP:", finalWRLP / 1e6);
        console.log("");
        console.log("=== SUMMARY ===");
        console.log("Tick change:", int256(finalTick) - int256(initialTick));
        console.log("Net waUSDC:", int256(finalWaUSDC) - int256(initialWaUSDC));
        console.log("Net wRLP:", int256(finalWRLP) - int256(initialWRLP));
    }
}
