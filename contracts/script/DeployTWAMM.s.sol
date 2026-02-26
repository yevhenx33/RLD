// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {HookMiner} from "v4-periphery/src/utils/HookMiner.sol";
import {JTM} from "../src/twamm/JTM.sol";

/**
 * @title DeployTWAMM
 * @notice Deploys the TWAMM Hook using CREATE2 salt mining
 * @dev TWAMM hooks require specific address prefix bits to encode hook permissions
 *      Uses HookMiner to find a salt that produces a valid hook address
 *
 * Run with: forge script script/DeployTWAMM.s.sol:DeployTWAMM --rpc-url http://127.0.0.1:8545 --broadcast -vvv
 */
contract DeployTWAMM is Script {
    // Standard CREATE2 deployer used by Foundry broadcast
    address constant CREATE2_DEPLOYER = address(0x4e59b44847b379578588920cA78FbF26c0B4956C);

    // Mainnet Uniswap V4 PoolManager
    address constant UNISWAP_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    // RLDCore (from previous deployment)
    address constant RLD_CORE = 0xAaC7D4A36DAb95955ef3c641c23F1fA46416CF71;

    // TWAMM Config
    uint256 constant EXPIRATION_INTERVAL = 1 hours; // 3600 seconds

    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);

        console.log("========================================");
        console.log("TWAMM HOOK DEPLOYMENT");
        console.log("========================================");
        console.log("Deployer:", deployer);
        console.log("CREATE2 Deployer:", CREATE2_DEPLOYER);
        console.log("Balance:", deployer.balance / 1e18, "ETH");
        console.log("");

        // TWAMM Hook Permissions
        // These must match getHookPermissions() in TWAMM.sol
        uint160 flags = uint160(
            Hooks.BEFORE_INITIALIZE_FLAG | Hooks.BEFORE_ADD_LIQUIDITY_FLAG | Hooks.BEFORE_REMOVE_LIQUIDITY_FLAG
                | Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG
        );

        console.log("Hook Permission Flags:", uint256(flags));
        console.log("");

        // Create constructor arguments
        bytes memory creationCode = type(JTM).creationCode;
        bytes memory constructorArgs = abi.encode(
            IPoolManager(UNISWAP_POOL_MANAGER),
            EXPIRATION_INTERVAL,
            deployer, // initialOwner
            RLD_CORE // rldCore
        );

        console.log("Mining for hook address with correct prefix...");
        console.log("(This may take a moment)");

        // Mine for the correct salt using the CREATE2 deployer
        (address hookAddress, bytes32 salt) =
            HookMiner.find(
                CREATE2_DEPLOYER, // Use the standard CREATE2 deployer
                flags,
                creationCode,
                constructorArgs
            );

        console.log("");
        console.log("Found valid hook address:", hookAddress);
        console.log("Salt:", vm.toString(salt));
        console.log("");

        vm.broadcast();

        // Deploy with the mined salt
        JTM twamm = new JTM{salt: salt}(IPoolManager(UNISWAP_POOL_MANAGER), EXPIRATION_INTERVAL, deployer, RLD_CORE);

        // Verify address matches
        require(address(twamm) == hookAddress, "Hook address mismatch!");

        console.log("========================================");
        console.log("TWAMM DEPLOYMENT COMPLETE!");
        console.log("========================================");
        console.log("");
        console.log("TWAMM Hook Address:", address(twamm));
        console.log("Expiration Interval:", EXPIRATION_INTERVAL, "seconds (1 hour)");
        console.log("Owner:", deployer);
        console.log("RLDCore:", RLD_CORE);
        console.log("");
        console.log("Add this to addresses.json:");
        console.log('  "TWAMM": "', address(twamm), '"');
    }
}
