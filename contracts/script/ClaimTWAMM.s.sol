// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IJTM} from "../src/twamm/IJTM.sol";

/**
 * @title ClaimTWAMM
 * @notice Claim TWAMM order proceeds
 */
contract ClaimTWAMM is Script {
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    function run() external {
        console.log("====== CLAIM TWAMM PROCEEDS ======");

        address token0 = vm.envAddress("TOKEN0");
        address token1 = vm.envAddress("TOKEN1");
        address hook = vm.envAddress("TWAMM_HOOK");
        uint256 userKey = vm.envUint("CLAIM_USER_KEY");

        address user = vm.addr(userKey);

        require(token0 < token1, "TOKEN0 must be < TOKEN1");

        console.log("Token0:", token0);
        console.log("Token1:", token1);
        console.log("Hook:", hook);
        console.log("User:", user);

        // Build pool key
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(hook)
        });

        // Get balances before
        uint256 bal0Before = ERC20(token0).balanceOf(user);
        uint256 bal1Before = ERC20(token1).balanceOf(user);

        console.log("");
        console.log("=== BALANCES BEFORE ===");
        console.log("Token0:", bal0Before);
        console.log("Token1:", bal1Before);

        // Claim tokens
        vm.startBroadcast(userKey);
        IJTM(hook).claimTokens(poolKey, Currency.wrap(token0));
        IJTM(hook).claimTokens(poolKey, Currency.wrap(token1));
        vm.stopBroadcast();

        // Get balances after
        uint256 bal0After = ERC20(token0).balanceOf(user);
        uint256 bal1After = ERC20(token1).balanceOf(user);

        console.log("");
        console.log("=== BALANCES AFTER ===");
        console.log("Token0:", bal0After);
        console.log("Token1:", bal1After);

        int256 change0 = int256(bal0After) - int256(bal0Before);
        int256 change1 = int256(bal1After) - int256(bal1Before);

        console.log("");
        console.log("=== CLAIMED ===");
        console.log("Token0 change:", change0);
        console.log("Token1 change:", change1);

        if (change0 > 0 || change1 > 0) {
            console.log("SUCCESS: Tokens claimed!");
        } else {
            console.log("No tokens to claim (order may still be active)");
        }
    }
}
