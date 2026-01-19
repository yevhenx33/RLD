// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../interfaces/IRLDCore.sol";
import {IRLDMarketFactory} from "../interfaces/IRLDMarketFactory.sol";
import {RLDAaveOracle} from "../modules/oracles/RLDAaveOracle.sol";
import {ChainlinkSpotOracle} from "../modules/oracles/ChainlinkSpotOracle.sol";
import {StandardFundingModel} from "../modules/funding/StandardFundingModel.sol";
import {CDSHook} from "../modules/hooks/CDSHook.sol";
import {StaticLiquidationModule} from "../modules/liquidation/StaticLiquidationModule.sol";
// import {SyntheticBond} from "../vaults/SyntheticBond.sol"; // Circular dep? Factory deploys it.

/// @title RLDMarketFactory
/// @notice Permissionless factory for "One-Click" RLD Markets.
/// @dev Automates Oracle, Hook, and Market creation steps.
contract RLDMarketFactory is IRLDMarketFactory {
    
    IRLDCore public immutable CORE;
    
    // Default Implementations (Immutable for creating clones, or just use references if stateless)
    // For MVP, we pass deployed addresses or deploy new instances if needed.
    // Ideally we have a registry. For now, we hardcode standard modules logic or deploy them.
    
    address public immutable AAVE_RATE_ORACLE; // Stateless
    address public immutable STD_FUNDING_MODEL;
    address public immutable CHAINLINK_SPOT_ORACLE; // Singleton
    address public immutable DEFAULT_ORACLE; // Singleton logic
    address public immutable STATIC_LIQ_MODULE;

    // Pool -> Funding -> MarketType -> MarketId
    mapping(address => mapping(address => mapping(IRLDCore.MarketType => MarketId))) public canonicalMarkets;

    constructor(address core, address fundingModel, address spotOracle, address rateOracle, address defaultOracle) {
        CORE = IRLDCore(core);
        STD_FUNDING_MODEL = fundingModel;
        CHAINLINK_SPOT_ORACLE = spotOracle;
        AAVE_RATE_ORACLE = rateOracle;
        DEFAULT_ORACLE = defaultOracle;
        STATIC_LIQ_MODULE = address(new StaticLiquidationModule());
    }

    function deployMarket(
        address underlyingPool,
        address underlyingToken,
        address collateralToken,
        IRLDCore.MarketType marketType,
        address feeRecipient,
        uint16 mintFeeBps,
        uint16 redeemFeeBps,
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
            fundingModel: STD_FUNDING_MODEL,
            feeHook: address(0), // Set to factory or specific hook?
            hook: address(cdsHook),
            defaultOracle: DEFAULT_ORACLE,
            liquidationModule: module
        });

        // Use feeRecipient for feeHook if desired, or keep as 0 for now until fee logic is strict.
        // Actually, previous implementation used feeRecipient logic.
        addresses.feeHook = feeRecipient;

        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            marketType: marketType,
            mintFeeBps: mintFeeBps,
            redeemFeeBps: redeemFeeBps,
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
    }

    function deployBondVault(MarketId marketId) external override returns (address vault) {
        // vault = new SyntheticBond(marketId, CORE);
        // return address(vault);
        return address(0);
    }
}
