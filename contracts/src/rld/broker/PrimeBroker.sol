// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPrimeBroker} from "../../shared/interfaces/IPrimeBroker.sol";
import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {IBrokerModule} from "../../shared/interfaces/IBrokerModule.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {ITWAMM} from "../../twamm/ITWAMM.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ISpotOracle} from "../../shared/interfaces/ISpotOracle.sol";
import {IRLDOracle} from "../../shared/interfaces/IRLDOracle.sol";

/// @dev Minimal ERC721 interface for ownership checks
interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
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
/// 3. **Enables Actions** - Generic `execute()` allows any DeFi interaction with solvency safety
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
/// │  │  execute(target, data) → Any DeFi call + solvency check         │    │
/// │  │  seize(amount, recipient) → Liquidation asset extraction        │    │
/// │  └─────────────────────────────────────────────────────────────────┘    │
/// │                               │                                          │
/// │                               ▼                                          │
/// │                    RLDCore (Solvency Checks)                           │
/// │                               │                                          │
/// │              isSolvent(broker) = value >= debt × ratio                  │
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
///
contract PrimeBroker is IPrimeBroker {
    using SafeTransferLib for ERC20;

    /* ============================================================================================ */
    /*                                         IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice The RLDCore singleton contract address
    /// @dev Set in implementation constructor, shared by all clones
    /// Used for: solvency checks, position modifications, lock pattern
    address public immutable CORE;

    /// @notice The Uniswap V4 LP valuation module
    /// @dev Implements IBrokerModule - getValue() and seize() for V4 positions
    address public immutable V4_MODULE;

    /// @notice The TWAMM order valuation module  
    /// @dev Implements IBrokerModule - getValue() for TWAMM orders
    /// WARNING: This is the VALUATION module, not the TWAMM hook itself
    address public immutable TWAMM_MODULE;

    /// @notice Universal V4 Position Manager (POSM) for NFT ownership checks
    /// @dev The Uniswap V4 contract that mints LP position NFTs
    address public immutable POSM;

    /* ============================================================================================ */
    /*                                      STORAGE VARIABLES                                       */
    /* ============================================================================================ */

    /// @notice The PrimeBrokerFactory that deployed this broker
    /// @dev Used for NFT-based ownership lookup: factory.ownerOf(tokenId)
    address public factory;

    /// @notice Optional metadata describing this broker as a "bond"
    /// @dev Used by UI/renderers to display bond-like information
    /// Not enforced by the contract - purely informational
    BondMetadata public bondMetadata;

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

    /// @notice Addresses authorized to act on behalf of the owner
    /// @dev Operators can do everything EXCEPT modify the operator set
    /// Use case: Keeper bots, automation contracts, multisig signers
    mapping(address => bool) public operators;
    
    /// @dev Prevents re-initialization of clones
    /// Set to true in implementation constructor AND after clone initialization
    bool private initialized;

    /* ============================================================================================ */
    /*                                         MODIFIERS                                           */
    /* ============================================================================================ */

    /// @dev Restricts function to RLDCore only
    /// Used for: seize() during liquidation
    modifier onlyCore() {
        require(msg.sender == CORE, "Not Core");
        _;
    }

    /// @dev Restricts function to the NFT holder only
    /// Used for: setOperator() - managing who can operate the account
    /// 
    /// Ownership is dynamic - the "owner" is whoever currently holds the NFT:
    ///   owner = factory.ownerOf(uint256(uint160(address(this))))
    modifier onlyOwner() {
        require(IERC721(factory).ownerOf(uint256(uint160(address(this)))) == msg.sender, "Not Owner");
        _;
    }

    /// @dev Restricts function to owner OR any approved operator
    /// Used for: execute(), modifyPosition(), setActiveV4Position(), etc.
    /// 
    /// This enables account delegation without transferring ownership:
    /// - Grant trading bot access: setOperator(bot, true)
    /// - Revoke access anytime: setOperator(bot, false)
    modifier onlyAuthorized() {
        address owner = IERC721(factory).ownerOf(uint256(uint160(address(this))));
        require(msg.sender == owner || operators[msg.sender], "Not Authorized");
        _;
    }

    /* ============================================================================================ */
    /*                                        CONSTRUCTOR                                          */
    /* ============================================================================================ */

    /// @notice Deploys the PrimeBroker implementation contract
    /// @dev This is the IMPLEMENTATION that clones will delegate to
    /// The constructor locks the implementation to prevent direct use
    ///
    /// @param _core RLDCore singleton address - the protocol's central contract
    /// @param _v4Module IBrokerModule for Uniswap V4 LP position valuation
    /// @param _twammModule IBrokerModule for TWAMM order valuation
    /// @param _posm Universal V4 Position Manager for NFT ownership checks
    constructor(address _core, address _v4Module, address _twammModule, address _posm) {
        CORE = _core;
        V4_MODULE = _v4Module;
        TWAMM_MODULE = _twammModule;
        POSM = _posm;
        
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
    /// 1. Sets the market ID this broker will operate in
    /// 2. Sets the factory address for ownership lookups
    /// 3. Caches market addresses from Core to save gas on future calls
    ///
    /// Gas Optimization: We cache token addresses because getNetAccountValue() is called
    /// on EVERY solvency check. Avoiding Core calls saves ~2600 gas per check.
    ///
    /// @param _marketId The market ID this broker is bound to
    /// @param _factory The PrimeBrokerFactory that deployed this clone
    function initialize(
        MarketId _marketId,
        address _factory
    ) external {
        // SECURITY: Prevent re-initialization
        require(!initialized, "Initialized");
        
        marketId = _marketId;
        factory = _factory;
        
        // OPTIMIZATION: Cache market addresses to save gas on solvency checks
        // Each getNetAccountValue() call would otherwise need Core.getMarketAddresses()
        IRLDCore.MarketAddresses memory vars = IRLDCore(CORE).getMarketAddresses(_marketId);
        collateralToken = vars.collateralToken;
        underlyingToken = vars.underlyingToken;
        positionToken = vars.positionToken;
        underlyingPool = vars.underlyingPool;
        rateOracle = vars.rateOracle;

        initialized = true;
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
    /// 3. **TWAMM Order** - Delegated to TWAMM_MODULE.getValue()
    /// 4. **V4 LP Position** - Delegated to V4_MODULE.getValue()
    ///
    /// ## Value Formula
    /// ```
    /// totalValue = collateralBalance 
    ///            + (wRLPBalance × indexPrice)
    ///            + twammModule.getValue(orderData)
    ///            + v4Module.getValue(positionData)
    /// ```
    ///
    /// ## V1 Limitations
    /// - Only ONE V4 LP position is counted (activeTokenId)
    /// - Only ONE TWAMM order is counted (activeTwammOrder)
    /// - User must track their LARGEST position to avoid false insolvency
    ///
    /// @return totalValue The total asset value in collateral token terms (e.g., USDC)
    function getNetAccountValue() external view override returns (uint256 totalValue) {
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
            uint256 indexPrice = IRLDOracle(rateOracle).getIndexPrice(underlyingPool, collateralToken);
            totalValue += FixedPointMathLib.mulWadDown(wRLPBalance, indexPrice);
        }

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 3: TWAMM ORDER VALUE                                   │
        // └─────────────────────────────────────────────────────────────────┘
        // Delegate to TWAMM valuation module
        // Module calls hook.getCancelOrderState() to get refund + earnings values
        if (activeTwammOrder.orderId != bytes32(0)) {
            bytes memory data = _encodeTwammData(activeTwammOrder);
            totalValue += IBrokerModule(TWAMM_MODULE).getValue(data);
        }

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 4: V4 LP POSITION VALUE                                │
        // └─────────────────────────────────────────────────────────────────┘
        // Delegate to V4 valuation module
        // Module queries position liquidity and calculates token values
        if (activeTokenId != 0) {
            bytes memory data = _encodeModuleData(activeTokenId, collateralToken, underlyingToken);
            totalValue += IBrokerModule(V4_MODULE).getValue(data);
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
    /// @param value The total value to seize (in collateral terms)
    /// @param recipient Where to send seized collateral (typically liquidator)
    /// @return output Breakdown of what was seized (collateral vs wRLP)
    function seize(uint256 value, address recipient) external override onlyCore returns (SeizeOutput memory output) {
        uint256 remaining = value;
        address _collateral = collateralToken; // Gas: stack variable
        address _positionToken = positionToken; // wRLP address

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 1: SEIZE CASH (collateralToken balance)                │
        // └─────────────────────────────────────────────────────────────────┘
        // Fastest path - direct ERC20 transfer
        uint256 cash = ERC20(_collateral).balanceOf(address(this));
        if (cash > 0) {
            uint256 take = cash >= remaining ? remaining : cash;
            ERC20(_collateral).safeTransfer(recipient, take);
            output.collateralSeized += take;
            remaining -= take;
        }
        
        if (remaining == 0) return output;

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 2: SEIZE TWAMM ORDER                                   │
        // └─────────────────────────────────────────────────────────────────┘
        // Cancel the order to recover tokens, then route appropriately
        if (activeTwammOrder.orderId != bytes32(0)) {
            // Step A: Cancel the order - returns unsold tokens + earned tokens
            (uint256 buyTokensOwed, uint256 sellTokensRefund) = ITWAMM(TWAMM_MODULE).cancelOrder(activeTwammOrder.key, activeTwammOrder.orderKey);
            
            // Step B: Identify which tokens are sell/buy
            // zeroForOne = true means selling token0 for token1
            address sellToken = activeTwammOrder.orderKey.zeroForOne 
                ? Currency.unwrap(activeTwammOrder.key.currency0) 
                : Currency.unwrap(activeTwammOrder.key.currency1);
            address buyToken = activeTwammOrder.orderKey.zeroForOne 
                ? Currency.unwrap(activeTwammOrder.key.currency1) 
                : Currency.unwrap(activeTwammOrder.key.currency0);
                
            // Step C: Clear tracking state
            delete activeTwammOrder;
            
            // Step D: Process sellTokensRefund (unsold tokens returned)
            if (sellTokensRefund > 0) {
                uint256 price = _getOraclePrice(sellToken, underlyingToken);
                uint256 val = FixedPointMathLib.mulWadDown(sellTokensRefund, price);
                
                uint256 takeAmt = sellTokensRefund;
                uint256 takeVal = val;
                
                // Cap at remaining value needed
                if (val > remaining) {
                    takeAmt = FixedPointMathLib.mulDivUp(sellTokensRefund, remaining, val);
                    takeVal = remaining;
                }
                
                // Route based on token type:
                // - collateralToken → liquidator (bonus)
                // - positionToken (wRLP) → Core (debt offset)
                // - other → stay in broker
                if (sellToken == _collateral) {
                    ERC20(sellToken).safeTransfer(recipient, takeAmt);
                    output.collateralSeized += takeAmt;
                } else if (sellToken == _positionToken) {
                    // wRLP goes to Core (msg.sender) for burning
                    ERC20(sellToken).safeTransfer(msg.sender, takeAmt);
                    output.wRLPExtracted += takeAmt;
                }
                // else: other tokens stay in broker (not transferred)
                
                remaining -= takeVal;
            }
            
            if (remaining == 0) return output;
            
            // Step E: Process buyTokensOwed (earned tokens from TWAMM execution)
            if (buyTokensOwed > 0) {
                uint256 price = _getOraclePrice(buyToken, underlyingToken);
                uint256 val = FixedPointMathLib.mulWadDown(buyTokensOwed, price);
                
                uint256 takeAmt = buyTokensOwed;
                uint256 takeVal = val;
                
                if (val > remaining) {
                    takeAmt = FixedPointMathLib.mulDivUp(buyTokensOwed, remaining, val);
                    takeVal = remaining;
                }
                
                // Same routing logic as sellTokensRefund
                if (buyToken == _collateral) {
                    ERC20(buyToken).safeTransfer(recipient, takeAmt);
                    output.collateralSeized += takeAmt;
                } else if (buyToken == _positionToken) {
                    ERC20(buyToken).safeTransfer(msg.sender, takeAmt);
                    output.wRLPExtracted += takeAmt;
                }
                
                remaining -= takeVal;
            }
        }
        
        if (remaining == 0) return output;

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PRIORITY 3: SEIZE V4 LP POSITION                                │
        // └─────────────────────────────────────────────────────────────────┘
        // Delegate to V4 module for complex LP position handling
        // TODO: Update V4 module to return SeizeOutput for proper wRLP routing
        if (activeTokenId != 0) {
            bytes memory data = _encodeModuleData(activeTokenId, _collateral, underlyingToken);
            IBrokerModule(V4_MODULE).seize(remaining, recipient, data);
        }
        
        return output;
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
    // │   1. broker.execute(collateral, approveCalldata) // Approve TWAMM hook                 │
    // │   2. broker.execute(twammHook, submitOrder...)   // Submit the order                   │
    // │   3. broker.setActiveTwammOrder(orderInfo)       // Track it for solvency              │
    // │                                                                                          │
    // │ WARNING: Untracked positions are INVISIBLE to solvency checks!                          │
    // │ If you have 100k in position A and 1k in position B, track position A.                 │
    // └─────────────────────────────────────────────────────────────────────────────────────────┘

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
    function setActiveV4Position(uint256 newTokenId) external onlyAuthorized {
        if (newTokenId != 0) {
            // SECURITY: Verify this broker actually owns the position
            require(IERC721(POSM).ownerOf(newTokenId) == address(this), "Not position owner");
        }
        
        activeTokenId = newTokenId;
        
        // SECURITY: Prevent gaming by switching to smaller positions
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Insolvent after update");
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
    function setActiveTwammOrder(TwammOrderInfo calldata info) external onlyAuthorized {
        if (info.orderId != bytes32(0)) {
            // The TWAMM hook is at info.key.hooks (NOT TWAMM_MODULE)
            address twammHook = address(info.key.hooks);
            ITWAMM.Order memory order = ITWAMM(twammHook).getOrder(info.key, info.orderKey);
            
            // SECURITY: Verify order exists and is owned by this broker
            require(order.sellRate > 0, "Order not found or empty");
            require(info.orderKey.owner == address(this), "Not order owner");
        }
        
        activeTwammOrder = info;
        
        // SECURITY: Prevent gaming by switching to smaller orders
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Insolvent after update");
    }

    /// @notice Clears the tracked V4 LP position
    /// @dev Convenience function equivalent to setActiveV4Position(0)
    /// Use after selling/closing your V4 position
    function clearActiveV4Position() external onlyAuthorized {
        activeTokenId = 0;
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Insolvent after clear");
    }

    /// @notice Clears the tracked TWAMM order
    /// @dev Convenience function for after an order is fully executed or cancelled
    function clearActiveTwammOrder() external onlyAuthorized {
        delete activeTwammOrder;
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Insolvent after clear");
    }

    /* ============================================================================================ */
    /*                                    GENERIC EXECUTION                                        */
    /* ============================================================================================ */

    /// @notice Executes an arbitrary call to an external contract
    /// @dev The "Swiss Army Knife" of the broker - enables any DeFi interaction
    ///
    /// ## How It Works
    ///
    /// 1. **Make the call** - target.call(data)
    /// 2. **Emit event** - For off-chain tracking
    /// 3. **Check solvency** - CRITICAL safety valve
    ///
    /// ## Safety Model
    ///
    /// The solvency check at the end is the broker's main protection:
    /// - If the call drains collateral → revert "Action causes Insolvency"
    /// - If the call swaps collateral for worthless tokens → revert
    /// - If the call increases collateral → allowed
    ///
    /// ## Example Uses
    ///
    /// ```solidity
    /// // Swap on Uniswap
    /// broker.execute(router, abi.encodeCall(Router.swap, (...)));
    ///
    /// // Provide liquidity on V4
    /// broker.execute(posm, abi.encodeCall(POSM.mint, (...)));
    ///
    /// // Submit TWAMM order
    /// broker.execute(twamm, abi.encodeCall(TWAMM.submitOrder, (...)));
    ///
    /// // Claim rewards
    /// broker.execute(rewardContract, abi.encodeCall(Rewards.claim, (...)));
    /// ```
    ///
    /// @param target The contract address to call
    /// @param data The encoded function call data
    function execute(address target, bytes calldata data) external onlyAuthorized {
        // Step 1: Make the external call
        (bool success, ) = target.call(data);
        require(success, "Interaction Failed");

        // Step 2: Emit for off-chain indexing
        emit Execute(target, data);

        // Step 3: CRITICAL SAFETY CHECK
        // If the interaction made the broker insolvent, the entire tx reverts
        // This is the main protection against malicious/bad trades
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Action causes Insolvency");
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
    function modifyPosition(bytes32 rawMarketId, int256 deltaCollateral, int256 deltaDebt) external onlyAuthorized {
        MarketId id = MarketId.wrap(rawMarketId);
        
        // SECURITY: Can only modify position in this broker's market
        require(MarketId.unwrap(id) == MarketId.unwrap(marketId), "Wrong Market");
        
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
        require(msg.sender == CORE, "Not Core");
        
        (MarketId id, int256 deltaCollateral, int256 deltaDebt) = abi.decode(data, (MarketId, int256, int256));
        
        // SECURITY: Double-check market ID
        require(MarketId.unwrap(id) == MarketId.unwrap(marketId), "Wrong Market");
        
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
    /*                                    INTERNAL HELPERS                                         */
    /* ============================================================================================ */

    /// @dev Encodes data for V4_MODULE.getValue() and seize()
    /// @param id The V4 position NFT ID
    /// @param inputToken The input token for valuation
    /// @param outputToken The output token for valuation
    /// @return Encoded bytes for module consumption
    function _encodeModuleData(uint256 id, address inputToken, address outputToken) internal view returns (bytes memory) {
         // Uses cached values to avoid calling Core
         return abi.encode(id, TWAMM_MODULE, rateOracle, inputToken, outputToken);
    }
    
    /// @dev Encodes data for TWAMM_MODULE.getValue()
    /// @param info The TWAMM order info
    /// @return Encoded bytes matching TwammBrokerModule.VerifyParams
    function _encodeTwammData(TwammOrderInfo memory info) internal view returns (bytes memory) {
        return abi.encode(
            TWAMM_MODULE,
            info.key,
            info.orderKey,
            rateOracle, 
            collateralToken,
            underlyingToken
        );
    }
    
    /// @dev Gets spot price from oracle
    /// @param quote The quote token address
    /// @param base The base token address  
    /// @return Price in WAD format (1e18 = 1.0)
    function _getOraclePrice(address quote, address base) internal view returns (uint256) {
        return ISpotOracle(rateOracle).getSpotPrice(quote, base);
    }

    /* ============================================================================================ */
    /*                                  BOND METADATA (OPTIONAL)                                   */
    /* ============================================================================================ */

    /// @notice Gets the optional bond metadata for this broker
    /// @dev Used by UI/renderers to display bond-like information
    /// This is purely informational - not enforced by the contract
    /// @return The BondMetadata struct
    function getBondMetadata() external view override returns (BondMetadata memory) {
        return bondMetadata;
    }

    /// @notice Sets the bond metadata for this broker
    /// @dev Optional - used for UI display purposes
    ///
    /// Example metadata:
    /// - rate: 5% (5e16)
    /// - maturityDate: 1 year from now
    /// - principal: 1000 ETH
    /// - bondType: YIELD or HEDGE
    ///
    /// @param _metadata The new bond metadata
    function setBondMetadata(BondMetadata calldata _metadata) external override onlyAuthorized {
        bondMetadata = _metadata;
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
    ///
    /// @param operator The address to grant/revoke operator status
    /// @param active True to grant, false to revoke
    function setOperator(address operator, bool active) external override onlyOwner {
        operators[operator] = active;
        emit OperatorUpdated(operator, active);
    }
}
