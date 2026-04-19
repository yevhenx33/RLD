// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {PositionToken} from "../tokens/PositionToken.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {IRLDOracle} from "../../shared/interfaces/IRLDOracle.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {PrimeBrokerFactory} from "./PrimeBrokerFactory.sol";
import {BrokerVerifier} from "../modules/verifier/BrokerVerifier.sol";
import {
    GhostSingletonOracle
} from "../modules/oracles/GhostSingletonOracle.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {FixedPointMathLib} from "../../shared/utils/FixedPointMathLib.sol";
import {IGhostRouter} from "../../dex/interfaces/IGhostRouter.sol";
import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title RLDMarketFactory
 * @author RLD Protocol
 * @notice Factory contract for deploying new RLD markets with Uniswap V4 integration
 * @dev Orchestrates atomic deployment of:
 *      1. PrimeBrokerFactory - Creates individual broker instances for positions
 *      2. BrokerVerifier - Validates broker authenticity
 *      3. PositionToken (wRLP) - ERC20 representing LP positions
 *      4. Uniswap V4 Pool - AMM pool for wRLP <-> collateral trading
 *      5. Market registration with RLDCore
 *
 * The factory ensures deterministic MarketId generation and prevents duplicate markets.
 * All critical addresses are validated at construction time.
 *
 * @custom:security-considerations
 * - Only owner can create markets (access controlled)
 * - MarketId is deterministically computed and verified post-deployment
 * - All oracle and module addresses must be non-zero
 * - Price bounds are set on TWAMM to prevent extreme price manipulation
 */
contract RLDMarketFactory is ReentrancyGuard {
    using Clones for address;
    using PoolIdLibrary for PoolKey;

    /* ============================================================================================ */
    /*                                            OWNER                                             */
    /* ============================================================================================ */

    /// @notice The owner of the factory with exclusive market creation rights
    address public owner;

    /// @dev Restricts function access to the contract owner
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    /// @notice Thrown when a non-owner attempts a restricted action
    error NotOwner();

    /// @notice Thrown when an invalid address is provided
    error InvalidAddress();

    /* ============================================================================================ */
    /*                                          IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice The funding period (e.g., 30 days)
    uint32 public immutable FUNDING_PERIOD;

    /// @notice The RLDCore contract that manages all markets
    /// @dev Initialized via initializeCore() after RLDCore deployment
    address public CORE;

    /// @notice The deployer address with one-time initialization rights
    address private immutable DEPLOYER;

    /// @notice Flag to prevent re-initialization of CORE
    bool private coreInitialized;

    /// @notice Uniswap V4 PoolManager for pool operations
    address public immutable POOL_MANAGER;

    /// @notice Implementation contract for PositionToken clones
    address public immutable POSITION_TOKEN_IMPL;

    /// @notice Implementation contract for PrimeBroker clones
    address public immutable PRIME_BROKER_IMPL;

    /// @notice Ghost oracle for TWAMM V3 price queries
    address public immutable GHOST_ORACLE;

    /// @notice Sovereign GhostRouter for TWAMM v3 markets
    address public immutable GHOST_ROUTER;

    /// @notice Standard funding model for interest rate calculations
    address public immutable STD_FUNDING_MODEL;

    /// @notice BrokerRouter address — pre-approved as operator on every new broker
    /// @dev Passed as defaultOperator to PrimeBrokerFactory during market creation.
    ///      Can be address(0) if no router is deployed yet.
    address public immutable BROKER_ROUTER;

    /// @notice Metadata renderer for Bond NFT display (CURRENTLY UNUSED)
    /// @dev Reserved for future on-chain metadata rendering functionality.
    ///      Currently, PrimeBrokerFactory.tokenURI() returns empty string and metadata
    ///      is handled off-chain or by frontend dynamic rendering.
    ///      This parameter is passed through to PrimeBrokerFactory but not utilized.
    ///      Kept for future extensibility without requiring contract upgrades.
    address public immutable METADATA_RENDERER;

    /// @notice Minimum allowed oracle price: 0.0001 collateral per wRLP (1e14 in WAD)
    /// @dev Used for both oracle price validation and TWAMM bounds calculation
    uint256 public constant MIN_PRICE = 1e14;

    /// @notice Maximum allowed oracle price: 100 collateral per wRLP (100e18 in WAD)
    /// @dev Used for both oracle price validation and TWAMM bounds calculation
    uint256 public constant MAX_PRICE = 100e18;

    /* ============================================================================================ */
    /*                                            STORAGE                                           */
    /* ============================================================================================ */

    /**
     * @notice Maps canonical key to MarketId to prevent duplicate markets
     * @dev Key = keccak256(abi.encode(collateralToken, underlyingToken, underlyingPool))
     *      This matches exactly with the MarketId computation in _precomputeId()
     */
    mapping(bytes32 => MarketId) public canonicalMarkets;

    /// @notice Thrown when attempting to create a market that already exists
    error MarketAlreadyExists();

    /// @notice Thrown when the computed MarketId doesn't match the expected value
    error IDMismatch();

    /* ============================================================================================ */
    /*                                            STRUCTS                                           */
    /* ============================================================================================ */

    /**
     * @notice Configuration parameters for creating a new RLD Market
     * @dev All addresses are validated in _validateParams() before use
     *
     * @param underlyingPool The lending pool (e.g., Aave V3) that holds the underlying asset
     * @param underlyingToken The base asset (e.g., USDC) - what asset is being structured
     * @param collateralToken The yield-bearing collateral (e.g., aUSDC) - backs the wRLP token
     * @param curator The risk manager address for this market
     * @param positionTokenName Human-readable name for the wRLP token (e.g., "Wrapped RLP: aUSDC")
     * @param positionTokenSymbol Token symbol for the wRLP token (e.g., "wRLPaUSDC")
     * @param minColRatio Minimum initial collateralization ratio in WAD (e.g., 1.2e18 = 120%)
     * @param maintenanceMargin Maintenance collateralization ratio in WAD (e.g., 1.1e18 = 110%)
     * @param liquidationCloseFactor Max portion liquidatable in single tx in WAD (e.g., 0.5e18 = 50%)
     * @param liquidationModule Contract handling liquidation logic
     * @param fundingModel Optional custom funding model (address(0) => factory default)
     * @param fundingPeriod Optional initial funding period (0 => factory default)
     * @param decayRateWad Optional CDS decay parameter F in annualized WAD (0 => unused/default)
     * @param settlementModule Optional CDS settlement module (address(0) = standard RLP)
     * @param liquidationParams Encoded parameters for the liquidation module
     * @param spotOracle External oracle for spot price (e.g., Chainlink)
     * @param rateOracle External oracle for interest rates (e.g., Aave rates)
     * @param oraclePeriod TWAP observation window in seconds (e.g., 3600 = 1 hour)
     * @param poolFee Uniswap V4 pool fee tier in hundredths of bips (e.g., 3000 = 0.3%)
     * @param tickSpacing Uniswap V4 tick spacing (e.g., 60)
     */
    struct DeployParams {
        // --- Assets ---
        address underlyingPool;
        address underlyingToken;
        address collateralToken;
        address curator;
        // --- Token Metadata ---
        string positionTokenName;
        string positionTokenSymbol;
        // --- Risk Parameters ---
        uint64 minColRatio;
        uint64 maintenanceMargin;
        uint64 liquidationCloseFactor;
        address liquidationModule;
        address fundingModel;
        uint32 fundingPeriod;
        uint96 decayRateWad;
        address settlementModule;
        bytes32 liquidationParams;
        // --- Oracle Configuration ---
        address spotOracle;
        address rateOracle;
        uint32 oraclePeriod;
        // --- V4 Pool Configuration ---
        uint24 poolFee;
        int24 tickSpacing;
    }

    /**
     * @notice Emitted when a new market is successfully deployed
     * @param id The unique MarketId for the new market
     * @param underlyingPool The lending pool address
     * @param collateral The collateral token address
     * @param positionToken The deployed wRLP token address
     * @param brokerFactory The deployed PrimeBrokerFactory address
     * @param verifier The deployed BrokerVerifier address
     */
    event MarketDeployed(
        MarketId indexed id,
        address indexed underlyingPool,
        address indexed collateral,
        address positionToken,
        address brokerFactory,
        address verifier
    );

    /* ============================================================================================ */
    /*                                         CONSTRUCTOR                                          */
    /* ============================================================================================ */

    /**
     * @notice Initializes the factory with all required protocol addresses
     * @dev CORE address is NOT set in constructor - it will be initialized via initializeCore()
     *      after RLDCore is deployed. This prevents CRITICAL-001 front-running vulnerability.
     *      All other addresses except TWAMM are validated to be non-zero.
     *      TWAMM can be address(0) for testing without V4 hooks.
     *
     * @param poolManager Uniswap V4 PoolManager address
     * @param positionTokenImpl PositionToken implementation for cloning
     * @param primeBrokerImpl PrimeBroker implementation for cloning
     * @param v4Oracle UniswapV4SingletonOracle address
     * @param fundingModel Standard funding model address
     * @param twamm TWAMM hook address (can be address(0) for testing)
     * @param metadataRenderer NFT metadata renderer address
     * @param _fundingPeriod Funding period in seconds (e.g., 30 days = 2592000)
     */
    /// @param brokerRouter BrokerRouter address (pre-approved on all new brokers, can be address(0))
    constructor(
        address poolManager,
        address positionTokenImpl,
        address primeBrokerImpl,
        address ghostOracle,
        address fundingModel,
        address ghostRouter,
        address metadataRenderer,
        uint32 _fundingPeriod,
        address brokerRouter
    ) {
        // Store deployer for one-time CORE initialization
        DEPLOYER = msg.sender;

        // Validate all critical immutables (GHOST_ROUTER can be 0 for testing)
        // NOTE: CORE is NOT validated here - it will be set via initializeCore()
        require(poolManager != address(0), "Invalid PoolManager");
        require(positionTokenImpl != address(0), "Invalid PositionTokenImpl");
        require(primeBrokerImpl != address(0), "Invalid PrimeBrokerImpl");
        require(ghostOracle != address(0), "Invalid GhostOracle");
        require(fundingModel != address(0), "Invalid FundingModel");
        require(metadataRenderer != address(0), "Invalid MetadataRenderer");

        // Validate funding period is within reasonable bounds
        require(
            _fundingPeriod >= 1 days && _fundingPeriod <= 365 days,
            "Invalid period"
        );

        owner = msg.sender;

        // CORE will be initialized via initializeCore() after RLDCore deployment
        CORE = address(0);
        POOL_MANAGER = poolManager;
        POSITION_TOKEN_IMPL = positionTokenImpl;
        PRIME_BROKER_IMPL = primeBrokerImpl;
        GHOST_ORACLE = ghostOracle;
        STD_FUNDING_MODEL = fundingModel;
        GHOST_ROUTER = ghostRouter;
        METADATA_RENDERER = metadataRenderer;
        FUNDING_PERIOD = _fundingPeriod;
        BROKER_ROUTER = brokerRouter;
    }

    /* ============================================================================================ */
    /*                                     EXTERNAL FUNCTIONS                                       */
    /* ============================================================================================ */

    /**
     * @notice Initializes the CORE address after RLDCore deployment
     * @dev This enables atomic deployment pattern:
     *      1. Deploy RLDMarketFactory (CORE = address(0))
     *      2. Deploy RLDCore(factoryAddress) - factory set in constructor
     *      3. Call factory.initializeCore(coreAddress) - completes the link
     *
     * @dev Security:
     *      - Only callable by deployer (msg.sender from constructor)
     *      - Can only be called once (coreInitialized flag)
     *      - CORE address cannot be zero
     *
     * @param _core The deployed RLDCore contract address
     *
     * @custom:security This prevents CRITICAL-001 front-running vulnerability
     */
    function initializeCore(address _core) external {
        require(msg.sender == DEPLOYER, "Not deployer");
        require(!coreInitialized, "Already initialized");
        require(_core != address(0), "Invalid core");

        CORE = _core;
        coreInitialized = true;
    }

    /**
     * @notice Deploys a new RLD market with full Uniswap V4 integration
     * @dev Executes a 7-phase atomic deployment:
     *      1. Validation - Fail fast on invalid params
     *      2. Identification - Precompute deterministic MarketId
     *      3. Infrastructure - Deploy PrimeBrokerFactory + BrokerVerifier
     *      4. Assets - Clone and initialize PositionToken (wRLP)
     *      5. Mechanics - Initialize V4 pool with correct pricing
     *      6. Registration - Register market with RLDCore
     *      7. Verification - Assert MarketId matches expectation
     *
     * @param params Complete market configuration (see DeployParams struct)
     * @return marketId The unique identifier for the created market
     * @return brokerFactory Address of the deployed PrimeBrokerFactory
     *
     * @custom:security Only callable by owner
     * @custom:reverts MarketAlreadyExists if market with same params exists
     * @custom:reverts IDMismatch if computed ID differs from RLDCore's
     */
    function createMarket(
        DeployParams calldata params
    )
        external
        onlyOwner
        nonReentrant
        returns (MarketId marketId, address brokerFactory)
    {
        // 0. Ensure CORE is initialized
        require(CORE != address(0), "Core not initialized");

        // 1. Validation Phase (Fail Fast)
        _validateParams(params);

        // 2. Identification Phase - Compute expected MarketId
        MarketId futureId = _precomputeId(params);

        // 2a. Duplicate Check (Fail Fast - Save Gas)
        // Check BEFORE deploying contracts to avoid wasting ~600k gas
        bytes32 canonicalKey = MarketId.unwrap(futureId);
        if (MarketId.unwrap(canonicalMarkets[canonicalKey]) != bytes32(0)) {
            revert MarketAlreadyExists();
        }

        // 3. Infrastructure Phase - Deploy broker infrastructure
        address verifier;
        (brokerFactory, verifier) = _deployInfrastructure(futureId, params);

        // 4. Asset Phase - Deploy position token
        address positionToken = _deployPositionToken(params);

        // 5. Market Mechanics Phase - Initialize Uniswap V4 pool
        _initializePool(positionToken, params);

        // 6. Registration Phase - Register with RLDCore
        marketId = _registerMarket(
            params,
            positionToken,
            verifier,
            brokerFactory
        );

        // 7. Post-Condition Check (Critical Security Invariant)
        // Ensures deterministic ID generation is consistent with RLDCore
        if (MarketId.unwrap(marketId) != MarketId.unwrap(futureId)) {
            revert IDMismatch();
        }
    }

    /* ============================================================================================ */
    /*                                     INTERNAL FUNCTIONS                                       */
    /* ============================================================================================ */

    /**
     * @notice Validates all deployment parameters
     * @dev Checks for:
     *      - Non-zero critical addresses
     *      - Sane risk parameters (minColRatio > 100%, minColRatio > maintenanceMargin)
     *      - Valid V4 configuration (positive tickSpacing and oraclePeriod)
     *
     * @param params The deployment parameters to validate
     */
    function _validateParams(DeployParams calldata params) internal pure {
        // Critical Address Checks
        require(params.underlyingPool != address(0), "Invalid Pool");
        require(params.underlyingToken != address(0), "Invalid Underlying");
        require(params.collateralToken != address(0), "Invalid Collateral");
        require(params.liquidationModule != address(0), "Invalid LiqModule");
        // Optional override; if provided must be non-zero and valid period constraints.
        if (params.fundingPeriod != 0) {
            require(
                params.fundingPeriod >= 1 days &&
                    params.fundingPeriod <= 365 days,
                "Invalid period"
            );
        }
        //require(params.spotOracle != address(0), "Invalid SpotOracle");
        require(params.rateOracle != address(0), "Invalid RateOracle");
        require(params.curator != address(0), "Invalid Curator");

        // Risk Parameter Logic Checks
        require(params.minColRatio > 1e18, "MinCol < 100%");
        require(params.maintenanceMargin >= 1e18, "Maintenance < 100%");
        require(
            params.minColRatio > params.maintenanceMargin,
            "Risk Config Error"
        );
        require(
            params.liquidationCloseFactor > 0 &&
                params.liquidationCloseFactor <= 1e18,
            "Invalid CloseFactor"
        );

        // V4 Configuration Checks
        require(params.tickSpacing > 0, "Invalid TickSpacing");
        require(params.oraclePeriod >= 60, "OraclePeriod < 1 min");
    }

    /**
     * @notice Precomputes the deterministic MarketId
     * @dev MarketId = keccak256(collateralToken, underlyingToken, underlyingPool)
     *      This must match RLDCore.createMarket() computation exactly
     *
     * @param params Deployment parameters containing the three key addresses
     * @return The precomputed MarketId
     */
    function _precomputeId(
        DeployParams calldata params
    ) internal pure returns (MarketId) {
        return
            MarketId.wrap(
                keccak256(
                    abi.encode(
                        params.collateralToken,
                        params.underlyingToken,
                        params.underlyingPool
                    )
                )
            );
    }

    /**
     * @notice Deploys broker infrastructure for the market
     * @dev Creates:
     *      1. PrimeBrokerFactory - Clones PrimeBroker instances for each position
     *      2. BrokerVerifier - Validates that brokers belong to this factory
     *
     * @param id The MarketId for naming the NFT collection
     * @param params Deployment parameters (uses underlyingToken for symbol)
     * @return factory The deployed PrimeBrokerFactory address
     * @return verifier The deployed BrokerVerifier address
     */
    function _deployInfrastructure(
        MarketId id,
        DeployParams calldata params
    ) internal returns (address factory, address verifier) {
        // SECURITY: Use try-catch with gas limit to prevent:
        // 1. Gas bombs from malicious token contracts
        // 2. Reverts from non-standard ERC20s without symbol()
        // 3. DOS attacks during market creation
        // CRITICAL for future permissionless market deployment workflow where
        // untrusted tokens may be used to create markets without owner approval
        string memory symbol;
        try ERC20(params.underlyingToken).symbol{gas: 50000}() returns (
            string memory s
        ) {
            symbol = s;
        } catch {
            symbol = "UNKNOWN"; // Fallback for non-standard tokens
        }
        string memory name = string(abi.encodePacked("RLD: ", symbol));
        string memory nftSymbol = string(abi.encodePacked("RLD-", symbol));

        // Deploy factory for this market
        // NOTE: METADATA_RENDERER is passed but currently unused by PrimeBrokerFactory
        // Reserved for future on-chain metadata rendering (see METADATA_RENDERER docs above)
        // CORE is passed so brokers can call RLDCore during initialization
        // Build default operators array (BrokerRouter if configured)
        address[] memory operators;
        if (BROKER_ROUTER != address(0)) {
            operators = new address[](1);
            operators[0] = BROKER_ROUTER;
        } else {
            operators = new address[](0);
        }

        PrimeBrokerFactory pbFactory = new PrimeBrokerFactory(
            PRIME_BROKER_IMPL,
            id,
            name,
            nftSymbol,
            METADATA_RENDERER, // Currently unused, reserved for future
            CORE, // Passed to brokers during init
            operators // BrokerRouter pre-approved on every broker
        );
        factory = address(pbFactory);

        // Deploy verifier linked to this factory
        verifier = address(new BrokerVerifier(factory));
    }

    /**
     * @notice Deploys and initializes the PositionToken (wRLP)
     * @dev Deploys a new ERC20 contract for each market
     *      Position token decimals match collateral decimals for 1:1 representation
     *      e.g., wRLPaUSDC has 6 decimals, wRLPaDAI has 18 decimals
     *
     * @param params Deployment parameters
     * @return tokenAddr The deployed PositionToken address
     */
    function _deployPositionToken(
        DeployParams calldata params
    ) internal returns (address tokenAddr) {
        // Query collateral token decimals for position token
        uint8 collateralDecimals = ERC20(params.collateralToken).decimals();

        // Deploy new PositionToken with matching decimals
        PositionToken token = new PositionToken(
            params.positionTokenName,
            params.positionTokenSymbol,
            collateralDecimals, // Match collateral decimals (e.g., 6 for aUSDC, 18 for aDAI)
            params.collateralToken // Backing asset (yield-bearing)
        );

        tokenAddr = address(token);
    }

    /**
     * @notice Initializes the Uniswap V4 pool for wRLP <-> collateral trading
     * @dev Key operations:
     *      1. Orders currencies (V4 requires token0 < token1)
     *      2. Fetches oracle price and inverts if necessary
     *      3. Computes sqrtPriceX96 in Q64.96 format
     *      4. Initializes V4 pool at computed price
     *      5. Sets TWAMM price bounds [0.0001, 100] relative to wRLP
     *      6. Registers pool with singleton oracle for TWAP queries
     *
     * @dev Price calculation:
     *      - Oracle returns: price = collateral per wRLP (e.g., 10 aUSDC per wRLP)
     *      - V4 stores: sqrtPrice = sqrt(token1/token0) * 2^96
     *      - If wRLP is token1, we invert the price
     *
     * @param positionToken The deployed wRLP token address
     * @param params Deployment parameters
     * @return The PoolId as bytes32
     */
    function _initializePool(
        address positionToken,
        DeployParams calldata params
    ) internal returns (bytes32) {
        // Step 1: Order currencies (V4 requires currency0 < currency1)
        Currency currency0 = Currency.wrap(positionToken);
        Currency currency1 = Currency.wrap(params.collateralToken);
        if (currency0 > currency1) {
            (currency0, currency1) = (currency1, currency0);
        }

        // Step 2: Fetch index price from oracle
        // Returns price in WAD: how many collateral tokens per 1 wRLP
        // NOTE: Use underlyingToken (e.g., USDC) for Aave rate lookup, not collateralToken (aUSDC)
        uint256 indexPrice = IRLDOracle(params.rateOracle).getIndexPrice(
            params.underlyingPool,
            params.underlyingToken
        );

        // Validate oracle price matches TWAMM bounds
        // This ensures consistency with TWAMM price bounds and prevents extreme prices
        require(
            indexPrice >= MIN_PRICE && indexPrice <= MAX_PRICE,
            "Price out of bounds"
        );

        // Step 3: Invert if wRLP is token1
        // V4 stores price as token1/token0, so if wRLP is token1, we need 1/price
        if (Currency.wrap(positionToken) == currency1) {
            indexPrice = 1e36 / indexPrice;
        }

        // Step 4: Calculate sqrtPriceX96
        // Formula: sqrtPriceX96 = sqrt(price) * 2^96
        // Since price is in WAD (1e18), we divide by 1e9 (sqrt(1e18))
        uint160 initSqrtPrice = uint160(
            (FixedPointMathLib.sqrt(indexPrice) * (1 << 96)) / 1e9
        );

        // Step 5: Build PoolKey
        PoolKey memory key = PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: params.poolFee,
            tickSpacing: params.tickSpacing,
            hooks: IHooks(address(0))
        });

        // Step 6: Initialize pool at computed price
        IPoolManager(POOL_MANAGER).initialize(key, initSqrtPrice);

        // Step 7: Register market with GhostRouter
        // The router natively wraps the V4 pool and acts as the central settlement hub.
        // It provides the spot price oracle from V4 slot0 and tracks TWAP cumulatives.
        if (GHOST_ROUTER != address(0)) {
            IGhostRouter(GHOST_ROUTER).initializeMarketWithUniswapOracle(key);
        }

        // Step 8: Register with singleton oracle for TWAP queries
        // CRITICAL: Oracle uses positionToken (wRLP) as lookup key
        // When queried via getSpotPrice(collateralToken, underlyingToken):
        //   - collateralToken param = positionToken (wRLP)
        //   - underlyingToken param = actual collateral (e.g., aUSDC)
        // The oracle will validate tokens match pool's currency0/currency1 in either order

        // Verify oracle can handle the pool configuration
        // Pool currencies are ordered by address, but oracle queries by semantic meaning
        address token0 = Currency.unwrap(currency0);
        address token1 = Currency.unwrap(currency1);
        require(
            (positionToken == token0 && params.collateralToken == token1) ||
                (positionToken == token1 && params.collateralToken == token0),
            "Oracle token mismatch"
        );

        GhostSingletonOracle(GHOST_ORACLE).registerPool(
            positionToken,
            key,
            GHOST_ROUTER,
            params.oraclePeriod
        );

        return PoolId.unwrap(key.toId());
    }

    /**
     * @notice Registers the market with RLDCore and completes setup
     * @dev Final steps:
     *      1. Checks market doesn't already exist (via canonicalKey)
     *      2. Builds MarketAddresses and MarketConfig structs
     *      3. Registers with RLDCore.createMarket()
     *      4. Links PositionToken to MarketId
     *      5. Transfers PositionToken ownership to RLDCore
     *      6. Emits MarketDeployed event with all component addresses
     *
     * @param params Deployment parameters
     * @param positionToken The deployed wRLP address
     * @param verifier The deployed BrokerVerifier address
     * @param brokerFactory The deployed PrimeBrokerFactory address
     * @return marketId The registered MarketId from RLDCore
     */
    function _registerMarket(
        DeployParams calldata params,
        address positionToken,
        address verifier,
        address brokerFactory
    ) internal returns (MarketId marketId) {
        address fundingModel = params.fundingModel == address(0)
            ? STD_FUNDING_MODEL
            : params.fundingModel;
        uint32 fundingPeriod = params.fundingPeriod == 0
            ? FUNDING_PERIOD
            : params.fundingPeriod;

        // Compute canonical key (must match _precomputeId exactly)
        bytes32 canonicalKey = keccak256(
            abi.encode(
                params.collateralToken,
                params.underlyingToken,
                params.underlyingPool
            )
        );

        // Note: Duplicate check now performed in createMarket() before deployment

        // Build addresses struct for RLDCore
        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: params.collateralToken,
            underlyingToken: params.underlyingToken,
            underlyingPool: params.underlyingPool,
            rateOracle: params.rateOracle,
            spotOracle: params.spotOracle,
            markOracle: GHOST_ORACLE,
            fundingModel: fundingModel,
            curator: params.curator,
            liquidationModule: params.liquidationModule,
            positionToken: positionToken,
            settlementModule: params.settlementModule
        });

        // Build config struct for RLDCore
        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            minColRatio: params.minColRatio,
            maintenanceMargin: params.maintenanceMargin,
            liquidationCloseFactor: params.liquidationCloseFactor,
            fundingPeriod: fundingPeriod,
            badDebtPeriod: 7 days, // Default 7-day socialization period
            debtCap: type(uint128).max, // Unlimited by default (max sentinel = no cap)
            minLiquidation: 0, // Default 0. Curator must manually configure based on asset price.
            liquidationParams: params.liquidationParams,
            decayRateWad: params.decayRateWad,
            brokerVerifier: verifier
        });

        // Register with RLDCore
        marketId = IRLDCore(CORE).createMarket(addresses, config);
        canonicalMarkets[canonicalKey] = marketId;

        // Link PositionToken to this market
        PositionToken(positionToken).setMarketId(marketId);

        // Transfer ownership to RLDCore (for minting/burning control)
        PositionToken(positionToken).transferOwnership(CORE);

        emit MarketDeployed(
            marketId,
            params.underlyingPool,
            params.collateralToken,
            positionToken,
            brokerFactory,
            verifier
        );
    }
}
