// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

// V3 Interfaces rely on basic types

/// @title Prime Broker Interface
/// @notice Interface for the "Smart Margin Account" that holds assets.
/// @dev The PrimeBroker uses execute() for all DeFi interactions (LP, TWAMM, etc.)
///      and tracking functions (setActiveV4Position, setActiveTwammOrder) to register
///      positions for solvency calculations.
interface IPrimeBroker {
    struct WithdrawalRequest {
        uint256 amount;
        address recipient;
        uint48 unlockAt;
        uint64 queueEpoch;
    }

    struct TwammOrderInfo {
        bytes32 marketId;
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

    // V4 LP Position Events
    /// @notice Emitted when a V4 LP position is created by this broker
    event LiquidityAdded(uint256 indexed tokenId, uint128 liquidity);
    /// @notice Emitted when a V4 LP position is removed/reduced by this broker
    event LiquidityRemoved(uint256 indexed tokenId, uint128 liquidity, bool burned);
    /// @notice Emitted when the registered (solvency-tracked) V4 position changes
    event ActivePositionChanged(uint256 oldTokenId, uint256 newTokenId);

    // TWAMM Order Events
    /// @notice Emitted when a TWAMM order is submitted via this broker
    event TwammOrderSubmitted(bytes32 indexed orderId, bool zeroForOne, uint256 amountIn, uint256 expiration);
    /// @notice Emitted when a TWAMM order is cancelled via this broker
    event TwammOrderCancelled(bytes32 indexed orderId, uint256 buyTokensOut, uint256 sellTokensRefund);
    /// @notice Emitted when an expired TWAMM order is claimed via this broker
    event TwammOrderClaimed(bytes32 indexed orderId, uint256 claimed0, uint256 claimed1);
    /// @notice Emitted when the registered (solvency-tracked) TWAMM order changes
    event ActiveTwammOrderChanged(bytes32 oldOrderId, bytes32 newOrderId);

    // Indexing Events
    /// @notice Emitted on any balance change for indexer tracking
    event AccountBalanceChanged(
        address indexed account,
        address indexed token,
        int256 delta,
        uint256 newBalance,
        bytes32 reason // keccak256("deposit"), keccak256("withdraw"), etc.
    );

    /// @notice Emitted when collateral is queued for delayed withdrawal.
    event WithdrawalRequested(
        uint256 indexed withdrawalId,
        address indexed recipient,
        uint256 amount,
        uint48 unlockAt,
        uint64 queueEpoch
    );

    /// @notice Emitted when a queued withdrawal is cancelled by the broker owner/operator.
    event WithdrawalCancelled(uint256 indexed withdrawalId);

    /// @notice Emitted when queued collateral is released after the delay period.
    event WithdrawalExecuted(
        uint256 indexed withdrawalId,
        address indexed recipient,
        uint256 amount
    );

    /// @notice Emitted when a stale withdrawal entry is pruned.
    event WithdrawalPruned(uint256 indexed withdrawalId, uint64 staleQueueEpoch);

    /// @notice Emitted when settlement invalidates the active withdrawal queue epoch.
    event WithdrawalQueueInvalidated(uint64 newQueueEpoch);


    /// @notice Sets an operator for the Prime Broker.
    /// @dev Operators can perform all actions except ownership transfer.
    /// @param operator The address to set as operator.
    /// @param active True to authorize, false to deauthorize.
    function setOperator(address operator, bool active) external;

    /// @notice Sets which V4 LP position is tracked for NAV calculation.
    /// @param newTokenId The NFT token ID to track (0 to clear).
    function setActiveV4Position(uint256 newTokenId) external;

    /// @notice Sets which TWAMM order is tracked for NAV calculation.
    /// @param twapEngine The TwapEngine Spoke address.
    /// @param info The TWAMM order info to track.
    function setActiveTwammOrder(address twapEngine, TwammOrderInfo calldata info) external;

    /// @notice Get the current nonce for signature-based operator authorization.
    /// @param caller The address of the caller (executor contract).
    function operatorNonces(address caller) external view returns (uint256);

    /// @notice Set operator via signature from the NFT owner.
    /// @param operator The address to grant/revoke operator status.
    /// @param active True to grant, false to revoke.
    /// @param signature EIP-191 signature from the NFT owner.
    /// @param nonce Must match operatorNonces[msg.sender].
    /// @param commitment Opaque data commitment bound to the signature (e.g. callsHash).
    function setOperatorWithSignature(
        address operator,
        bool active,
        bytes calldata signature,
        uint256 nonce,
        bytes32 commitment
    ) external;

    /// @notice Submits a TWAMM stream order via TwapEngine with auto-registration.
    /// @dev The broker becomes the order owner. Uses JIT approval internally.
    /// @param twapEngine Address of the TwapEngine.
    /// @param marketId ID of the market to trade against.
    /// @param zeroForOne Direction of the swap.
    /// @param duration Order lifespan in seconds.
    /// @param amountIn Total volume to stream.
    /// @return orderId The unique identifier of the created order.
    function submitTwammOrder(
        address twapEngine,
        bytes32 marketId,
        bool zeroForOne,
        uint256 duration,
        uint256 amountIn
    ) external returns (bytes32 orderId);

    /// @notice Cancels the active TWAMM order and claims proceeds.
    /// @return buyTokensOut Amount of buy tokens received.
    /// @return sellTokensRefund Amount of sell tokens refunded.
    function cancelTwammOrder()
        external
        returns (uint256 buyTokensOut, uint256 sellTokensRefund);

    /// @notice Claims tokens from an expired TWAMM order that isn't actively tracked.
    /// @return claimedBuyToken Amount of buy tokens claimed.
    function claimExpiredTwammOrderWithId(
        address twapEngine,
        bytes32 marketId,
        bytes32 orderId
    ) external returns (uint256 claimedBuyToken);

    /// @notice Withdraws a generic ERC20 token to a specified recipient tracking solvency limits
    /// @param token The address of the token to withdraw
    /// @param recipient The address to receive the tokens
    /// @param amount The amount to withdraw
    function withdrawToken(address token, address recipient, uint256 amount) external;

    /// @notice Requests delayed collateral withdrawal for CDS markets with active debt.
    /// @param amount Amount of collateral to queue.
    /// @param recipient Recipient for delayed release.
    /// @return withdrawalId Unique request identifier.
    function requestWithdrawal(
        uint256 amount,
        address recipient
    ) external returns (uint256 withdrawalId);

    /// @notice Cancels a queued withdrawal request.
    function cancelWithdrawal(uint256 withdrawalId) external;

    /// @notice Executes a queued withdrawal once delay has elapsed.
    function executeWithdrawal(uint256 withdrawalId) external;

    /// @notice Deletes stale queue entries after settlement invalidation.
    function pruneWithdrawal(uint256 withdrawalId) external;

    /// @notice Invalidates all active queue entries by bumping queue epoch.
    /// @dev Only callable by RLDCore during settlement processing.
    /// @return newQueueEpoch The incremented queue epoch.
    function invalidateWithdrawalQueue() external returns (uint64 newQueueEpoch);

    /// @notice Returns a queued withdrawal request by id.
    function getWithdrawalRequest(
        uint256 withdrawalId
    ) external view returns (WithdrawalRequest memory request);

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
