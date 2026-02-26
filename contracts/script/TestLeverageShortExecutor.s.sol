// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
import {LeverageShortExecutor} from "../src/periphery/LeverageShortExecutor.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";
import {IERC20} from "../src/shared/interfaces/IERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";

contract TestLeverageShortExecutor is Script, StdCheats {
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    function run() external {
        // Load env
        address waUSDC = vm.envAddress("WAUSDC");
        address wRLP = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");
        bytes32 marketId = vm.envBytes32("MARKET_ID");
        address brokerFactory = vm.envAddress("BROKER_FACTORY");
        address executor = vm.envAddress("LEVERAGE_SHORT_EXECUTOR");

        uint256 userCKey = vm.envUint("USER_C_PRIVATE_KEY");
        address userC = vm.addr(userCKey);

        uint256 targetLTV = vm.envOr("TARGET_LTV", uint256(40)); // 40% LTV default

        console.log("=== LEVERAGE SHORT VIA EXECUTOR ===");
        console.log("User C:", userC);
        console.log("Executor:", executor);
        console.log("Target LTV:", targetLTV, "%");

        vm.startBroadcast(userCKey);

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

        // Create broker for User C
        address broker = PrimeBrokerFactory(brokerFactory)
            .createBroker(keccak256(abi.encode(block.timestamp, userC, "leverage-short")));
        console.log("Broker:", broker);

        // Get initial waUSDC balance
        uint256 initialCollateral = IERC20(waUSDC).balanceOf(userC);
        console.log("Initial waUSDC:", initialCollateral / 1e6);

        // Calculate optimal debt for target leverage
        // Using estimated wRLP price of $5 (5e6 in 6 decimals)
        uint256 wRLPPriceE6 = 5_000_000; // $5.00
        uint256 targetDebt =
            LeverageShortExecutor(executor).calculateOptimalDebt(initialCollateral, targetLTV, wRLPPriceE6);
        console.log("Target wRLP debt:", targetDebt / 1e6);

        // Transfer collateral to broker
        IERC20(waUSDC).transfer(broker, initialCollateral);
        console.log("Transferred collateral to broker");

        // Get nonce and sign authorization
        uint256 nonce = PrimeBroker(payable(broker)).operatorNonces(executor);
        bytes32 messageHash = LeverageShortExecutor(executor).getEthSignedMessageHash(broker, nonce);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(userCKey, messageHash);
        bytes memory signature = abi.encodePacked(r, s, v);

        console.log("");
        console.log("=== EXECUTING ATOMIC LEVERAGE SHORT ===");

        // Execute single-swap leverage short!
        LeverageShortExecutor(executor)
            .executeLeverageShort(broker, marketId, waUSDC, wRLP, initialCollateral, targetDebt, poolKey, signature);

        vm.stopBroadcast();

        // Report
        uint256 finalCollateral = IERC20(waUSDC).balanceOf(broker);
        uint256 finalDebt = IERC20(wRLP).balanceOf(broker); // Should be 0, debt is in core

        console.log("");
        console.log("=== LEVERAGE SHORT COMPLETE ===");
        console.log("Initial collateral:", initialCollateral / 1e6);
        console.log("Final collateral:", finalCollateral / 1e6);
        console.log("Collateral increase:", (finalCollateral - initialCollateral) / 1e6);
        console.log("wRLP debt:", targetDebt / 1e6);
        console.log("");
        console.log("Effective leverage:", (targetDebt * wRLPPriceE6 / 1e6) * 100 / finalCollateral, "% LTV");
        console.log("");
        console.log("=== SINGLE SWAP - OPTIMAL GAS ===");
    }
}
