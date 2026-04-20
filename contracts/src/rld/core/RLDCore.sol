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
import {
    ILiquidationModule
} from "../../shared/interfaces/ILiquidationModule.sol";

import {PositionToken} from "../tokens/PositionToken.sol";
import {IBrokerVerifier} from "../../shared/interfaces/IBrokerVerifier.sol";
import {IPrimeBroker} from "../../shared/interfaces/IPrimeBroker.sol";
import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

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

    /// @notice The canonical Uniswap V4 PoolManager for this deployment
    address public immutable poolManager;

    /// @dev Minimum chunk divisor: chunk floor = totalSupply / MIN_CHUNK_DIVISOR (0.0001%)
    uint256 constant MIN_CHUNK_DIVISOR = 1_000_000;

    /// @notice Initializes RLDCore with the factory and PoolManager
    /// @dev Factory must be deployed first, then RLDCore is deployed with factory address.
    ///      This prevents front-running attacks on factory initialization.
    /// @param _factory The address of the RLDMarketFactory contract
    /// @param _poolManager The address of the V4 PoolManager
    constructor(address _factory, address _poolManager) {
        require(_factory != address(0), "Invalid factory");
        require(_poolManager != address(0), "Invalid poolManager");
        factory = _factory;
        poolManager = _poolManager;
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
    function createMarket(
        MarketAddresses calldata addresses,
        MarketConfig calldata config
    ) external override onlyFactory returns (MarketId) {
        // === Validate Critical Addresses ===
        // All core addresses must be non-zero to ensure market functions correctly
        if (addresses.collateralToken == address(0))
            revert InvalidParam("Collateral");
        if (addresses.underlyingToken == address(0))
            revert InvalidParam("Underlying");
        if (addresses.rateOracle == address(0))
            revert InvalidParam("Rate Oracle");
        //if (addresses.spotOracle == address(0)) revert InvalidParam("Spot Oracle");
        if (addresses.fundingModel == address(0))
            revert InvalidParam("Funding");
        if (addresses.positionToken == address(0))
            revert InvalidParam("Position Token");
        if (addresses.liquidationModule == address(0))
            revert InvalidParam("LiqModule");

        // === Generate Deterministic MarketId ===
        // Hash of (collateral, underlying, pool) ensures:
        // 1. Same tokens with different pools = different markets
        // 2. Predictable IDs for off-chain systems
        // 3. No duplicate markets possible
        MarketId id = MarketId.wrap(
            keccak256(
                abi.encode(
                    addresses.collateralToken,
                    addresses.underlyingToken,
                    addresses.underlyingPool // Pool distinguishes markets
                )
            )
        );

        // === Duplicate Check ===
        if (marketExists[id]) revert MarketAlreadyExists();

        // === Store Market Data ===
        marketExists[id] = true;
        marketAddresses[id] = addresses;
        marketConfigs[id] = config;
        marketStates[id] = MarketState({
            normalizationFactor: 1e18, // Start at 1:1 (no accrued interest)
            totalDebt: 0, // No debt initially
            lastUpdateTimestamp: uint48(block.timestamp),
            globalSettlementTimestamp: 0, // Settlement disabled at market genesis
            badDebt: 0 // No bad debt initially
        });

        emit MarketCreated(
            id,
            addresses.collateralToken,
            addresses.underlyingToken,
            addresses.underlyingPool
        );
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

    /// @notice Modifier requiring caller to be the market settlement module.
    /// @param id The market ID to check settlement authority for
    modifier onlySettlementModule(MarketId id) {
        if (msg.sender != marketAddresses[id].settlementModule)
            revert Unauthorized();
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
    function lock(
        bytes calldata data
    ) external override returns (bytes memory) {
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
            // Propagate revert reason from callback
            // Note: transient storage auto-clears on revert, no manual cleanup needed
            assembly {
                revert(add(reason, 32), mload(reason))
            }
        }
        // 3. Exit Lock & Enforce Solvency
        // All positions touched during the callback must be solvent
        _checkSolvencyOfTouched();

        // 4. Clear lock active flag
        TransientStorage.tstore(LOCK_ACTIVE_KEY, 0);

        // 5. Cleanup - Clear transient storage
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
    function modifyPosition(
        MarketId id,
        int256 deltaCollateral,
        int256 deltaDebt
    ) external onlyLock onlyLockHolder {
        if (marketStates[id].globalSettlementTimestamp != 0) {
            revert MarketInGlobalSettlement();
        }

        // Note: deltaCollateral is unused - collateral is managed by PrimeBroker
        // Parameter kept for interface compatibility

        // 1. Update Funding (Lazy) - Applies accrued interest to normalization factor
        _applyFunding(id);

        Position storage pos = positions[id][msg.sender];
        MarketAddresses storage addresses = marketAddresses[id];
        MarketState storage state = marketStates[id];

        // 2. Update Debt Principal
        if (deltaDebt != 0) {
            uint256 newDebt = _addSignedDelta(pos.debtPrincipal, deltaDebt);
            pos.debtPrincipal = uint128(newDebt);
        }

        // 3. Tokenize Debt Changes (wRLP)
        // wRLP tokens represent debt obligations and can be traded
        // NOTE: Mint/burn BEFORE debt cap check so totalSupply() is current
        if (deltaDebt > 0) {
            // Minting debt = creating short position
            PositionToken(addresses.positionToken).mint(
                msg.sender,
                uint256(deltaDebt)
            );

            // Debt cap check — uses totalSupply() as source of truth
            // F-07 NOTE: Cap is enforced in ECONOMIC terms (principal × normFactor).
            // As normFactor drifts, the effective principal limit changes:
            //   NF=0.5 → allows 2× more principal; NF=2.0 → allows half.
            // type(uint128).max = unlimited (skip entirely)
            uint128 cap = _getEffectiveConfig(id).debtCap;
            if (cap < type(uint128).max) {
                uint256 trueTotalDebt = PositionToken(addresses.positionToken)
                    .totalSupply()
                    .mulWad(state.normalizationFactor);
                if (trueTotalDebt > cap) {
                    revert DebtCapExceeded();
                }
            }
        } else if (deltaDebt < 0) {
            // Repaying debt = closing short position
            PositionToken(addresses.positionToken).burn(
                msg.sender,
                uint256(-deltaDebt)
            );
        }

        // 4. Sync totalDebt from totalSupply (single source of truth)
        // Kept in struct for integration consumers (indexers, UIs)
        if (deltaDebt != 0) {
            state.totalDebt = uint128(
                PositionToken(addresses.positionToken).totalSupply()
            );
        }

        // 5. Track Action Type for Solvency Ratio Selection
        // - Type 1: Maintenance operations → uses maintenanceMargin (less strict)
        // - Type 2: New minting → uses minColRatio (more strict)
        bytes32 actionKey = keccak256(abi.encode(id, msg.sender, ACTION_SALT));
        uint256 currentType = TransientStorage.tload(actionKey);
        uint256 newType = deltaDebt > 0 ? 2 : 1;

        // Only upgrade action type, never downgrade (most restrictive wins)
        if (newType > currentType) {
            TransientStorage.tstore(actionKey, newType);
        }

        // 6. Add to Touched List for Post-Lock Solvency Check
        _addTouchedPosition(id, msg.sender);

        emit PositionModified(id, msg.sender, deltaCollateral, deltaDebt);

        // Emit market state update for indexer
        if (deltaDebt != 0) {
            emit MarketStateUpdated(
                id,
                state.normalizationFactor,
                state.totalDebt
            );
        }
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
            MarketConfig memory config = _peekEffectiveConfig(id);

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
    function _checkSolvency(
        MarketId id,
        address user,
        uint256 minRatio
    ) internal view {
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
    function _isSolvent(
        MarketId id,
        address user,
        uint256 minRatio
    ) internal view returns (bool) {
        Position memory pos = positions[id][user];

        // No debt = always solvent
        if (pos.debtPrincipal == 0) return true;

        MarketAddresses storage addresses = marketAddresses[id];
        MarketState memory state = marketStates[id];
        MarketConfig memory config = _peekEffectiveConfig(id);

        // 1. Verify Broker Status (Strict)
        // Only verified brokers can have positions - prevents arbitrary contracts
        if (config.brokerVerifier == address(0)) return false;
        if (!IBrokerVerifier(config.brokerVerifier).isValidBroker(user))
            return false;

        // 2. Calculate Debt Value
        // True Debt = Principal * NormalizationFactor (accounts for accrued interest)
        uint256 trueDebt = uint256(pos.debtPrincipal).mulWad(
            state.normalizationFactor
        );

        // Get price in collateral terms
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool,
            addresses.underlyingToken
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

    // NOTE: Bad debt socialization period is now per-market via config.badDebtPeriod

    // MIN_CHUNK_DIVISOR defined at contract top (L97)

    /// @dev Context struct for liquidation pipeline to avoid multiple oracle/NAV calls (H-1/H-2/H-3 fix)
    struct LiquidationCtx {
        uint256 indexPrice;
        uint256 totalAssets;
        uint256 normFactor;
        uint256 principalToCover;
        uint256 seizeAmount;
    }

    /// @notice Applies pending funding rate to update the normalization factor.
    /// @dev Called lazily on first interaction per block.
    /// @dev Normalization factor compounds over time to track accumulated interest.
    /// @dev If bad debt exists, a second NF adjustment gradually socializes it over 7 days.
    /// @param id The market ID to apply funding for
    function _applyFunding(MarketId id) internal {
        MarketState storage state = marketStates[id];
        MarketAddresses storage addresses = marketAddresses[id];

        uint256 oldNormFactor = state.normalizationFactor;
        uint256 timeDelta = block.timestamp - state.lastUpdateTimestamp;

        // 1. Calculate new normalization factor via external model
        (uint256 newNormFactor, int256 fundingRate) = IFundingModel(
            addresses.fundingModel
        ).calculateFunding(
                MarketId.unwrap(id),
                address(this),
                oldNormFactor,
                state.lastUpdateTimestamp
            );

        // 2. Update storage with overflow protection
        if (newNormFactor != oldNormFactor) {
            require(newNormFactor <= type(uint128).max, "NormFactor overflow");
            state.normalizationFactor = uint128(newNormFactor);

            // Emit for indexer tracking
            emit FundingApplied(
                id,
                oldNormFactor,
                newNormFactor,
                fundingRate,
                timeDelta
            );
        }

        // 3. Bad debt bleeding: gradually socialize unbacked debt via NF over configurable period
        // B-3 FIX: Guard against supply == 0 (all positions closed with badDebt remaining)
        if (state.badDebt > 0 && timeDelta > 0) {
            uint256 supply = PositionToken(addresses.positionToken)
                .totalSupply();
            if (supply > 0) {
                uint256 minChunk = supply / MIN_CHUNK_DIVISOR;
                uint256 bdPeriod = uint256(marketConfigs[id].badDebtPeriod);
                if (bdPeriod == 0) bdPeriod = 7 days; // fallback for legacy markets
                uint256 chunk = (uint256(state.badDebt) * timeDelta) / bdPeriod;
                if (chunk < minChunk) chunk = minChunk;
                if (chunk > state.badDebt) chunk = state.badDebt;
                state.normalizationFactor += uint128((chunk * 1e18) / supply);
                state.badDebt -= uint128(chunk);
                emit BadDebtSocialized(
                    id,
                    uint128(chunk),
                    state.badDebt,
                    state.normalizationFactor
                );
            }
            // If supply == 0, bad debt remains frozen until new positions are opened
        }

        state.lastUpdateTimestamp = uint48(block.timestamp);
    }

    /// @notice External wrapper to apply funding for testing purposes.
    /// @dev TODO: REMOVE BEFORE PRODUCTION - This is only for testnet debugging.
    /// @dev In production, funding is applied lazily during modifyPosition/liquidate.
    /// @param id The market ID to apply funding for
    function applyFunding(MarketId id) external {
        require(marketExists[id], "Market does not exist");
        _applyFunding(id);
    }

    /// @notice Applies a signed delta to a value with underflow protection.
    /// @dev Used for adjusting collateral and debt values.
    /// @param start The starting value
    /// @param delta The change to apply (can be negative)
    /// @return The new value after applying delta
    function _addSignedDelta(
        uint128 start,
        int256 delta
    ) internal pure returns (uint256) {
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
    function liquidate(
        MarketId id,
        address user,
        uint256 debtToCover,
        uint256 minCollateralOut
    ) external override nonReentrant {
        _applyFunding(id);

        MarketConfig memory config = _getEffectiveConfig(id);

        // 1. Validation
        _validateLiquidationChecks(id, user, config);
        _validateLiquidationAmount(debtToCover, config);

        // 2. Cache indexPrice + NAV once (H-1/H-2/H-3 fix: prevents
        //    divergent values from multiple oracle/NAV calls)
        LiquidationCtx memory ctx;
        MarketAddresses storage addresses = marketAddresses[id];
        ctx.indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool,
            addresses.underlyingToken
        );
        try IPrimeBroker(user).getNetAccountValue() returns (uint256 v) {
            ctx.totalAssets = v;
        } catch {
            ctx.totalAssets = 0; // Reverted broker = treat as underwater
        }
        // 3. Snapshot principal BEFORE optimistic reduction
        uint256 principalSnapshot = uint256(positions[id][user].debtPrincipal);

        // 4. Debt Calculations & Updates (optimistic reduction)
        (ctx.principalToCover, ctx.normFactor) = _updateLiquidationDebt(
            id,
            user,
            debtToCover,
            config,
            ctx
        );

        // 5. Seize Calculation via Oracle & Module
        ctx.seizeAmount = _calculateLiquidationSeize(
            id,
            user,
            debtToCover,
            config,
            principalSnapshot,
            ctx
        );

        // 6. Execution & Settlement (with negative equity protection)
        _settleLiquidation(id, user, ctx, debtToCover, minCollateralOut);
    }

    /* ============================================================================================ */
    /*                                   LIQUIDATION HELPERS                                        */
    /* ============================================================================================ */

    function _validateLiquidationChecks(
        MarketId id,
        address user,
        MarketConfig memory config
    ) internal view {
        // 1. Broker validity first — reject non-brokers before solvency check
        if (
            config.brokerVerifier == address(0) ||
            !IBrokerVerifier(config.brokerVerifier).isValidBroker(user)
        ) {
            revert InvalidBroker(user);
        }
        // 2. Solvency check — only reached for valid brokers
        if (_isSolvent(id, user, uint256(config.maintenanceMargin))) {
            revert UserSolvent(user);
        }
    }

    function _validateLiquidationAmount(
        uint256 debtToCover,
        MarketConfig memory config
    ) internal pure {
        if (debtToCover < config.minLiquidation) {
            revert("Liquidation amount too small");
        }
    }

    function _updateLiquidationDebt(
        MarketId id,
        address user,
        uint256 debtToCover,
        MarketConfig memory config,
        LiquidationCtx memory ctx
    ) internal returns (uint256 principalToCover, uint256 normFactor) {
        // Cache storage
        MarketState storage state = marketStates[id];
        normFactor = state.normalizationFactor;
        Position storage pos = positions[id][user];
        uint128 principal = pos.debtPrincipal;

        // Calculate true debt
        uint256 trueDebt = uint256(principal).mulWad(normFactor);

        // Dynamic close factor (Aave-style):
        // If position is underwater (assets < debt), allow 100% liquidation
        // Otherwise, enforce the configured close factor (e.g. 50%)
        // Uses cached indexPrice and totalAssets from ctx (H-1/H-2/H-3 fix)
        uint256 debtValue = trueDebt.mulWad(ctx.indexPrice);

        if (ctx.totalAssets >= debtValue) {
            // Not underwater — enforce close factor
            if (
                debtToCover >
                trueDebt.mulWad(uint256(config.liquidationCloseFactor))
            ) {
                revert CloseFactorExceeded();
            }
        }
        // else: underwater — skip close factor check (allow up to 100%)

        // Calculate Principal to burn
        principalToCover = debtToCover.divWad(normFactor);

        // Update Storage (Optimistic Reduction)
        pos.debtPrincipal = principal - uint128(principalToCover);
    }

    function _calculateLiquidationSeize(
        MarketId id,
        address /* user */,
        uint256 debtToCover,
        MarketConfig memory config,
        uint256 principalSnapshot,
        LiquidationCtx memory ctx
    ) internal view returns (uint256 seizeAmount) {
        address module = marketAddresses[id].liquidationModule;

        ILiquidationModule.PriceData memory priceData;
        priceData.normalizationFactor = ctx.normFactor;
        priceData.spotPrice = 1e18; // waUSDC valuation unit

        {
            uint256 spotPrice = marketAddresses[id].spotOracle != address(0)
                ? ISpotOracle(marketAddresses[id].spotOracle).getSpotPrice(
                    marketAddresses[id].collateralToken,
                    marketAddresses[id].underlyingToken
                )
                : 1e18;

            // Protect liquidator from overpaying by using min price
            priceData.indexPrice = ctx.indexPrice < spotPrice ? ctx.indexPrice : spotPrice;
        }

        (, seizeAmount) = ILiquidationModule(module).calculateSeizeAmount(
            debtToCover,
            ctx.totalAssets,
            principalSnapshot, // H-2 FIX: Pre-reduction principal
            priceData,
            config,
            config.liquidationParams
        );
    }

    function _settleLiquidation(
        MarketId id,
        address user,
        LiquidationCtx memory ctx,
        uint256 debtToCover,
        uint256 minCollateralOut
    ) internal {
        // NEGATIVE EQUITY PROTECTION: Cap seize at available collateral
        // H-2 FIX: Use cached ctx.totalAssets instead of a second getNetAccountValue() call.
        // This prevents state divergence between the seize calculation and settlement.
        uint256 actualSeizeAmount;
        uint256 actualPrincipalToCover;

        {
            uint256 availableCollateral = ctx.totalAssets;
            uint256 seizeAmount = ctx.seizeAmount;

            if (seizeAmount > availableCollateral) {
                actualSeizeAmount = availableCollateral;
                uint256 actualDebtCovered = availableCollateral
                    .mulWad(debtToCover)
                    .divWad(seizeAmount);
                actualPrincipalToCover = actualDebtCovered.divWad(ctx.normFactor);

                // Restore uncovered principal to pos.debtPrincipal
                Position storage pos = positions[id][user];
                pos.debtPrincipal =
                    pos.debtPrincipal +
                    uint128(ctx.principalToCover) -
                    uint128(actualPrincipalToCover);
            } else {
                actualSeizeAmount = seizeAmount;
                actualPrincipalToCover = ctx.principalToCover;
            }
        }

        // Execute seize — broker may unwind positions (TWAMM, LP) during this call
        IPrimeBroker.SeizeOutput memory seizeOutput = IPrimeBroker(user).seize(
            actualSeizeAmount,
            actualPrincipalToCover,
            msg.sender
        );

        uint256 wRLPFromBroker = seizeOutput.wRLPExtracted >
            actualPrincipalToCover
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

        // BAD DEBT DETECTION: After all seize & burns, check what's left
        // If the user still has debtPrincipal, it is truly unbacked.
        {
            Position storage pos = positions[id][user];
            MarketState storage state = marketStates[id];
            if (pos.debtPrincipal > 0 && ctx.seizeAmount > ctx.totalAssets) {
                uint128 badDebtAmount = pos.debtPrincipal;
                state.badDebt += badDebtAmount;
                pos.debtPrincipal = 0;
                emit BadDebtRegistered(id, badDebtAmount, state.badDebt);
            }
            
            // Sync totalDebt from totalSupply (single source of truth)
            state.totalDebt = uint128(PositionToken(positionToken).totalSupply());
            emit MarketStateUpdated(id, state.normalizationFactor, state.totalDebt);
        }

        // SLIPPAGE PROTECTION: Ensure liquidator receives minimum collateral
        require(
            seizeOutput.collateralSeized >= minCollateralOut,
            "Slippage: collateral below minimum"
        );

        // B-2 FIX: Emit dedicated Liquidation event for indexers and liquidators
        emit Liquidation(
            id,
            user,
            msg.sender,
            debtToCover,
            seizeOutput.collateralSeized,
            wRLPFromBroker
        );
    }

    /* ============================================================================================ */
    /*                                     GLOBAL SETTLEMENT                                        */
    /* ============================================================================================ */

    /// @notice Activates terminal global settlement mode for a CDS market.
    /// @dev Once active, regular `modifyPosition` flow is halted for the market.
    /// @dev Callable only by the market's configured settlementModule.
    function enterGlobalSettlement(
        MarketId id
    ) external override onlySettlementModule(id) nonReentrant {
        if (!marketExists[id]) revert InvalidMarket();

        MarketState storage state = marketStates[id];
        if (state.globalSettlementTimestamp != 0) {
            revert InvalidParam("Settlement already active");
        }

        uint48 ts = uint48(block.timestamp);
        state.globalSettlementTimestamp = ts;
        emit GlobalSettlementEntered(id, ts);
    }

    /// @notice Invalidates queued broker withdrawals during settlement sweeps.
    /// @dev Callable only by the market settlement module.
    /// @dev Settlement module should invoke this as part of broker confiscation flow.
    function invalidateBrokerWithdrawalQueue(
        MarketId id,
        address broker
    ) external override onlySettlementModule(id) nonReentrant {
        if (!marketExists[id]) revert InvalidMarket();
        if (marketStates[id].globalSettlementTimestamp == 0) {
            revert InvalidParam("Settlement not active");
        }

        MarketConfig memory config = _peekEffectiveConfig(id);
        if (
            config.brokerVerifier == address(0) ||
            !IBrokerVerifier(config.brokerVerifier).isValidBroker(broker)
        ) {
            revert InvalidBroker(broker);
        }

        uint64 newEpoch = IPrimeBroker(broker).invalidateWithdrawalQueue();
        emit BrokerWithdrawalQueueInvalidated(id, broker, newEpoch);
    }

    /* ============================================================================================ */
    /*                                         VIEW FUNCTIONS                                       */
    /* ============================================================================================ */

    /// @notice Checks if a user's position is currently solvent.
    /// @dev Uses maintenance margin as the threshold.
    /// @param id The market ID
    /// @param user The user to check
    /// @return True if position is solvent (above maintenance margin)
    function isSolvent(
        MarketId id,
        address user
    ) external view override returns (bool) {
        MarketConfig memory config = _peekEffectiveConfig(id);
        return _isSolvent(id, user, uint256(config.maintenanceMargin));
    }

    /// @dev Simulates normFactor after pending funding + bad debt bleeding (M-4 fix).
    /// @dev Shared helper used by isSolventAfterFunding and any view that needs future NF.
    /// @param id The market ID
    /// @return simNormFactor The projected normalization factor
    function _simulateNormFactor(
        MarketId id
    ) internal view returns (uint256 simNormFactor) {
        MarketState memory state = marketStates[id];
        MarketAddresses storage addresses = marketAddresses[id];

        // 1. Simulate funding drift
        (simNormFactor, ) = IFundingModel(addresses.fundingModel)
            .calculateFunding(
                MarketId.unwrap(id),
                address(this),
                state.normalizationFactor,
                state.lastUpdateTimestamp
            );

        // 2. Simulate bad debt bleeding (same logic as _applyFunding L559-577)
        uint256 timeDelta = block.timestamp - state.lastUpdateTimestamp;
        if (state.badDebt > 0 && timeDelta > 0) {
            uint256 supply = PositionToken(addresses.positionToken)
                .totalSupply();
            if (supply > 0) {
                uint256 minChunk = supply / MIN_CHUNK_DIVISOR;
                uint256 bdPeriod = uint256(marketConfigs[id].badDebtPeriod);
                if (bdPeriod == 0) bdPeriod = 7 days;
                uint256 chunk = (uint256(state.badDebt) * timeDelta) / bdPeriod;
                if (chunk < minChunk) chunk = minChunk;
                if (chunk > state.badDebt) chunk = state.badDebt;
                simNormFactor += (chunk * 1e18) / supply;
            }
        }
    }

    /// @notice F-06 FIX: Checks solvency after simulating pending funding + bad debt bleeding.
    /// @dev Uses _simulateNormFactor() to project the normFactor including both sources of drift.
    /// @param id The market ID
    /// @param user The user to check
    /// @return True if position would be solvent after funding is applied
    function isSolventAfterFunding(
        MarketId id,
        address user
    ) external view returns (bool) {
        Position memory pos = positions[id][user];
        if (pos.debtPrincipal == 0) return true;

        MarketAddresses storage addresses = marketAddresses[id];
        MarketConfig memory config = _peekEffectiveConfig(id);

        // Simulate normFactor including both funding drift AND bad debt bleeding (M-4 fix)
        uint256 simNormFactor = _simulateNormFactor(id);

        // Broker validity
        if (config.brokerVerifier == address(0)) return false;
        if (!IBrokerVerifier(config.brokerVerifier).isValidBroker(user))
            return false;

        uint256 trueDebt = uint256(pos.debtPrincipal).mulWad(simNormFactor);
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool,
            addresses.underlyingToken
        );
        uint256 debtValue = trueDebt.mulWad(indexPrice);

        uint256 totalAssets;
        try IPrimeBroker(user).getNetAccountValue() returns (uint256 value) {
            totalAssets = value;
        } catch {
            return false;
        }
        if (totalAssets < debtValue) return false;
        uint256 netWorth = totalAssets - debtValue;
        uint256 marginRequirement = uint256(config.maintenanceMargin) - 1e18;
        return netWorth >= debtValue.mulWad(marginRequirement);
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
    function getMarketState(
        MarketId id
    ) external view returns (MarketState memory) {
        return marketStates[id];
    }

    /// @notice Returns the addresses associated with a market.
    /// @param id The market ID
    /// @return All market addresses
    function getMarketAddresses(
        MarketId id
    ) external view returns (MarketAddresses memory) {
        return marketAddresses[id];
    }

    /// @notice Returns the configuration of a market.
    /// @dev Auto-applies pending risk updates if timelock has expired.
    /// @param id The market ID
    /// @return The market configuration (with pending updates applied if ready)
    function getMarketConfig(
        MarketId id
    ) external view returns (MarketConfig memory) {
        return _peekEffectiveConfig(id);
    }

    /// @notice Returns a user's position in a market.
    /// @param id The market ID
    /// @param user The user's address
    /// @return The user's position (debtPrincipal only - collateral is in broker)
    function getPosition(
        MarketId id,
        address user
    ) external view returns (Position memory) {
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
    /// @param badDebtPeriod New bad debt socialization period (must be between 1 and 90 days)
    /// @param decayRateWad New CDS decay parameter F in annualized WAD (0 allowed for non-CDS)
    /// @param debtCap New debt cap in economic USD (0 = unlimited)
    /// @param minLiquidation New minimum liquidation amount in collateral decimals
    /// @param liquidationParams New liquidation parameters
    function proposeRiskUpdate(
        MarketId id,
        uint64 minColRatio,
        uint64 maintenanceMargin,
        uint64 liquidationCloseFactor,
        uint32 fundingPeriod,
        uint32 badDebtPeriod,
        uint96 decayRateWad,
        uint128 debtCap,
        uint128 minLiquidation,
        bytes32 liquidationParams
    ) external onlyCurator(id) nonReentrant {
        if (!marketExists[id]) revert InvalidMarket();

        // Validate parameters (same rules as factory)
        if (minColRatio <= 1e18) revert InvalidParam("MinCol <= 100%");
        if (maintenanceMargin < 1e18) revert InvalidParam("Maintenance < 100%");
        if (minColRatio <= maintenanceMargin)
            revert InvalidParam("Risk Config Error");
        if (liquidationCloseFactor == 0 || liquidationCloseFactor > 1e18) {
            revert InvalidParam("Invalid CloseFactor");
        }
        if (fundingPeriod < 1 days || fundingPeriod > 365 days) {
            revert InvalidParam("Invalid period");
        }
        if (badDebtPeriod < 1 days || badDebtPeriod > 90 days) {
            revert InvalidParam("Invalid badDebtPeriod");
        }

        // Store pending update
        uint48 executeAt = uint48(block.timestamp + CONFIG_TIMELOCK);

        pendingRiskUpdates[id] = PendingRiskUpdate({
            minColRatio: minColRatio,
            maintenanceMargin: maintenanceMargin,
            liquidationCloseFactor: liquidationCloseFactor,
            fundingPeriod: fundingPeriod,
            badDebtPeriod: badDebtPeriod,
            decayRateWad: decayRateWad,
            debtCap: debtCap,
            minLiquidation: minLiquidation,
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
            badDebtPeriod,
            decayRateWad,
            debtCap,
            minLiquidation,
            liquidationParams,
            executeAt
        );
    }

    /// @notice Cancels a pending risk parameter update.
    /// @dev Only callable by market curator.
    /// @param id The market ID
    function cancelRiskUpdate(
        MarketId id
    ) external onlyCurator(id) nonReentrant {
        if (!pendingRiskUpdates[id].pending)
            revert InvalidParam("No pending update");

        delete pendingRiskUpdates[id];
        emit RiskUpdateCancelled(id);
    }

    /// @notice Gets the pending risk update for a market.
    /// @param id The market ID
    /// @return The pending update struct
    function getPendingRiskUpdate(
        MarketId id
    ) external view returns (PendingRiskUpdate memory) {
        return pendingRiskUpdates[id];
    }

    /* ============================================================================================ */
    /*                                      INTERNAL HELPERS                                        */
    /* ============================================================================================ */

    /// @notice Gets the effective market config (auto-applies pending updates).
    /// @dev This is the source of truth for all protocol operations.
    /// @dev Returns effective config, auto-applying expired timelocked updates (M-1 fix: write-through).
    /// @dev When timelock expires, writes to storage and clears pending — subsequent reads use storage directly.
    /// @param id The market ID
    /// @return The effective market configuration
    function _getEffectiveConfig(
        MarketId id
    ) internal returns (MarketConfig memory) {
        PendingRiskUpdate storage pending = pendingRiskUpdates[id];

        if (pending.pending && block.timestamp >= pending.executeAt) {
            // Timelock expired — write-through to storage (M-1 fix)
            MarketConfig storage config = marketConfigs[id];
            config.minColRatio = pending.minColRatio;
            config.maintenanceMargin = pending.maintenanceMargin;
            config.liquidationCloseFactor = pending.liquidationCloseFactor;
            config.fundingPeriod = pending.fundingPeriod;
            config.badDebtPeriod = pending.badDebtPeriod;
            config.decayRateWad = pending.decayRateWad;
            config.debtCap = pending.debtCap;
            config.minLiquidation = pending.minLiquidation;
            config.liquidationParams = pending.liquidationParams;
            // Clear pending update
            delete pendingRiskUpdates[id];
            emit RiskUpdateApplied(id);
            return config;
        }

        return marketConfigs[id];
    }

    /// @dev View-safe version that reads effective config without writing to storage.
    function _peekEffectiveConfig(
        MarketId id
    ) internal view returns (MarketConfig memory) {
        PendingRiskUpdate storage pending = pendingRiskUpdates[id];

        if (pending.pending && block.timestamp >= pending.executeAt) {
            MarketConfig memory config = marketConfigs[id];
            config.minColRatio = pending.minColRatio;
            config.maintenanceMargin = pending.maintenanceMargin;
            config.liquidationCloseFactor = pending.liquidationCloseFactor;
            config.fundingPeriod = pending.fundingPeriod;
            config.badDebtPeriod = pending.badDebtPeriod;
            config.decayRateWad = pending.decayRateWad;
            config.debtCap = pending.debtCap;
            config.minLiquidation = pending.minLiquidation;
            config.liquidationParams = pending.liquidationParams;
            return config;
        }

        return marketConfigs[id];
    }
}
