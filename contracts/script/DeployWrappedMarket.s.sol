// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {WrappedAToken} from "../src/shared/wrappers/WrappedAToken.sol";
import {RLDMarketFactory} from "../src/rld/core/RLDMarketFactory.sol";
import {RLDCore} from "../src/rld/core/RLDCore.sol";
import {IRLDCore, MarketId} from "../src/shared/interfaces/IRLDCore.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

/**
 * @title DeployWrappedMarket
 * @notice Deploys waUSDC wrapper and creates a new market using it as collateral
 */
contract DeployWrappedMarket is Script {
    // Mainnet addresses
    address constant AAVE_V3_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;

    function run() external {
        // Load existing deployment
        string memory json = vm.readFile("./deployments.json");
        address coreAddr = vm.parseJsonAddress(json, ".RLDCore");
        address factoryAddr = vm.parseJsonAddress(json, ".RLDMarketFactory");
        address liqModule = vm.parseJsonAddress(json, ".DutchLiquidationModule");
        address aaveOracle = vm.parseJsonAddress(json, ".RLDAaveOracle");

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        
        console.log("");
        console.log("=== DEPLOYING WAUSDC WRAPPER ===");
        console.log("Deployer:", deployer);
        
        vm.startBroadcast(deployerKey);
        
        // 1. Deploy waUSDC wrapper
        WrappedAToken waUSDC = new WrappedAToken(
            AUSDC,
            "Wrapped aUSDC",
            "waUSDC"
        );
        console.log("waUSDC deployed:", address(waUSDC));
        
        // 2. Create new market with waUSDC as collateral
        console.log("");
        console.log("=== CREATING waUSDC MARKET ===");
        
        RLDMarketFactory factory = RLDMarketFactory(factoryAddr);
        
        RLDMarketFactory.DeployParams memory params = RLDMarketFactory.DeployParams({
            underlyingPool: AAVE_V3_POOL,
            underlyingToken: USDC,
            collateralToken: address(waUSDC),  // Use waUSDC instead of aUSDC!
            curator: deployer,
            positionTokenName: "Wrapped RLD LP waUSDC",
            positionTokenSymbol: "wRLPwaUSDC",
            minColRatio: 1.5e18,
            maintenanceMargin: 1.1e18,
            liquidationCloseFactor: 0.5e18,
            liquidationModule: liqModule,
            liquidationParams: bytes32(0),
            spotOracle: address(0),
            rateOracle: aaveOracle,
            oraclePeriod: 1 hours,
            poolFee: 500,
            tickSpacing: 5
        });
        
        (MarketId marketId, address brokerFactory) = factory.createMarket(params);
        
        console.log("");
        console.log("=== WRAPPED MARKET CREATED ===");
        console.log("MarketId:", vm.toString(MarketId.unwrap(marketId)));
        console.log("BrokerFactory:", brokerFactory);
        
        vm.stopBroadcast();
        
        // Query market addresses
        RLDCore core = RLDCore(coreAddr);
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(marketId);
        
        console.log("");
        console.log("=== MARKET ADDRESSES ===");
        console.log("collateralToken (waUSDC):", addrs.collateralToken);
        console.log("positionToken (wRLP):", addrs.positionToken);
        
        // Output for script consumption
        console.log("");
        console.log("WAUSDC_ADDRESS=%s", address(waUSDC));
        console.log("WRAPPED_MARKET_ID=%s", vm.toString(MarketId.unwrap(marketId)));
        console.log("WRAPPED_BROKER_FACTORY=%s", brokerFactory);
        console.log("WRAPPED_POSITION_TOKEN=%s", addrs.positionToken);
    }
}
