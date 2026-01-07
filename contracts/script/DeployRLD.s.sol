// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/oracles/RLDAaveOracle.sol";
import "../src/oracles/SymbioticRateOracle.sol";

contract DeployRLD is Script {
    function run() external {
        // Load the private key from .env (for local testing, we'll use Anvil's default)
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address operator = vm.addr(deployerPrivateKey);

        vm.startBroadcast(deployerPrivateKey);

        // 1. Deploy the standardized Spot Oracle (reads Aave)
        RLDAaveOracle spotOracle = new RLDAaveOracle();
        console.log("RLDAaveOracle deployed at:", address(spotOracle));

        // 2. Deploy the Symbiotic Oracle (validates Operator signatures)
        SymbioticRateOracle symbioticOracle = new SymbioticRateOracle(
            address(spotOracle),
            operator
        );
        console.log(
            "SymbioticRateOracle deployed at:",
            address(symbioticOracle)
        );
        console.log("Operator set to:", operator);

        vm.stopBroadcast();
    }
}
