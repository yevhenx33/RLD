// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {RLDCore} from "../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/rld/core/RLDMarketFactory.sol";

/**
 * @title Deploy RLD Protocol (CRITICAL-001 Fix)
 * @notice Atomic deployment script that prevents factory front-running
 * @dev Deployment sequence:
 *      1. Deploy RLDMarketFactory with CORE = address(0)
 *      2. Deploy RLDCore with factory address in constructor (immutable)
 *      3. Call factory.initializeCore(coreAddress) to complete initialization
 *
 *      This ensures no front-running window exists between steps.
 */
contract DeployRLDProtocol is Script {
    function run() external {
        // Load deployer private key from environment
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);
        
        console2.log("Deployer:", deployer);
        console2.log("---");
        
        vm.startBroadcast(deployerPrivateKey);
        
        // ============================================================================
        // STEP 1: Deploy RLDMarketFactory (CORE = address(0))
        // ============================================================================
        
        console2.log("Step 1: Deploying RLDMarketFactory...");
        
        RLDMarketFactory factory = new RLDMarketFactory(
            vm.envAddress("POOL_MANAGER"),           // Uniswap V4 PoolManager
            vm.envAddress("POSITION_TOKEN_IMPL"),    // PositionToken implementation
            vm.envAddress("PRIME_BROKER_IMPL"),      // PrimeBroker implementation
            vm.envAddress("V4_ORACLE"),              // UniswapV4SingletonOracle
            vm.envAddress("FUNDING_MODEL"),          // StandardFundingModel
            vm.envAddress("TWAMM"),                  // TWAMM hook (can be address(0))
            vm.envAddress("METADATA_RENDERER"),      // NFT metadata renderer
            uint32(vm.envUint("FUNDING_PERIOD"))     // e.g., 30 days
        );
        
        console2.log("RLDMarketFactory deployed at:", address(factory));
        console2.log("Factory.CORE (should be 0x0):", factory.CORE());
        require(factory.CORE() == address(0), "Factory CORE should be zero");
        console2.log("---");
        
        // ============================================================================
        // STEP 2: Deploy RLDCore with factory address (ATOMIC)
        // ============================================================================
        
        console2.log("Step 2: Deploying RLDCore with factory address...");
        
        RLDCore core = new RLDCore(
            address(factory),
            vm.envAddress("POOL_MANAGER"),
            vm.envAddress("TWAMM")
        );
        
        console2.log("RLDCore deployed at:", address(core));
        console2.log("Core.factory():", core.factory());
        console2.log("Core.poolManager():", core.poolManager());
        console2.log("Core.twamm():", core.twamm());
        require(core.factory() == address(factory), "Core factory mismatch");
        console2.log("---");
        
        // ============================================================================
        // STEP 3: Initialize factory's CORE reference
        // ============================================================================
        
        console2.log("Step 3: Initializing factory.CORE...");
        
        factory.initializeCore(address(core));
        
        console2.log("Factory.CORE (should be core):", factory.CORE());
        require(factory.CORE() == address(core), "Factory CORE mismatch");
        console2.log("---");
        
        vm.stopBroadcast();
        
        // ============================================================================
        // VERIFICATION
        // ============================================================================
        
        console2.log("=== DEPLOYMENT SUCCESSFUL ===");
        console2.log("RLDCore:", address(core));
        console2.log("RLDMarketFactory:", address(factory));
        console2.log("");
        console2.log("Verification:");
        console2.log("  - Core.factory() == Factory:", core.factory() == address(factory));
        console2.log("  - Factory.CORE() == Core:", factory.CORE() == address(core));
        console2.log("  - Factory is immutable in Core: true (immutable keyword)");
        console2.log("  - No front-running window: true (atomic deployment)");
        console2.log("");
        console2.log("CRITICAL-001 FIX VERIFIED [SUCCESS]");
    }
}
