// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {ITWAMM} from "../src/twamm/ITWAMM.sol";

/**
 * @title QueryTWAMMOrder
 * @notice Query TWAMM order state via getCancelOrderState
 */
contract QueryTWAMMOrder is Script {
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    function run() external view {
        console.log("====== QUERY TWAMM ORDER ======");
        
        address token0 = vm.envAddress("TOKEN0");
        address token1 = vm.envAddress("TOKEN1");
        address hook = vm.envAddress("TWAMM_HOOK");
        uint160 expiration = uint160(vm.envUint("EXPIRATION"));
        address owner = vm.envAddress("ORDER_OWNER");
        
        require(token0 < token1, "TOKEN0 must be < TOKEN1");
        
        console.log("Token0:", token0);
        console.log("Token1:", token1);
        console.log("Hook:", hook);
        console.log("Expiration:", expiration);
        console.log("Owner:", owner);
        
        // Build pool key
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(hook)
        });
        
        // Build order key
        ITWAMM.OrderKey memory orderKey = ITWAMM.OrderKey({
            owner: owner,
            expiration: expiration,
            zeroForOne: true
        });
        
        console.log("");
        console.log("=== ORDER STATE ===");
        
        // Get order
        ITWAMM.Order memory order = ITWAMM(hook).getOrder(poolKey, orderKey);
        console.log("Sell Rate:", order.sellRate);
        
        if (order.sellRate == 0) {
            console.log("Order not found or empty!");
            return;
        }
        
        // Get cancel state (what we'd get if cancelled now)
        (uint256 buyTokensOwed, uint256 sellTokensRefund) = ITWAMM(hook).getCancelOrderState(poolKey, orderKey);
        
        console.log("");
        console.log("=== CANCEL ORDER STATE (getValue input) ===");
        console.log("buyTokensOwed (earned):", buyTokensOwed);
        console.log("sellTokensRefund (remaining):", sellTokensRefund);
        
        // Calculate rough USD value
        console.log("");
        console.log("=== ESTIMATED VALUE ===");
        // token1 (wRLP) earned + token0 (waUSDC) remaining
        // Assuming waUSDC is 1:1 with USD and wRLP ~$4.24
        uint256 waUSDCValue = sellTokensRefund; // 6 decimals
        uint256 wRLPValue = (buyTokensOwed * 424) / 100; // rough price estimate
        console.log("waUSDC value (remaining):", waUSDCValue / 1e6);
        console.log("wRLP value @ $4.24:     ", wRLPValue / 1e6);
        console.log("Total estimated:        ", (waUSDCValue + wRLPValue) / 1e6);
    }
}
