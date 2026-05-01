// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {MarketId} from "../shared/interfaces/IRLDCore.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {
    ISignatureTransfer
} from "permit2/src/interfaces/ISignatureTransfer.sol";
import {BrokerRouterLib} from "./lib/BrokerRouterLib.sol";

/// @title  BrokerRouter — Universal Execution Layer for PrimeBroker
/// @author RLD Protocol
/// @notice Single contract handling deposit, long, and short operations
///         for any RLD market.  Replaces BrokerExecutor +
///         LeverageShortExecutor with one unified router.
///
/// @dev ## Architecture
///
///     ```
///     ┌─────────────────── BrokerRouter ────────────────────┐
///     │  Pre-approved operator on every broker (set once)    │
///     │                                                      │
///     │  deposit()  executeLong()  closeLong()               │
///     │  depositWithApproval()  executeShort()  closeShort() │
///     │      │           │            │                       │
///     │      └───────────┼────────────┘                      │
///     │                  │                                    │
///     │       onlyBrokerAuthorized(broker)                   │
///     │     msg.sender == NFT owner OR operator              │
///     │                  │                                    │
///     │       GhostRouter.swap()    ← routed swaps           │
///     └──────────────────────────────────────────────────────┘
///     ```
///
/// ## Security Model
///
///     1. **Permanent Operator** — Router is set as operator during
///        `PrimeBroker.initialize()`, NOT per-transaction.  This avoids
///        the nonce / signature overhead of BrokerExecutor.
///     2. **Caller Gating** — Every mutating function verifies
///        `msg.sender` is the broker NFT owner or a registered
///        operator via `onlyBrokerAuthorized(broker)`.
///     3. **No Token Custody** — Router never holds user funds between
///        transactions.  All proceeds/residuals are swept to the broker
///        before the function returns.
///     4. **Pool Key Validation** — `_validatePoolKey()` ensures the
///        user-supplied `PoolKey` currencies match the broker's token
///        pair, preventing swaps against rogue pools.
///     5. **Reentrancy Guard** — All external functions are `nonReentrant`.
///
/// ## Flows
///
///     | Function        | Tokens In              | Tokens Out             |
///     |-----------------|------------------------|------------------------|
///     | `deposit()`     | Underlying → adapter    | collateral → broker    |
///     | `executeLong()`  | waUSDC → V4 swap       | wRLP → broker          |
///     | `closeLong()`    | wRLP → V4 swap         | waUSDC → broker        |
///     | `executeShort()` | mint wRLP → V4 swap    | waUSDC → broker (loop) |
///     | `closeShort()`   | waUSDC → V4 swap       | wRLP → burn debt       |
///
/// ## Access Control
///
///     | Function              | Guard                       | Who Can Call         |
///     |-----------------------|-----------------------------|----------------------|
///     | `deposit()`           | `onlyBrokerAuthorized`      | NFT owner / operator |
///     | `depositWithApproval` | `onlyBrokerAuthorized`      | NFT owner / operator |
///     | `executeLong()`       | `onlyBrokerAuthorized`      | NFT owner / operator |
///     | `closeLong()`         | `onlyBrokerAuthorized`      | NFT owner / operator |
///     | `executeShort()`      | `onlyBrokerAuthorized`      | NFT owner / operator |
///     | `closeShort()`        | `onlyBrokerAuthorized`      | NFT owner / operator |
///
/// ## Test Coverage (Phase 4 — 17 tests)
///
///     - End-to-end long / close long with V4 solvency checks
///     - End-to-end short / close short with V4 solvency checks
///     - `closeShort()` debt capping (avoids underflow on full repay)
///     - Wrong pool key reverts with `PoolKeyMismatch()`
///     - No token residuals left in router after any operation
///     - Unauthorized caller reverts
///
/// ## Known Limitations
///
///     - Deposit conversion is delegated to a per-market adapter configured at
///       deployment.
contract BrokerRouter is ReentrancyGuard {
    uint8 internal constant ACTION_EXECUTE_LONG = 1;
    uint8 internal constant ACTION_CLOSE_LONG = 2;

    /* ============================================================================================ */
    /*                                          IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice PrimeBrokerFactory trusted for broker NFT ownership.
    address public immutable brokerFactory;

    /// @notice Market this router is bound to.
    MarketId public immutable marketId;

    /// @notice Broker collateral token for this market.
    address public immutable collateralToken;

    /// @notice Broker position/debt token for this market.
    address public immutable positionToken;

    /// @notice Token accepted for user deposits.
    address public immutable underlyingToken;

    /// @notice Protocol-specific converter for underlying deposits.
    address public immutable depositAdapter;

    /// @notice GhostRouter used for all executable swaps
    address public immutable ghostRouter;

    /// @notice Permit2 singleton for gasless token approvals
    ISignatureTransfer public immutable permit2;

    /* ============================================================================================ */
    /*                                       DEPOSIT ROUTES                                         */
    /* ============================================================================================ */

    /// @notice Constructor configuration for a per-market router.
    struct MarketConfig {
        address brokerFactory;
        MarketId marketId;
        address collateralToken;
        address positionToken;
        address underlyingToken;
        address depositAdapter;
    }

    /* ============================================================================================ */
    /*                                           EVENTS                                             */
    /* ============================================================================================ */

    event SwapExecuted(
        address indexed broker,
        uint8 indexed action,
        uint256 amountIn,
        uint256 amountOut
    );
    event ShortPositionUpdated(
        address indexed broker,
        uint256 debtAmount,
        uint256 proceeds
    );
    event ShortPositionClosed(
        address indexed broker,
        uint256 debtRepaid,
        uint256 collateralSpent
    );
    event Deposited(
        address indexed broker,
        uint256 underlyingAmount,
        uint256 collateralAmount
    );

    /* ============================================================================================ */
    /*                                           ERRORS                                             */
    /* ============================================================================================ */

    error NotAuthorized();
    error PoolKeyMismatch();
    error SlippageExceeded();
    error UnexpectedHook();
    error InvalidRoute();
    error PermitTokenMismatch();
    error InvalidBroker();

    /* ============================================================================================ */
    /*                                          MODIFIERS                                           */
    /* ============================================================================================ */

    /// @notice Restricts to broker NFT owner or any operator of that broker
    /// @dev Mirrors PrimeBroker's own onlyAuthorized modifier
    modifier onlyBrokerAuthorized(address broker) {
        _onlyBrokerAuthorized(broker);
        _;
    }

    function _onlyBrokerAuthorized(address broker) internal view {
        BrokerRouterLib.authorizeBroker(
            broker,
            brokerFactory,
            marketId,
            collateralToken,
            positionToken
        );
    }

    /* ============================================================================================ */
    /*                                         CONSTRUCTOR                                          */
    /* ============================================================================================ */

    /// @param _ghostRouter GhostRouter singleton
    /// @param _permit2 Permit2 singleton address
    /// @param config Per-market immutable router configuration
    constructor(
        address _ghostRouter,
        address _permit2,
        MarketConfig memory config
    ) {
        if (
            _ghostRouter == address(0) ||
            config.brokerFactory == address(0) ||
            MarketId.unwrap(config.marketId) == bytes32(0) ||
            config.collateralToken == address(0) ||
            config.positionToken == address(0) ||
            config.underlyingToken == address(0) ||
            config.depositAdapter == address(0)
        ) revert InvalidRoute();

        brokerFactory = config.brokerFactory;
        marketId = config.marketId;
        collateralToken = config.collateralToken;
        positionToken = config.positionToken;
        underlyingToken = config.underlyingToken;
        depositAdapter = config.depositAdapter;
        ghostRouter = _ghostRouter;
        permit2 = ISignatureTransfer(_permit2);
    }

    /* ============================================================================================ */
    /*                                         DEPOSIT                                              */
    /* ============================================================================================ */

    /// @notice Deposit underlying tokens and convert them into broker collateral.
    /// @dev Flow: pull underlying via Permit2 → adapter converts → broker receives collateral.
    /// @param broker The PrimeBroker to deposit into
    /// @param amount Amount of underlying tokens to deposit
    /// @param minCollateralOut Minimum collateral required from the adapter
    /// @param permit Permit2 transfer parameters (token, amount, nonce, deadline)
    /// @param signature User's Permit2 signature authorizing the transfer
    function deposit(
        address broker,
        uint256 amount,
        uint256 minCollateralOut,
        ISignatureTransfer.PermitTransferFrom calldata permit,
        bytes calldata signature
    ) external onlyBrokerAuthorized(broker) nonReentrant {
        uint256 collateralAmount = BrokerRouterLib.depositWithPermit(
            broker,
            amount,
            minCollateralOut,
            permit2,
            underlyingToken,
            collateralToken,
            depositAdapter,
            permit,
            signature
        );
        emit Deposited(broker, amount, collateralAmount);
    }

    /// @notice Deposit underlying tokens using a standard ERC20 approval (no Permit2)
    /// @dev Requires user to have called underlying.approve(router, amount) beforehand
    /// @param broker The PrimeBroker to deposit into
    /// @param amount Amount of underlying tokens to deposit
    /// @param minCollateralOut Minimum collateral required from the adapter
    function depositWithApproval(
        address broker,
        uint256 amount,
        uint256 minCollateralOut
    ) external onlyBrokerAuthorized(broker) nonReentrant {
        uint256 collateralAmount = BrokerRouterLib.depositWithApproval(
            broker,
            amount,
            minCollateralOut,
            underlyingToken,
            collateralToken,
            depositAdapter
        );
        emit Deposited(broker, amount, collateralAmount);
    }

    /* ============================================================================================ */
    /*                                       EXECUTE LONG                                           */
    /* ============================================================================================ */

    /// @notice Buy position tokens (wRLP) with collateral (waUSDC) — simple long
    /// @dev Flow: Withdraw waUSDC from broker → swap through GhostRouter → return wRLP to broker
    ///      Reads collateralToken and positionToken from broker (market-independent).
    /// @param broker The PrimeBroker to trade from
    /// @param amountIn Amount of collateral to swap
    /// @param poolKey V4 pool key for routing the swap
    /// @param minAmountOut Minimum position tokens required from the swap
    /// @return amountOut Amount of position tokens received
    function executeLong(
        address broker,
        uint256 amountIn,
        PoolKey calldata poolKey,
        uint256 minAmountOut
    )
        external
        onlyBrokerAuthorized(broker)
        nonReentrant
        returns (uint256 amountOut)
    {
        amountOut = BrokerRouterLib.swapBrokerExactInput(
            broker,
            ghostRouter,
            poolKey,
            collateralToken,
            positionToken,
            amountIn,
            minAmountOut
        );
        emit SwapExecuted(broker, ACTION_EXECUTE_LONG, amountIn, amountOut);
    }

    /* ============================================================================================ */
    /*                                       CLOSE LONG                                             */
    /* ============================================================================================ */

    /// @notice Close a long position: swap wRLP → collateral (waUSDC)
    /// @dev Flow: withdrawPositionToken → swap wRLP→waUSDC → transfer proceeds to broker
    /// @param broker The PrimeBroker to trade from
    /// @param amountIn Amount of wRLP to sell
    /// @param poolKey V4 pool key for routing the swap
    /// @param minAmountOut Minimum collateral required from the swap
    /// @return amountOut Amount of collateral received
    function closeLong(
        address broker,
        uint256 amountIn,
        PoolKey calldata poolKey,
        uint256 minAmountOut
    )
        external
        onlyBrokerAuthorized(broker)
        nonReentrant
        returns (uint256 amountOut)
    {
        amountOut = BrokerRouterLib.swapBrokerExactInput(
            broker,
            ghostRouter,
            poolKey,
            positionToken,
            collateralToken,
            amountIn,
            minAmountOut
        );
        emit SwapExecuted(broker, ACTION_CLOSE_LONG, amountIn, amountOut);
    }

    /* ============================================================================================ */
    /*                                       EXECUTE SHORT                                          */
    /* ============================================================================================ */

    /// @notice Leveraged short: deposit collateral + mint debt → sell wRLP → deposit proceeds
    /// @dev Flow: modifyPosition(+col,+debt) → withdraw wRLP → swap → deposit proceeds
    ///      Mirrors the LeverageShortExecutor pattern in a single universal contract.
    /// @param broker The PrimeBroker to trade from
    /// @param initialCollateral Amount of collateral to use as margin
    /// @param targetDebtAmount Amount of wRLP debt to mint
    /// @param poolKey V4 pool key for routing the swap
    /// @param minProceeds Minimum collateral proceeds required from the swap
    /// @return proceeds Amount of collateral received from selling wRLP
    function executeShort(
        address broker,
        uint256 initialCollateral,
        uint256 targetDebtAmount,
        PoolKey calldata poolKey,
        uint256 minProceeds
    )
        external
        onlyBrokerAuthorized(broker)
        nonReentrant
        returns (uint256 proceeds)
    {
        proceeds = BrokerRouterLib.executeShort(
            broker,
            initialCollateral,
            targetDebtAmount,
            poolKey,
            minProceeds,
            ghostRouter,
            marketId,
            collateralToken,
            positionToken
        );
        emit ShortPositionUpdated(broker, targetDebtAmount, proceeds);
    }

    /* ============================================================================================ */
    /*                                       CLOSE SHORT                                            */
    /* ============================================================================================ */

    /// @notice Close (partially or fully) a short position by buying back wRLP and repaying debt
    /// @dev Flow: withdraw collateral → swap waUSDC → wRLP → transfer wRLP to broker → modifyPosition(0, -debt)
    ///      The bought wRLP is burned by Core when repaying debt.
    /// @param broker The PrimeBroker to close the short on
    /// @param collateralToSpend Amount of waUSDC to use for buying wRLP
    /// @param poolKey V4 pool key for routing the swap
    /// @param minDebtBought Minimum wRLP required from the swap
    /// @return debtRepaid Amount of wRLP debt repaid
    function closeShort(
        address broker,
        uint256 collateralToSpend,
        PoolKey calldata poolKey,
        uint256 minDebtBought
    )
        external
        onlyBrokerAuthorized(broker)
        nonReentrant
        returns (uint256 debtRepaid)
    {
        debtRepaid = BrokerRouterLib.closeShort(
            broker,
            collateralToSpend,
            poolKey,
            minDebtBought,
            ghostRouter,
            marketId,
            collateralToken,
            positionToken
        );
        emit ShortPositionClosed(broker, debtRepaid, collateralToSpend);
    }

    /// @notice Validates that the pool's currencies match the broker's token pair
    /// @dev Prevents swapping against wrong pools — currencies must be {collateral, position}
    function _validatePoolKey(
        PoolKey calldata poolKey,
        address collateral,
        address position
    ) internal pure {
        BrokerRouterLib.validatePoolKey(poolKey, collateral, position);
    }

}
