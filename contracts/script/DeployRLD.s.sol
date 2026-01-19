// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/core/RLDCore.sol";
import "../src/core/RLDMarketFactory.sol";
import "../src/modules/oracles/RLDAaveOracle.sol";
import "../src/modules/oracles/ChainlinkSpotOracle.sol";
import "../src/modules/oracles/DefaultOracle.sol";
import "../src/modules/funding/StandardFundingModel.sol";
import "../src/modules/liquidation/DutchLiquidationModule.sol";

contract DeployRLD is Script {
    struct NetworkConfig {
        address aavePool;
        address usdc;
        address usdt;
        address dai;
        address wbtc;
        address weth;
    }

    function getNetworkConfig() public view returns (NetworkConfig memory) {
        if (block.chainid == 1 || block.chainid == 31337) {
            return NetworkConfig({
                aavePool: 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2,
                usdc: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48,
                usdt: 0xdAC17F958D2ee523a2206206994597C13D831ec7,
                dai: 0x6B175474E89094C44Da98b954EedeAC495271d0F,
                wbtc: 0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599,
                weth: 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
            });
        } 
        revert("Active Network Not Found");
    }

    function run() external {
        // Load Network Config
        NetworkConfig memory config = getNetworkConfig();
        // address targetAsset = config.usdc;

        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        // address operator = vm.addr(deployerPrivateKey);

        vm.startBroadcast(deployerPrivateKey);

        // 1. Deploy Oracles & Models
        RLDAaveOracle rateOracle = new RLDAaveOracle(); 
        ChainlinkSpotOracle spotOracle = new ChainlinkSpotOracle();
        DefaultOracle defaultOracle = new DefaultOracle();
        StandardFundingModel fundingModel = new StandardFundingModel();

        // 2. Deploy Modules
        DutchLiquidationModule dutchModule = new DutchLiquidationModule();

        // 3. Deploy Core
        RLDCore core = new RLDCore();

        // 4. Deploy Factory
        RLDMarketFactory factory = new RLDMarketFactory(
            address(core), 
            address(fundingModel), 
            address(spotOracle),
            address(rateOracle),
            address(defaultOracle)
        );

        console.log("RLD Core deployed at:", address(core));
        console.log("RLD Factory deployed at:", address(factory));
        console.log("Dutch Liquidation Module:", address(dutchModule));

        vm.stopBroadcast();

        // 5. Export Addresses
        string memory obj = "key";
        string memory addressesJson = vm.serializeAddress(obj, "RLDCore", address(core));
        addressesJson = vm.serializeAddress(obj, "RLDMarketFactory", address(factory));
        addressesJson = vm.serializeAddress(obj, "RLDAaveOracle", address(rateOracle));
        addressesJson = vm.serializeAddress(obj, "DefaultOracle", address(defaultOracle));
        addressesJson = vm.serializeAddress(obj, "DutchLiquidationModule", address(dutchModule));
        
        string memory path = "../shared/addresses.json";
        vm.writeFile(path, addressesJson);
    }
}
