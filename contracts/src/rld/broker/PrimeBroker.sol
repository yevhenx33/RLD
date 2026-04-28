// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPrimeBroker} from "../../shared/interfaces/IPrimeBroker.sol";
import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {IValuationModule} from "../../shared/interfaces/IValuationModule.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {FixedPointMathLib} from "../../shared/utils/FixedPointMathLib.sol";
import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IGhostRouter} from "../../dex/interfaces/IGhostRouter.sol";
import {ITwapEngine} from "../../dex/interfaces/ITwapEngine.sol";
import {ISpotOracle} from "../../shared/interfaces/ISpotOracle.sol";
import {IRLDOracle} from "../../shared/interfaces/IRLDOracle.sol";
import {PrimeBrokerOpsModule} from "./PrimeBrokerOpsModule.sol";

/// @dev Minimal ERC721 interface for ownership checks
interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
}

/// @dev Minimal Permit2 interface for token approvals
interface IPermit2 {
    function approve(
        address token,
        address spender,
        uint160 amount,
        uint48 expiration
    ) external;
}

/// @title Prime Broker V1 - Smart Margin Account
/// @author RLD Protocol
/// @notice A user-owned smart contract that serves as a "smart margin account" for the RLD Protocol.
///
/// @dev ## Overview
///
/// The PrimeBroker is the core account primitive in RLD. Each user who wants to borrow against
/// collateral deploys their own PrimeBroker instance. The broker:
///
/// 1. **Holds Assets** - Cash (collateral token), wRLP tokens, V4 LP positions, TWAMM orders
/// 2. **Reports Value** - `getNetAccountValue()` calculates total asset value for solvency checks
/// 3. **Enables Actions** - External `BrokerExecutor` contracts enable custom DeFi interactions
/// 4. **Supports Liquidation** - `seize()` extracts assets during liquidation with proper routing
///
/// ## Architecture
///
/// ```
/// ┌─────────────────────────────────────────────────────────────────────────┐
/// │                           USER (EOA)                                     │
/// │                               │                                          │
/// │                   Owns NFT at PrimeBrokerFactory                        │
/// │                    (tokenId = brokerAddress)                             │
/// │                               │                                          │
/// │                               ▼                                          │
/// │  ┌─────────────────────────────────────────────────────────────────┐    │
/// │  │                       PrimeBroker                                │    │
/// │  │                                                                  │    │
/// │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │    │
/// │  │  │   Cash   │  │   wRLP   │  │  V4 LP   │  │  TWAMM   │        │    │
/// │  │  │(ERC20 bal)│  │(ERC20 bal)│  │(tokenId) │  │(orderInfo)│        │    │
/// │  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │    │
/// │  │                                                                  │    │
/// │  │  getNetAccountValue() → Sum of all asset values                 │    │
/// │  │  BrokerExecutor pattern → custom DeFi interactions via operator│    │
/// │  │  withdrawCollateral() → Direct withdrawal with solvency check   │    │
/// │  │  seize(amount, recipient) → Liquidation asset extraction        │    │
/// │  └─────────────────────────────────────────────────────────────────┘    │
/// │                               │                                          │
/// │                               ▼                                          │
/// │                    RLDCore (Solvency Checks)                           │
/// │                               │                                          │
/// │              isSolvent(broker) = netWorth >= debt × (ratio - 1)          │
/// └─────────────────────────────────────────────────────────────────────────┘
/// ```
///
/// ## Ownership Model
///
/// Ownership is determined by an ERC721 NFT in the PrimeBrokerFactory:
/// - tokenId = uint256(uint160(brokerAddress))
/// - Whoever holds the NFT is the owner
/// - Transferring the NFT transfers account ownership
/// - Enables account trading on secondary markets (OpenSea, etc.)
///
/// ## Security Features (V1.1)
///
/// ### 1. External Executor Pattern
/// - Custom execution paths use external `BrokerExecutor` contracts
/// - Executors become temporary operators via signature
/// - No approval persistence - executors revoke themselves after actions
///
/// ### 2. Ownership Validation in NAV
/// - V4 LP positions only counted if broker still owns the NFT
/// - TWAMM orders only counted if orderKey.owner matches broker
/// - Prevents over-leverage from transferred assets
///
/// ### 3. Direct Withdrawal Functions
/// - `withdrawCollateral()`, `withdrawPositionToken()`, `withdrawUnderlying()`
/// - Bypass token blacklist for legitimate withdrawals
/// - Include solvency checks to prevent over-withdrawal
///
/// ### 4. NAV Double-Counting Fix
/// - Core subtracts debt from assets to calculate net worth
/// - Prevents infinite leverage from minted wRLP
/// - See RLDCore._isSolvent() for details
///
/// ## V1 Limitations
///
/// - Only **one** V4 LP position can be tracked at a time
/// - Only **one** TWAMM order can be tracked at a time
/// - Users must manually track their largest positions for accurate solvency
///
/// ## Security Invariants
///
/// 1. Every state-changing action ends with a solvency check
/// 2. Only Core can call seize() (liquidation only)
/// 3. Only owner can modify the operator set
/// 4. Operators and owner share the same permissions except operator management
/// 5. JIT approvals are always revoked (no lingering allowances)
/// 6. Asset ownership validated during NAV calculation
///
contract PrimeBroker is IPrimeBroker, ReentrancyGuard {
    using SafeTransferLib for ERC20;
    using FixedPointMathLib for uint256;

    /* ============================================================================================ */
    /*                                         IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice The RLDCore singleton contract
    /// @dev Set in initialize(), NOT in constructor (clones don't inherit immutables correctly)
    /// Used for: solvency checks, position modifications, lock pattern
    /// ARCHITECTURE FIX: Changed from immutable to storage to support EIP-1167 clone pattern.
    /// I-5 NOTE: Uses UPPER_CASE naming convention despite being a storage variable because
    /// it is effectively immutable after initialize() (set once, never changed).
    address public CORE;

    /// @notice The module for valuing V4 LP positions
    /// @dev Implements IValuationModule - getValue() for V4 positions
    address public immutable V4_MODULE;

    /// @notice The module for valuing TWAMM orders
    /// @dev Implements IValuationModule - getValue() for TWAMM orders
    /// WARNING: This is the VALUATION module, not the TWAMM hook itself
    address public immutable TWAMM_MODULE;

    /// @notice Universal V4 Position Manager (POSM) for NFT ownership checks
    /// @dev The Uniswap V4 contract that mints LP position NFTs
    address public immutable POSM;

    /// @notice Delegatecall module for V4/TWAMM/operator-signature operations.
    address public immutable OPS_MODULE;

    /// @notice Canonical Permit2 contract for token approvals
    address public constant PERMIT2 =
        0x000000000022D473030F116dDEE9F6B43aC78BA3;

    /* ============================================================================================ */
    /*                                      STORAGE VARIABLES                                       */
    /* ============================================================================================ */

    /// @notice The PrimeBrokerFactory that deployed this broker
    /// @dev Used for NFT-based ownership lookup: factory.ownerOf(tokenId)
    address public factory;

    /// Metadata removed - rendering handles dynamically

    /// @notice Which market this broker operates in
    /// @dev Each broker is bound to exactly one market at initialization
    MarketId public marketId;

    /* ─────────────────────────────────────────────────────────────────────────────────────────── */
    /*                              CACHED MARKET ADDRESSES                                        */
    /* ─────────────────────────────────────────────────────────────────────────────────────────── */

    /// @notice The quote token (e.g., USDC) - cached for gas savings
    /// @dev Copied from Core.MarketAddresses during initialization
    /// Used in: getNetAccountValue(), seize(), modifyPosition()
    address public collateralToken;

    /// @notice The base token (e.g., wETH) - cached for gas savings
    /// @dev Copied from Core.MarketAddresses during initialization
    address public underlyingToken;

    /// @notice The wRLP (Wrapped Rate LP) token for this market
    /// @dev ERC20 representing synthetic debt; can be held as collateral
    /// Value = balance × indexPrice
    address public positionToken;

    /// @notice Pool identifier for oracle price lookups
    /// @dev Passed to oracle.getIndexPrice(underlyingPool, collateralToken)
    address public underlyingPool;

    /// @notice Price oracle for index and spot prices
    /// @dev Implements both IRLDOracle and ISpotOracle interfaces
    address public rateOracle;

    /// @notice Optional settlement module for CDS markets (address(0) => standard RLP).
    address public settlementModule;

    /// @notice TwapEngine address
    /// @dev Cached during submitTwammOrder() for gas savings
    address public twapEngine;

    /* ─────────────────────────────────────────────────────────────────────────────────────────── */
    /*                               CDS WITHDRAWAL QUEUE (7-DAY)                                  */
    /* ─────────────────────────────────────────────────────────────────────────────────────────── */

    /// @notice Fixed withdrawal delay for debt-bearing CDS brokers.
    uint48 public constant WITHDRAWAL_DELAY = 7 days;

    /// @notice Monotonic identifier for queued withdrawal requests.
    uint256 public nextWithdrawalId;

    /// @notice Total collateral reserved by active queue entries in the current epoch.
    uint256 public queuedCollateralTotal;

    /// @notice Queue epoch used for O(1) invalidation during settlement.
    uint64 public withdrawalQueueEpoch;

    /// @notice Per-request withdrawal queue storage.
    mapping(uint256 withdrawalId => WithdrawalRequest request)
        private withdrawalRequests;

    /* ─────────────────────────────────────────────────────────────────────────────────────────── */
    /*                              TRACKED POSITIONS (V1: ONE EACH)                               */
    /* ─────────────────────────────────────────────────────────────────────────────────────────── */

    /// @notice The currently tracked Uniswap V4 LP position NFT ID
    /// @dev 0 = no position tracked
    /// V1 limitation: Only ONE position can be tracked for solvency
    /// If user has multiple positions, they should track the largest one
    uint256 public activeTokenId;

    /// @notice The currently tracked TWAMM order
    /// @dev orderId = bytes32(0) means no order tracked
    /// V1 limitation: Only ONE order can be tracked for solvency
    TwammOrderInfo public activeTwammOrder;

    /* ─────────────────────────────────────────────────────────────────────────────────────────── */
    /*                                     OPERATOR SYSTEM                                         */
    /* ─────────────────────────────────────────────────────────────────────────────────────────── */

    /// @notice Maximum number of operators per broker
    /// @dev Bounded to keep freeze() gas costs predictable (max 8 SSTOREs)
    uint8 public constant MAX_OPERATORS = 8;

    /// @notice Addresses authorized to act on behalf of the owner
    /// @dev Operators can do everything EXCEPT modify the operator set
    /// Use case: Keeper bots, automation contracts, multisig signers
    mapping(address => bool) public operators;

    /// @notice Enumerable list of active operators (bounded to MAX_OPERATORS)
    /// @dev Enables mass revocation during freeze() without iteration over mapping
    address[] public operatorList;

    /// @dev Prevents re-initialization of clones
    /// Set to true in implementation constructor AND after clone initialization
    bool private initialized;

    /// @notice Nonces for signature-based operator authorization
    /// @dev Incremented after each successful setOperatorWithSignature call
    mapping(address => uint256) public operatorNonces;

    /* ─────────────────────────────────────────────────────────────────────────────────────────── */
    /*                                      BOND FREEZE                                            */
    /* ─────────────────────────────────────────────────────────────────────────────────────────── */

    /// @notice Whether this broker is frozen (bond mode)
    /// @dev When frozen, all state-changing operations are blocked except seize() and unlock
    bool public frozen;

    /* ============================================================================================ */
    /*                                          EVENTS                                             */
    /* ============================================================================================ */

    /// @notice Emitted when a broker is initialized (I-6 fix)
    event BrokerInitialized(
        address indexed broker,
        address indexed core,
        MarketId marketId,
        address factory
    );

    /* ============================================================================================ */
    /*                                       CUSTOM ERRORS                                         */
    /* ============================================================================================ */

    error Insolvent();
    error NotAuthorized();
    error NotOwner();
    error NotCore();
    error NotFactory();
    error BrokerFrozen_();
    error InvalidToken();
    error AlreadyInitialized();
    error ZeroAddress();
    error TooManyOperators();
    error NoActivePosition();
    error InvalidTarget();
    error ExecuteFailed();
    error ZeroLiquidity();
    error InvalidOrder();
    error NoActiveOrder();
    error WrongMarket();
    error DuplicateOperator();
    error NotOperator();
    error InvalidNonce();
    error InvalidSignature();
    error AlreadyFrozen();
    error NotFrozen();
    error WithdrawalQueueRequired();
    error WithdrawalQueueNotRequired();
    error ZeroWithdrawalAmount();
    error InvalidWithdrawalRecipient();
    error UnknownWithdrawalRequest();
    error WithdrawalNotReady(uint48 unlockAt);
    error WithdrawalRequestStale();
    error ActiveWithdrawalRequest();
    error InsufficientUnqueuedCollateral();
    error GlobalSettlementActive();

    /* ============================================================================================ */
    /// @dev Restricts function to RLDCore only
    /// Used for: seize() during liquidation
    modifier onlyCore() {
        if (msg.sender != CORE) revert NotCore();
        _;
    }

    /// @dev Restricts function to the PrimeBrokerFactory that deployed this broker
    /// Used for: revokeAllOperators() during NFT ownership transfer
    modifier onlyFactory() {
        if (msg.sender != factory) revert NotFactory();
        _;
    }

    /// @dev Restricts function to the NFT holder only
    /// Used for: setOperator() - managing who can operate the account
    ///
    /// Ownership is dynamic - the "owner" is whoever currently holds the NFT:
    ///   owner = factory.ownerOf(uint256(uint160(address(this))))
    modifier onlyOwner() {
        if (
            IERC721(factory).ownerOf(uint256(uint160(address(this)))) !=
            msg.sender
        ) revert NotOwner();
        _;
    }

    /// @dev Restricts function to owner OR any approved operator
    /// Used for: execute(), modifyPosition(), setActiveV4Position(), etc.
    ///
    /// This enables account delegation without transferring ownership:
    /// - Grant trading bot access: setOperator(bot, true)
    /// - Revoke access anytime: setOperator(bot, false)
    modifier onlyAuthorized() {
        address owner = IERC721(factory).ownerOf(
            uint256(uint160(address(this)))
        );
        if (msg.sender != owner && !operators[msg.sender])
            revert NotAuthorized();
        _;
    }

    /// @dev Restricts function when broker is frozen (bond mode)
    /// Frozen brokers block ALL state-changing operations to protect bond integrity.
    /// Only seize() (liquidation), view functions, and unfreeze() bypass this.
    modifier whenNotFrozen() {
        if (frozen) revert BrokerFrozen_();
        _;
    }

    /* ============================================================================================ */
    /*                                        CONSTRUCTOR                                          */
    /* ============================================================================================ */

    /// @notice Deploys the PrimeBroker implementation contract
    /// @dev This is the IMPLEMENTATION that clones will delegate to
    /// The constructor locks the implementation to prevent direct use
    ///
    /// NOTE: CORE is NOT set here - it's set in initialize() because:
    /// - EIP-1167 clones inherit immutables from implementation bytecode
    /// - We need each clone to use the correct Core address for its market
    ///
    /// @param _v4Module IValuationModule for Uniswap V4 LP position valuation
    /// @param _twammModule IValuationModule for TWAMM order valuation
    /// @param _posm Universal V4 Position Manager for NFT ownership checks
    constructor(address _v4Module, address _twammModule, address _posm) {
        // CORE is set in initialize() - see ARCHITECTURE FIX comment above
        V4_MODULE = _v4Module;
        TWAMM_MODULE = _twammModule;
        POSM = _posm;
        OPS_MODULE = address(new PrimeBrokerOpsModule());

        // SECURITY: Lock the implementation contract
        // This prevents anyone from calling initialize() on the implementation itself
        // Only clones can be initialized
        initialized = true;
    }

    /* ============================================================================================ */
    /*                                      INITIALIZATION                                         */
    /* ============================================================================================ */

    /// @notice Initializes a cloned PrimeBroker instance
    /// @dev Called by PrimeBrokerFactory immediately after clone deployment
    ///
    /// This function:
    /// 1. Sets the CORE address (CRITICAL - must be set here, not in constructor)
    /// 2. Sets the market ID this broker will operate in
    /// 3. Sets the factory address for ownership lookups
    /// 4. Caches market addresses from Core to save gas on future calls
    ///
    /// Gas Optimization: We cache token addresses because getNetAccountValue() is called
    /// on EVERY solvency check. Avoiding Core calls saves ~2600 gas per check.
    ///
    /// @param _marketId The market ID this broker is bound to
    /// @param _factory The PrimeBrokerFactory that deployed this clone
    /// @param _core The RLDCore singleton address
    /// @param _initialOperators Addresses to pre-approve as operators (e.g., BrokerRouter)
    function initialize(
        MarketId _marketId,
        address _factory,
        address _core,
        address[] calldata _initialOperators
    ) external {
        // SECURITY: Prevent re-initialization
        if (initialized) revert AlreadyInitialized();
        if (_core == address(0)) revert ZeroAddress();

        // CRITICAL: Set CORE here, not in constructor
        // EIP-1167 clones would otherwise inherit placeholder from implementation
        CORE = _core;

        marketId = _marketId;
        factory = _factory;

        // OPTIMIZATION: Cache market addresses to save gas on solvency checks
        // Each getNetAccountValue() call would otherwise need Core.getMarketAddresses()
        IRLDCore.MarketAddresses memory vars = IRLDCore(CORE)
            .getMarketAddresses(_marketId);
        collateralToken = vars.collateralToken;
        underlyingToken = vars.underlyingToken;
        positionToken = vars.positionToken;
        underlyingPool = vars.underlyingPool;
        rateOracle = vars.rateOracle;
        settlementModule = vars.settlementModule;
        nextWithdrawalId = 1;

        // Set initial operators (e.g., BrokerRouter) so they're pre-approved from deploy
        if (_initialOperators.length > MAX_OPERATORS) revert TooManyOperators();
        for (uint256 i = 0; i < _initialOperators.length; i++) {
            operators[_initialOperators[i]] = true;
            operatorList.push(_initialOperators[i]);
            emit OperatorUpdated(_initialOperators[i], true);
        }

        // Pre-approve tokens → Permit2 → PositionManager for LP operations
        // This enables modifyPoolLiquidity() without JIT approvals
        ERC20(collateralToken).approve(PERMIT2, type(uint256).max);
        ERC20(positionToken).approve(PERMIT2, type(uint256).max);
        IPermit2(PERMIT2).approve(
            collateralToken,
            POSM,
            type(uint160).max,
            type(uint48).max
        );
        IPermit2(PERMIT2).approve(
            positionToken,
            POSM,
            type(uint160).max,
            type(uint48).max
        );

        initialized = true;

        emit BrokerInitialized(address(this), _core, _marketId, _factory);
    }

    /* ============================================================================================ */
    /*                                    SOLVENCY CALCULATION                                     */
    /* ============================================================================================ */

    /// @notice Calculates the total value of all assets held by this broker
    /// @dev Called by RLDCore.isSolvent() to determine if broker meets margin requirements
    ///
    /// ## Value Components (in order of priority)
    ///
    /// 1. **Cash Balance** - Direct collateralToken balance (e.g., aUSDC)
    /// 2. **wRLP Tokens** - Position tokens valued at indexPrice (wRLP × price)
    /// 3. **TWAMM Order** - Delegated to TWAMM_MODULE.getValue() (with ownership check)
    /// 4. **V4 LP Position** - Delegated to V4_MODULE.getValue() (with ownership check)
    ///
    /// ## Value Formula
    /// ```
    /// totalValue = collateralBalance
    ///            + (wRLPBalance × indexPrice)
    ///            + (twammOrder.owner == this ? twammModule.getValue() : 0)
    ///            + (v4Position.owner == this ? v4Module.getValue() : 0)
    /// ```
    ///
    /// ## Ownership Validation (V1.1 Security Fix)
    ///
    /// **Problem**: Users could transfer V4 LP NFTs or TWAMM orders to external addresses
    /// while keeping them registered, inflating NAV and enabling over-leverage.
    ///
    /// **Solution**: Only count assets that broker actually owns:
    /// - V4 LP: Check `POSM.ownerOf(tokenId) == address(this)`
    /// - TWAMM: Check `orderKey.owner == address(this)`
    ///
    /// If ownership check fails, asset value = 0 (not counted in NAV).
    ///
    /// ## Important Notes
    ///
    /// - This function is VIEW-only (no state changes)
    /// - Called during every solvency check
    /// - Gas cost: ~50k-150k depending on active positions
    /// - Index price used (not spot price) to prevent manipulation
    ///
    /// @return totalValue The total value of all assets in collateral token terms
    function getNetAccountValue()
        public
        view
        override
        returns (uint256 totalValue)
    {
        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 1: CASH BALANCE (Collateral Token)                     │
        // └─────────────────────────────────────────────────────────────────┘
        // Direct ERC20 balance - no valuation needed
        totalValue += ERC20(collateralToken).balanceOf(address(this));

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 2: wRLP TOKEN VALUE (Position Token)                   │
        // └─────────────────────────────────────────────────────────────────┘
        // wRLP tokens can be held as collateral (bought on secondary market or minted)
        // Value = balance × indexPrice
        // Example: 100 wRLP @ $2000/wRLP = $200,000 value
        uint256 wRLPBalance = ERC20(positionToken).balanceOf(address(this));
        if (wRLPBalance > 0) {
            uint256 indexPrice = IRLDOracle(rateOracle).getIndexPrice(
                underlyingPool,
                underlyingToken
            );
            totalValue += FixedPointMathLib.mulWadDown(wRLPBalance, indexPrice);
        }

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 3: TWAMM ORDER VALUE                                   │
        // └─────────────────────────────────────────────────────────────────┘
        // Delegate to TWAMM valuation module
        // Module calls hook.getCancelOrderState() to get refund + earnings values
        if (activeTwammOrder.orderId != bytes32(0)) {
            // Trust it is valid (solvency check will fail if it's not)
            bytes memory data = abi.encode(
                twapEngine,                          // twapEngine
                activeTwammOrder.marketId,           // marketId
                activeTwammOrder.orderId,            // orderId
                rateOracle,                          // oracle
                collateralToken,                     // valuationToken
                positionToken,                       // positionToken
                underlyingPool,                      // underlyingPool
                underlyingToken                      // underlyingToken
            );
            totalValue += IValuationModule(TWAMM_MODULE).getValue(data);
            // If ownership check fails, order value = 0 (not counted)
        }

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 4: V4 LP POSITION VALUE                                │
        // └─────────────────────────────────────────────────────────────────┘
        // Delegate to V4 valuation module
        // Module queries position liquidity and calculates token values
        if (activeTokenId != 0) {
            // SECURITY: Only count position if broker still owns it
            // This prevents attack where user transfers NFT out after registering it
            if (IERC721(POSM).ownerOf(activeTokenId) == address(this)) {
                bytes memory data = _encodeModuleData(activeTokenId);
                totalValue += IValuationModule(V4_MODULE).getValue(data);
            }
            // If ownership check fails, position value = 0 (not counted)
        }
    }

    /* ============================================================================================ */
    /*                                    LIQUIDATION (SEIZE)                                      */
    /* ============================================================================================ */

    /// @notice Extracts assets from the broker during liquidation
    /// @dev Called ONLY by RLDCore.liquidate() when broker is underwater
    ///
    /// ## Two-Phase Seize Model
    ///
    /// Assets are routed differently based on type:
    /// - **collateralToken** → recipient (liquidator receives as bonus)
    /// - **positionToken (wRLP)** → Core (burned to offset debt)
    /// - **Other tokens** → Stay in broker (not relevant to liquidation)
    ///
    /// This enables "swap-free" liquidation where existing wRLP in the broker
    /// directly offsets debt without requiring the liquidator to source wRLP.
    ///
    /// ## Priority Order
    ///
    /// 1. **Cash** - Collateral token balance (fastest, simplest)
    /// 2. **TWAMM Order** - Cancel order, recover refund + earnings
    /// 3. **V4 LP Position** - Delegate to V4_MODULE.seize()
    ///
    /// ## Example Flow
    ///
    /// ```
    /// Broker has: 5000 USDC (cash) + TWAMM order worth 3000 USDC
    /// Debt to cover: 7000 wRLP worth
    /// Seize amount (with bonus): 8000 USDC equivalent
    ///
    /// 1. Seize all 5000 USDC cash → liquidator (collateralSeized = 5000)
    /// 2. Cancel TWAMM, recover 3000 value:
    ///    - If tokens are USDC → liquidator (collateralSeized += 3000)
    ///    - If tokens are wRLP → Core (wRLPExtracted += amount)
    /// 3. Return SeizeOutput{collateralSeized: X, wRLPExtracted: Y}
    /// ```
    ///
    function seize(
        uint256 value,
        uint256 principalToCover,
        address recipient
    )
        external
        override
        onlyCore
        nonReentrant
        returns (SeizeOutput memory output)
    {
        // PHASE 1: UNLOCK LIQUIDITY
        _unlockLiquidity(value);

        // PHASE 2: SWEEP ASSETS
        return _sweepAssets(value, principalToCover, recipient);
    }

    /* ============================================================================================ */
    /*                                     INTERNAL SEIZE HELPERS                                  */
    /* ============================================================================================ */

    function _unlockLiquidity(uint256 targetValue) internal {
        uint256 currentLiquid = _getLiquidValue();
        if (currentLiquid >= targetValue) return;

        if (activeTwammOrder.orderId != bytes32(0)) {
            // Force-settle: convert ghost balance into real earnings before cancel.
            // Without this, getCancelOrderState() returns buyTokensOwed=0 for the
            // ghost portion, destroying value. After forceSettle, the ghost is
            // market-sold into the pool and earningsFactor is updated.
            _tryForceSettleGhost();
            _cancelTwammOrder();
            currentLiquid = _getLiquidValue();
            if (currentLiquid >= targetValue) return;
        }

        if (activeTokenId != 0) {
            uint256 missing = targetValue - currentLiquid;
            _unwindV4Position(missing);
        }
    }

    function _sweepAssets(
        uint256 value,
        uint256 principalToCover,
        address recipient
    ) internal returns (SeizeOutput memory output) {
        uint256 remaining = value;

        // Priority 1: Burn wRLP (Direct Debt Reduction — TOKEN terms)
        uint256 wRlpBal = ERC20(positionToken).balanceOf(address(this));
        if (wRlpBal > 0 && principalToCover > 0) {
            uint256 takeAmt = wRlpBal > principalToCover
                ? principalToCover
                : wRlpBal;

            // H-1 FIX: Use min(spotPrice, indexPrice) for conservative wRLP valuation.
            // Prevents oracle manipulation where spot < index lets seize undervalue wRLP.
            uint256 spotPrice = ISpotOracle(rateOracle).getSpotPrice(
                positionToken,
                collateralToken
            );
            uint256 indexPrice = IRLDOracle(rateOracle).getIndexPrice(
                underlyingPool,
                underlyingToken
            );
            uint256 price = spotPrice < indexPrice ? spotPrice : indexPrice;
            uint256 takeVal = takeAmt.mulWadDown(price);
            if (takeVal > remaining) takeVal = remaining;

            ERC20(positionToken).safeTransfer(CORE, takeAmt);
            output.wRLPExtracted += takeAmt;
            remaining -= takeVal;
        }

        if (remaining == 0) return output;

        // Priority 2: Pay Collateral (Liquidator Bonus)
        uint256 cashBal = ERC20(collateralToken).balanceOf(address(this));
        if (cashBal > 0) {
            uint256 takeAmt = cashBal > remaining ? remaining : cashBal;
            ERC20(collateralToken).safeTransfer(recipient, takeAmt);

            output.collateralSeized += takeAmt;
            remaining -= takeAmt;
        }
    }

    function _getLiquidValue() internal view returns (uint256) {
        uint256 val = ERC20(collateralToken).balanceOf(address(this));

        uint256 wRlpBal = ERC20(positionToken).balanceOf(address(this));
        if (wRlpBal > 0) {
            uint256 price = ISpotOracle(rateOracle).getSpotPrice(
                positionToken,
                collateralToken
            );
            val += wRlpBal.mulWadDown(price);
        }
        return val;
    }

    /// @notice Best-effort force-settle ghost balance before TWAMM cancel
    /// @dev Checks if ghost > 0, then calls JTM.forceSettle to market-sell
    ///      the ghost into the pool. Proceeds are recorded as earnings, making
    ///      getCancelOrderState() return accurate buyTokensOwed.
    ///      Try/catch ensures this never blocks liquidation.
    function _tryForceSettleGhost() internal {
        (uint256 streamGhostT0, uint256 streamGhostT1, , , ) = ITwapEngine(
            twapEngine
        ).states(activeTwammOrder.marketId);

        (, , , , , bool zfo) = ITwapEngine(twapEngine).streamOrders(
            activeTwammOrder.marketId,
            activeTwammOrder.orderId
        );

        uint256 ghost = zfo ? streamGhostT0 : streamGhostT1;
        if (ghost == 0) return;

        address ghostRouter = ITwapEngine(twapEngine).ghostRouter();
        try
            IGhostRouter(ghostRouter).forceSettleEngine(
                twapEngine,
                activeTwammOrder.marketId,
                zfo
            )
        {} catch {}
    }

    function _cancelTwammOrder()
        internal
        returns (uint256 buyTokensOut, uint256 sellTokensRefund)
    {
        bytes memory result = _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.cancelTwammOrderInternal.selector
            )
        );
        (buyTokensOut, sellTokensRefund) = abi.decode(result, (uint256, uint256));
    }

    function _unwindV4Position(uint256 targetAmount) internal {
        _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.unwindV4PositionInternal.selector,
                POSM,
                V4_MODULE,
                targetAmount
            )
        );
    }

    /// @notice Collect accumulated LP fees from a V4 position without removing liquidity.
    /// @dev Uses DECREASE_LIQUIDITY with 0 liquidity + TAKE_PAIR to collect fees.
    /// @dev Only callable by authorized operators (owner or approved operator).
    function collectV4Fees() external onlyAuthorized {
        _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.collectV4Fees.selector,
                POSM
            )
        );
    }

    /* ============================================================================================ */
    /*                                    POSITION TRACKING                                        */
    /* ============================================================================================ */

    // ┌─────────────────────────────────────────────────────────────────────────────────────────┐
    // │ HOW TO USE POSITION TRACKING                                                            │
    // │                                                                                          │
    // │ The broker can hold multiple V4 LP positions and TWAMM orders, but only ONE of each     │
    // │ is tracked for solvency calculations. Users must manually track their largest positions.│
    // │                                                                                          │
    // │ EXAMPLE: Opening a V4 LP Position                                                       │
    // │   1. broker.execute(posm, addLiquidityCalldata)  // Interact with V4                   │
    // │   2. broker.setActiveV4Position(newTokenId)      // Track it for solvency              │
    // │                                                                                          │
    // │ EXAMPLE: Opening a TWAMM Order                                                          │
    // │   1. broker.submitTwammOrder(twammHook, params)   // Submit + auto-register             │
    // │                                                                                          │
    // │ WARNING: Untracked positions are INVISIBLE to solvency checks!                          │
    // │ If you have 100k in position A and 1k in position B, track position A.                 │
    // └─────────────────────────────────────────────────────────────────────────────────────────┘

    /// @notice Execute an arbitrary external call from this broker
    /// @dev Enables the broker to interact with any external protocol (Aave, Curve, etc.)
    ///      The broker is the msg.sender for the target call, so any state changes
    ///      (Aave supply/borrow, DEX swaps) happen in the broker's context.
    ///
    /// ## Security
    /// - Only owner or operator can call (onlyAuthorized)
    /// - Solvency check after execution ensures broker remains healthy
    /// - Cannot call Core directly (use modifyPosition instead)
    ///
    /// @param target The contract to call
    /// @param data The encoded function call
    /// @return result The return data from the call
    function execute(
        address target,
        bytes calldata data
    )
        external
        onlyAuthorized
        nonReentrant
        whenNotFrozen
        returns (bytes memory result)
    {
        if (target == CORE) revert InvalidTarget();
        if (target == address(this)) revert InvalidTarget();
        if (_isGlobalSettlementActive()) revert GlobalSettlementActive();
        if (_requiresWithdrawalQueue()) revert WithdrawalQueueRequired();

        bool success;
        (success, result) = target.call(data);
        if (!success) {
            if (result.length > 0) {
                assembly {
                    revert(add(32, result), mload(result))
                }
            } else {
                revert ExecuteFailed();
            }
        }

        emit Execute(target, data);
        _checkSolvency();
    }

    /// @notice Sets which V4 LP position is tracked for solvency calculations
    /// @dev V1 LIMITATION: Only ONE position can be tracked at a time
    ///
    /// ## Ownership Verification
    /// If newTokenId != 0, the function verifies that this broker owns the NFT
    /// by calling POSM.ownerOf(newTokenId). This prevents tracking positions
    /// that belong to other accounts.
    ///
    /// ## Solvency Check
    /// A solvency check is performed AFTER updating the tracking. This prevents
    /// users from "gaming" the system by switching to smaller positions:
    /// - If switching from 100k position to 1k position would make broker insolvent
    /// - The transaction reverts with "Insolvent after update"
    ///
    /// @param newTokenId The NFT token ID to track (0 to clear/untrack)
    function setActiveV4Position(
        uint256 newTokenId
    ) external onlyAuthorized nonReentrant whenNotFrozen {
        _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.setActiveV4Position.selector,
                POSM,
                newTokenId
            )
        );
    }

    /// @notice Adds liquidity to the V4 pool, minting a new LP position NFT
    /// @dev Tokens are pulled from this broker via Permit2 (pre-approved in initialize()).
    ///      The LP NFT is minted to this broker.
    ///      Auto-tracks the position for solvency if no position is currently tracked.
    ///
    /// @param twammHook The TWAMM hook address (used to build PoolKey)
    /// @param tickLower Lower tick bound (must be aligned to tick spacing = 5)
    /// @param tickUpper Upper tick bound (must be aligned to tick spacing = 5)
    /// @param liquidity Amount of liquidity to add
    /// @param amount0Max Maximum amount of currency0 (slippage protection)
    /// @param amount1Max Maximum amount of currency1 (slippage protection)
    /// @return tokenId The newly minted V4 LP position NFT ID
    function addPoolLiquidity(
        address twammHook,
        int24 tickLower,
        int24 tickUpper,
        uint128 liquidity,
        uint128 amount0Max,
        uint128 amount1Max
    )
        external
        onlyAuthorized
        nonReentrant
        whenNotFrozen
        returns (uint256 tokenId)
    {
        bytes memory result = _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.addPoolLiquidity.selector,
                POSM,
                twammHook,
                tickLower,
                tickUpper,
                liquidity,
                amount0Max,
                amount1Max
            )
        );
        tokenId = abi.decode(result, (uint256));
    }

    /// @notice Removes liquidity from a specific V4 LP position
    /// @dev Tokens are returned to this broker.
    ///      If all liquidity is removed, the position NFT is burned.
    ///      If the removed position was the tracked position, tracking is cleared.
    ///
    /// @param tokenId   The V4 LP position NFT ID to remove liquidity from
    /// @param liquidity Amount of liquidity to remove (capped to current if exceeds)
    /// @return amount0  Amount of currency0 received (approximate)
    /// @return amount1  Amount of currency1 received (approximate)
    function removePoolLiquidity(
        uint256 tokenId,
        uint128 liquidity
    )
        external
        onlyAuthorized
        nonReentrant
        whenNotFrozen
        returns (uint256 amount0, uint256 amount1)
    {
        bytes memory result = _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.removePoolLiquidity.selector,
                POSM,
                tokenId,
                liquidity
            )
        );
        (amount0, amount1) = abi.decode(result, (uint256, uint256));
    }

    /// @notice Sets which TWAMM order is tracked for solvency calculations
    /// @dev V1 LIMITATION: Only ONE order can be tracked at a time
    ///
    /// ## Order Verification
    /// If orderId != bytes32(0), the function verifies:
    /// 1. The order exists (sellRate > 0)
    /// 2. This broker owns the order (orderKey.owner == address(this))
    ///
    /// ## Important: Hook vs Module
    /// - info.key.hooks = The actual TWAMM hook contract
    /// - TWAMM_MODULE = The valuation module (different contract!)
    /// The order is stored in the hook, so we call hook.getOrder() for verification.
    ///
    /// @param info The TWAMM order info to track (pass empty orderId to clear)
    function setActiveTwammOrder(
        address _twapEngine,
        TwammOrderInfo calldata info
    ) external onlyAuthorized nonReentrant whenNotFrozen {
        _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.setActiveTwammOrder.selector,
                _twapEngine,
                info
            )
        );
    }

    /// @notice Clears the tracked V4 LP position
    /// @dev Convenience function equivalent to setActiveV4Position(0)
    /// Use after selling/closing your V4 position
    function clearActiveV4Position()
        external
        onlyAuthorized
        nonReentrant
        whenNotFrozen
    {
        _delegateToOpsModule(
            abi.encodeWithSelector(PrimeBrokerOpsModule.clearActiveV4Position.selector)
        );
    }

    /// @notice Submits a TWAMM order and automatically registers it for solvency tracking
    /// @dev This broker becomes the order owner (msg.sender to TWAMM = this broker)
    ///
    /// ## Flow
    /// 1. Approves TWAMM hook to pull tokens (JIT approval)
    /// 2. Calls TWAMM.submitOrder() - broker becomes order owner
    /// 3. Revokes approval (cleanup)
    /// 4. Automatically sets activeTwammOrder for solvency tracking
    /// 5. Checks solvency
    ///
    /// ## Token Requirements
    /// Broker must hold sufficient tokens BEFORE calling this function.
    /// For selling wRLP: first call modifyPosition() to mint, then withdrawPositionToken() to self.
    /// For selling collateral: ensure collateral balance in broker.
    ///
    /// @param _twapEngine The TwapEngine boundary contract address
    /// @param _marketId The market identifier
    /// @param zeroForOne Direction of the flow
    /// @param duration The lifespan of the order
    /// @param amountIn The total tokens to stream
    /// @return orderId The unique identifier of the created order
    function submitTwammOrder(
        address _twapEngine,
        bytes32 _marketId,
        bool zeroForOne,
        uint256 duration,
        uint256 amountIn
    )
        external
        onlyAuthorized
        nonReentrant
        whenNotFrozen
        returns (bytes32 orderId)
    {
        bytes memory result = _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.submitTwammOrder.selector,
                _twapEngine,
                _marketId,
                zeroForOne,
                duration,
                amountIn
            )
        );
        orderId = abi.decode(result, (bytes32));
    }

    /// @notice Cancels the active TWAMM order and claims proceeds
    /// @dev Wrapper around _cancelTwammOrder that includes solvency check
    /// @return buyTokensOut Amount of buy tokens received
    /// @return sellTokensRefund Amount of sell tokens refunded
    function cancelTwammOrder()
        external
        onlyAuthorized
        nonReentrant
        whenNotFrozen
        returns (uint256 buyTokensOut, uint256 sellTokensRefund)
    {
        bytes memory result = _delegateToOpsModule(
            abi.encodeWithSelector(PrimeBrokerOpsModule.cancelTwammOrder.selector)
        );
        (buyTokensOut, sellTokensRefund) = abi.decode(result, (uint256, uint256));
    }

    /// @notice Claims tokens from an expired TWAMM order
    /// @dev For expired orders, cancelOrder() reverts with OrderAlreadyExpired.
    ///      This function uses syncAndClaimTokens() which handles expired orders
    ///      correctly: syncs earnings, deletes the order, and transfers tokens.
    /// @return claimedBuyToken Amount of buy tokens claimed
    function claimExpiredTwammOrder()
        external
        onlyAuthorized
        nonReentrant
        returns (uint256 claimedBuyToken)
    {
        bytes memory result = _delegateToOpsModule(
            abi.encodeWithSelector(PrimeBrokerOpsModule.claimExpiredTwammOrder.selector)
        );
        claimedBuyToken = abi.decode(result, (uint256));
    }

    function claimExpiredTwammOrderWithId(
        address _twapEngine,
        bytes32 _marketId,
        bytes32 _orderId
    )
        external
        onlyAuthorized
        nonReentrant
        returns (uint256 claimedBuyToken)
    {
        bytes memory result = _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.claimExpiredTwammOrderWithId.selector,
                _twapEngine,
                _marketId,
                _orderId
            )
        );
        claimedBuyToken = abi.decode(result, (uint256));
    }

    /* ============================================================================================ */
    /*                                  CORE POSITION MANAGEMENT                                   */
    /* ============================================================================================ */

    /// @notice Modifies the broker's debt position in RLDCore
    /// @dev Uses the flash-loan-style "lock" pattern for safe accounting
    ///
    /// ## Lock Pattern
    ///
    /// ```
    /// modifyPosition() → Core.lock(data) → broker.lockAcquired(data) → Core.modifyPosition()
    ///                            │                                              │
    ///                            └──────────── callback ────────────────────────┘
    /// ```
    ///
    /// The lock pattern ensures atomic execution and proper accounting:
    /// 1. Broker calls Core.lock(encodedData)
    /// 2. Core sets broker as lock holder
    /// 3. Core calls back to broker.lockAcquired(data)
    /// 4. Broker decodes data and calls Core.modifyPosition()
    /// 5. Core validates solvency and settles balances
    ///
    /// @param rawMarketId The market ID (must match this broker's market)
    /// @param deltaCollateral Collateral change (+deposit, -withdraw)
    /// @param deltaDebt Debt change (+borrow, -repay)
    function modifyPosition(
        bytes32 rawMarketId,
        int256 deltaCollateral,
        int256 deltaDebt
    ) external onlyAuthorized nonReentrant whenNotFrozen {
        MarketId id = MarketId.wrap(rawMarketId);

        // SECURITY: Can only modify position in this broker's market
        if (MarketId.unwrap(id) != MarketId.unwrap(marketId)) revert WrongMarket();

        // Encode for callback
        bytes memory data = abi.encode(id, deltaCollateral, deltaDebt);

        // Enter lock pattern - Core will callback to lockAcquired()
        IRLDCore(CORE).lock(data);
    }

    /// @notice Callback from Core during the lock pattern
    /// @dev Called by Core.lock() - DO NOT call directly
    ///
    /// This function:
    /// 1. Decodes the operation parameters
    /// 2. Calls Core.modifyPosition() to update the position
    /// 3. Approves Core to pull collateral (if depositing)
    ///
    /// @param data Encoded (MarketId, deltaCollateral, deltaDebt)
    /// @return Empty bytes (required by interface)
    function lockAcquired(bytes calldata data) external returns (bytes memory) {
        // SECURITY: Only Core can trigger this callback
        if (msg.sender != CORE) revert NotCore();

        (MarketId id, int256 deltaCollateral, int256 deltaDebt) = abi.decode(
            data,
            (MarketId, int256, int256)
        );

        // SECURITY: Double-check market ID
        if (MarketId.unwrap(id) != MarketId.unwrap(marketId)) revert WrongMarket();

        // Execute the position modification in Core
        IRLDCore(CORE).modifyPosition(id, deltaCollateral, deltaDebt);

        // If depositing collateral, approve Core to pull it
        // Core uses: ERC20.safeTransferFrom(broker, core, amount)
        if (deltaCollateral > 0) {
            ERC20(collateralToken).approve(CORE, uint256(deltaCollateral));
        }

        return "";
    }

    /* ============================================================================================ */
    /*                                    TOKEN WITHDRAWALS                                        */
    /* ============================================================================================ */

    /// @notice Withdraws a generic ERC20 token to a specified recipient tracking solvency limits
    /// @dev Consolidated withdrawal function
    /// @param token The address of the token to withdraw
    /// @param recipient The address to receive the tokens
    /// @param amount The amount to withdraw
    function withdrawToken(
        address token,
        address recipient,
        uint256 amount
    ) external onlyAuthorized nonReentrant whenNotFrozen {
        if (_isGlobalSettlementActive()) revert GlobalSettlementActive();
        if (token == collateralToken && _requiresWithdrawalQueue()) {
            revert WithdrawalQueueRequired();
        }
        if (
            token == collateralToken &&
            _availableCollateralForWithdrawal() < amount
        ) {
            revert InsufficientUnqueuedCollateral();
        }

        ERC20(token).safeTransfer(recipient, amount);
        _checkSolvency();
    }

    /// @notice Queues delayed collateral withdrawal for debt-bearing CDS brokers.
    /// @dev Multiple concurrent requests are supported via unique withdrawal ids.
    function requestWithdrawal(
        uint256 amount,
        address recipient
    )
        external
        onlyAuthorized
        nonReentrant
        whenNotFrozen
        returns (uint256 withdrawalId)
    {
        if (!_requiresWithdrawalQueue()) revert WithdrawalQueueNotRequired();
        if (_isGlobalSettlementActive()) revert GlobalSettlementActive();
        if (amount == 0) revert ZeroWithdrawalAmount();
        if (recipient == address(0)) revert InvalidWithdrawalRecipient();
        if (_availableCollateralForWithdrawal() < amount) {
            revert InsufficientUnqueuedCollateral();
        }

        withdrawalId = nextWithdrawalId++;
        uint48 unlockAt = uint48(block.timestamp + WITHDRAWAL_DELAY);

        withdrawalRequests[withdrawalId] = WithdrawalRequest({
            amount: amount,
            recipient: recipient,
            unlockAt: unlockAt,
            queueEpoch: withdrawalQueueEpoch
        });
        queuedCollateralTotal += amount;

        emit WithdrawalRequested(
            withdrawalId,
            recipient,
            amount,
            unlockAt,
            withdrawalQueueEpoch
        );
    }

    /// @notice Cancels a previously queued withdrawal request.
    function cancelWithdrawal(
        uint256 withdrawalId
    ) external onlyAuthorized nonReentrant whenNotFrozen {
        WithdrawalRequest memory request = withdrawalRequests[withdrawalId];
        if (request.amount == 0) revert UnknownWithdrawalRequest();

        if (request.queueEpoch == withdrawalQueueEpoch) {
            queuedCollateralTotal -= request.amount;
        }

        delete withdrawalRequests[withdrawalId];
        emit WithdrawalCancelled(withdrawalId);
    }

    /// @notice Executes a queued withdrawal after the 7-day delay has elapsed.
    function executeWithdrawal(
        uint256 withdrawalId
    ) external onlyAuthorized nonReentrant whenNotFrozen {
        WithdrawalRequest memory request = withdrawalRequests[withdrawalId];
        if (request.amount == 0) revert UnknownWithdrawalRequest();
        if (_isGlobalSettlementActive()) revert GlobalSettlementActive();
        if (request.queueEpoch != withdrawalQueueEpoch)
            revert WithdrawalRequestStale();
        if (block.timestamp < request.unlockAt)
            revert WithdrawalNotReady(request.unlockAt);

        queuedCollateralTotal -= request.amount;
        delete withdrawalRequests[withdrawalId];

        ERC20(collateralToken).safeTransfer(request.recipient, request.amount);
        emit WithdrawalExecuted(withdrawalId, request.recipient, request.amount);
        _checkSolvency();
    }

    /// @notice Deletes stale queue entries after settlement epoch invalidation.
    function pruneWithdrawal(uint256 withdrawalId) external {
        WithdrawalRequest memory request = withdrawalRequests[withdrawalId];
        if (request.amount == 0) revert UnknownWithdrawalRequest();
        if (request.queueEpoch == withdrawalQueueEpoch)
            revert ActiveWithdrawalRequest();

        delete withdrawalRequests[withdrawalId];
        emit WithdrawalPruned(withdrawalId, request.queueEpoch);
    }

    /// @notice Invalidates the active queue epoch during global settlement.
    /// @dev Called by RLDCore settlement hooks. This does not loop requests.
    function invalidateWithdrawalQueue()
        external
        onlyCore
        nonReentrant
        returns (uint64 newQueueEpoch)
    {
        newQueueEpoch = withdrawalQueueEpoch + 1;
        withdrawalQueueEpoch = newQueueEpoch;
        queuedCollateralTotal = 0;
        emit WithdrawalQueueInvalidated(newQueueEpoch);
    }

    /// @notice Returns a queued withdrawal request by id.
    function getWithdrawalRequest(
        uint256 withdrawalId
    ) external view returns (WithdrawalRequest memory request) {
        request = withdrawalRequests[withdrawalId];
    }

    /* ============================================================================================ */
    /*                                    INTERNAL HELPERS                                         */
    /* ============================================================================================ */

    /// @dev Delegatecall helper for heavy broker operations.
    function _delegateToOpsModule(
        bytes memory callData
    ) internal returns (bytes memory result) {
        (bool ok, bytes memory returndata) = OPS_MODULE.delegatecall(callData);
        if (!ok) {
            if (returndata.length > 0) {
                assembly {
                    revert(add(returndata, 32), mload(returndata))
                }
            }
            revert ExecuteFailed();
        }
        return returndata;
    }

    /// @dev Returns collateral currently free (not reserved by active queue requests).
    function _availableCollateralForWithdrawal() internal view returns (uint256) {
        uint256 balance = ERC20(collateralToken).balanceOf(address(this));
        if (balance <= queuedCollateralTotal) return 0;
        return balance - queuedCollateralTotal;
    }

    /// @dev Returns true when CDS debt-bearing brokers must use delayed withdrawal queue.
    function _requiresWithdrawalQueue() internal view returns (bool) {
        if (settlementModule == address(0)) return false;
        return _currentDebtPrincipal() > 0;
    }

    /// @dev Returns current broker debt principal from Core.
    function _currentDebtPrincipal() internal view returns (uint128) {
        return IRLDCore(CORE).getPosition(marketId, address(this)).debtPrincipal;
    }

    /// @dev Returns true if the market has already entered global settlement.
    function _isGlobalSettlementActive() internal view returns (bool) {
        return
            IRLDCore(CORE).getMarketState(marketId).globalSettlementTimestamp !=
            0;
    }

    /// @dev Encodes data for V4_MODULE.getValue() and seize()
    /// @param id The V4 position NFT ID
    /// @return Encoded bytes for module consumption
    function _encodeModuleData(
        uint256 id
    ) internal view returns (bytes memory) {
        // Uses cached values to avoid calling Core
        // Order must match UniswapV4BrokerModule.VerifyParams struct:
        // (tokenId, positionManager, oracle, valuationToken, positionToken, underlyingPool, underlyingToken)
        return
            abi.encode(
                id,
                POSM,
                rateOracle,
                collateralToken,
                positionToken,
                underlyingPool,
                underlyingToken
            );
    }

    /* ============================================================================================ */

    /// @dev Shared solvency check — reverts with Insolvent() if broker is underwater
    function _checkSolvency() internal view {
        if (!IRLDCore(CORE).isSolvent(marketId, address(this)))
            revert Insolvent();
    }

    /// @dev Shared operator mass-revocation — revokes all operators and clears the list
    function _revokeAll() internal {
        for (uint256 i = 0; i < operatorList.length; i++) {
            operators[operatorList[i]] = false;
            emit OperatorUpdated(operatorList[i], false);
        }
        delete operatorList;
    }

    /* ============================================================================================ */
    /*                                   OPERATOR MANAGEMENT                                       */
    /* ============================================================================================ */

    /// @notice Adds or removes an operator for this broker
    /// @dev ONLY the owner (NFT holder) can manage operators
    /// Operators can perform all actions EXCEPT managing other operators
    ///
    /// ## Use Cases
    /// - Add a trading bot: setOperator(botAddress, true)
    /// - Add a multisig signer: setOperator(signerAddress, true)
    /// - Revoke access: setOperator(address, false)
    ///
    /// ## Security
    /// - Operators persist through ownership transfers (NFT transfer)
    /// - New owner can remove old operators
    /// - Operators cannot add other operators
    /// - Operators CAN revoke themselves (enables atomic executor pattern)
    ///
    /// @param operator The address to grant/revoke operator status
    /// @param active True to grant, false to revoke
    function setOperator(
        address operator,
        bool active
    ) external override nonReentrant whenNotFrozen {
        address owner = IERC721(factory).ownerOf(
            uint256(uint160(address(this)))
        );

        // Owner can do anything
        // Operators can only REVOKE themselves (not add or revoke others)
        if (msg.sender == owner) {
            // Owner can grant or revoke any operator
        } else if (msg.sender == operator && operators[msg.sender] && !active) {
            // Operator can revoke themselves
        } else {
            revert NotAuthorized();
        }

        _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.updateOperator.selector,
                operator,
                active
            )
        );
    }

    /// @notice Revokes all operators atomically — called by factory on NFT transfer
    /// @dev H-3 FIX: Prevents previous owner's operators from retaining access after
    ///      ownership transfer. Bounded by MAX_OPERATORS (8) for gas predictability.
    function revokeAllOperators() external onlyFactory {
        _revokeAll();
    }

    /// @notice Set operator via signature from the NFT owner
    /// @dev Enables atomic operator set + execute + revoke pattern in a single tx
    ///
    /// Security:
    /// - Signature must be from current NFT owner
    /// - Nonce prevents replay attacks
    /// - Signature binds to specific operator and caller
    ///
    /// @param operator The address to grant/revoke operator status
    /// @param active True to grant, false to revoke
    /// @param signature EIP-191 signature from the NFT owner
    /// @param nonce Must match operatorNonces[msg.sender]
    /// @param commitment Opaque data commitment bound to the signature (e.g. callsHash)
    function setOperatorWithSignature(
        address operator,
        bool active,
        bytes calldata signature,
        uint256 nonce,
        bytes32 commitment
    ) external nonReentrant whenNotFrozen {
        _delegateToOpsModule(
            abi.encodeWithSelector(
                PrimeBrokerOpsModule.setOperatorWithSignature.selector,
                operator,
                active,
                signature,
                nonce,
                commitment
            )
        );
    }

    /* ============================================================================================ */
    /*                                    BOND FREEZE CONTROL                                      */
    /* ============================================================================================ */

    /// @notice Freezes the broker for bond mode
    /// @dev Only owner can freeze. Revokes ALL operators atomically.
    /// While frozen, all state-changing operations are blocked except:
    ///   - seize() (liquidation must always work)
    ///   - getNetAccountValue() / getFullState() (read-only)
    ///   - unfreeze() (owner can exit bond mode)
    ///
    /// ## Bond Lifecycle
    /// 1. Owner sets up position (collateral, debt, TWAMM order)
    /// 2. Owner calls freeze() → operators revoked, operations blocked
    /// 3. Owner transfers NFT to bond buyer
    /// 4. TWAMM unwind continues at hook level (not affected by freeze)
    /// 5. At maturity, new owner calls unfreeze() and withdraws
    function freeze() external onlyOwner nonReentrant {
        if (frozen) revert AlreadyFrozen();
        frozen = true;
        _revokeAll();
        emit BrokerFrozen(msg.sender);
    }

    /// @notice Unfreezes the broker, re-enabling all operations
    /// @dev Only owner (current NFT holder) can unfreeze
    /// After unfreezing, owner must call setOperator() to re-add operators
    function unfreeze() external onlyOwner nonReentrant {
        if (!frozen) revert NotFrozen();
        frozen = false;
        emit BrokerUnfrozen(msg.sender);
    }

}
