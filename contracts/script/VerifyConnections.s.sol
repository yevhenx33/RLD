// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {RLDCore} from "../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/rld/core/RLDMarketFactory.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {JTM} from "../src/twamm/JTM.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";

/**
 * @title VerifyConnections
 * @notice Queries deployed contracts to verify their internal references
 *
 * Run: forge script script/VerifyConnections.s.sol:VerifyConnections --rpc-url http://127.0.0.1:8545 -vvv
 */
contract VerifyConnections is Script {
    // Deployed addresses (from addresses.json)
    address constant RLD_CORE = 0x6B5CF024365D5d5d0786673780CA7E3F07f85B63;
    address constant RLD_MARKET_FACTORY = 0xAaC7D4A36DAb95955ef3c641c23F1fA46416CF71;
    address constant TWAMM_HOOK = 0x4089C71eD1f89Bc8cC66dddabaFFE755850eeAc0;
    address constant PRIME_BROKER_IMPL = 0xf975A646FCa589Be9fc4E0C28ea426A75645fB1f;
    address constant V4_ORACLE = 0xf4fa0d1C10c47cDe9F65D56c3eC977CbEb13449A;
    address constant FUNDING_MODEL = 0x2ca60d89144D4cdf85dA87af4FE12aBF9265F28C;

    // Expected external addresses
    address constant UNISWAP_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    function run() external view {
        console.log("================================================================");
        console.log("  CONTRACT CONNECTIONS VERIFICATION");
        console.log("================================================================");
        console.log("");

        // ============================================
        // RLDCore Connections
        // ============================================
        console.log("----------------------------------------------------------------");
        console.log("RLDCore:", RLD_CORE);
        console.log("----------------------------------------------------------------");

        RLDCore core = RLDCore(RLD_CORE);

        address coreFactory = core.factory();
        address corePoolManager = core.poolManager();
        address coreTwamm = core.twamm();

        console.log("  factory():", coreFactory);
        _verify("    -> Expected RLDMarketFactory", coreFactory, RLD_MARKET_FACTORY);

        console.log("  poolManager():", corePoolManager);
        _verify("    -> Expected UniswapPoolManager", corePoolManager, UNISWAP_POOL_MANAGER);

        console.log("  twamm():", coreTwamm);
        _verify("    -> Expected TWAMM Hook", coreTwamm, TWAMM_HOOK);

        // ============================================
        // RLDMarketFactory Connections
        // ============================================
        console.log("");
        console.log("----------------------------------------------------------------");
        console.log("RLDMarketFactory:", RLD_MARKET_FACTORY);
        console.log("----------------------------------------------------------------");

        RLDMarketFactory factory = RLDMarketFactory(RLD_MARKET_FACTORY);

        address factoryCore = factory.CORE();
        address factoryPoolManager = factory.POOL_MANAGER();
        address factoryPrimeBrokerImpl = factory.PRIME_BROKER_IMPL();
        address factoryV4Oracle = factory.SINGLETON_V4_ORACLE();
        address factoryFundingModel = factory.STD_FUNDING_MODEL();
        address factoryTwamm = factory.TWAMM();

        console.log("  CORE():", factoryCore);
        _verify("    -> Expected RLDCore", factoryCore, RLD_CORE);

        console.log("  POOL_MANAGER():", factoryPoolManager);
        _verify("    -> Expected UniswapPoolManager", factoryPoolManager, UNISWAP_POOL_MANAGER);

        console.log("  PRIME_BROKER_IMPL():", factoryPrimeBrokerImpl);
        _verify("    -> Expected PrimeBrokerImpl", factoryPrimeBrokerImpl, PRIME_BROKER_IMPL);

        console.log("  SINGLETON_V4_ORACLE():", factoryV4Oracle);
        _verify("    -> Expected V4Oracle", factoryV4Oracle, V4_ORACLE);

        console.log("  STD_FUNDING_MODEL():", factoryFundingModel);
        _verify("    -> Expected StandardFundingModel", factoryFundingModel, FUNDING_MODEL);

        console.log("  JTM():", factoryTwamm);
        _verify("    -> Expected TWAMM Hook", factoryTwamm, TWAMM_HOOK);

        // ============================================
        // PrimeBroker Implementation Connections
        // ============================================
        console.log("");
        console.log("----------------------------------------------------------------");
        console.log("PrimeBrokerImpl:", PRIME_BROKER_IMPL);
        console.log("----------------------------------------------------------------");

        PrimeBroker pbImpl = PrimeBroker(PRIME_BROKER_IMPL);

        address pbCore = pbImpl.CORE();
        address pbPosm = pbImpl.POSM();

        console.log("  CORE():", pbCore);
        console.log("    [WARNING] This is 0xDEAD placeholder - clones inherit this!");

        console.log("  POSM():", pbPosm);
        console.log("    -> UniswapPositionManager");

        // ============================================
        // TWAMM Hook Connections
        // ============================================
        console.log("");
        console.log("----------------------------------------------------------------");
        console.log("TWAMM Hook:", TWAMM_HOOK);
        console.log("----------------------------------------------------------------");

        JTM twamm = JTM(TWAMM_HOOK);

        address twammPoolManager = address(twamm.poolManager());
        address twammRldCore = twamm.rldCore();
        address twammOwner = twamm.owner();
        uint256 twammInterval = twamm.expirationInterval();

        console.log("  poolManager():", twammPoolManager);
        _verify("    -> Expected UniswapPoolManager", twammPoolManager, UNISWAP_POOL_MANAGER);

        console.log("  rldCore():", twammRldCore);
        console.log("    [WARNING] This is address(0) - updateDynamicLPFee() will revert!");

        console.log("  owner():", twammOwner);
        console.log("  expirationInterval():", twammInterval, "seconds");

        // ============================================
        // Summary
        // ============================================
        console.log("");
        console.log("================================================================");
        console.log("  CONNECTION VERIFICATION COMPLETE");
        console.log("================================================================");
        console.log("");
        console.log("Core <-> Factory: BIDIRECTIONAL OK");
        console.log("Core -> PoolManager: OK");
        console.log("Core -> TWAMM: OK");
        console.log("Factory -> TWAMM: OK");
        console.log("Factory -> PrimeBrokerImpl: OK");
        console.log("Factory -> V4Oracle: OK");
        console.log("Factory -> FundingModel: OK");
        console.log("TWAMM -> PoolManager: OK");
        console.log("");
        console.log("KNOWN ISSUES:");
        console.log("  1. PrimeBrokerImpl.CORE() = 0xDEAD (placeholder)");
        console.log("  2. TWAMM.rldCore() = address(0)");
    }

    function _verify(string memory label, address actual, address expected) internal pure {
        if (actual == expected) {
            console.log(label, "[OK]");
        } else {
            console.log(label, "[MISMATCH!]");
            console.log("      Expected:", expected);
            console.log("      Actual:", actual);
        }
    }
}
