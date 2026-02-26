// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {RLDDeployConfig as C} from "../src/shared/config/RLDDeployConfig.sol";

// Core
import {RLDCore} from "../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/rld/core/RLDMarketFactory.sol";

// Templates
import {PositionToken} from "../src/rld/tokens/PositionToken.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";

// Modules
import {
    DutchLiquidationModule
} from "../src/rld/modules/liquidation/DutchLiquidationModule.sol";
import {
    StandardFundingModel
} from "../src/rld/modules/funding/StandardFundingModel.sol";
import {
    UniswapV4SingletonOracle
} from "../src/rld/modules/oracles/UniswapV4SingletonOracle.sol";
import {RLDAaveOracle} from "../src/rld/modules/oracles/RLDAaveOracle.sol";
import {
    UniswapV4BrokerModule
} from "../src/rld/modules/broker/UniswapV4BrokerModule.sol";
import {JTMBrokerModule} from "../src/rld/modules/broker/JTMBrokerModule.sol";
import {JTM} from "../src/twamm/JTM.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {HookMiner} from "v4-periphery/src/utils/HookMiner.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";

// Periphery
import {BrokerRouter} from "../src/periphery/BrokerRouter.sol";

/// @notice Minimal metadata renderer that returns empty strings (satisfies non-zero check)
contract MinimalMetadataRenderer {
    function tokenURI(uint256) external pure returns (string memory) {
        return "";
    }
}

/// @notice Minimal valuation module placeholder (for PrimeBroker constructor)
contract MinimalValuationModule {
    function getValue(bytes calldata) external pure returns (uint256) {
        return 0;
    }
}

/**
 * @title DeployRLDProtocol
 * @notice Deploys the full RLD Protocol to an Anvil Mainnet Fork
 * @dev Run with: forge script script/DeployRLDProtocol.s.sol --rpc-url http://127.0.0.1:8545 --broadcast
 */
contract DeployRLDProtocol is Script {
    // ============================================
    // DEPLOYED ADDRESSES (filled during deployment)
    // ============================================

    // Helpers
    address public metadataRenderer;
    address public v4ValuationModule;
    address public twammValuationModule;

    // Templates
    address public positionTokenImpl;
    address public primeBrokerImpl;

    // Modules
    address public dutchLiquidationModule;
    address public standardFundingModel;
    address public v4Oracle;
    address public rldAaveOracle;

    // Periphery
    address public brokerRouter;

    // Core
    address public rldMarketFactory;
    address public rldCore;

    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);

        console.log("========================================");
        console.log("RLD PROTOCOL DEPLOYMENT");
        console.log("========================================");
        console.log("Deployer:", deployer);
        console.log("Balance:", deployer.balance / 1e18, "ETH");
        console.log("");

        vm.startBroadcast(deployerPrivateKey);

        // ============================================
        // PHASE 0: Deploy Helper Contracts
        // ============================================
        console.log("PHASE 0: Helper Contracts");
        console.log("----------------------------------");

        MinimalMetadataRenderer renderer = new MinimalMetadataRenderer();
        metadataRenderer = address(renderer);
        console.log("MetadataRenderer:", metadataRenderer);

        UniswapV4BrokerModule v4Module = new UniswapV4BrokerModule();
        v4ValuationModule = address(v4Module);
        console.log("V4 Valuation Module:", v4ValuationModule);

        JTMBrokerModule twammModule = new JTMBrokerModule();
        twammValuationModule = address(twammModule);
        console.log("TWAMM Valuation Module:", twammValuationModule);

        console.log("");

        // TWAMM Hook (Core set to address(0) initially)
        // Mine for available hook address
        uint160 flags = uint160(
            Hooks.BEFORE_INITIALIZE_FLAG |
                Hooks.BEFORE_ADD_LIQUIDITY_FLAG |
                Hooks.BEFORE_REMOVE_LIQUIDITY_FLAG |
                Hooks.BEFORE_SWAP_FLAG |
                Hooks.AFTER_SWAP_FLAG |
                Hooks.BEFORE_SWAP_RETURNS_DELTA_FLAG
        );

        bytes memory creationCode = type(JTM).creationCode;
        bytes memory constructorArgs = abi.encode(
            IPoolManager(C.POOL_MANAGER),
            C.TWAMM_EXPIRATION_INTERVAL,
            deployer,
            address(0) // Core logic initialized later
        );

        console.log("Mining TWAMM salt...");
        (address hookAddress, bytes32 salt) = HookMiner.find(
            C.CREATE2_DEPLOYER,
            flags,
            creationCode,
            constructorArgs
        );
        console.log("Mined salt:", vm.toString(salt));

        JTM twammHook = new JTM{salt: salt}(
            IPoolManager(C.POOL_MANAGER),
            C.TWAMM_EXPIRATION_INTERVAL,
            deployer,
            address(0) // Core logic initialized later
        );
        require(address(twammHook) == hookAddress, "Hook address mismatch");

        address twammAddress = address(twammHook);
        console.log("TWAMM Hook:", twammAddress);

        console.log("");

        // ============================================
        // PHASE 1: Deploy Singleton Modules
        // ============================================
        console.log("PHASE 1: Singleton Modules");
        console.log("----------------------------------");

        // DutchLiquidationModule
        DutchLiquidationModule liqModule = new DutchLiquidationModule();
        dutchLiquidationModule = address(liqModule);
        console.log("DutchLiquidationModule:", dutchLiquidationModule);

        // StandardFundingModel
        StandardFundingModel fundingModel = new StandardFundingModel();
        standardFundingModel = address(fundingModel);
        console.log("StandardFundingModel:", standardFundingModel);

        // UniswapV4SingletonOracle
        UniswapV4SingletonOracle oracle = new UniswapV4SingletonOracle();
        v4Oracle = address(oracle);
        console.log("UniswapV4SingletonOracle:", v4Oracle);

        // RLDAaveOracle (for rate/index prices)
        RLDAaveOracle aaveOracle = new RLDAaveOracle();
        rldAaveOracle = address(aaveOracle);
        console.log("RLDAaveOracle:", rldAaveOracle);

        console.log("");

        // ============================================
        // PHASE 2: Deploy Implementation Templates
        // ============================================
        console.log("PHASE 2: Implementation Templates");
        console.log("----------------------------------");

        // PositionToken Implementation (dummy - will be deployed fresh per market)
        // The factory checks for non-zero but doesn't actually clone it
        PositionToken positionTokenImplContract = new PositionToken(
            "Implementation",
            "IMPL",
            18,
            address(1) // Placeholder collateral
        );
        positionTokenImpl = address(positionTokenImplContract);
        console.log("PositionToken Impl:", positionTokenImpl);

        // PrimeBroker needs CORE, but CORE needs Factory which needs PrimeBroker impl
        // PrimeBroker implementation - CORE is now set in initialize()
        PrimeBroker primeBrokerImplContract = new PrimeBroker(
            v4ValuationModule, // _v4Module
            twammValuationModule, // _twammModule
            C.POSITION_MANAGER // _posm
        );
        primeBrokerImpl = address(primeBrokerImplContract);
        console.log("PrimeBroker Impl:", primeBrokerImpl);

        console.log("");

        // ============================================
        // PHASE 2.5: Deploy BrokerRouter
        // ============================================
        console.log("PHASE 2.5: BrokerRouter");
        console.log("----------------------------------");

        BrokerRouter router = new BrokerRouter(
            C.POOL_MANAGER, // poolManager
            C.PERMIT2 // permit2
        );
        brokerRouter = address(router);
        console.log("BrokerRouter:", brokerRouter);

        console.log("");

        // ============================================
        // PHASE 3: Deploy Factory
        // ============================================
        console.log("PHASE 3: Market Factory");
        console.log("----------------------------------");

        RLDMarketFactory factory = new RLDMarketFactory(
            C.POOL_MANAGER, // poolManager
            positionTokenImpl, // positionTokenImpl
            primeBrokerImpl, // primeBrokerImpl
            v4Oracle, // v4Oracle
            standardFundingModel, // fundingModel
            twammAddress, // twamm (real contract)
            metadataRenderer, // metadataRenderer
            C.FUNDING_PERIOD, // fundingPeriod
            brokerRouter // brokerRouter (pre-approved operator on every broker)
        );
        rldMarketFactory = address(factory);
        console.log("RLDMarketFactory:", rldMarketFactory);

        console.log("");

        // ============================================
        // PHASE 4: Deploy Core
        // ============================================
        console.log("PHASE 4: RLD Core");
        console.log("----------------------------------");

        RLDCore core = new RLDCore(
            rldMarketFactory, // factory
            C.POOL_MANAGER, // poolManager
            twammAddress // twamm (real contract)
        );
        rldCore = address(core);
        console.log("RLDCore:", rldCore);

        console.log("");

        // ============================================
        // PHASE 5: Initialize Factory <-> Core Link
        // ============================================
        console.log("PHASE 5: Initialize Links");
        console.log("----------------------------------");

        factory.initializeCore(rldCore);
        console.log("Factory linked to Core: OK");

        twammHook.setRldCore(rldCore);
        console.log("TWAMM linked to Core: OK");

        vm.stopBroadcast();

        // ============================================
        // SUMMARY
        // ============================================
        console.log("");
        console.log("========================================");
        console.log("DEPLOYMENT COMPLETE!");
        console.log("========================================");
        console.log("");
        console.log("Core Protocol:");
        console.log("  RLDCore:", rldCore);
        console.log("  RLDMarketFactory:", rldMarketFactory);
        console.log("");
        console.log("Modules:");
        console.log("  DutchLiquidationModule:", dutchLiquidationModule);
        console.log("  StandardFundingModel:", standardFundingModel);
        console.log("  UniswapV4SingletonOracle:", v4Oracle);
        console.log("  RLDAaveOracle:", rldAaveOracle);
        console.log("");
        console.log("External (Mainnet):");
        console.log("  Uniswap PoolManager:", C.POOL_MANAGER);
        console.log("  Aave Pool:", C.AAVE_POOL);
        console.log("  aUSDC:", C.AUSDC);
        console.log("  USDC:", C.USDC);

        // ============================================
        // EXPORT ADDRESSES
        // ============================================
        string memory jsonObj = "deployment";
        vm.serializeAddress(jsonObj, "RLDCore", rldCore);
        vm.serializeAddress(jsonObj, "RLDMarketFactory", rldMarketFactory);
        vm.serializeAddress(
            jsonObj,
            "DutchLiquidationModule",
            dutchLiquidationModule
        );
        vm.serializeAddress(
            jsonObj,
            "StandardFundingModel",
            standardFundingModel
        );
        vm.serializeAddress(jsonObj, "UniswapV4SingletonOracle", v4Oracle);
        vm.serializeAddress(jsonObj, "RLDAaveOracle", rldAaveOracle);
        vm.serializeAddress(jsonObj, "JTMBrokerModule", twammValuationModule);
        vm.serializeAddress(
            jsonObj,
            "UniswapV4BrokerModule",
            v4ValuationModule
        );
        vm.serializeAddress(jsonObj, "TWAMM", twammAddress);
        vm.serializeAddress(jsonObj, "PrimeBrokerImpl", primeBrokerImpl);
        vm.serializeAddress(jsonObj, "PositionTokenImpl", positionTokenImpl);
        vm.serializeAddress(jsonObj, "BrokerRouter", brokerRouter);

        string memory finalJson = vm.serializeAddress(
            jsonObj,
            "MetadataRenderer",
            metadataRenderer
        );

        vm.writeJson(finalJson, "./deployments.json");
        console.log("Addresses saved to ./deployments.json");
    }
}
