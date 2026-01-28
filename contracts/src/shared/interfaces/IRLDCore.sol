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
    }

    struct MarketConfig {


        uint64 minColRatio;
        uint64 maintenanceMargin;
        uint64 liquidationCloseFactor; // e.g., 50% (5e17)
        uint32 fundingPeriod; // Added: Configurable Funding Period (e.g. 30 days)
        // liquidationIncentive moved to module params
        bytes32 liquidationParams; // Packed params for the module

        address brokerVerifier; // Trusted Verifier for Prime Brokers (Immutable)
    }

    struct MarketState {
        uint128 normalizationFactor; // Debt Scaler (starts at 1e18)
        uint48 lastUpdateTimestamp;

    }

    struct Position {
        uint128 debtPrincipal; // Collateral tracking removed - solvency delegated to PrimeBroker
    }

    /* ============================================================================================ */
    /*                                           EVENTS                                             */
    /* ============================================================================================ */
    // --- Events ---
    event MarketCreated(MarketId indexed id, address indexed collateral, address indexed underlying, address pool); // Indexed pool

    event PositionModified(MarketId indexed id, address indexed user, int256 deltaCollateral, int256 deltaDebt);
    event SecurityUpdate(MarketId indexed id, string indexed action, address indexed operator);
    
    // --- Errors ---
    error Unauthorized();
    error InvalidMarket();
    error InvalidParam(string param);
    error MarketAlreadyExists();

    error NotLocked();
    error Insolvent(address user);
    error UserSolvent(address user);
    error InvalidBroker(address user);
    error SlippageExceeded();
    error CloseFactorExceeded();

    /* ============================================================================================ */
    /*                                          FUNCTIONS                                           */
    /* ============================================================================================ */

    // --- Market Creation ---

    /// @notice Creates a new RLD Market
    /// @param addresses The address parameters for the market
    /// @param config The configuration parameters for the market
    /// @return marketId The unique identifier of the created market
    function createMarket(MarketAddresses calldata addresses, MarketConfig calldata config) external returns (MarketId);

    /// @notice Checks if a market exists and is valid
    function isValidMarket(MarketId id) external view returns (bool);

    /// @notice Returns the current state of a market
    function getMarketState(MarketId id) external view returns (MarketState memory);

    /// @notice Returns the addresses of a market
    function getMarketAddresses(MarketId id) external view returns (MarketAddresses memory);

    /// @notice Returns the config of a market
    function getMarketConfig(MarketId id) external view returns (MarketConfig memory);

    /// @notice Returns the position of a user in a market
    function getPosition(MarketId id, address user) external view returns (Position memory);


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
    function modifyPosition(MarketId id, int256 deltaCollateral, int256 deltaDebt) external;
    
    /// @notice Checks if a user is solvent.
    function isSolvent(MarketId id, address user) external view returns (bool);

    /* ============================================================================================ */
    /*                                      SETTLEMENT / LIQ                                        */
    /* ============================================================================================ */



    /// @notice Liquidates an insolvent position (Legacy/Direct mode).
    /// @notice Liquidates an insolvent position (Legacy/Direct mode).
    function liquidate(MarketId id, address user, uint256 debtToCover) external;


}
