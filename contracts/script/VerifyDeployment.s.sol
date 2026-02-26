// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {RLDCore} from "../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/rld/core/RLDMarketFactory.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {JTM} from "../src/twamm/JTM.sol";

/**
 * @title VerifyDeployment
 * @notice Comprehensive verification of deployed contract relationships
 * @dev Run: forge script script/VerifyDeployment.s.sol --rpc-url http://127.0.0.1:8545 -vvv
 */
contract VerifyDeployment is Script {
    // Deployed addresses (from latest deployment)
    address constant CORE = 0x6B5CF024365D5d5d0786673780CA7E3F07f85B63;
    address constant FACTORY = 0xAaC7D4A36DAb95955ef3c641c23F1fA46416CF71;
    address constant TWAMM_HOOK = 0x8E894E20a38B89C004E4FF5691553B08e8e52ac0;
    address constant PRIME_BROKER_IMPL = 0xf975A646FCa589Be9fc4E0C28ea426A75645fB1f;
    address constant POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    uint256 issueCount = 0;
    uint256 warningCount = 0;

    function run() external view {
        console.log("");
        console.log("================================================================");
        console.log("  RLD PROTOCOL - DEPLOYMENT VERIFICATION REPORT");
        console.log("================================================================");
        console.log("");

        // ============================================
        // SECTION 1: CONTRACT EXISTENCE
        // ============================================
        console.log("--- 1. CONTRACT EXISTENCE ---");
        _checkContractExists("RLDCore", CORE);
        _checkContractExists("RLDMarketFactory", FACTORY);
        _checkContractExists("TWAMM", TWAMM_HOOK);
        _checkContractExists("PrimeBrokerImpl", PRIME_BROKER_IMPL);
        console.log("");

        // ============================================
        // SECTION 2: CORE <-> FACTORY BIDIRECTIONAL LINK
        // ============================================
        console.log("--- 2. CORE <-> FACTORY BIDIRECTIONAL LINK ---");

        RLDCore core = RLDCore(CORE);
        RLDMarketFactory factory = RLDMarketFactory(FACTORY);

        address coreFactory = core.factory();
        address factoryCore = factory.CORE();

        console.log("  Core.factory():", coreFactory);
        console.log("  Factory.CORE():", factoryCore);

        if (coreFactory == FACTORY && factoryCore == CORE) {
            console.log("  [OK] Bidirectional link verified");
        } else {
            console.log("  [CRITICAL] Link mismatch!");
        }
        console.log("");

        // ============================================
        // SECTION 3: TWAMM CONNECTIONS
        // ============================================
        console.log("--- 3. TWAMM CONNECTIONS ---");

        JTM twamm = JTM(payable(TWAMM_HOOK));

        // Check Core -> TWAMM
        address coreTwamm = core.twamm();
        console.log("  Core.twamm():", coreTwamm);
        if (coreTwamm == TWAMM_HOOK) {
            console.log("  [OK] Core -> TWAMM link correct");
        } else {
            console.log("  [ERROR] Core -> TWAMM mismatch!");
        }

        // Check Factory -> TWAMM
        address factoryTwamm = factory.TWAMM();
        console.log("  Factory.TWAMM():", factoryTwamm);
        if (factoryTwamm == TWAMM_HOOK) {
            console.log("  [OK] Factory -> TWAMM link correct");
        } else {
            console.log("  [ERROR] Factory -> TWAMM mismatch!");
        }

        // Check TWAMM -> Core (THE FIX WE VERIFIED)
        address twammCore = twamm.rldCore();
        console.log("  TWAMM.rldCore():", twammCore);
        if (twammCore == CORE) {
            console.log("  [OK] TWAMM -> Core link correct (FIX VERIFIED!)");
        } else if (twammCore == address(0)) {
            console.log("  [ERROR] TWAMM.rldCore = 0x0 (FIX NOT APPLIED!)");
        } else {
            console.log("  [ERROR] TWAMM -> Core mismatch!");
        }

        // Check TWAMM -> PoolManager
        address twammPM = address(twamm.poolManager());
        console.log("  TWAMM.poolManager():", twammPM);
        if (twammPM == POOL_MANAGER) {
            console.log("  [OK] TWAMM -> PoolManager correct");
        } else {
            console.log("  [ERROR] TWAMM -> PoolManager mismatch!");
        }
        console.log("");

        // ============================================
        // SECTION 4: PRIMEBROKER IMPLEMENTATION
        // ============================================
        console.log("--- 4. PRIMEBROKER IMPLEMENTATION ---");

        PrimeBroker pbImpl = PrimeBroker(payable(PRIME_BROKER_IMPL));

        // Check CORE in impl (should be address(0) now, set per-clone)
        address implCore = pbImpl.CORE();
        console.log("  PrimeBrokerImpl.CORE():", implCore);
        if (implCore == address(0)) {
            console.log("  [OK] Impl CORE is 0x0 (set per-clone in initialize)");
        } else if (implCore == address(0xDEAD)) {
            console.log("  [ERROR] Still using 0xDEAD placeholder!");
        } else {
            console.log("  [INFO] Impl CORE has unexpected value");
        }

        // Check Factory uses correct impl
        address factoryImpl = factory.PRIME_BROKER_IMPL();
        console.log("  Factory.PRIME_BROKER_IMPL():", factoryImpl);
        if (factoryImpl == PRIME_BROKER_IMPL) {
            console.log("  [OK] Factory uses correct implementation");
        } else {
            console.log("  [ERROR] Factory uses different implementation!");
        }
        console.log("");

        // ============================================
        // SECTION 5: POOLMANAGER CONNECTIONS
        // ============================================
        console.log("--- 5. POOLMANAGER CONNECTIONS ---");

        address corePM = address(core.poolManager());
        address factoryPM = factory.POOL_MANAGER();

        console.log("  Core.poolManager():", corePM);
        console.log("  Factory.POOL_MANAGER():", factoryPM);

        if (corePM == POOL_MANAGER && factoryPM == POOL_MANAGER) {
            console.log("  [OK] All contracts use correct PoolManager");
        } else {
            console.log("  [ERROR] PoolManager address mismatch!");
        }
        console.log("");

        // ============================================
        // SECTION 6: MODULES & ORACLES
        // ============================================
        console.log("--- 6. MODULES & ORACLES ---");

        address v4Oracle = factory.SINGLETON_V4_ORACLE();
        address fundingModel = factory.STD_FUNDING_MODEL();

        console.log("  Factory.SINGLETON_V4_ORACLE():", v4Oracle);
        console.log("  Factory.STD_FUNDING_MODEL():", fundingModel);

        if (v4Oracle != address(0)) {
            console.log("  [OK] V4 Oracle configured");
        } else {
            console.log("  [WARNING] V4 Oracle is zero");
        }

        if (fundingModel != address(0)) {
            console.log("  [OK] Funding Model configured");
        } else {
            console.log("  [WARNING] Funding Model is zero");
        }
        console.log("");

        // ============================================
        // SECTION 7: OWNERSHIP
        // ============================================
        console.log("--- 7. OWNERSHIP ---");

        address factoryOwner = factory.owner();
        address twammOwner = twamm.owner();

        console.log("  Factory.owner():", factoryOwner);
        console.log("  TWAMM.owner():", twammOwner);

        if (factoryOwner == twammOwner) {
            console.log("  [OK] Same owner for Factory and TWAMM");
        } else {
            console.log("  [WARNING] Different owners (may be intentional)");
        }
        console.log("");

        // ============================================
        // SECTION 8: WEIRD BEHAVIORS / OBSERVATIONS
        // ============================================
        console.log("--- 8. OBSERVATIONS & POTENTIAL ISSUES ---");

        // Check PositionToken impl (unused in factory)
        address posTokenImpl = factory.POSITION_TOKEN_IMPL();
        console.log("  Factory.POSITION_TOKEN_IMPL():", posTokenImpl);
        console.log("  [INFO] This is stored but unused - factory deploys fresh PositionTokens");

        // Check metadata renderer
        address renderer = factory.METADATA_RENDERER();
        console.log("  Factory.METADATA_RENDERER():", renderer);
        console.log("  [INFO] Reserved for future NFT metadata rendering");

        // Check funding period
        uint32 fundingPeriod = factory.FUNDING_PERIOD();
        console.log("  Factory.FUNDING_PERIOD():", fundingPeriod, "seconds");
        console.log("    =", fundingPeriod / 1 days, "days");

        console.log("");
        console.log("================================================================");
        console.log("  VERIFICATION COMPLETE");
        console.log("================================================================");
    }

    function _checkContractExists(string memory name, address addr) internal view {
        uint256 size;
        assembly { size := extcodesize(addr) }
        if (size > 0) {
            console.log("  [OK]", name, "deployed at:");
            console.log("       ", addr);
        } else {
            console.log("  [ERROR]", name, "NOT FOUND at:");
            console.log("       ", addr);
        }
    }
}
