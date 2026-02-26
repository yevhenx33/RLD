// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {RLDCore} from "../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/rld/core/RLDMarketFactory.sol";
import {IRLDCore} from "../src/shared/interfaces/IRLDCore.sol";
import {MarketId} from "../src/shared/interfaces/IRLDCore.sol";

import {IJTM} from "../src/twamm/IJTM.sol";
import {RLDDeployConfig as C} from "../src/shared/config/RLDDeployConfig.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";

/**
 * @title CreateTestMarket
 * @notice Creates a test aUSDC market and queries its data
 */
contract CreateTestMarket is Script {
    using PoolIdLibrary for PoolKey;

    function run() external {
        string memory json = vm.readFile("./deployments.json");
        address coreAddr = vm.parseJsonAddress(json, ".RLDCore");
        address factoryAddr = vm.parseJsonAddress(json, ".RLDMarketFactory");
        address liqModule = vm.parseJsonAddress(json, ".DutchLiquidationModule");
        address aaveOracle = vm.parseJsonAddress(json, ".RLDAaveOracle");

        address twammAddr = vm.parseJsonAddress(json, ".TWAMM");

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        console.log("");
        console.log("=== CREATING TEST MARKET ===");
        console.log("Deployer:", deployer);
        console.log("Core:", coreAddr);
        console.log("Factory:", factoryAddr);

        vm.startBroadcast(deployerKey);

        RLDMarketFactory factory = RLDMarketFactory(factoryAddr);
        RLDCore core = RLDCore(coreAddr);

        // Create market params
        RLDMarketFactory.DeployParams memory params = RLDMarketFactory.DeployParams({
            underlyingPool: C.AAVE_POOL,
            underlyingToken: C.USDC,
            collateralToken: C.AUSDC,
            curator: deployer,
            positionTokenName: C.POSITION_TOKEN_NAME,
            positionTokenSymbol: C.POSITION_TOKEN_SYMBOL,
            minColRatio: C.MIN_COL_RATIO,
            maintenanceMargin: C.MAINTENANCE_MARGIN,
            liquidationCloseFactor: C.LIQUIDATION_CLOSE_FACTOR,
            liquidationModule: liqModule,
            liquidationParams: C.LIQUIDATION_PARAMS,
            spotOracle: address(0),
            rateOracle: aaveOracle, // index
            oraclePeriod: C.ORACLE_PERIOD,
            poolFee: C.POOL_FEE,
            tickSpacing: C.TICK_SPACING
        });

        // Create the market
        (MarketId marketId, address brokerFactory) = factory.createMarket(params);

        console.log("");
        console.log("=== MARKET CREATED ===");
        console.log("MarketId:", vm.toString(MarketId.unwrap(marketId)));
        console.log("BrokerFactory:", brokerFactory);

        // --- Increase Oracle Cardinality to Maximum ---
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(marketId);

        Currency currency0 = Currency.wrap(addrs.positionToken);
        Currency currency1 = Currency.wrap(params.collateralToken);
        if (currency0 > currency1) {
            (currency0, currency1) = (currency1, currency0);
        }

        PoolKey memory key = PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: params.poolFee,
            tickSpacing: params.tickSpacing,
            hooks: IHooks(factory.TWAMM())
        });

        PoolId poolId = key.toId();
        console.log("Setting Oracle Cardinality to MAX for PoolId...");
        uint16 nextCard = IJTM(address(factory.TWAMM())).increaseCardinality(poolId, type(uint16).max);
        console.log("New Cardinality Next:", nextCard);

        vm.stopBroadcast();

        // Query market data
        console.log("");
        console.log("=== MARKET STATE ===");

        IRLDCore.MarketState memory state = core.getMarketState(marketId);
        console.log("normalizationFactor:", state.normalizationFactor);
        console.log("totalDebt:", state.totalDebt);
        console.log("lastUpdateTimestamp:", state.lastUpdateTimestamp);

        console.log("");
        console.log("=== MARKET CONFIG (Risk Parameters) ===");

        IRLDCore.MarketConfig memory config = core.getMarketConfig(marketId);
        console.log("minColRatio:", config.minColRatio);
        console.log("maintenanceMargin:", config.maintenanceMargin);
        console.log("liquidationCloseFactor:", config.liquidationCloseFactor);
        console.log("fundingPeriod:", config.fundingPeriod);
        console.log("debtCap:", config.debtCap);
        console.log("brokerVerifier:", config.brokerVerifier);

        console.log("");
        console.log("=== MARKET ADDRESSES ===");

        addrs = core.getMarketAddresses(marketId);
        console.log("collateralToken:", addrs.collateralToken);
        console.log("underlyingToken:", addrs.underlyingToken);
        console.log("underlyingPool:", addrs.underlyingPool);
        console.log("positionToken:", addrs.positionToken);
        console.log("curator:", addrs.curator);
        console.log("spotOracle:", addrs.spotOracle);
        console.log("rateOracle:", addrs.rateOracle);
        console.log("markOracle:", addrs.markOracle);
        console.log("fundingModel:", addrs.fundingModel);
        console.log("fundingModel:", addrs.fundingModel);
        console.log("liquidationModule:", addrs.liquidationModule);

        // ============================================
        // EXPORT MARKET DATA
        // ============================================
        string memory jsonObj = "market_deployment";
        vm.serializeAddress(jsonObj, "BrokerFactory", brokerFactory);
        string memory finalJson = vm.serializeBytes32(jsonObj, "MarketId", MarketId.unwrap(marketId));

        vm.writeJson(finalJson, "./market_deployments.json");
        console.log("Market data saved to ./market_deployments.json");
    }
}
