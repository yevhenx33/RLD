// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";

abstract contract OracleConfig is Script {
    struct NetworkConfig {
        address aavePool;
        address usdc;
        address usdt;
        address dai;
        address wbtc;
        address weth;
    }

    NetworkConfig public activeNetworkConfig;

    function getNetworkConfig() public returns (NetworkConfig memory) {
        // Mainnet Chain ID: 1
        if (block.chainid == 1) {
            activeNetworkConfig = NetworkConfig({
                aavePool: 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2,
                usdc: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48,
                usdt: 0xdAC17F958D2ee523a2206206994597C13D831ec7,
                dai: 0x6B175474E89094C44Da98b954EedeAC495271d0F,
                wbtc: 0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599,
                weth: 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
            });
        } 
        // Chain ID 31337 is Anvil/Localhost
        else if (block.chainid == 31337) {
             // For testing on Anvil fork, we behave like Mainnet
             activeNetworkConfig = NetworkConfig({
                aavePool: 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2,
                usdc: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48,
                usdt: 0xdAC17F958D2ee523a2206206994597C13D831ec7,
                dai: 0x6B175474E89094C44Da98b954EedeAC495271d0F,
                wbtc: 0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599,
                weth: 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
            });
        }
        else {
            revert("Unsupported Network");
        }
        return activeNetworkConfig;
    }
}
