// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {ITWAMM} from "../../twamm/ITWAMM.sol";

/// @title Prime Broker Interface
/// @notice Interface for the "Smart Margin Account" that holds assets.
/// @dev The PrimeBroker uses execute() for all DeFi interactions (LP, TWAMM, etc.)
///      and tracking functions (setActiveV4Position, setActiveTwammOrder) to register
///      positions for solvency calculations.
interface IPrimeBroker {
    struct TwammOrderInfo {
        PoolKey key;
        ITWAMM.OrderKey orderKey;
        bytes32 orderId;
    }

    /// @notice Output from seize() during liquidation
    /// @dev Enables two-phase seize: wRLP extracted is burned to offset debt,
    ///      collateral goes to liquidator as bonus.
    struct SeizeOutput {
        uint256 collateralSeized; // collateralToken transferred to recipient (liquidator bonus)
        uint256 wRLPExtracted; // positionToken (wRLP) sent to Core for burning
    }

    /// @notice Returns the total Net Asset Value of the account in collateral terms.
    /// @dev Used by RLDCore for solvency checks.
    /// @dev Includes: cash + wRLP tokens + tracked TWAMM + tracked V4 LP
    function getNetAccountValue() external view returns (uint256);

    /// @notice Seizes assets from the account during liquidation.
    /// @dev Only callable by RLDCore during liquidation.
    /// @dev Priority order: wRLP (token terms) → Cash → TWAMM → V4 LP
    /// @dev Design: wRLP is extracted in TOKEN terms (up to principalToCover) for 1:1 debt
    ///      cancellation. Collateral goes to recipient (liquidator). Other tokens stay in broker.
    /// @param value The value (in collateral terms) to seize.
    /// @param principalToCover The wRLP debt principal being covered (in token terms).
    /// @param recipient The liquidator address to receive collateral.
    /// @return output The amounts of collateral and wRLP extracted.
    function seize(
        uint256 value,
        uint256 principalToCover,
        address recipient
    ) external returns (SeizeOutput memory output);

    /// @notice Emitted when a generic execution is performed.
    event Execute(address indexed target, bytes data);

    /// @notice Emitted when an operator is updated.
    event OperatorUpdated(address indexed operator, bool active);

    // Indexing Events
    /// @notice Emitted on any balance change for indexer tracking
    event AccountBalanceChanged(
        address indexed account,
        address indexed token,
        int256 delta,
        uint256 newBalance,
        bytes32 reason // keccak256("deposit"), keccak256("withdraw"), etc.
    );

    /// @notice Emitted for periodic state verification
    event StateAudit(
        address indexed account,
        uint256 collateralBalance,
        uint256 positionBalance,
        uint128 debtPrincipal,
        uint256 nav,
        uint256 blockNumber
    );

    /// @notice Complete broker state for indexing
    struct BrokerState {
        uint256 collateralBalance;
        uint256 positionBalance;
        uint128 debtPrincipal;
        uint256 debtValue;
        uint256 twammSellOwed;
        uint256 twammBuyOwed;
        uint256 v4LPValue;
        uint256 netAccountValue;
        uint256 healthFactor;
        bool isSolvent;
    }

    /// @notice Returns the complete state of this broker
    function getFullState() external view returns (BrokerState memory);

    /// @notice Emits a StateAudit event for reconciliation
    function emitStateAudit() external;

    /// @notice Sets an operator for the Prime Broker.
    /// @dev Operators can perform all actions except ownership transfer.
    /// @param operator The address to set as operator.
    /// @param active True to authorize, false to deauthorize.
    function setOperator(address operator, bool active) external;

    /// @notice Sets which V4 LP position is tracked for NAV calculation.
    /// @param newTokenId The NFT token ID to track (0 to clear).
    function setActiveV4Position(uint256 newTokenId) external;

    /// @notice Sets which TWAMM order is tracked for NAV calculation.
    /// @param info The TWAMM order info to track.
    function setActiveTwammOrder(TwammOrderInfo calldata info) external;

    /// @notice Get the current nonce for signature-based operator authorization.
    /// @param caller The address of the caller (executor contract).
    function operatorNonces(address caller) external view returns (uint256);

    /// @notice Set operator via signature from the NFT owner.
    /// @param operator The address to grant/revoke operator status.
    /// @param active True to grant, false to revoke.
    /// @param signature EIP-191 signature from the NFT owner.
    /// @param nonce Must match operatorNonces[msg.sender].
    function setOperatorWithSignature(
        address operator,
        bool active,
        bytes calldata signature,
        uint256 nonce
    ) external;

    /// @notice Submits a TWAMM order with automatic registration for solvency tracking.
    /// @dev The broker becomes the order owner. Uses JIT approval internally.
    /// @param twammHook The TWAMM hook contract address.
    /// @param params The order parameters (key, zeroForOne, duration, amountIn).
    /// @return orderId The unique identifier of the created order.
    /// @return orderKey The order key for tracking and claiming.
    function submitTwammOrder(
        address twammHook,
        ITWAMM.SubmitOrderParams calldata params
    ) external returns (bytes32 orderId, ITWAMM.OrderKey memory orderKey);

    /// @notice Cancels the active TWAMM order and claims proceeds.
    /// @return buyTokensOut Amount of buy tokens received.
    /// @return sellTokensRefund Amount of sell tokens refunded.
    function cancelTwammOrder()
        external
        returns (uint256 buyTokensOut, uint256 sellTokensRefund);

    /* ============================================================================================ */
    /*                                      BOND FREEZE                                            */
    /* ============================================================================================ */

    /// @notice Emitted when the broker is frozen for bond mode
    event BrokerFrozen(address indexed owner);

    /// @notice Emitted when the broker is unfrozen
    event BrokerUnfrozen(address indexed owner);

    /// @notice Whether this broker is currently frozen
    function frozen() external view returns (bool);

    /// @notice Freezes the broker — revokes all operators and blocks state changes
    function freeze() external;

    /// @notice Unfreezes the broker — re-enables all operations
    function unfreeze() external;

    /* ============================================================================================ */
    /*                                        NFT METADATA                                          */
    /* ============================================================================================ */

    // BondMetadata removed - rendering is now dynamic based on chain state
}
