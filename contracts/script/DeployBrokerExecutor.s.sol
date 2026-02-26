// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {BrokerExecutor} from "../src/periphery/BrokerExecutor.sol";

contract DeployBrokerExecutor is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");

        vm.startBroadcast(deployerKey);

        BrokerExecutor executor = new BrokerExecutor();

        vm.stopBroadcast();

        console.log("=== BrokerExecutor Deployed ===");
        console.log("EXECUTOR_ADDRESS=%s", address(executor));
    }
}
