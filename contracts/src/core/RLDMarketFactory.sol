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
import {UniswapV4SingletonOracle} from "../modules/oracles/UniswapV4SingletonOracle.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";
import {IRLDOracle} from "../interfaces/IRLDOracle.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

/// @title RLDMarketFactory
/// @notice Permissionless factory for "One-Click" RLD Markets.
/// @dev Automates Oracle, Hook, and Market creation steps.
contract RLDMarketFactory is IRLDMarketFactory {
    
    IRLDCore public immutable CORE;
    IPoolManager public immutable poolManager;
    ITWAMM public immutable twamm;
    
    using PoolIdLibrary for PoolKey;
    
    event MarketDeployed(MarketId indexed id, address indexed pool, address underlying, IRLDCore.MarketType marketType);
    error Unauthorized();
    error MarketAlreadyExists();
    
    // Default Implementations (Immutable for creating clones, or just use references if stateless)
    // For MVP, we pass deployed addresses or deploy new instances if needed.
    // Ideally we have a registry. For now, we hardcode standard modules logic or deploy them.
    
    address public immutable AAVE_RATE_ORACLE; // Stateless
    address public immutable STD_FUNDING_MODEL;
    address public immutable CHAINLINK_SPOT_ORACLE; // Singleton
    address public immutable DEFAULT_ORACLE; // Singleton logic
    address public immutable STATIC_LIQ_MODULE;
    address public immutable MARK_ORACLE; // For legacy support
    address public immutable CDS_HOOK;
    address public immutable WRAPPED_RLP_IMPL;
    address public immutable SINGLETON_V4_ORACLE; // New Singleton
    
    // Pool -> Funding -> MarketType -> MarketId

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
        CDS_HOOK = address(new CDSHook());
        WRAPPED_RLP_IMPL = address(new WrappedRLP());
        SINGLETON_V4_ORACLE = address(new UniswapV4SingletonOracle());
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
            hook: CDS_HOOK,
            defaultOracle: DEFAULT_ORACLE,
            liquidationModule: module,
            positionToken: address(0)
        });



        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            marketType: marketType,
            minColRatio: minColRatio,
            maintenanceMargin: maintenanceMargin,
            liquidationParams: liquidationParams
        });
        
        // 4. Register & Validate Constraints
        if (MarketId.unwrap(canonicalMarkets[underlyingPool][STD_FUNDING_MODEL][marketType]) != bytes32(0)) {
            revert MarketAlreadyExists();
        }

        marketId = CORE.createMarket(addresses, config);
        canonicalMarkets[underlyingPool][STD_FUNDING_MODEL][marketType] = marketId;
        
        // 5. Initialize Uniswap Pool (Empty for Classic Market)
        poolId = bytes32(0); 
    }

    /// @notice Deploys a new RLD Market compatible with Uniswap V4.
    /// @dev This function orchestrates the entire setup: wRLP clone, V4 Pool init, Price calculation, Registry, and Core market creation.
    /// @param underlyingPool Address of the lending protocol pool (e.g., Aave Pool) to query rates from.
    /// @param underlyingToken Address of the asset being lent/borrowed (e.g., USDC).
    /// @param collateralToken Address of the collateral asset (e.g., aUSDC). Used for wRLP naming and Core config.
    /// @param marketType The type of market (e.g., RLP, BOND). Should usually be RLP for this Factory.
    /// @param minColRatio Minimum Collateral Ratio (18 decimals, e.g., 1.2e18 suitable for stablecoins).
    /// @param maintenanceMargin Maintenance Margin required (18 decimals, e.g., 1.1e18).
    /// @param liquidationModule Address of the module handling liquidations (must be non-zero).
    /// @param liquidationParams Config params bytes for the liquidation module (optional).
    /// @param spotOracle Address of the Spot Oracle (e.g., Chainlink) for the Collateral Token.
    /// @param rateOracle Address of the Rate Oracle (e.g., RLDAaveOracle) to fetch the Index Price.
    /// @param oraclePeriod The TWAP period in seconds for the Singleton Oracle (e.g., 3600 for 1h).
    /// @param poolFee The Uniswap V4 Pool Fee (e.g., 3000 for 0.3%).
    /// @param tickSpacing The Uniswap V4 Tick Spacing (e.g., 60).
    /// @return marketId The unique ID of the created market in RLDCore.
    /// @return oracle The Rate Oracle address used.
    /// @return _spotOracle The Spot Oracle address used.
    /// @return defaultOracle The Default Oracle address used.
    /// @return poolId The ID of the initialized Uniswap V4 Pool.
    function deployMarketV4(
        address underlyingPool,
        address underlyingToken,
        address collateralToken, 
        IRLDCore.MarketType marketType,
        uint64 minColRatio,
        uint64 maintenanceMargin,
        address liquidationModule,
        bytes32 liquidationParams,
        address spotOracle,
        address rateOracle,
        uint32 oraclePeriod,
        uint24 poolFee,
        int24 tickSpacing
    ) external override returns (MarketId marketId, address oracle, address _spotOracle, address defaultOracle, bytes32 poolId) {
        if (liquidationModule == address(0)) revert("Invalid Liquidation Module");

        // 1. Deploy wRLP (Clone)
        address wRLPAddr = Clones.clone(WRAPPED_RLP_IMPL);
        // Assuming collateralToken is ERC20 compatible
        string memory colSymbol = ERC20(collateralToken).symbol();
        WrappedRLP(wRLPAddr).initialize(underlyingToken, colSymbol);
        
        // Note: wRLP is the tokenized debt/position, NOT the collateral.
        // collateralToken is passed as argument.

        // 2. Setup V4 Pool params
        Currency currency0 = Currency.wrap(wRLPAddr);
        Currency currency1 = Currency.wrap(underlyingToken);
        if (currency0 > currency1) (currency0, currency1) = (currency1, currency0);

        // Calculate Init Sqrt Price from Rate Oracle (Index Price)
        // wRLP tracks Index Price. 1 wRLP = IndexPrice * Underlying.
        // If wRLP is token0, Price = Amount1/Amount0 = IndexPrice.
        // If wRLP is token1, Price = Amount0/Amount1 = 1/IndexPrice.
        
        uint256 indexPrice = IRLDOracle(rateOracle).getIndexPrice(underlyingPool, underlyingToken);
        
        // Invert if wRLP is token1 (currency1)
        if (Currency.wrap(wRLPAddr) == currency1) {
             indexPrice = 1e36 / indexPrice;
        }

        // sqrtPriceX96 = (sqrt(price) * 2^96) / 1e9
        uint160 initSqrtPrice = uint160( (FixedPointMathLib.sqrt(indexPrice) * (1 << 96)) / 1e9 );

        PoolKey memory key = PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: poolFee, 
            tickSpacing: tickSpacing,
            hooks: IHooks(address(twamm))
        });
        
        // Initialize Pool
        poolManager.initialize(key, initSqrtPrice);
        poolId = PoolId.unwrap(key.toId());

        // 3. Register with Singleton Oracle
        UniswapV4SingletonOracle(SINGLETON_V4_ORACLE).registerPool(
            wRLPAddr,
            key,
            address(twamm),
            oraclePeriod
        );

        oracle = rateOracle;
        _spotOracle = spotOracle; 
        
        address marketOracle = SINGLETON_V4_ORACLE;
        
        // 4. Create Market Params
        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: collateralToken,
            underlyingToken: underlyingToken,
            underlyingPool: underlyingPool,
            rateOracle: rateOracle,
            spotOracle: spotOracle, // Collateral Oracle
            markOracle: SINGLETON_V4_ORACLE, // Funding/Debt Oracle (V4 TWAP Singleton)
            fundingModel: STD_FUNDING_MODEL,
            feeHook: address(0),
            hook: CDS_HOOK,
            defaultOracle: DEFAULT_ORACLE,
            liquidationModule: liquidationModule,
            positionToken: wRLPAddr
        });


        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            marketType: marketType,
            minColRatio: minColRatio,
            maintenanceMargin: maintenanceMargin,
            liquidationParams: liquidationParams
        });

        if (MarketId.unwrap(canonicalMarkets[underlyingPool][STD_FUNDING_MODEL][marketType]) != bytes32(0)) {
            revert MarketAlreadyExists();
        }

        marketId = CORE.createMarket(addresses, config);
        canonicalMarkets[underlyingPool][STD_FUNDING_MODEL][marketType] = marketId;

        // 5. Link wRLP
        WrappedRLP(wRLPAddr).setMarketId(marketId);
        WrappedRLP(wRLPAddr).transferOwnership(address(CORE));
        
        defaultOracle = DEFAULT_ORACLE;
        
        emit MarketDeployed(marketId, underlyingPool, underlyingToken, marketType);
    }

    function deployBondVault(MarketId /*marketId*/) external override returns (address vault) {
        // vault = new SyntheticBond(marketId, CORE);
        // return address(vault);
        return address(0);
    }
}
