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
 * @title ChaosRouter
 * @notice Swap router for chaotic testing
 */
contract ChaosRouter {
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

        if (data.params.zeroForOne) {
            if (delta.amount0() < 0) {
                data.key.currency0.settle(manager, data.sender, uint256(-int256(delta.amount0())), false);
            }
            if (delta.amount1() > 0) {
                data.key.currency1.take(manager, data.sender, uint256(int256(delta.amount1())), false);
            }
        } else {
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
 * @title ChaoticSwapTest
 * @notice CHAOTIC stress test with random sizes, random direction, time warps
 * @dev Simulates volatile market conditions
 */
contract ChaoticSwapTest is Script {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    uint256 constant NUM_SWAPS = 100;

    // Randomness seed (changes each block)
    uint256 seed;

    function run() external {
        address waUSDC = vm.envAddress("WAUSDC");
        address wRLP = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");

        uint256 traderKey = vm.envUint("USER_B_PRIVATE_KEY");
        address trader = vm.addr(traderKey);

        console.log("=== CHAOTIC 100-Swap Test ===");
        console.log("Trader:", trader);
        console.log("Chaos mode: ENABLED");

        // Initialize pseudo-random seed
        seed = uint256(keccak256(abi.encode(block.timestamp, block.prevrandao, trader)));

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

        int24 minTick = initialTick;
        int24 maxTick = initialTick;
        uint256 successCount = 0;
        uint256 skipCount = 0;
        uint256 failCount = 0;

        uint256 initialWaUSDC = ERC20(waUSDC).balanceOf(trader);
        uint256 initialWRLP = ERC20(wRLP).balanceOf(trader);

        console.log("");
        console.log("=== INITIAL STATE ===");
        console.log("Tick:", initialTick);
        console.log("waUSDC:", initialWaUSDC / 1e6);
        console.log("wRLP:", initialWRLP / 1e6);

        vm.startBroadcast(traderKey);

        ChaosRouter router = new ChaosRouter(pm);
        console.log("ChaosRouter:", address(router));

        ERC20(waUSDC).approve(address(router), type(uint256).max);
        ERC20(wRLP).approve(address(router), type(uint256).max);
        ERC20(waUSDC).approve(V4_POOL_MANAGER, type(uint256).max);
        ERC20(wRLP).approve(V4_POOL_MANAGER, type(uint256).max);

        console.log("");
        console.log("=== CHAOS BEGINS ===");
        console.log("iter,action,size,tick,status");

        for (uint256 i = 0; i < NUM_SWAPS; i++) {
            // Random direction
            bool isBuy = _random() % 2 == 0;

            // Random type (exact in/out)
            bool isExactInput = _random() % 2 == 0;

            // Random size: 10 to 1000 tokens (chaos!)
            uint256 baseSize = 10_000_000 + (_random() % 990_000_000); // 10-1000 tokens

            // 10% chance of "whale" swap (5x normal)
            if (_random() % 10 == 0) {
                baseSize = baseSize * 5;
            }

            // 5% chance of "dust" swap (tiny amount)
            if (_random() % 20 == 0) {
                baseSize = 1_000_000; // 1 token
            }

            // Time warp: advance 1-60 seconds randomly
            uint256 timeSkip = 1 + (_random() % 60);
            vm.warp(block.timestamp + timeSkip);

            // Occasionally roll blocks too
            if (_random() % 5 == 0) {
                vm.roll(block.number + 1);
            }

            // === BALANCE CHECK: Cap swap to available balance ===
            uint256 waUsdcBal = ERC20(waUSDC).balanceOf(trader);
            uint256 wrlpBal = ERC20(wRLP).balanceOf(trader);

            // For buys: need waUSDC. For sells: need wRLP.
            uint256 availableBalance = isBuy ? waUsdcBal : wrlpBal;

            // Cap size to 80% of available balance (leave buffer)
            uint256 maxSize = (availableBalance * 80) / 100;
            if (baseSize > maxSize) {
                baseSize = maxSize;
            }

            // Skip if insufficient (less than 1 token) - not a failure, just skip
            if (baseSize < 1_000_000) {
                console.log(string.concat(vm.toString(i), ",SKIP,0,?,LOW_BAL"));
                skipCount++;
                continue;
            }

            bool zeroForOne = isBuy == waUsdcIsCurrency0;

            int256 amountSpecified = isExactInput ? int256(baseSize) : -int256(baseSize);

            uint160 sqrtPriceLimit = zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1;

            SwapParams memory params = SwapParams({
                zeroForOne: zeroForOne, amountSpecified: amountSpecified, sqrtPriceLimitX96: sqrtPriceLimit
            });

            string memory action = string.concat(isBuy ? "BUY" : "SELL", isExactInput ? "_IN" : "_OUT");

            try router.swap(poolKey, params) returns (BalanceDelta) {
                (, int24 tickAfter,,) = pm.getSlot0(poolKey.toId());

                if (tickAfter < minTick) minTick = tickAfter;
                if (tickAfter > maxTick) maxTick = tickAfter;

                successCount++;

                console.log(
                    string.concat(
                        vm.toString(i),
                        ",",
                        action,
                        ",",
                        vm.toString(baseSize / 1e6),
                        ",",
                        vm.toString(int256(tickAfter)),
                        ",OK"
                    )
                );
            } catch {
                failCount++;
                console.log(string.concat(vm.toString(i), ",", action, ",", vm.toString(baseSize / 1e6), ",?,FAIL"));
            }
        }

        vm.stopBroadcast();

        (, int24 finalTick,,) = pm.getSlot0(poolKey.toId());
        uint256 finalWaUSDC = ERC20(waUSDC).balanceOf(trader);
        uint256 finalWRLP = ERC20(wRLP).balanceOf(trader);

        console.log("");
        console.log("=== CHAOS RESULTS ===");
        console.log("Success:", successCount);
        console.log("Skipped:", skipCount);
        console.log("Failed:", failCount);
        console.log("Total:", successCount + skipCount);
        console.log("");
        console.log("=== TICK VOLATILITY ===");
        console.log("Initial:", initialTick);
        console.log("Final:", finalTick);
        console.log("Min:", minTick);
        console.log("Max:", maxTick);
        console.log("Range:", int256(maxTick) - int256(minTick));
        console.log("");
        console.log("=== NET P&L ===");
        console.log("Net waUSDC:", int256(finalWaUSDC) - int256(initialWaUSDC));
        console.log("Net wRLP:", int256(finalWRLP) - int256(initialWRLP));
    }

    function _random() internal returns (uint256) {
        seed = uint256(keccak256(abi.encode(seed)));
        return seed;
    }
}
