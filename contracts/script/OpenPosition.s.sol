// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {IRLDCore, MarketId} from "../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";

interface IPool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

interface IPrimeBrokerFactory {
    function createBroker(bytes32 salt) external returns (address broker);
    function isBroker(address broker) external view returns (bool);
    function MARKET_ID() external view returns (MarketId);
}

contract OpenPosition is Script {
    // Mainnet addresses
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    
    // Amounts (USDC has 6 decimals)
    uint256 constant COLLATERAL_AMOUNT = 10_000_000e6; // 10M USDC
    uint256 constant DEBT_AMOUNT = 200_000e6;          // 200k wRLP (6 decimals, matches collateral)

    function run() external {
        // Load deployment addresses
        string memory json = vm.readFile("deployments.json");
        address CORE = vm.parseJsonAddress(json, ".RLDCore");
        address BROKER_FACTORY = vm.parseJsonAddress(json, ".BrokerFactory");
        bytes32 MARKET_ID = vm.parseJsonBytes32(json, ".MarketId");
        
        // USDC whale with 114M+ USDC at block 24335184
        address USDC_WHALE = vm.parseAddress("0xCFFAd3200574698b78f32232aa9D63eABD290703");
        
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        console.log("=== OPEN POSITION SCRIPT ===");
        console.log("Deployer:", deployer);
        console.log("Core:", CORE);
        console.log("BrokerFactory:", BROKER_FACTORY);
        console.log("");

        // ============================================
        // STEP 1: Impersonate whale and deposit to Aave
        // Note: vm.prank works in simulation. For broadcast to work,
        // the --unlocked flag must be used and Anvil must have this address unlocked.
        // ============================================
        console.log("STEP 1: Acquiring aUSDC via Aave deposit...");
        
        uint256 whaleBalance = ERC20(USDC).balanceOf(USDC_WHALE);
        console.log("  Whale USDC balance:", whaleBalance / 1e6, "USDC");
        require(whaleBalance >= COLLATERAL_AMOUNT, "Whale insufficient balance");

        // Impersonate whale (prank for simulation, broadcast with --unlocked for real tx)
        vm.startPrank(USDC_WHALE);
        ERC20(USDC).approve(AAVE_POOL, COLLATERAL_AMOUNT);
        IPool(AAVE_POOL).supply(USDC, COLLATERAL_AMOUNT, deployer, 0);
        vm.stopPrank();
        
        uint256 deployerAusdcBalance = ERC20(AUSDC).balanceOf(deployer);
        console.log("  Deployer aUSDC balance:", deployerAusdcBalance / 1e6, "aUSDC");
        require(deployerAusdcBalance >= COLLATERAL_AMOUNT * 9999 / 10000, "aUSDC not received");

        // ============================================
        // STEP 2-4: All deployer actions in ONE broadcast block
        // This ensures state consistency across all operations
        // ============================================
        vm.startBroadcast(deployerKey);

        // STEP 2: Create PrimeBroker
        console.log("");
        console.log("STEP 2: Setting up PrimeBroker...");
        
        IPrimeBrokerFactory factory = IPrimeBrokerFactory(BROKER_FACTORY);
        bytes32 salt = keccak256(abi.encodePacked(deployer, block.timestamp));
        address broker = factory.createBroker(salt);
        console.log("  Broker created:", broker);

        // STEP 3: Transfer aUSDC to broker
        console.log("");
        console.log("STEP 3: Depositing collateral to broker...");
        ERC20(AUSDC).transfer(broker, deployerAusdcBalance);
        console.log("  Transferred:", deployerAusdcBalance / 1e6, "aUSDC to broker");

        // STEP 4: Mint wRLP debt
        console.log("");
        console.log("STEP 4: Minting wRLP debt...");
        PrimeBroker(payable(broker)).modifyPosition(MARKET_ID, 0, int256(DEBT_AMOUNT));
        console.log("  Minted:", DEBT_AMOUNT / 1e6, "wRLP debt");

        vm.stopBroadcast();

        // ============================================
        // STEP 5: Verify position
        // ============================================
        console.log("");
        console.log("=== POSITION OPENED ===");
        
        IRLDCore core = IRLDCore(CORE);
        IRLDCore.MarketState memory state = core.getMarketState(MarketId.wrap(MARKET_ID));
        
        console.log("Total Market Debt:", state.totalDebt / 1e6, "wRLP");
        
        uint256 nav = PrimeBroker(payable(broker)).getNetAccountValue();
        console.log("Broker NAV:", nav / 1e6, "USDC-equivalent");
        
        // Get position token balance
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(MarketId.wrap(MARKET_ID));
        uint256 wrlpBalance = ERC20(addrs.positionToken).balanceOf(broker);
        console.log("wRLP minted to broker:", wrlpBalance / 1e6, "wRLP");
    }
}
