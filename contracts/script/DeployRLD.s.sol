// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/oracles/RLDAaveOracle.sol";
import "../src/oracles/SymbioticRateOracle.sol";
import "./OracleConfig.s.sol";

contract DeployRLD is Script, OracleConfig {
    function run() external {
        // Load Network Config
        NetworkConfig memory config = getNetworkConfig();
        
        // Select Asset to Deploy Oracle For (Defaulting to USDC)
        // You can change this line to config.usdt, config.dai, etc.
        address targetAsset = config.usdc;

        // Load the private key from .env
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address operator = vm.addr(deployerPrivateKey);

        vm.startBroadcast(deployerPrivateKey);

        // 1. Deploy the standardized Spot Oracle (reads Aave)
        RLDAaveOracle spotOracle = new RLDAaveOracle(
            config.aavePool,
            targetAsset
        );
        console.log("RLDAaveOracle deployed at:", address(spotOracle));
        console.log("Tracking Asset:", targetAsset);

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

        // 3. Export Addresses to JSON
        string memory obj = "key";
        string memory addressesJson = vm.serializeAddress(obj, "RLDAaveOracle", address(spotOracle));
        addressesJson = vm.serializeAddress(obj, "SymbioticRateOracle", address(symbioticOracle));
        addressesJson = vm.serializeAddress(obj, "Operator", operator);
        
        string memory path = "../shared/addresses.json";
        vm.writeFile(path, addressesJson);
        console.log("Addresses written to shared/addresses.json");
    }
}
