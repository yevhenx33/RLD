// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

// Define MarketId as a robust type? For now, bytes32.
type MarketId is bytes32;

interface IRLDCore {
    /* ============================================================================================ */
    /*                                           STRUCTS                                            */
    /* ============================================================================================ */

    struct MarketAddresses {
        address collateralToken;
        address underlyingToken;
        address underlyingPool;
        address rateOracle;
        address spotOracle;
        address markOracle; // Restored (Required by FundingModel)
        address fundingModel;
        address curator; // Renamed from feeHook
        address liquidationModule;
        address positionToken; // ERC20 token representing debt (WrappedRLP)
        address settlementModule; // Optional CDS settlement module (0 = standard RLP market)
    }

    struct MarketConfig {
        uint64 minColRatio;
        uint64 maintenanceMargin;
        uint64 liquidationCloseFactor; // e.g., 50% (5e17)
        uint32 fundingPeriod; // Standard funding model period (seconds)
        uint32 badDebtPeriod; // Period over which bad debt is socialized via NF (e.g. 7 days)
        uint128 debtCap; // Max TRUE debt in economic USD (type(uint128).max = unlimited)
        uint128 minLiquidation; // Minimum liquidation amount in collateral decimals
        bytes32 liquidationParams; // Packed params for the module
        uint96 decayRateWad; // CDS decay model parameter F in annualized WAD
        address brokerVerifier; // Trusted Verifier for Prime Brokers (Immutable)
    }

    struct MarketState {
        uint128 normalizationFactor; // Debt Scaler (starts at 1e18)
        uint128 totalDebt; // Total debt principal across all positions
        uint48 lastUpdateTimestamp;
        uint48 globalSettlementTimestamp; // Non-zero once market enters global settlement
        uint128 badDebt; // Unbacked wRLP principal, socialized via NF over 7 days
    }

    struct Position {
        uint128 debtPrincipal; // Collateral tracking removed - solvency delegated to PrimeBroker
    }

    /// @notice Pending risk parameter update with timelock
    struct PendingRiskUpdate {
        uint64 minColRatio;
        uint64 maintenanceMargin;
        uint64 liquidationCloseFactor;
        uint32 fundingPeriod;
        uint32 badDebtPeriod;
        uint96 decayRateWad;
        uint128 debtCap;
        uint128 minLiquidation;
        bytes32 liquidationParams;
        uint48 executeAt; // Timestamp when update auto-applies
        bool pending; // Whether an update is pending
    }

    /* ============================================================================================ */
    /*                                           EVENTS                                             */
    /* ============================================================================================ */
    // --- Events ---
    event MarketCreated(
        MarketId indexed id,
        address indexed collateral,
        address indexed underlying,
        address pool
    ); // Indexed pool

    event PositionModified(
        MarketId indexed id,
        address indexed user,
        int256 deltaCollateral,
        int256 deltaDebt
    );
    event SecurityUpdate(
        MarketId indexed id,
        string indexed action,
        address indexed operator
    );

    // Curator Events
    event RiskUpdateProposed(
        MarketId indexed id,
        uint64 minColRatio,
        uint64 maintenanceMargin,
        uint64 liquidationCloseFactor,
        uint32 fundingPeriod,
        uint32 badDebtPeriod,
        uint96 decayRateWad,
        uint128 debtCap,
        uint128 minLiquidation,
        bytes32 liquidationParams,
        uint48 executeAt
    );
    event RiskUpdateCancelled(MarketId indexed id);
    event RiskUpdateApplied(MarketId indexed id);
    event PoolFeeUpdated(MarketId indexed id, uint24 newFee);

    /// @notice Emitted when a liquidation is executed
    event Liquidation(
        MarketId indexed id,
        address indexed user,
        address indexed liquidator,
        uint256 debtCovered,
        uint256 collateralSeized,
        uint256 wRLPBurned
    );

    // Indexing Events
    /// @notice Emitted when funding is applied to a market
    event FundingApplied(
        MarketId indexed marketId,
        uint256 oldNormFactor,
        uint256 newNormFactor,
        int256 fundingRate,
        uint256 timeDelta
    );

    /// @notice Emitted when market state changes (debt, NF)
    event MarketStateUpdated(
        MarketId indexed marketId,
        uint128 normalizationFactor,
        uint128 totalDebt
    );

    /// @notice Emitted when bad debt is registered from an underwater liquidation
    event BadDebtRegistered(
        MarketId indexed marketId,
        uint128 amount,
        uint128 totalBadDebt
    );

    /// @notice Emitted when bad debt is socialized via NF bleeding in _applyFunding
    event BadDebtSocialized(
        MarketId indexed marketId,
        uint128 chunk,
        uint128 remainingBadDebt,
        uint128 newNormFactor
    );

    /// @notice Emitted when a market enters terminal global settlement mode
    event GlobalSettlementEntered(
        MarketId indexed marketId,
        uint48 settlementTimestamp
    );

    /// @notice Emitted when settlement invalidates a broker's queued withdrawals
    event BrokerWithdrawalQueueInvalidated(
        MarketId indexed marketId,
        address indexed broker,
        uint64 newEpoch
    );

    /// @notice Emitted for account state verification
    event AccountStateHash(
        MarketId indexed marketId,
        address indexed account,
        bytes32 stateHash
    );

    // --- Errors ---
    error Unauthorized();
    error InvalidMarket();
    error InvalidParam(string param);
    error MarketAlreadyExists();
    error DebtCapExceeded();

    error NotLocked();
    error ReentrancyGuardActive();
    error Insolvent(address user);
    error UserSolvent(address user);
    error InvalidBroker(address user);
    error SlippageExceeded();
    error CloseFactorExceeded();
    error MarketInGlobalSettlement();

    /* ============================================================================================ */
    /*                                          FUNCTIONS                                           */
    /* ============================================================================================ */

    // --- Market Creation ---

    /// @notice Creates a new RLD Market
    /// @param addresses The address parameters for the market
    /// @param config The configuration parameters for the market
    /// @return marketId The unique identifier of the created market
    function createMarket(
        MarketAddresses calldata addresses,
        MarketConfig calldata config
    ) external returns (MarketId);

    /// @notice Checks if a market exists and is valid
    function isValidMarket(MarketId id) external view returns (bool);

    /// @notice Returns the current state of a market
    function getMarketState(
        MarketId id
    ) external view returns (MarketState memory);

    /// @notice Returns the addresses of a market
    function getMarketAddresses(
        MarketId id
    ) external view returns (MarketAddresses memory);

    /// @notice Returns the config of a market
    function getMarketConfig(
        MarketId id
    ) external view returns (MarketConfig memory);

    /// @notice Returns the position of a user in a market
    function getPosition(
        MarketId id,
        address user
    ) external view returns (Position memory);

    /* ============================================================================================ */
    /*                                      FLASH ACCOUNTING                                        */
    /* ============================================================================================ */

    /// @notice Entry point for all interactions.
    /// @dev Sets the `LOCK_HOLDER` and enables `modifyPosition`. Checks solvency at the end.
    /// @param data Arbitrary data passed to the callback.
    function lock(bytes calldata data) external returns (bytes memory);

    /// @notice Callback function that must be implemented by the caller of `lock`.
    function lockAcquired(bytes calldata data) external returns (bytes memory);

    /// @notice Modifies the position of the `LOCK_HOLDER`.
    /// @param id The market ID.
    /// @param deltaCollateral Change in collateral (+Deposit, -Withdraw).
    /// @param deltaDebt Change in debt (+Mint, -Burn/Repay).
    /// @dev Can only be called *inside* the `lock` context.
    function modifyPosition(
        MarketId id,
        int256 deltaCollateral,
        int256 deltaDebt
    ) external;

    /// @notice Checks if a user is solvent.
    function isSolvent(MarketId id, address user) external view returns (bool);

    /* ============================================================================================ */
    /*                                      SETTLEMENT / LIQ                                        */
    /* ============================================================================================ */

    /// @notice Liquidates an insolvent position (Legacy/Direct mode).
    function liquidate(
        MarketId id,
        address user,
        uint256 debtToCover,
        uint256 minCollateralOut
    ) external;

    /* ============================================================================================ */
    /*                                     GLOBAL SETTLEMENT                                        */
    /* ============================================================================================ */

    /// @notice Enters terminal global settlement mode for a market.
    /// @dev Callable only by the market's settlementModule address.
    function enterGlobalSettlement(MarketId id) external;

    /// @notice Invalidates queued broker withdrawals during settlement sweeps.
    /// @dev Callable only by the market's settlementModule address.
    function invalidateBrokerWithdrawalQueue(MarketId id, address broker) external;

    /* ============================================================================================ */
    /*                                      CURATOR FUNCTIONS                                       */
    /* ============================================================================================ */

    /// @notice Proposes a risk parameter update (auto-applies after 7 days)
    /// @param id The market ID
    /// @param minColRatio New minimum collateralization ratio
    /// @param maintenanceMargin New maintenance margin
    /// @param liquidationCloseFactor New liquidation close factor
    /// @param fundingPeriod New funding period (1 day to 365 days)
    /// @param badDebtPeriod New bad debt socialization period (1 day to 90 days)
    /// @param decayRateWad New CDS decay parameter F (annualized WAD; 0 allowed for non-CDS)
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
    ) external;

    /// @notice Cancels a pending risk parameter update
    /// @param id The market ID
    function cancelRiskUpdate(MarketId id) external;

    /// @notice Updates the Uniswap V4 pool fee (immediate, no timelock)
    /// @param id The market ID
    /// @param newFee New fee in hundredths of bips (e.g., 3000 = 0.3%)
    /// @param tickSpacing The pool's tick spacing (must match the deployed pool)
    function updatePoolFee(
        MarketId id,
        uint24 newFee,
        int24 tickSpacing
    ) external;

    /// @notice Gets the pending risk update for a market
    /// @param id The market ID
    /// @return The pending update struct
    function getPendingRiskUpdate(
        MarketId id
    ) external view returns (PendingRiskUpdate memory);
}
