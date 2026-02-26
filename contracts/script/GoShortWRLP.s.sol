// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
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

import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";

interface IWrappedAToken {
    function wrap(uint256 aTokenAmount) external returns (uint256 shares);
    function unwrap(uint256 shares) external returns (uint256 aTokenAmount);
}

interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

/**
 * @title ShortRouter
 * @notice Swap router for short flow (sell wRLP for waUSDC)
 */
contract ShortRouter {
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
 * @title GoShortWRLP
 * @notice User C goes SHORT on RLD index:
 *   1. Deposit waUSDC as collateral
 *   2. Mint wRLP (debt)
 *   3. Sell wRLP → waUSDC on V4
 *   4. Redeposit waUSDC to enhance collateral
 * @dev Supports single cycle and leverage loop modes
 */
contract GoShortWRLP is Script, StdCheats {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    // Mainnet addresses
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;

    // Pool params
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    // Short params
    uint256 constant LTV_PERCENT = 40; // Target 40% LTV for safety margin

    struct ShortReport {
        uint256 initialCollateral;
        uint256 finalCollateral;
        uint256 totalDebt;
        uint256 totalSold;
        uint256 totalProceeds;
        uint256 loops;
        int24 startTick;
        int24 endTick;
    }

    function run() external {
        // Read env vars
        address waUSDC = vm.envAddress("WAUSDC");
        address wRLP = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");
        bytes32 marketId = vm.envBytes32("MARKET_ID");
        address brokerFactory = vm.envAddress("BROKER_FACTORY");

        uint256 userCKey = vm.envUint("USER_C_PRIVATE_KEY");
        address userC = vm.addr(userCKey);

        // Optional: number of leverage loops (0 or 1 = single cycle)
        uint256 loops = vm.envOr("LEVERAGE_LOOPS", uint256(1));

        console.log("=== GO SHORT wRLP ===");
        console.log("User C:", userC);
        console.log("Mode:", loops > 1 ? "LEVERAGE LOOP" : "SINGLE CYCLE");
        console.log("Loops:", loops);

        vm.startBroadcast(userCKey);

        // Deploy swap router
        ShortRouter router = new ShortRouter(IPoolManager(V4_POOL_MANAGER));
        console.log("ShortRouter:", address(router));

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
        (, int24 startTick,,) = pm.getSlot0(poolKey.toId());

        // Approvals
        ERC20(waUSDC).approve(address(router), type(uint256).max);
        ERC20(wRLP).approve(address(router), type(uint256).max);
        ERC20(waUSDC).approve(V4_POOL_MANAGER, type(uint256).max);
        ERC20(wRLP).approve(V4_POOL_MANAGER, type(uint256).max);

        // Create broker for User C
        address broker =
            PrimeBrokerFactory(brokerFactory).createBroker(keccak256(abi.encode(block.timestamp, userC, "short")));
        console.log("Broker:", broker);

        // Get initial waUSDC balance
        uint256 initialWaUSDC = ERC20(waUSDC).balanceOf(userC);
        console.log("Initial waUSDC:", initialWaUSDC / 1e6);

        // Report tracking
        ShortReport memory report;
        report.initialCollateral = initialWaUSDC;
        report.startTick = startTick;
        report.loops = loops;

        // === EXECUTE SHORT LOOPS ===
        for (uint256 i = 0; i < loops; i++) {
            console.log("");
            console.log("=== LOOP", i + 1, "===");

            // Step 1: Transfer waUSDC to broker
            uint256 waUsdcBal = ERC20(waUSDC).balanceOf(userC);
            if (waUsdcBal < 1_000_000) {
                console.log("Insufficient waUSDC, stopping");
                break;
            }

            console.log("  Collateral:", waUsdcBal / 1e6);
            ERC20(waUSDC).transfer(broker, waUsdcBal);

            // Step 2: Deposit and mint at target LTV
            // LTV = debt_value / collateral_value
            // debt_value = wRLP_amount * wRLP_price_in_collateral
            // At ~$5/wRLP: to achieve 40% LTV on 50k collateral:
            //   target_debt_value = 50k * 0.4 = 20k
            //   wRLP_amount = 20k / 5 = 4k wRLP
            // Using estimated price of 5 (tick ~15600)
            uint256 estimatedWRLPPrice = 5;
            uint256 targetDebtValue = (waUsdcBal * LTV_PERCENT) / 100;
            uint256 mintAmount = targetDebtValue / estimatedWRLPPrice;
            console.log("  Minting wRLP:", mintAmount / 1e6);

            PrimeBroker(payable(broker)).modifyPosition(marketId, int256(waUsdcBal), int256(mintAmount));

            report.totalDebt += mintAmount;

            // Step 3: Withdraw wRLP to User C
            PrimeBroker(payable(broker)).withdrawPositionToken(userC, mintAmount);
            console.log("  Withdrew wRLP to user");

            // Step 4: Sell wRLP for waUSDC on V4
            uint256 wRLPBal = ERC20(wRLP).balanceOf(userC);
            console.log("  Selling wRLP:", wRLPBal / 1e6);

            // Sell = wRLP -> waUSDC
            // zeroForOne = true if wRLP is currency0
            bool zeroForOne = !waUsdcIsCurrency0;

            SwapParams memory params = SwapParams({
                zeroForOne: zeroForOne,
                amountSpecified: int256(wRLPBal), // exact input
                sqrtPriceLimitX96: zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1
            });

            BalanceDelta delta = router.swap(poolKey, params);

            // Calculate proceeds
            uint256 proceeds = zeroForOne ? uint256(int256(delta.amount1())) : uint256(int256(delta.amount0()));

            console.log("  Proceeds waUSDC:", proceeds / 1e6);

            report.totalSold += wRLPBal;
            report.totalProceeds += proceeds;

            // Note: waUSDC proceeds stay with User C for next loop
            // or as enhanced collateral potential
        }

        // Step 5: Redeposit proceeds to broker (enhance collateral)
        uint256 finalWaUSDC = ERC20(waUSDC).balanceOf(userC);
        if (finalWaUSDC > 0) {
            console.log("");
            console.log("=== REDEPOSIT PROCEEDS ===");
            console.log("  Redepositing:", finalWaUSDC / 1e6);

            ERC20(waUSDC).transfer(broker, finalWaUSDC);

            // Deposit as additional collateral (no new debt)
            PrimeBroker(payable(broker))
                .modifyPosition(
                    marketId,
                    int256(finalWaUSDC),
                    int256(0) // No new debt
                );
        }

        vm.stopBroadcast();

        // Final state
        (, int24 endTick,,) = pm.getSlot0(poolKey.toId());
        report.endTick = endTick;
        report.finalCollateral = ERC20(waUSDC).balanceOf(broker);

        // === COMPREHENSIVE REPORT ===
        console.log("");
        console.log("========================================");
        console.log("       SHORT POSITION REPORT           ");
        console.log("========================================");
        console.log("");
        console.log("=== POSITION ===");
        console.log("Mode:", report.loops > 1 ? "LEVERAGE LOOP" : "SINGLE CYCLE");
        console.log("Loops completed:", report.loops);
        console.log("");
        console.log("=== COLLATERAL ===");
        console.log("Initial waUSDC:", report.initialCollateral / 1e6);
        console.log("Final waUSDC (in broker):", report.finalCollateral / 1e6);
        console.log("");
        console.log("=== DEBT ===");
        console.log("Total wRLP minted:", report.totalDebt / 1e6);
        console.log("Total wRLP sold:", report.totalSold / 1e6);
        console.log("Sale proceeds:", report.totalProceeds / 1e6);
        console.log("");
        console.log("=== MARKET IMPACT ===");
        console.log("Start tick:", report.startTick);
        console.log("End tick:", report.endTick);
        console.log("Tick change:", int256(report.endTick) - int256(report.startTick));
        console.log("");
        console.log("=== ECONOMICS ===");

        // Calculate effective leverage
        uint256 leverage = (report.initialCollateral > 0) ? (report.totalDebt * 100) / report.initialCollateral : 0;
        console.log("Effective leverage:", leverage, "% debt/collateral");

        // Calculate slippage
        int256 slippage = int256(report.totalProceeds) - int256(report.totalSold);
        console.log("Net slippage:", slippage);

        console.log("");
        console.log("=== SHORT POSITION ACTIVE ===");
        console.log("You OWE:", report.totalDebt / 1e6, "wRLP");
        console.log("If wRLP price FALLS -> you PROFIT");
        console.log("If wRLP price RISES -> you LOSE");
    }
}
