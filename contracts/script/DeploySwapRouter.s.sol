// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";

// Import the router from LifecycleSwap.s.sol
import {LifecycleSwapRouter} from "./LifecycleSwap.s.sol";

/**
 * @title DeploySwapRouter
 * @notice Deploy LifecycleSwapRouter once for reuse across all swaps
 */
contract DeploySwapRouter is Script {
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_KEY");

        vm.startBroadcast(deployerKey);

        LifecycleSwapRouter router = new LifecycleSwapRouter(IPoolManager(V4_POOL_MANAGER));

        vm.stopBroadcast();

        console.log("SWAP_ROUTER:", address(router));
    }
}
