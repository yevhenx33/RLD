// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../../shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../shared/interfaces/ISpotOracle.sol";
import {IFundingModel} from "../../shared/interfaces/IFundingModel.sol";
import {RLDStorage} from "./RLDStorage.sol";
import {TransientStorage} from "../../shared/libraries/TransientStorage.sol";
import {FixedPointMath} from "../../shared/libraries/FixedPointMath.sol";
import {IERC20} from "../../shared/interfaces/IERC20.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ILiquidationModule} from "../../shared/interfaces/ILiquidationModule.sol";

import {PositionToken} from "../tokens/PositionToken.sol";
import {IBrokerVerifier} from "../../shared/interfaces/IBrokerVerifier.sol";
import {IPrimeBroker} from "../../shared/interfaces/IPrimeBroker.sol";

/// @title RLD Core Singleton
/// @author RLD Protocol
/// @notice The central hyperstructure managing all RLD markets, positions, and liquidations.
/// @dev This is a singleton contract - there is only one instance managing all markets.
///
/// ## Architecture Overview
///
/// RLDCore is the central hub of the RLD Protocol, responsible for:
///
/// 1. **Market Registry**: Creating and storing market configurations
/// 2. **Flash Accounting**: Uniswap V4-style lock pattern for atomic operations
/// 3. **Position Management**: Tracking user debt (collateral delegated to PrimeBroker)
/// 4. **Solvency Enforcement**: Post-operation health checks for all touched positions
/// 5. **Liquidation**: Permissionless liquidation of undercollateralized positions
///
/// ## Flash Accounting Pattern
///
/// ```
/// User → lock(data)
///   │
///   ├─1─→ Sets LOCK_HOLDER in transient storage
///   │
///   ├─2─→ Calls user.lockAcquired(data)
///   │        └── User performs operations (modifyPosition, etc.)
///   │            └── Each operation adds to TOUCHED_LIST
///   │
///   ├─3─→ _checkSolvencyOfTouched()
///   │        └── Iterates all touched positions
///   │            └── Reverts if any position is insolvent
///   │
///   └─4─→ Clears transient storage
/// ```
///
/// ## Collateral Architecture
///
/// Unlike traditional lending protocols, RLDCore does NOT hold collateral directly.
/// Instead, collateral is managed by PrimeBroker contracts:
///
/// - **Core tracks**: Debt principal only
/// - **Broker tracks**: All assets (collateral, yields, LP positions)
/// - **Solvency check**: Queries `broker.getNetAccountValue()` vs debt value
///
/// This enables sophisticated collateral strategies within the broker while
/// keeping Core logic simple and gas-efficient.
///
/// ## Key Invariants
///
/// 1. Only the factory can create markets
/// 2. Only the lock holder can modify their position during a lock
/// 3. All touched positions must be solvent when lock is released
/// 4. Debt tokenization (wRLP) always matches actual debt principal
/// 5. Liquidations can only occur when position is below maintenance margin
contract RLDCore is IRLDCore, RLDStorage {
    using FixedPointMath for uint256;
    using SafeTransferLib for ERC20;

    /* ============================================================================================ */
    /*                                        FACTORY SETUP                                         */
    /* ============================================================================================ */

    /// @notice The trusted factory address allowed to create markets.
    /// @dev Set once via `setFactory()`. Cannot be changed after initial set.
    address public factory;

    /// @notice Sets the factory address. Can only be called once.
    /// @dev Should be called immediately after deployment, ideally in the same transaction.
    /// @dev This is a one-time initialization pattern - no owner/admin can change it later.
    /// @param _factory The address of the RLDMarketFactory contract
    function setFactory(address _factory) external {
        if (factory != address(0)) revert Unauthorized();
        if (_factory == address(0)) revert InvalidParam("Factory");
        factory = _factory;
        emit SecurityUpdate(MarketId.wrap(bytes32(0)), "SetFactory", msg.sender);
    }

    /* ============================================================================================ */
    /*                                        MARKET LOGIC                                          */
    /* ============================================================================================ */

    /// @notice Creates a new RLD market with the given addresses and configuration.
    /// @dev Only callable by the registered factory.
    /// @dev MarketId is deterministically computed from (collateral, underlying, pool) tuple.
    /// @dev Validates all critical addresses are non-zero to prevent deployment with invalid deps.
    /// @param addresses The immutable addresses defining market infrastructure
    /// @param config The risk parameters and market configuration
    /// @return The unique MarketId for this market
    function createMarket(MarketAddresses calldata addresses, MarketConfig calldata config) external override onlyFactory returns (MarketId) {

        // === Validate Critical Addresses ===
        // All core addresses must be non-zero to ensure market functions correctly
        if (addresses.collateralToken == address(0)) revert InvalidParam("Collateral");
        if (addresses.underlyingToken == address(0)) revert InvalidParam("Underlying");
        if (addresses.rateOracle == address(0)) revert InvalidParam("Rate Oracle");
        if (addresses.spotOracle == address(0)) revert InvalidParam("Spot Oracle");
        if (addresses.fundingModel == address(0)) revert InvalidParam("Funding");
        if (addresses.positionToken == address(0)) revert InvalidParam("Position Token");
        if (addresses.liquidationModule == address(0)) revert InvalidParam("LiqModule");
        
        // === Generate Deterministic MarketId ===
        // Hash of (collateral, underlying, pool) ensures:
        // 1. Same tokens with different pools = different markets
        // 2. Predictable IDs for off-chain systems
        // 3. No duplicate markets possible
        MarketId id = MarketId.wrap(keccak256(abi.encode(
            addresses.collateralToken,
            addresses.underlyingToken,
            addresses.underlyingPool // Pool distinguishes markets
        )));

        // === Duplicate Check ===
        if (marketExists[id]) revert MarketAlreadyExists();

        // === Store Market Data ===
        marketExists[id] = true;
        marketAddresses[id] = addresses;
        marketConfigs[id] = config;
        marketStates[id] = MarketState({
            normalizationFactor: 1e18,  // Start at 1:1 (no accrued interest)
            lastUpdateTimestamp: uint48(block.timestamp)
        });

        emit MarketCreated(id, addresses.collateralToken, addresses.underlyingToken, addresses.underlyingPool);
        return id;
    }

    /* ============================================================================================ */
    /*                                      FLASH ACCOUNTING                                        */
    /* ============================================================================================ */

    /// @notice Modifier requiring an active lock.
    /// @dev Operations like modifyPosition can only be called during a lock session.
    modifier onlyLock() {
        if (!_isLocked()) revert NotLocked();
        _;
    }

    /// @notice Modifier requiring caller to be the current lock holder.
    /// @dev Prevents other contracts from modifying positions during someone else's lock.
    modifier onlyLockHolder() {
        if (msg.sender != _getLockHolder()) revert Unauthorized();
        _;
    }

    /// @notice Modifier requiring caller to be the registered factory.
    modifier onlyFactory() {
        if (msg.sender != factory) revert Unauthorized();
        _;
    }

    /// @notice Modifier requiring caller to be the market's curator.
    /// @param id The market ID to check curator for
    modifier onlyCurator(MarketId id) {
        if (msg.sender != marketAddresses[id].curator) revert Unauthorized();
        _;
    }

    /// @notice Acquires a lock for atomic operations and enforces solvency post-callback.
    /// @dev This is the entry point for all position modifications.
    /// @dev Pattern inspired by Uniswap V4's flash accounting.
    ///
    /// ## Execution Flow:
    /// 1. Store msg.sender as lock holder in transient storage
    /// 2. Reset touched positions counter
    /// 3. Call `lockAcquired(data)` on msg.sender
    /// 4. Check solvency of all touched positions
    /// 5. Clear transient storage
    ///
    /// ## Security Properties:
    /// - Reentrancy-safe: Transient storage isolates each lock session
    /// - Atomic: Either all operations succeed with valid solvency, or all revert
    /// - Gas-efficient: Uses EIP-1153 transient storage (cleared automatically)
    ///
    /// @param data Arbitrary data passed to the lockAcquired callback
    /// @return Result of the lockAcquired callback
    function lock(bytes calldata data) external override returns (bytes memory) {
        // 1. Enter Lock - Store caller as lock holder
        TransientStorage.tstore(LOCK_HOLDER_KEY, uint256(uint160(msg.sender)));

        // Reset touched positions counter for this session
        TransientStorage.tstore(TOUCHED_COUNT_KEY, 0);

        // 2. Callback - Invoke lockAcquired on the caller
        // The caller performs their operations (modifyPosition, etc.)
        bytes memory result;
        try IRLDCore(msg.sender).lockAcquired(data) returns (bytes memory res) {
            result = res;
        } catch (bytes memory reason) {
            // Propagate revert reason from callback
            assembly {
                revert(add(reason, 32), mload(reason))
            }
        }

        // 3. Exit Lock & Enforce Solvency
        // All positions touched during the callback must be solvent
        _checkSolvencyOfTouched();
        
        // 4. Cleanup - Clear transient storage
        TransientStorage.tstore(LOCK_HOLDER_KEY, 0);
        TransientStorage.tstore(TOUCHED_COUNT_KEY, 0);
        
        return result;
    }
    
    /// @notice Default callback implementation - always reverts.
    /// @dev The actual callback is implemented by the caller (e.g., PrimeBroker).
    /// @dev This exists to satisfy the IRLDCore interface.
    function lockAcquired(bytes calldata) external pure returns (bytes memory) {
        revert("Not Implemented by Core");
    }

    /* ============================================================================================ */
    /*                                     POSITION MANAGEMENT                                      */
    /* ============================================================================================ */

    /// @notice Modifies a user's position by adjusting debt.
    /// @dev Only callable during an active lock by the lock holder.
    /// @dev Collateral is managed by PrimeBroker, not tracked in Core.
    ///
    /// ## Key Operations:
    /// 1. Apply pending funding (lazy update)
    /// 2. Update debt principal
    /// 3. Mint/burn wRLP tokens to match debt
    /// 4. Track action type for solvency ratio selection
    /// 5. Add position to touched list for solvency check
    ///
    /// @param id The market ID
    /// @param deltaCollateral Unused - kept for interface compatibility (collateral in broker)
    /// @param deltaDebt Change in debt principal (positive = borrow, negative = repay)
    function modifyPosition(MarketId id, int256 deltaCollateral, int256 deltaDebt) external onlyLock onlyLockHolder {
        // Note: deltaCollateral is unused - collateral is managed by PrimeBroker
        // Parameter kept for interface compatibility
        
        // 1. Update Funding (Lazy) - Applies accrued interest to normalization factor
        _applyFunding(id);

        Position storage pos = positions[id][msg.sender];
        MarketAddresses storage addresses = marketAddresses[id];

        // 2. Update Debt Principal
        if (deltaDebt != 0) {
            uint256 newDebt = _applyDelta(pos.debtPrincipal, deltaDebt);
            pos.debtPrincipal = uint128(newDebt);
        }

        // 3. Tokenize Debt Changes (wRLP)
        // wRLP tokens represent debt obligations and can be traded
        if (deltaDebt > 0) {
            // Minting debt = creating short position
            PositionToken(addresses.positionToken).mint(msg.sender, uint256(deltaDebt));
        } else if (deltaDebt < 0) {
            // Repaying debt = closing short position
            PositionToken(addresses.positionToken).burn(msg.sender, uint256(-deltaDebt));
        }

        // 4. Track Action Type for Solvency Ratio Selection
        // - Type 1: Maintenance operations → uses maintenanceMargin (less strict)
        // - Type 2: New minting → uses minColRatio (more strict)
        bytes32 actionKey = keccak256(abi.encode(id, msg.sender, ACTION_SALT));
        uint256 currentType = TransientStorage.tload(actionKey);
        uint256 newType = deltaDebt > 0 ? 2 : 1; 
        
        // Only upgrade action type, never downgrade (most restrictive wins)
        if (newType > currentType) {
            TransientStorage.tstore(actionKey, newType);
        }
        
        // 5. Add to Touched List for Post-Lock Solvency Check
        _addTouchedPosition(id, msg.sender);

        emit PositionModified(id, msg.sender, deltaCollateral, deltaDebt);
    }

    /* ============================================================================================ */
    /*                                     SOLVENCY CHECKING                                        */
    /* ============================================================================================ */

    /// @notice Iterates through all touched (Market, Account) pairs and verifies solvency.
    /// @dev Called automatically when lock is released.
    /// @dev Reverts if any touched position is insolvent.
    function _checkSolvencyOfTouched() internal view {
        uint256 count = TransientStorage.tload(TOUCHED_COUNT_KEY);
        for (uint256 i = 0; i < count; i++) {
            (MarketId id, address user) = _getTouchedPosition(i);
            MarketConfig storage config = marketConfigs[id];
            
            // Retrieve action type to determine which ratio to use
            bytes32 actionKey = keccak256(abi.encode(id, user, ACTION_SALT));
            uint256 actionType = TransientStorage.tload(actionKey);
            
            // Type 2 (minting) = stricter minColRatio
            // Type 1 or 0 (default) = more lenient maintenanceMargin
            uint256 requiredRatio = actionType == 2 
                ? uint256(config.minColRatio) 
                : uint256(config.maintenanceMargin);
            
            _checkSolvency(id, user, requiredRatio);
        }
    }

    /// @notice Checks if a specific user is solvent with a custom ratio.
    /// @dev Reverts with Insolvent error if check fails.
    /// @param id The market ID
    /// @param user The user to check
    /// @param minRatio The minimum collateralization ratio required (WAD format)
    function _checkSolvency(MarketId id, address user, uint256 minRatio) internal view {
        if (!_isSolvent(id, user, minRatio)) {
            revert Insolvent(user);
        }
    }

    /// @notice Calculates whether a position is solvent.
    /// @dev Solvency equation: brokerValue >= debtValue * minRatio
    ///
    /// ## Calculation Steps:
    /// 1. Verify user is a valid broker (non-brokers always insolvent)
    /// 2. Calculate true debt: principal * normalizationFactor * price
    /// 3. Get total value from broker: IPrimeBroker.getNetAccountValue()
    /// 4. Compare: totalValue >= debtValue * minRatio
    ///
    /// @param id The market ID
    /// @param user The user to check (must be a PrimeBroker)
    /// @param minRatio The minimum ratio (1.5e18 = 150%)
    /// @return True if position meets the minimum ratio
    function _isSolvent(MarketId id, address user, uint256 minRatio) internal view returns (bool) {
        Position memory pos = positions[id][user];
        
        // No debt = always solvent
        if (pos.debtPrincipal == 0) return true;

        MarketAddresses storage addresses = marketAddresses[id];
        MarketState memory state = marketStates[id];
        MarketConfig memory config = marketConfigs[id];

        // 1. Verify Broker Status (Strict)
        // Only verified brokers can have positions - prevents arbitrary contracts
        if (config.brokerVerifier == address(0)) return false;
        if (!IBrokerVerifier(config.brokerVerifier).isValidBroker(user)) return false;

        // 2. Calculate Debt Value
        // True Debt = Principal * NormalizationFactor (accounts for accrued interest)
        uint256 trueDebt = uint256(pos.debtPrincipal).mulWad(state.normalizationFactor);
        
        // Get price in collateral terms
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool, 
            addresses.collateralToken
        );
        uint256 debtValue = trueDebt.mulWad(indexPrice);

        // 3. Get Total Assets from Broker
        // Broker reports total value of all its holdings (including wRLP)
        uint256 totalAssets = IPrimeBroker(user).getNetAccountValue();
        
        // 4. CRITICAL FIX: Calculate Net Worth
        // Net worth = Assets - Liabilities
        // This prevents double-counting wRLP (which appears in both assets and debt)
        if (totalAssets < debtValue) return false; // Underwater
        uint256 netWorth = totalAssets - debtValue;
        
        // 5. Check Margin Requirement
        // Net worth must be at least (minRatio - 100%) of debt
        // Example: 150% ratio → net worth ≥ 50% of debt
        // Derivation: netWorth >= debt × (ratio - 1)
        //             assets - debt >= debt × (ratio - 1)
        //             assets >= debt × ratio (original formula, but with debt subtracted first)
        uint256 marginRequirement = minRatio - 1e18;
        return netWorth >= debtValue.mulWad(marginRequirement);
    }

    /* ============================================================================================ */
    /*                                     FUNDING APPLICATION                                      */
    /* ============================================================================================ */

    /// @notice Applies pending funding rate to update the normalization factor.
    /// @dev Called lazily on first interaction per block.
    /// @dev Normalization factor compounds over time to track accumulated interest.
    /// @param id The market ID to apply funding for
    function _applyFunding(MarketId id) internal {
        MarketState storage state = marketStates[id];
        MarketAddresses storage addresses = marketAddresses[id];
        
        // 1. Calculate new normalization factor via external model
        (uint256 newNormFactor, ) = IFundingModel(addresses.fundingModel).calculateFunding(
            MarketId.unwrap(id),
            address(this),
            state.normalizationFactor,
            state.lastUpdateTimestamp
        );

        // 2. Update storage with overflow protection
        if (newNormFactor != state.normalizationFactor) {
            require(newNormFactor <= type(uint128).max, "NormFactor overflow");
            state.normalizationFactor = uint128(newNormFactor);
        }
        state.lastUpdateTimestamp = uint48(block.timestamp);
    }

    /// @notice Applies a signed delta to a value with underflow protection.
    /// @dev Used for adjusting collateral and debt values.
    /// @param start The starting value
    /// @param delta The change to apply (can be negative)
    /// @return The new value after applying delta
    function _applyDelta(uint128 start, int256 delta) internal pure returns (uint256) {
        int256 result = int256(uint256(start)) + delta;
        if (result < 0) revert("Underflow");
        return uint256(result);
    }

    /* ============================================================================================ */
    /*                                        LIQUIDATION                                           */
    /* ============================================================================================ */

    /// @notice Liquidates an insolvent position.
    /// @dev Permissionless - anyone can liquidate if position is below maintenance margin.
    ///
    /// ## Liquidation Flow:
    /// 1. Apply pending funding
    /// 2. Verify position is insolvent (below maintenance margin)
    /// 3. Verify user is a valid broker
    /// 4. Check liquidation amount doesn't exceed close factor
    /// 5. Calculate debt value to cover
    /// 6. Decrement debt principal
    /// 7. Burn wRLP from liquidator (they're buying the debt)
    /// 8. Calculate seize amount via liquidation module (includes bonus)
    /// 9. Seize assets from broker to liquidator
    ///
    /// ## Liquidation Economics:
    /// - Liquidator burns wRLP tokens equal to debt covered
    /// - Liquidator receives collateral worth debt + liquidation bonus
    /// - Bonus is calculated by the liquidation module (e.g., Dutch auction)
    ///
    /// @param id The market ID
    /// @param user The user to liquidate (must be a PrimeBroker)
    /// @param debtToCover Amount of debt principal to liquidate
    function liquidate(MarketId id, address user, uint256 debtToCover) external override {
        // 1. Apply Funding - Ensure normalization factor is current
        _applyFunding(id);

        MarketState storage state = marketStates[id];
        MarketAddresses storage addresses = marketAddresses[id];
        MarketConfig storage config = marketConfigs[id];
        
        // 2. Verify Insolvency
        // Position must be below maintenance margin to be liquidatable
        if (_isSolvent(id, user, uint256(config.maintenanceMargin))) {
            revert UserSolvent(user);
        }
        
        // 3. Verify Broker Status
        // Only valid brokers can be liquidated (they hold the collateral)
        if (config.brokerVerifier == address(0) || !IBrokerVerifier(config.brokerVerifier).isValidBroker(user)) {
            revert InvalidBroker(user);
        }

        Position storage pos = positions[id][user];
        
        // 4. Liquidation Cap (Close Factor)
        // Prevents liquidating entire position in one tx (protects borrower)
        if (debtToCover > uint256(pos.debtPrincipal).mulWad(uint256(config.liquidationCloseFactor))) {
            revert CloseFactorExceeded();
        }
        
        // 5. Calculate Debt Value
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool, 
            addresses.underlyingToken
        );
        
        // Cost = DebtToCover * NormFactor * IndexPrice
        uint256 brokerCost = uint256(debtToCover).mulWad(state.normalizationFactor).mulWad(indexPrice);

        // 6. Decrement Debt (optimistically - will be reduced by amount actually covered)
        pos.debtPrincipal -= uint128(debtToCover);

        // 7. Calculate Seize Amount
        // Spot price: Use oracle if configured, else assume 1:1 parity
        uint256 spotPrice = addresses.spotOracle != address(0)
            ? ISpotOracle(addresses.spotOracle).getSpotPrice(addresses.collateralToken, addresses.underlyingToken)
            : 1e18;
            
        ILiquidationModule.PriceData memory priceData = ILiquidationModule.PriceData({
            indexPrice: indexPrice,
            spotPrice: spotPrice,
            normalizationFactor: state.normalizationFactor
        });

        // Pass real values for accurate bonus calculation
        uint256 userAssetValue = IPrimeBroker(user).getNetAccountValue();
        uint256 trueDebtValue = uint256(pos.debtPrincipal).mulWad(state.normalizationFactor).mulWad(indexPrice);
        
        ( , uint256 seizeAmount) = ILiquidationModule(addresses.liquidationModule).calculateSeizeAmount(
            debtToCover, 
            userAssetValue,   // Real collateral value from broker
            trueDebtValue,    // Real remaining debt value
            priceData, 
            config, 
            config.liquidationParams
        );
        
        // 8. Seize Assets - returns wRLP extracted from broker's positions
        IPrimeBroker.SeizeOutput memory seizeOutput = IPrimeBroker(user).seize(seizeAmount, msg.sender);
        
        // 9. Use extracted wRLP to offset debt (cap at debtToCover)
        uint256 wRLPFromBroker = seizeOutput.wRLPExtracted > debtToCover 
            ? debtToCover 
            : seizeOutput.wRLPExtracted;
        
        // 10. Burn extracted wRLP (transferred from broker to Core)
        if (wRLPFromBroker > 0) {
            PositionToken(addresses.positionToken).burn(address(this), wRLPFromBroker);
        }
        
        // 11. Burn remaining debt from liquidator
        // Liquidator only needs to provide the delta not covered by broker's wRLP
        uint256 liquidatorOwes = debtToCover - wRLPFromBroker;
        if (liquidatorOwes > 0) {
            PositionToken(addresses.positionToken).burn(msg.sender, liquidatorOwes);
        }
        
        emit PositionModified(id, user, 0, -int256(debtToCover));
    }

    /* ============================================================================================ */
    /*                                         VIEW FUNCTIONS                                       */
    /* ============================================================================================ */

    /// @notice Checks if a user's position is currently solvent.
    /// @dev Uses maintenance margin as the threshold.
    /// @param id The market ID
    /// @param user The user to check
    /// @return True if position is solvent (above maintenance margin)
    function isSolvent(MarketId id, address user) external view override returns (bool) {
        MarketConfig storage config = marketConfigs[id];
        return _isSolvent(id, user, uint256(config.maintenanceMargin));
    }

    /// @notice Checks if a market exists.
    /// @param id The market ID to check
    /// @return True if the market exists
    function isValidMarket(MarketId id) external view override returns (bool) {
        return marketExists[id];
    }

    /// @notice Returns the current state of a market.
    /// @param id The market ID
    /// @return The market state (normalizationFactor, lastUpdateTimestamp)
    function getMarketState(MarketId id) external view returns (MarketState memory) {
        return marketStates[id];
    }

    /// @notice Returns the addresses associated with a market.
    /// @param id The market ID
    /// @return All market addresses
    function getMarketAddresses(MarketId id) external view returns (MarketAddresses memory) {
        return marketAddresses[id];
    }

    /// @notice Returns the configuration of a market.
    /// @param id The market ID
    /// @return The market configuration
    function getMarketConfig(MarketId id) external view returns (MarketConfig memory) {
        return marketConfigs[id];
    }

    /// @notice Returns a user's position in a market.
    /// @param id The market ID
    /// @param user The user's address
    /// @return The user's position (debtPrincipal only - collateral is in broker)
    function getPosition(MarketId id, address user) external view returns (Position memory) {
        return positions[id][user];
    }
}
