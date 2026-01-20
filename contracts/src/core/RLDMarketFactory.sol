// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../interfaces/IRLDCore.sol";
import {IRLDMarketFactory} from "../interfaces/IRLDMarketFactory.sol";
import {RLDAaveOracle} from "../modules/oracles/RLDAaveOracle.sol";
import {ChainlinkSpotOracle} from "../modules/oracles/ChainlinkSpotOracle.sol";
import {StandardFundingModel} from "../modules/funding/StandardFundingModel.sol";
import {CDSHook} from "../modules/hooks/CDSHook.sol";
import {StaticLiquidationModule} from "../modules/liquidation/StaticLiquidationModule.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {ITWAMM} from "v4-twamm-hook/src/ITWAMM.sol";
import {WrappedRLP} from "../tokens/WrappedRLP.sol";
import {UniswapV4OracleAdapter} from "../modules/oracles/UniswapV4OracleAdapter.sol";

/// @title RLDMarketFactory
/// @notice Permissionless factory for "One-Click" RLD Markets.
/// @dev Automates Oracle, Hook, and Market creation steps.
contract RLDMarketFactory is IRLDMarketFactory {
    
    IRLDCore public immutable CORE;
    IPoolManager public immutable poolManager;
    ITWAMM public immutable twamm;
    
    using PoolIdLibrary for PoolKey;
    
    // Default Implementations (Immutable for creating clones, or just use references if stateless)
    // For MVP, we pass deployed addresses or deploy new instances if needed.
    // Ideally we have a registry. For now, we hardcode standard modules logic or deploy them.
    
    address public immutable AAVE_RATE_ORACLE; // Stateless
    address public immutable STD_FUNDING_MODEL;
    address public immutable CHAINLINK_SPOT_ORACLE; // Singleton
    address public immutable DEFAULT_ORACLE; // Singleton logic
    address public immutable STATIC_LIQ_MODULE;
    address public immutable MARK_ORACLE; // For legacy support

    // Pool -> Funding -> MarketType -> MarketId
    mapping(address => mapping(address => mapping(IRLDCore.MarketType => MarketId))) public canonicalMarkets;

    constructor(
        address core, 
        address fundingModel, 
        address spotOracle, 
        address rateOracle, 
        address defaultOracle,
        address _poolManager,
        address _twamm,
        address markOracle // Added for legacy support
    ) {
        CORE = IRLDCore(core);
        STD_FUNDING_MODEL = fundingModel;
        CHAINLINK_SPOT_ORACLE = spotOracle;
        AAVE_RATE_ORACLE = rateOracle;
        DEFAULT_ORACLE = defaultOracle;
        poolManager = IPoolManager(_poolManager);
        twamm = ITWAMM(_twamm);
        MARK_ORACLE = markOracle;
        STATIC_LIQ_MODULE = address(new StaticLiquidationModule());
    }

    function deployMarket(
        address underlyingPool,
        address underlyingToken,
        address collateralToken,
        IRLDCore.MarketType marketType,
        uint64 minColRatio,
        uint64 maintenanceMargin,
        address liquidationModule,
        bytes32 liquidationParams
    ) external override returns (MarketId marketId, address oracle, address spotOracle, address defaultOracle, bytes32 poolId) {
        
        oracle = AAVE_RATE_ORACLE;
        spotOracle = CHAINLINK_SPOT_ORACLE; 
        // 2. Deploy Hooks
        CDSHook cdsHook = new CDSHook();

        address module = liquidationModule == address(0) ? STATIC_LIQ_MODULE : liquidationModule;
        
        // 3. Create Market Params (Using passed MarketType)
        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: collateralToken,
            underlyingToken: underlyingToken,
            underlyingPool: underlyingPool,
            rateOracle: AAVE_RATE_ORACLE,
            spotOracle: CHAINLINK_SPOT_ORACLE,
            markOracle: MARK_ORACLE,
            fundingModel: STD_FUNDING_MODEL,
            feeHook: address(0), 
            hook: address(cdsHook),
            defaultOracle: DEFAULT_ORACLE,
            liquidationModule: module
        });



        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            marketType: marketType,
            minColRatio: minColRatio,
            maintenanceMargin: maintenanceMargin,
            liquidationParams: liquidationParams
        });
        
        // 4. Register & Validate Constraints
        if (MarketId.unwrap(canonicalMarkets[underlyingPool][STD_FUNDING_MODEL][marketType]) != bytes32(0)) {
            revert("Market Already Exists");
        }

        marketId = CORE.createMarket(addresses, config);
        canonicalMarkets[underlyingPool][STD_FUNDING_MODEL][marketType] = marketId;
        
        // 5. Initialize Uniswap Pool (Placeholder)
        poolId = bytes32(0); 
        // 5. Initialize Uniswap Pool (Placeholder)
        poolId = bytes32(0); 
    }

    function deployMarketV4(
        address underlyingPool,
        address underlyingToken,
        address collateralToken, // Passed explicitly (e.g., aUSDC)
        IRLDCore.MarketType marketType,
        uint64 minColRatio,
        uint64 maintenanceMargin,
        address liquidationModule,
        bytes32 liquidationParams,
        uint160 initSqrtPrice,
        uint32 oraclePeriod
    ) external override returns (MarketId marketId, address oracle, address spotOracle, address defaultOracle, bytes32 poolId) {
        // 1. Deploy wRLP
        WrappedRLP wRLP = new WrappedRLP(underlyingToken);
        // Note: wRLP is the tokenized debt/position, NOT the collateral.
        // collateralToken is passed as argument.

        // 2. Setup V4 Pool params
        Currency currency0 = Currency.wrap(address(wRLP));
        Currency currency1 = Currency.wrap(underlyingToken);
        if (currency0 > currency1) (currency0, currency1) = (currency1, currency0);

        PoolKey memory key = PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: 3000, 
            tickSpacing: 60,
            hooks: IHooks(address(twamm))
        });
        
        // Initialize Pool
        poolManager.initialize(key, initSqrtPrice);
        poolId = PoolId.unwrap(key.toId());

        // 3. Deploy Oracle Adapter
        UniswapV4OracleAdapter oracleAdapter = new UniswapV4OracleAdapter(
            poolManager,
            twamm,
            key,
            oraclePeriod
        );

        oracle = AAVE_RATE_ORACLE;
        spotOracle = CHAINLINK_SPOT_ORACLE; // Use Standard Spot Oracle for Collateral (e.g. aUSDC/USDC)
        
        // 4. Create Market Params
        CDSHook cdsHook = new CDSHook();
        address module = liquidationModule == address(0) ? STATIC_LIQ_MODULE : liquidationModule;

        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: collateralToken,
            underlyingToken: underlyingToken,
            underlyingPool: underlyingPool,
            rateOracle: AAVE_RATE_ORACLE,
            spotOracle: CHAINLINK_SPOT_ORACLE, // Collateral Oracle
            markOracle: address(oracleAdapter), // Funding/Debt Oracle (V4 TWAP)
            fundingModel: STD_FUNDING_MODEL,
            feeHook: address(0),
            hook: address(cdsHook),
            defaultOracle: DEFAULT_ORACLE,
            liquidationModule: module
        });


        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            marketType: marketType,
            minColRatio: minColRatio,
            maintenanceMargin: maintenanceMargin,
            liquidationParams: liquidationParams
        });

        if (MarketId.unwrap(canonicalMarkets[underlyingPool][STD_FUNDING_MODEL][marketType]) != bytes32(0)) {
            revert("Market Already Exists");
        }

        marketId = CORE.createMarket(addresses, config);
        canonicalMarkets[underlyingPool][STD_FUNDING_MODEL][marketType] = marketId;

        // 5. Link wRLP
        wRLP.setMarketId(marketId);
        
        defaultOracle = DEFAULT_ORACLE;
    }

    function deployBondVault(MarketId /*marketId*/) external override returns (address vault) {
        // vault = new SyntheticBond(marketId, CORE);
        // return address(vault);
        return address(0);
    }
}
