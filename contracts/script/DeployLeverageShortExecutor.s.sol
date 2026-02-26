// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {LeverageShortExecutor} from "../src/periphery/LeverageShortExecutor.sol";

contract DeployLeverageShortExecutor is Script {
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");

        vm.startBroadcast(deployerKey);

        LeverageShortExecutor executor = new LeverageShortExecutor(V4_POOL_MANAGER);

        console.log("LEVERAGE_SHORT_EXECUTOR=%s", address(executor));

        vm.stopBroadcast();
    }
}
