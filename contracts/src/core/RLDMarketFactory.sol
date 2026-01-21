// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../interfaces/IRLDCore.sol";
import {WrappedRLP} from "../tokens/WrappedRLP.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol"; 
import {IRLDOracle} from "../interfaces/IRLDOracle.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {PrimeBrokerFactory} from "../vaults/PrimeBrokerFactory.sol";
import {BrokerVerifier} from "../modules/verifier/BrokerVerifier.sol";
import {UniswapV4SingletonOracle} from "../modules/oracles/UniswapV4SingletonOracle.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";


contract RLDMarketFactory {
    using Clones for address;
    using PoolIdLibrary for PoolKey;

    /* ============================================================================================ */
    /*                                          IMMUTABLES                                          */
    /* ============================================================================================ */

    address public immutable CORE;
    address public immutable POOL_MANAGER;
    address public immutable WRAPPED_RLP_IMPL;
    address public immutable PRIME_BROKER_IMPL; 
    address public immutable SINGLETON_V4_ORACLE; 
    address public immutable TWAMM;
    
    // Default Modules
    address public immutable STD_FUNDING_MODEL;
    address public immutable CDS_HOOK;
    address public immutable DEFAULT_ORACLE;

    /* ============================================================================================ */
    /*                                            STORAGE                                           */
    /* ============================================================================================ */

    // Key: keccak256(abi.encode(underlyingPool, underlyingToken, marketType))
    mapping(bytes32 => MarketId) public canonicalMarkets;
    
    error MarketAlreadyExists();
    error IDMismatch();

    /* ============================================================================================ */
    /*                                            STRUCTS                                           */
    /* ============================================================================================ */

    /// @notice Configuration parameters for creating a new RLD Market
    struct DeployParams {
        // --- Assets ---
        address underlyingPool;     // The Uniswap V4 Pool storing the underlying asset
        address underlyingToken;    // The base asset (e.g., USDC)
        address collateralToken;    // The collateral asset (e.g., aUSDC)
        address curator;            // The market curator (risk manager)
        
        // --- Market Type ---
        IRLDCore.MarketType marketType; // e.g., RLP, CDS
        
        // --- Risk Parameters ---
        uint64 minColRatio;         // e.g., 1.2e18 (120%)
        uint64 maintenanceMargin;   // e.g., 1.1e18 (110%)
        address liquidationModule;  // Module responsible for liquidating positions
        bytes32 liquidationParams;  // Encoded params for the liquidationmodule
        
        // --- Oracle Configuration ---
        address spotOracle;         // External Oracle for Spot Price (Chainlink)
        address rateOracle;         // External Oracle for Interest Rates (Aave)
        uint32 oraclePeriod;        // TWAMM Period (e.g., 3600 seconds)
        
        // --- V4 Pool Configuration ---
        uint24 poolFee;             // e.g., 3000 (0.3%)
        int24 tickSpacing;          // e.g., 60
    }

    event MarketDeployed(MarketId indexed id, address indexed underlyingPool, address indexed collateral);

    /* ============================================================================================ */
    /*                                         CONSTRUCTOR                                          */
    /* ============================================================================================ */

    constructor(
        address core, 
        address poolManager, 
        address wRLPImpl,
        address primeBrokerImpl,
        address v4Oracle,
        address fundingModel,
        address cdsHook,
        address defaultOracle,
        address twamm
    ) {
        CORE = core;
        POOL_MANAGER = poolManager;
        WRAPPED_RLP_IMPL = wRLPImpl;
        PRIME_BROKER_IMPL = primeBrokerImpl;
        SINGLETON_V4_ORACLE = v4Oracle;
        STD_FUNDING_MODEL = fundingModel;
        CDS_HOOK = cdsHook;
        DEFAULT_ORACLE = defaultOracle;
        TWAMM = twamm;
    }

    /* ============================================================================================ */
    /*                                     EXTERNAL FUNCTIONS                                       */
    /* ============================================================================================ */

    /// @notice Deploys a new RLP Market with Uniswap V4 Integration + Atomic Trust
    /// @dev Orchestrates the deployment phases: Validation -> ID -> Infrastructure -> Assets -> Mechanics -> Registration
    function createMarket(DeployParams calldata params) external returns (MarketId marketId, address brokerFactory) {
        // 1. Validation Phase (Fail Fast)
        _validateParams(params);

        // 2. Identification Phase
        MarketId futureId = _precomputeId(params);

        // 3. Infrastructure Phase (Atomic Trust)
        address verifier;
        (brokerFactory, verifier) = _deployInfrastructure(futureId);

        // 4. Asset Phase
        address wRLP = _deployPositionToken(params.collateralToken, params.underlyingToken);

        // 5. Market Mechanics Phase (Uniswap V4)
        _initializePool(wRLP, params);

        // 6. Registration Phase (Settlement)
        marketId = _registerMarket(params, wRLP, verifier);

        // 7. Post-Condition Check (Critical Security Invariant)
        if (MarketId.unwrap(marketId) != MarketId.unwrap(futureId)) revert IDMismatch();
    }

    function getCanonicalId(address pool, address token, IRLDCore.MarketType mType) public pure returns (bytes32) {
        return keccak256(abi.encode(pool, token, mType));
    }

    /* ============================================================================================ */
    /*                                     INTERNAL FUNCTIONS                                       */
    /* ============================================================================================ */

    function _validateParams(DeployParams calldata params) internal pure {
        // Critical Address Checks (Sharp Edge: Dangerous Defaults)
        require(params.underlyingPool != address(0), "Invalid Pool");
        require(params.underlyingToken != address(0), "Invalid Underlying");
        require(params.collateralToken != address(0), "Invalid Collateral");
        require(params.liquidationModule != address(0), "Invalid LiqModule");
        
        // Logic Checks (Sharp Edge: Insolvent Params)
        require(params.minColRatio > 1e18, "MinCol < 100%"); // Must be over-collateralized
        require(params.minColRatio > params.maintenanceMargin, "Risk Config Error");
        
        // V4 Spec Checks (Sharp Edge: Configuration Cliffs)
        require(params.tickSpacing > 0, "Invalid TickSpacing");
        require(params.oraclePeriod > 0, "Invalid OraclePeriod");
    }

    function _precomputeId(DeployParams calldata params) internal pure returns (MarketId) {
        return MarketId.wrap(keccak256(abi.encode(
            params.collateralToken,
            params.underlyingToken,
            params.underlyingPool,
            params.marketType
        )));
    }

    function _deployInfrastructure(MarketId id) internal returns (address factory, address verifier) {
        // Deploy Factory specific to this MarketID
        PrimeBrokerFactory pbFactory = new PrimeBrokerFactory(PRIME_BROKER_IMPL, id);
        factory = address(pbFactory);
        
        // Deploy Verifier linked to this Factory
        verifier = address(new BrokerVerifier(factory));
    }

    function _deployPositionToken(address collateralToken, address underlyingToken) internal returns (address wRLPAddr) {
        wRLPAddr = Clones.clone(WRAPPED_RLP_IMPL);
        string memory colSymbol = ERC20(collateralToken).symbol();
        WrappedRLP(wRLPAddr).initialize(underlyingToken, colSymbol); 
    }

    function _initializePool(address wRLP, DeployParams calldata params) internal returns (bytes32) {
        Currency currency0 = Currency.wrap(wRLP);
        Currency currency1 = Currency.wrap(params.underlyingToken);
        if (currency0 > currency1) (currency0, currency1) = (currency1, currency0);

        // Rate Verification
        uint256 indexPrice = IRLDOracle(params.rateOracle).getIndexPrice(params.underlyingPool, params.underlyingToken);
        if (Currency.wrap(wRLP) == currency1) {
             indexPrice = 1e36 / indexPrice;
        }

        uint160 initSqrtPrice = uint160( (FixedPointMathLib.sqrt(indexPrice) * (1 << 96)) / 1e9 );

        PoolKey memory key = PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: params.poolFee, 
            tickSpacing: params.tickSpacing,
            hooks: IHooks(TWAMM)
        });
        
        IPoolManager(POOL_MANAGER).initialize(key, initSqrtPrice);
        
        // Register with Singleton Oracle
        UniswapV4SingletonOracle(SINGLETON_V4_ORACLE).registerPool(
            wRLP,
            key,
            TWAMM,
            params.oraclePeriod
        );
        
        return PoolId.unwrap(key.toId());
    }

    function _registerMarket(
        DeployParams calldata params, 
        address wRLPAddr, 
        address verifier
    ) internal returns (MarketId marketId) {
        // Register & Validate Constraints
        bytes32 canonicalKey = getCanonicalId(params.underlyingPool, params.underlyingToken, params.marketType);
        
        if (MarketId.unwrap(canonicalMarkets[canonicalKey]) != bytes32(0)) {
            revert MarketAlreadyExists();
        }

        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: params.collateralToken,
            underlyingToken: params.underlyingToken,
            underlyingPool: params.underlyingPool,
            rateOracle: params.rateOracle,
            spotOracle: params.spotOracle, 
            markOracle: SINGLETON_V4_ORACLE,
            fundingModel: STD_FUNDING_MODEL,
            curator: params.curator, 
            hook: CDS_HOOK,
            defaultOracle: DEFAULT_ORACLE,
            liquidationModule: params.liquidationModule,
            positionToken: wRLPAddr
        });

        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            marketType: params.marketType,
            minColRatio: params.minColRatio,
            maintenanceMargin: params.maintenanceMargin,
            liquidationParams: params.liquidationParams,
            brokerVerifier: verifier
        });

        marketId = IRLDCore(CORE).createMarket(addresses, config);
        canonicalMarkets[canonicalKey] = marketId;
        
        // Link wRLP
        WrappedRLP(wRLPAddr).setMarketId(marketId);
        WrappedRLP(wRLPAddr).transferOwnership(CORE);
        
        emit MarketDeployed(marketId, params.underlyingPool, params.underlyingToken);
    }
}
