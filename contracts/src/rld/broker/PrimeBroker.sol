// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPrimeBroker} from "../../shared/interfaces/IPrimeBroker.sol";
import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {IValuationModule} from "../../shared/interfaces/IValuationModule.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {ITWAMM} from "../../twamm/ITWAMM.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ISpotOracle} from "../../shared/interfaces/ISpotOracle.sol";
import {IRLDOracle} from "../../shared/interfaces/IRLDOracle.sol";

// Uniswap V4 Imports
import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {LiquidityAmounts} from "../../shared/libraries/LiquidityAmounts.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {PositionInfo, PositionInfoLibrary} from "v4-periphery/src/libraries/PositionInfoLibrary.sol";

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
    using PositionInfoLibrary for PositionInfo;
    using FixedPointMathLib for uint256;

    /* ============================================================================================ */
    /*                                         IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice The RLDCore singleton contract address
    /// @dev Set in implementation constructor, shared by all clones
    /// Used for: solvency checks, position modifications, lock pattern
    address public immutable CORE;

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
    /// @param _v4Module IValuationModule for Uniswap V4 LP position valuation
    /// @param _twammModule IValuationModule for TWAMM order valuation
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
            // SECURITY: Verify order is still owned by this broker
            // TWAMM orders are tied to orderKey.owner and cannot be transferred
            // But we check defensively in case of edge cases
            if (activeTwammOrder.orderKey.owner == address(this)) {
                bytes memory data = _encodeTwammData(activeTwammOrder);
                totalValue += IValuationModule(TWAMM_MODULE).getValue(data);
            }
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
    function seize(uint256 value, address recipient) 
        external 
        override 
        onlyCore 
        returns (SeizeOutput memory output) 
    {
        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PHASE 1: UNLOCK LIQUIDITY (Sourcing)                            │
        // └─────────────────────────────────────────────────────────────────┘
        // Ensure we have enough liquid assets (Cash + wRLP) to cover 'value'
        _unlockLiquidity(value);
        
        // ┌─────────────────────────────────────────────────────────────────┐
        // │ PHASE 2: SWEEP ASSETS (Settlement)                              │
        // └─────────────────────────────────────────────────────────────────┘
        // Distribute assets to cover debt
        return _sweepAssets(value, recipient);
    }

    /* ============================================================================================ */
    /*                                     INTERNAL SEIZE HELPERS                                  */
    /* ============================================================================================ */

    function _unlockLiquidity(uint256 targetValue) internal {
        // 1. Check current liquid resources (Cash + wRLP)
        uint256 currentLiquid = _getLiquidValue();
        if (currentLiquid >= targetValue) return;

        // 2. Unlock TWAMM (Price Check)
        if (activeTwammOrder.orderId != bytes32(0)) {
            _cancelTwammOrder();
            currentLiquid = _getLiquidValue();
            if (currentLiquid >= targetValue) return;
        }

        // 3. Unlock V4 (Price Check)
        if (activeTokenId != 0) {
            uint256 missing = targetValue - currentLiquid;
            _unwindV4Position(missing);
        }
    }

    function _sweepAssets(uint256 value, address recipient) internal returns (SeizeOutput memory output) {
        uint256 remaining = value;
        
        // Priority 1: Burn wRLP (Direct Debt Reduction)
        uint256 wRlpBal = ERC20(positionToken).balanceOf(address(this));
        if (wRlpBal > 0) {
            uint256 price = ISpotOracle(rateOracle).getSpotPrice(positionToken, collateralToken);
            uint256 wRlpValue = wRlpBal.mulWadDown(price);
            
            uint256 takeVal = wRlpValue > remaining ? remaining : wRlpValue;
            uint256 takeAmt = wRlpBal; // Default all
            
            if (wRlpValue > remaining) {
                takeAmt = takeVal.divWadDown(price);
            }
            
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
        
        // Note: Other tokens (ETH, etc.) remain in broker as they are not accepted for liquidation payment
    }

    function _getLiquidValue() internal view returns (uint256) {
        uint256 val = ERC20(collateralToken).balanceOf(address(this));
        
        uint256 wRlpBal = ERC20(positionToken).balanceOf(address(this));
        if (wRlpBal > 0) {
             uint256 price = ISpotOracle(rateOracle).getSpotPrice(positionToken, collateralToken);
             val += wRlpBal.mulWadDown(price);
        }
        return val;
    }

    function _cancelTwammOrder() internal {
        // Cancel order - tokens return to this contract (msg.sender)
        ITWAMM(address(activeTwammOrder.key.hooks)).cancelOrder(
            activeTwammOrder.key, 
            activeTwammOrder.orderKey
        );
        delete activeTwammOrder;
    }

    function _unwindV4Position(uint256 targetAmount) internal {
        // 1. Value the position
        bytes memory valData = _encodeModuleData(activeTokenId);
        uint256 totalValue = IValuationModule(V4_MODULE).getValue(valData);
        if (totalValue == 0) return;

        // 2. Calculate amount to remove
        uint128 totalLiquidity = IPositionManager(POSM).getPositionLiquidity(activeTokenId);
        uint128 liquidityToRemove = totalLiquidity;
        
        if (totalValue > targetAmount) {
            liquidityToRemove = uint128(uint256(totalLiquidity).mulDivUp(targetAmount, totalValue));
        }
        if (liquidityToRemove == 0) return;

        // 3. Execute Unwind (Decrease -> TakePair)
        bytes memory actions = abi.encodePacked(uint8(0x01), uint8(0x11));
        
        bytes[] memory params = new bytes[](2);
        params[0] = abi.encode(activeTokenId, liquidityToRemove, uint128(0), uint128(0), bytes(""));
        
        (PoolKey memory poolKey, ) = IPositionManager(POSM).getPoolAndPositionInfo(activeTokenId);
        params[1] = abi.encode(poolKey.currency0, poolKey.currency1, address(this)); 
        
        IPositionManager(POSM).modifyLiquidities(abi.encode(actions, params), block.timestamp + 60);

        // 4. Update State
        if (liquidityToRemove == totalLiquidity) {
            activeTokenId = 0;
        }
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

    /// @notice Executes an arbitrary call with Just-In-Time (JIT) approval pattern
    /// @dev The "Swiss Army Knife" of the broker - enables any DeFi interaction with approval safety
    ///
    /// ## Security Model: Flash Approvals
    ///
    /// This function implements a "flash approval" pattern inspired by EIP-1153 transient storage:
    /// 1. **Grant** - Approve target for exact amount needed
    /// 2. **Execute** - Perform the action
    /// 3. **Revoke** - Force approval back to 0
    /// 4. **Verify** - Check solvency
    ///
    /// Approvals exist ONLY during the execution and are ALWAYS revoked, even if unused.
    /// This prevents the Critical "Approval Drain" vulnerability where persistent approvals
    /// could be exploited via transferFrom in a separate transaction.
    ///
    /// ## Example Uses
    ///
    /// ```solidity
    /// // Swap on Uniswap (needs approval)
    /// broker.executeWithApproval(
    ///     router, 
    ///     abi.encodeCall(Router.swap, (...)),
    ///     USDC,  // Token to approve
    ///     1000e6 // Amount
    /// );
    ///
    /// // Claim rewards (no approval needed)
    /// broker.executeWithApproval(
    ///     rewardContract, 
    ///     abi.encodeCall(Rewards.claim, (...)),
    ///     address(0), // No token
    ///     0           // No amount
    /// );
    /// ```
    ///
    /// @param target The contract address to call
    /// @param data The encoded function call data
    /// @param approvalToken The token to approve (address(0) to skip approval)
    /// @param approvalAmount The amount to approve (0 to skip approval)
    function executeWithApproval(
        address target, 
        bytes calldata data,
        address approvalToken,
        uint256 approvalAmount
    ) external onlyAuthorized {
        // SECURITY: Prevent direct calls to token contracts
        // Users must use the approval parameters to interact with tokens
        // This prevents bypassing the JIT approval cleanup mechanism
        require(
            target != collateralToken &&
            target != positionToken &&
            target != underlyingToken,
            "Use approval parameters for token interactions"
        );
        
        // Step 1: Grant JIT Approval (if requested)
        if (approvalToken != address(0) && approvalAmount > 0) {
            ERC20(approvalToken).approve(target, approvalAmount);
        }

        // Step 2: Execute the external call
        (bool success, ) = target.call(data);
        require(success, "Interaction Failed");

        // Step 3: Revoke Approval (force cleanup)
        // This runs even if the target didn't use the full allowance
        if (approvalToken != address(0) && approvalAmount > 0) {
            ERC20(approvalToken).approve(target, 0);
        }

        // Step 4: Emit for off-chain indexing
        emit Execute(target, data);

        // Step 5: CRITICAL SAFETY CHECK
        // If the interaction made the broker insolvent, the entire tx reverts
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Action causes Insolvency");
    }

    /// @notice Executes multiple calls in a single transaction
    /// @dev Standard multicall pattern - allows batching operations atomically
    ///
    /// ## Use Case: Leverage Looping
    ///
    /// ```solidity
    /// bytes[] memory calls = new bytes[](4);
    /// calls[0] = abi.encodeCall(modifyPosition, (marketId, 1000e6, 0));     // Deposit
    /// calls[1] = abi.encodeCall(modifyPosition, (marketId, 0, 500e18));     // Borrow
    /// calls[2] = abi.encodeCall(executeWithApproval, (router, swapData, wRLP, 500e18)); // Swap
    /// calls[3] = abi.encodeCall(modifyPosition, (marketId, 500e6, 0));      // Re-deposit
    /// broker.multicall(calls);
    /// ```
    ///
    /// @param data Array of encoded function calls to this contract
    /// @return results Array of return data from each call
    function multicall(bytes[] calldata data) external returns (bytes[] memory results) {
        results = new bytes[](data.length);
        for (uint256 i = 0; i < data.length; i++) {
            // Use delegatecall to preserve msg.sender and execute in this contract's context
            (bool success, bytes memory result) = address(this).delegatecall(data[i]);
            require(success, "Multicall: call failed");
            results[i] = result;
        }
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
    /*                                    TOKEN WITHDRAWALS                                        */
    /* ============================================================================================ */

    /// @notice Withdraws collateral token to a specified recipient
    /// @dev Bypasses the executeWithApproval blacklist for legitimate withdrawals
    /// @param recipient The address to receive the tokens
    /// @param amount The amount to withdraw
    function withdrawCollateral(address recipient, uint256 amount) external onlyAuthorized {
        ERC20(collateralToken).safeTransfer(recipient, amount);
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Insolvent after withdrawal");
    }

    /// @notice Withdraws position token (wRLP) to a specified recipient
    /// @dev Bypasses the executeWithApproval blacklist for legitimate withdrawals
    /// @param recipient The address to receive the tokens
    /// @param amount The amount to withdraw
    function withdrawPositionToken(address recipient, uint256 amount) external onlyAuthorized {
        ERC20(positionToken).safeTransfer(recipient, amount);
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Insolvent after withdrawal");
    }

    /// @notice Withdraws underlying token to a specified recipient
    /// @dev Bypasses the executeWithApproval blacklist for legitimate withdrawals
    /// @param recipient The address to receive the tokens
    /// @param amount The amount to withdraw
    function withdrawUnderlying(address recipient, uint256 amount) external onlyAuthorized {
        ERC20(underlyingToken).safeTransfer(recipient, amount);
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Insolvent after withdrawal");
    }

    /* ============================================================================================ */
    /*                                    INTERNAL HELPERS                                         */
    /* ============================================================================================ */

    /// @dev Encodes data for V4_MODULE.getValue() and seize()
    /// @param id The V4 position NFT ID
    /// @return Encoded bytes for module consumption
    function _encodeModuleData(uint256 id) internal view returns (bytes memory) {
         // Uses cached values to avoid calling Core
         return abi.encode(id, POSM, rateOracle, collateralToken);
    }
    
    /// @dev Encodes data for TWAMM_MODULE.getValue()
    /// @param info The TWAMM order info
    /// @return Encoded bytes matching TwammBrokerModule.VerifyParams
    function _encodeTwammData(TwammOrderInfo memory info) internal view returns (bytes memory) {
        // NOTE: Order must match TwammBrokerModule.VerifyParams struct:
        // (hook, key, orderKey, oracle, valuationToken)
        return abi.encode(
            address(info.key.hooks),  // hook - The TWAMM hook address
            info.key,                  // key - PoolKey
            info.orderKey,             // orderKey - OrderKey
            rateOracle,                // oracle - for pricing
            collateralToken            // valuationToken - Target currency (e.g. USDC)
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
