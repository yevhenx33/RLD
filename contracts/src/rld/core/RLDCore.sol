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
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

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
contract RLDCore is IRLDCore, RLDStorage, ReentrancyGuard {
    using FixedPointMath for uint256;
    using SafeTransferLib for ERC20;

    /* ============================================================================================ */
    /*                                        FACTORY SETUP                                         */
    /* ============================================================================================ */

    /// @notice The trusted factory address allowed to create markets.
    /// @dev Set once in constructor. Immutable to prevent any changes post-deployment.
    address public immutable factory;

    /// @notice The Uniswap V4 PoolManager address for fee updates
    address public immutable poolManager;

    /// @notice The TWAMM hook address for V4 pool fee updates
    address public immutable twamm;

    /// @notice Initializes RLDCore with the factory, poolManager, and TWAMM addresses
    /// @dev Factory must be deployed first, then RLDCore is deployed with factory address.
    ///      This prevents front-running attacks on factory initialization.
    /// @param _factory The address of the RLDMarketFactory contract
    /// @param _poolManager The address of the V4 PoolManager
    /// @param _twamm The address of the TWAMM hook contract
    constructor(address _factory, address _poolManager, address _twamm) {
        require(_factory != address(0), "Invalid factory");
        require(_poolManager != address(0), "Invalid poolManager");
        // TWAMM can be 0 for testing (pool fees won't work)
        factory = _factory;
        poolManager = _poolManager;
        twamm = _twamm;
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
            totalDebt: 0,  // No debt initially
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
    /// 1. Check no lock is already active (reentrancy guard)
    /// 2. Store msg.sender as lock holder in transient storage
    /// 3. Reset touched positions counter
    /// 4. Call `lockAcquired(data)` on msg.sender
    /// 5. Check solvency of all touched positions
    /// 6. Clear transient storage
    ///
    /// ## Security Properties:
    /// - Reentrancy-safe: Explicit check prevents nested locks
    /// - Atomic: Either all operations succeed with valid solvency, or all revert
    /// - Gas-efficient: Uses EIP-1153 transient storage (cleared automatically)
    ///
    /// @param data Arbitrary data passed to the lockAcquired callback
    /// @return Result of the lockAcquired callback
    function lock(bytes calldata data) external override returns (bytes memory) {
        // HIGH-001 FIX: Prevent nested locks (reentrancy protection)
        // If a lock is already active, revert to prevent solvency check bypass
        if (TransientStorage.tload(LOCK_ACTIVE_KEY) != 0) {
            revert ReentrancyGuardActive();
        }
        
        // Mark lock as active
        TransientStorage.tstore(LOCK_ACTIVE_KEY, 1);
        
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
            // Clear lock active flag before reverting
            TransientStorage.tstore(LOCK_ACTIVE_KEY, 0);
            // Propagate revert reason from callback
            assembly {
                revert(add(reason, 32), mload(reason))
            }
        }

        // 3. Exit Lock & Enforce Solvency
        // All positions touched during the callback must be solvent
        _checkSolvencyOfTouched();
        
        // 4. Clear lock active flag
        TransientStorage.tstore(LOCK_ACTIVE_KEY, 0);
        
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
        MarketState storage state = marketStates[id];

        // 2. Update Debt Principal
        if (deltaDebt != 0) {
            uint256 newDebt = _applyDelta(pos.debtPrincipal, deltaDebt);
            pos.debtPrincipal = uint128(newDebt);
            
            // Update total debt and check debt cap
            if (deltaDebt > 0) {
                // Increasing debt - check debt cap
                uint256 newTotalDebt = uint256(state.totalDebt) + uint256(deltaDebt);
                
                MarketConfig memory config = _getEffectiveConfig(id);
                if (config.debtCap > 0 && newTotalDebt > config.debtCap) {
                    revert DebtCapExceeded();
                }
                
                state.totalDebt = uint128(newTotalDebt);
            } else {
                // Decreasing debt
                state.totalDebt = uint128(uint256(state.totalDebt) - uint256(-deltaDebt));
            }
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
            MarketConfig memory config = _getEffectiveConfig(id);
            
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
        MarketConfig memory config = _getEffectiveConfig(id);

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
        // HIGH-003 FIX: Use try-catch to prevent malicious brokers from blocking liquidation
        // If broker reverts, treat as insolvent (return false)
        uint256 totalAssets;
        try IPrimeBroker(user).getNetAccountValue() returns (uint256 value) {
            totalAssets = value;
        } catch {
            // Broker reverted - treat as insolvent to allow liquidation
            return false;
        }
        
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
    function liquidate(MarketId id, address user, uint256 debtToCover) external override nonReentrant {
        _applyFunding(id);
        
        MarketConfig memory config = _getEffectiveConfig(id);
        
        // 1. Validation
        _validateLiquidationChecks(id, user, config);
        _validateLiquidationAmount(debtToCover);

        // 2. Debt Calculations & Updates
        (uint256 principalToCover, uint256 normFactor) = _updateLiquidationDebt(id, user, debtToCover, config);

        // 3. Seize Calculation via Oracle & Module
        uint256 seizeAmount = _calculateLiquidationSeize(id, user, debtToCover, normFactor, config);

        // 4. Execution & Settlement (with negative equity protection)
        _settleLiquidation(id, user, seizeAmount, principalToCover, debtToCover, normFactor);
    }

    /* ============================================================================================ */
    /*                                   LIQUIDATION HELPERS                                        */
    /* ============================================================================================ */

    function _validateLiquidationChecks(MarketId id, address user, MarketConfig memory config) internal view {
        if (_isSolvent(id, user, uint256(config.maintenanceMargin))) {
            revert UserSolvent(user);
        }
        if (config.brokerVerifier == address(0) || !IBrokerVerifier(config.brokerVerifier).isValidBroker(user)) {
            revert InvalidBroker(user);
        }
    }

    function _validateLiquidationAmount(uint256 debtToCover) internal pure {
        if (debtToCover < MIN_LIQUIDATION) {
            revert("Liquidation amount too small");
        }
    }

    function _updateLiquidationDebt(
        MarketId id, 
        address user, 
        uint256 debtToCover, 
        MarketConfig memory config
    ) internal returns (uint256 principalToCover, uint256 normFactor) {
        // Cache storage
        MarketState storage state = marketStates[id];
        normFactor = state.normalizationFactor;
        Position storage pos = positions[id][user];
        uint128 principal = pos.debtPrincipal;

        // Verify Close Factor against True Debt
        uint256 trueDebt = uint256(principal).mulWad(normFactor);
        if (debtToCover > trueDebt.mulWad(uint256(config.liquidationCloseFactor))) {
            revert CloseFactorExceeded();
        }

        // Calculate Principal to burn
        principalToCover = debtToCover.divWad(normFactor);
        
        // Update Storage (Optimistic Reduction)
        pos.debtPrincipal = principal - uint128(principalToCover);
    }

    function _calculateLiquidationSeize(
        MarketId id,
        address user,
        uint256 debtToCover,
        uint256 normFactor,
        MarketConfig memory config
    ) internal view returns (uint256 seizeAmount) {
         MarketAddresses storage addresses = marketAddresses[id];
         
         uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool, 
            addresses.underlyingToken
        );
        
        uint256 spotPrice = addresses.spotOracle != address(0)
            ? ISpotOracle(addresses.spotOracle).getSpotPrice(addresses.collateralToken, addresses.underlyingToken)
            : 1e18;

        // PRICE PROTECTION: Use min(spot, index) for liquidator benefit (conservative debt valuation)
        // and max(spot, index) for borrower protection (generous collateral valuation)
        // This prevents arbitrage from price divergence
        uint256 debtPrice = indexPrice < spotPrice ? indexPrice : spotPrice;
        uint256 collateralPrice = indexPrice > spotPrice ? indexPrice : spotPrice;

        ILiquidationModule.PriceData memory priceData = ILiquidationModule.PriceData({
            indexPrice: debtPrice,
            spotPrice: collateralPrice,
            normalizationFactor: normFactor
        });

        // Use updated debt principal for remaining debt calculation
        uint256 remainingTrueDebt = uint256(positions[id][user].debtPrincipal).mulWad(normFactor).mulWad(debtPrice);

        ( , seizeAmount) = ILiquidationModule(addresses.liquidationModule).calculateSeizeAmount(
            debtToCover, 
            IPrimeBroker(user).getNetAccountValue(),
            remainingTrueDebt,
            priceData, 
            config, 
            config.liquidationParams
        );
    }

    function _settleLiquidation(
        MarketId id,
        address user,
        uint256 seizeAmount,
        uint256 principalToCover,
        uint256 debtToCover,
        uint256 normFactor
    ) internal {
        // NEGATIVE EQUITY PROTECTION: Cap seize at available collateral
        uint256 availableCollateral = IPrimeBroker(user).getNetAccountValue();
        uint256 actualSeizeAmount = seizeAmount;
        uint256 actualPrincipalToCover = principalToCover;
        
        if (seizeAmount > availableCollateral) {
            // Adjust seize amount to available collateral
            actualSeizeAmount = availableCollateral;
            
            // Proportionally reduce debt coverage
            // actualDebtCovered = (availableCollateral * debtToCover) / seizeAmount
            uint256 actualDebtCovered = availableCollateral.mulWad(debtToCover).divWad(seizeAmount);
            actualPrincipalToCover = actualDebtCovered.divWad(normFactor);
            
            // Revert the optimistic debt reduction and apply correct amount
            Position storage pos = positions[id][user];
            pos.debtPrincipal = pos.debtPrincipal + uint128(principalToCover) - uint128(actualPrincipalToCover);
        }
        
        IPrimeBroker.SeizeOutput memory seizeOutput = IPrimeBroker(user).seize(actualSeizeAmount, msg.sender);
        
        uint256 wRLPFromBroker = seizeOutput.wRLPExtracted > actualPrincipalToCover 
            ? actualPrincipalToCover 
            : seizeOutput.wRLPExtracted;
        
        address positionToken = marketAddresses[id].positionToken;
        
        if (wRLPFromBroker > 0) {
            PositionToken(positionToken).burn(address(this), wRLPFromBroker);
        }
        
        uint256 liquidatorOwes = actualPrincipalToCover - wRLPFromBroker;
        if (liquidatorOwes > 0) {
            PositionToken(positionToken).burn(msg.sender, liquidatorOwes);
        }
        
        emit PositionModified(id, user, 0, -int256(actualPrincipalToCover));
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
        MarketConfig memory config = _getEffectiveConfig(id);
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
    /// @dev Auto-applies pending risk updates if timelock has expired.
    /// @param id The market ID
    /// @return The market configuration (with pending updates applied if ready)
    function getMarketConfig(MarketId id) external view returns (MarketConfig memory) {
        return _getEffectiveConfig(id);
    }

    /// @notice Returns a user's position in a market.
    /// @param id The market ID
    /// @param user The user's address
    /// @return The user's position (debtPrincipal only - collateral is in broker)
    function getPosition(MarketId id, address user) external view returns (Position memory) {
        return positions[id][user];
    }

    /* ============================================================================================ */
    /*                                      CURATOR FUNCTIONS                                       */
    /* ============================================================================================ */

    /// @notice Proposes a risk parameter update (auto-applies after 7 days).
    /// @dev Only callable by market curator.
    /// @dev Validates all parameters before scheduling.
    /// @dev Pending updates can be cancelled by curator before execution.
    /// @param id The market ID
    /// @param minColRatio New minimum collateralization ratio (must be > 100%)
    /// @param maintenanceMargin New maintenance margin (must be >= 100%)
    /// @param liquidationCloseFactor New liquidation close factor (must be > 0 and <= 100%)
    /// @param fundingPeriod New funding period (must be between 1 day and 365 days)
    /// @param debtCap New debt cap (0 = unlimited)
    /// @param liquidationParams New liquidation parameters
    function proposeRiskUpdate(
        MarketId id,
        uint64 minColRatio,
        uint64 maintenanceMargin,
        uint64 liquidationCloseFactor,
        uint32 fundingPeriod,
        uint128 debtCap,
        bytes32 liquidationParams
    ) external onlyCurator(id) nonReentrant {
        if (!marketExists[id]) revert InvalidMarket();

        // Validate parameters (same rules as factory)
        if (minColRatio <= 1e18) revert InvalidParam("MinCol <= 100%");
        if (maintenanceMargin < 1e18) revert InvalidParam("Maintenance < 100%");
        if (minColRatio <= maintenanceMargin) revert InvalidParam("Risk Config Error");
        if (liquidationCloseFactor == 0 || liquidationCloseFactor > 1e18) {
            revert InvalidParam("Invalid CloseFactor");
        }
        if (fundingPeriod < 1 days || fundingPeriod > 365 days) {
            revert InvalidParam("Invalid period");
        }

        // Store pending update
        uint48 executeAt = uint48(block.timestamp + CONFIG_TIMELOCK);
        
        pendingRiskUpdates[id] = PendingRiskUpdate({
            minColRatio: minColRatio,
            maintenanceMargin: maintenanceMargin,
            liquidationCloseFactor: liquidationCloseFactor,
            fundingPeriod: fundingPeriod,
            debtCap: debtCap,
            liquidationParams: liquidationParams,
            executeAt: executeAt,
            pending: true
        });

        emit RiskUpdateProposed(
            id,
            minColRatio,
            maintenanceMargin,
            liquidationCloseFactor,
            fundingPeriod,
            debtCap,
            liquidationParams,
            executeAt
        );
    }

    /// @notice Cancels a pending risk parameter update.
    /// @dev Only callable by market curator.
    /// @param id The market ID
    function cancelRiskUpdate(MarketId id) external onlyCurator(id) nonReentrant {
        if (!pendingRiskUpdates[id].pending) revert InvalidParam("No pending update");
        
        delete pendingRiskUpdates[id];
        emit RiskUpdateCancelled(id);
    }

    /// @notice Updates the Uniswap V4 pool fee (immediate, no timelock).
    /// @dev Only callable by market curator.
    /// @dev Requires pool to support dynamic fees and TWAMM to be configured.
    /// @param id The market ID
    /// @param newFee New fee in hundredths of bips (e.g., 3000 = 0.3%)
    function updatePoolFee(MarketId id, uint24 newFee) external onlyCurator(id) nonReentrant {
        if (!marketExists[id]) revert InvalidMarket();
        if (twamm == address(0)) revert InvalidParam("TWAMM not configured");
        
        // Validate fee (V4 max is 100% = 1000000)
        if (newFee > 1000000) revert InvalidParam("Fee too high");
        
        // Get market addresses to build PoolKey
        MarketAddresses storage addresses = marketAddresses[id];
        
        // Build PoolKey (currencies must be sorted)
        address token0 = addresses.collateralToken;
        address token1 = addresses.underlyingToken;
        if (token0 > token1) {
            (token0, token1) = (token1, token0);
        }
        
        // Note: We use fundingPeriod/60 as tick spacing approximation
        // Real implementation should store tickSpacing in MarketAddresses
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: newFee,
            tickSpacing: 60, // Default tick spacing
            hooks: IHooks(twamm)
        });
        
        // Call TWAMM to update the dynamic LP fee
        // TWAMM will call poolManager.updateDynamicLPFee()
        (bool success, bytes memory reason) = twamm.call(
            abi.encodeWithSignature(
                "updateDynamicLPFee((address,address,uint24,int24,address),uint24)",
                key,
                newFee
            )
        );
        if (!success) {
            assembly {
                revert(add(reason, 32), mload(reason))
            }
        }
        
        emit PoolFeeUpdated(id, newFee);
    }

    /// @notice Gets the pending risk update for a market.
    /// @param id The market ID
    /// @return The pending update struct
    function getPendingRiskUpdate(MarketId id) external view returns (PendingRiskUpdate memory) {
        return pendingRiskUpdates[id];
    }

    /* ============================================================================================ */
    /*                                      INTERNAL HELPERS                                        */
    /* ============================================================================================ */

    /// @notice Gets the effective market config (auto-applies pending updates).
    /// @dev This is the source of truth for all protocol operations.
    /// @dev If a pending update exists and timelock has expired, returns the new config.
    /// @param id The market ID
    /// @return The effective market configuration
    function _getEffectiveConfig(MarketId id) internal view returns (MarketConfig memory) {
        PendingRiskUpdate storage pending = pendingRiskUpdates[id];
        
        // Auto-apply if timelock expired
        if (pending.pending && block.timestamp >= pending.executeAt) {
            MarketConfig memory config = marketConfigs[id];
            
            // Apply pending changes
            config.minColRatio = pending.minColRatio;
            config.maintenanceMargin = pending.maintenanceMargin;
            config.liquidationCloseFactor = pending.liquidationCloseFactor;
            config.fundingPeriod = pending.fundingPeriod;
            config.debtCap = pending.debtCap;
            config.liquidationParams = pending.liquidationParams;
            
            return config;
        }
        
        // Return current config if no pending update or timelock not expired
        return marketConfigs[id];
    }
}
