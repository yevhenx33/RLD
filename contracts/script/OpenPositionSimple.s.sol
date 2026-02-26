// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {IRLDCore, MarketId} from "../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";

interface IPrimeBrokerFactory {
    function createBroker(bytes32 salt) external returns (address broker);
    function isBroker(address broker) external view returns (bool);
    function MARKET_ID() external view returns (MarketId);
}

/// @notice Simplified script - assumes deployer already has aUSDC
contract OpenPositionSimple is Script {
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    uint256 constant DEBT_AMOUNT = 200_000e6; // 200k wRLP

    function run() external {
        string memory json = vm.readFile("deployments.json");
        address CORE = vm.parseJsonAddress(json, ".RLDCore");
        address BROKER_FACTORY = vm.parseJsonAddress(json, ".BrokerFactory");
        bytes32 MARKET_ID = vm.parseJsonBytes32(json, ".MarketId");

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        uint256 deployerAusdcBalance = ERC20(AUSDC).balanceOf(deployer);
        console.log("=== OPEN POSITION (SIMPLE) ===");
        console.log("Deployer:", deployer);
        console.log("Deployer aUSDC:", deployerAusdcBalance / 1e6, "aUSDC");
        require(deployerAusdcBalance > 0, "No aUSDC balance");

        vm.startBroadcast(deployerKey);

        // Create broker
        IPrimeBrokerFactory factory = IPrimeBrokerFactory(BROKER_FACTORY);
        bytes32 salt = keccak256(abi.encodePacked(deployer, "openposition"));
        address broker = factory.createBroker(salt);
        console.log("Broker:", broker);

        // Transfer collateral to broker
        ERC20(AUSDC).transfer(broker, deployerAusdcBalance);
        console.log("Transferred collateral to broker");

        // Mint debt
        PrimeBroker(payable(broker)).modifyPosition(MARKET_ID, 0, int256(DEBT_AMOUNT));
        console.log("Minted 200k wRLP debt");

        vm.stopBroadcast();

        // Verify
        IRLDCore core = IRLDCore(CORE);
        IRLDCore.MarketState memory state = core.getMarketState(MarketId.wrap(MARKET_ID));
        console.log("");
        console.log("=== RESULT ===");
        console.log("Total Market Debt:", state.totalDebt / 1e6, "wRLP");
        console.log("Broker NAV:", PrimeBroker(payable(broker)).getNetAccountValue() / 1e6, "USDC-eq");
    }
}
