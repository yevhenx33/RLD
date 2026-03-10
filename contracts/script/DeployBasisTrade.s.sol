// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {BasisTradeFactory} from "../src/periphery/BasisTradeFactory.sol";
import {RLDDeployConfig as C} from "../src/shared/config/RLDDeployConfig.sol";

/**
 * @title DeployBasisTrade
 * @notice Deploys the BasisTradeFactory (Flash Loan edition) for sUSDe Basis Trade
 *
 * @dev Constructor signature:
 *   BasisTradeFactory(
 *     brokerFactory, twammHook, collateral, poolManager,
 *     morpho, sUsde, usde, usdc, pyusd,
 *     curveUsdeUsdcPool, curvePyusdUsdcPool,
 *     curveUsdeIndex, curveUsdcIndexUsde,
 *     curvePyusdIndex, curveUsdcIndexPyusd,
 *     morphoOracle, morphoIrm, morphoLltv
 *   )
 */
contract DeployBasisTrade is Script {
    function run() external {
        // Read from the docker deployment output
        string memory json = vm.readFile("../docker/deployment.json");

        address twammAddr = vm.parseJsonAddress(json, ".twamm_hook");
        address brokerFactoryAddr = vm.parseJsonAddress(
            json,
            ".broker_factory"
        );
        address collateral = vm.parseJsonAddress(json, ".wausdc");

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        vm.startBroadcast(deployerKey);

        console.log("");
        console.log("=== DEPLOYING BASIS TRADE FACTORY (Flash Loan) ===");
        console.log("Deployer:", deployer);
        console.log("BrokerFactory:", brokerFactoryAddr);
        console.log("TWAMM Hook:", twammAddr);
        console.log("Collateral:", collateral);

        // ── Token addresses (Ethereum Mainnet) ──
        address morpho = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb;
        address susde = 0x9D39A5DE30e57443BfF2A8307A4256c8797A3497;
        address usde = 0x4c9EDD5852cd905f086C759E8383e09bff1E68B3;
        address usdc = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
        address pyusd = 0x6c3ea9036406852006290770BEdFcAbA0e23A0e8;

        // ── Curve pools ──
        address curveUsdeUsdcPool = 0x02950460E2b9529D0E00284A5fA2d7bDF3fA4d72;
        address curvePyusdUsdcPool = 0x383E6b4437b59fff47B619CBA855CA29342A8559;

        // ── Morpho sUSDe/PYUSD market params ──
        address morphoOracle = 0xE6212D05cB5aF3C821Fef1C1A233a678724F9E7E;
        address morphoIrm = 0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC;
        uint256 morphoLltv = 915000000000000000; // 91.5%

        // Deploy the BasisTradeFactory
        BasisTradeFactory basisTradeFactory = new BasisTradeFactory(
            brokerFactoryAddr,
            twammAddr,
            collateral,
            C.POOL_MANAGER,
            // Morpho Blue
            morpho,
            susde,
            usde,
            usdc,
            pyusd,
            // Curve pools
            curveUsdeUsdcPool,
            curvePyusdUsdcPool,
            int128(0), // curveUsdeIndex
            int128(1), // curveUsdcIndexUsde
            int128(0), // curvePyusdIndex
            int128(1), // curveUsdcIndexPyusd
            // Morpho market params
            morphoOracle,
            morphoIrm,
            morphoLltv
        );

        console.log("");
        console.log("=== BASIS TRADE FACTORY DEPLOYED ===");
        console.log("Address:", address(basisTradeFactory));

        vm.stopBroadcast();

        // Save deployment
        string memory finalJson = vm.serializeAddress(
            "deployment",
            "BasisTradeFactory",
            address(basisTradeFactory)
        );
        vm.writeJson(finalJson, "../docker/basis_trade_deployment.json");
        console.log("Address saved to ../docker/basis_trade_deployment.json");
    }
}
