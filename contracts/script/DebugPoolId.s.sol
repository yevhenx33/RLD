// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {PoolKey} from "v4-core/types/PoolKey.sol";
import {PoolIdLibrary, PoolId} from "v4-core/types/PoolId.sol";
import {Currency} from "v4-core/types/Currency.sol";
import {IHooks} from "v4-core/interfaces/IHooks.sol";

contract DebugPoolId is Script {
    function run() external view {
        address token0 = vm.envAddress("TOKEN0");
        address token1 = vm.envAddress("TOKEN1");
        address twamm = vm.envAddress("TWAMM_HOOK");
        
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(twamm)
        });
        
        PoolId poolId = key.toId();
        console.log("POOL_ID:", uint256(PoolId.unwrap(poolId)));
        console.logBytes32(PoolId.unwrap(poolId));
    }
}
