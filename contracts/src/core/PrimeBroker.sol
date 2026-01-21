// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPrimeBroker} from "../interfaces/IPrimeBroker.sol";
import {IRLDCore, MarketId} from "../interfaces/IRLDCore.sol";
import {IBrokerModule} from "../interfaces/IBrokerModule.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
}

/// @title Prime Broker V1 (Thin Version)
/// @notice "Smart Margin Account" protecting RLD Protocol.
/// @dev Optimized for cloning and Core-based data fetching.
contract PrimeBroker is IPrimeBroker {
    using SafeTransferLib for ERC20;

    /* ============================================================================================ */
    /*                                          IMMUTABLES                                          */
    /* ============================================================================================ */

    // System Config (Universal)
    address public immutable CORE;
    address public immutable V4_MODULE;
    address public immutable TWAMM_MODULE;
    address public immutable POSM; // Universal V4 Position Manager

    /* ============================================================================================ */
    /*                                            STORAGE                                           */
    /* ============================================================================================ */

    // Market Identity
    // address public owner; // Replaced by NFT Ownership
    address public factory;
    BondMetadata public bondMetadata;
    MarketId public marketId;
    
    // Cached Configuration (Gas Savings for Solvency Checks)
    address public collateralToken;
    address public underlyingToken;
    address public rateOracle;
    address public hook;
    
    // Active Assets (V1 Limit: One of each)
    uint256 public activeTokenId; // V4 Position
    uint256 public activeOrderId; // TWAMM Order
    
    // Init check
    bool private initialized;

    /* ============================================================================================ */
    /*                                          MODIFIERS                                           */
    /* ============================================================================================ */

    modifier onlyCore() {
        require(msg.sender == CORE, "Not Core");
        _;
    }

    modifier onlyOwner() {
        // TokenID is the uint256 representation of the Broker's address
        require(IERC721(factory).ownerOf(uint256(uint160(address(this)))) == msg.sender, "Not Owner");
        _;
    }

    /* ============================================================================================ */
    /*                                         CONSTRUCTOR                                          */
    /* ============================================================================================ */

    constructor(address _core, address _v4Module, address _twammModule, address _posm) {
        CORE = _core;
        V4_MODULE = _v4Module;
        TWAMM_MODULE = _twammModule;
        POSM = _posm;
        TWAMM_MODULE = _twammModule;
        POSM = _posm;
        // FACTORY = _factory; // Removed immutable
        
        // Lock the implementation contract
        initialized = true;
    }

    function initialize(
        MarketId _marketId,
        address _factory
    ) external {
        require(!initialized, "Initialized");
        // owner = _owner; // Managed by NFT
        marketId = _marketId;
        factory = _factory;
        
        // Caching: Fetch tokens once to save gas on every future interaction
        IRLDCore.MarketAddresses memory vars = IRLDCore(CORE).getMarketAddresses(_marketId);
        collateralToken = vars.collateralToken;
        underlyingToken = vars.underlyingToken;
        rateOracle = vars.rateOracle;
        hook = vars.hook;
        
        initialized = true;
    }

    /* ============================================================================================ */
    /*                                       VALUATION LOGIC                                        */
    /* ============================================================================================ */

    function getNetAccountValue() external view override returns (uint256 totalValue) {
        // 1. Cash Balance (Collateral)
        totalValue += ERC20(collateralToken).balanceOf(address(this));

        // 2. TWAMM Value
        if (activeOrderId != 0) {
            bytes memory data = _encodeModuleData(activeOrderId, collateralToken, underlyingToken);
            totalValue += IBrokerModule(TWAMM_MODULE).getValue(data);
        }

        // 3. V4 Value
        if (activeTokenId != 0) {
            bytes memory data = _encodeModuleData(activeTokenId, collateralToken, underlyingToken);
            totalValue += IBrokerModule(V4_MODULE).getValue(data);
        }
    }

    /* ============================================================================================ */
    /*                                      LIQUIDATION LOGIC                                       */
    /* ============================================================================================ */

    function seize(uint256 value, address recipient) external override onlyCore {
        uint256 remaining = value;
        address _collateral = collateralToken; // Gas saving: stack variable

        // 1. Priority: Cash
        uint256 cash = ERC20(_collateral).balanceOf(address(this));
        if (cash > 0) {
            uint256 take = cash >= remaining ? remaining : cash;
            ERC20(_collateral).safeTransfer(recipient, take);
            remaining -= take;
        }
        
        if (remaining == 0) return;

        // 2. Priority: TWAMM
        if (activeOrderId != 0) {
            bytes memory data = _encodeModuleData(activeOrderId, _collateral, underlyingToken);
            uint256 seized = IBrokerModule(TWAMM_MODULE).seize(remaining, recipient, data);
            
            if (seized >= remaining) return;
            remaining -= seized;
        }

        // 3. Priority: V4 LP
        if (activeTokenId != 0) {
            bytes memory data = _encodeModuleData(activeTokenId, _collateral, underlyingToken);
            IBrokerModule(V4_MODULE).seize(remaining, recipient, data);
        }
    }
    
    // Transfer logic placeholder
    function deposit(uint256 tokenId) external onlyOwner {
        require(activeTokenId == 0, "Slot Full");
        // IERC721(POSM).safeTransferFrom(msg.sender, address(this), tokenId);
        activeTokenId = tokenId;
    }

    /// @notice Executes arbitrary calls to external contracts.
    /// @dev Protected by a post-execution solvency check.
    /// @param target The address to call.
    /// @param data The calldata to send.
    function execute(address target, bytes calldata data) external onlyOwner {
        // 1. Interaction
        (bool success, ) = target.call(data);
        require(success, "Interaction Failed");

        emit Execute(target, data);

        // 2. Safety Valve: Solvency Check
        // If the interaction (e.g., selling collateral for worthless tokens) made the broker insolvent, revert.
        require(IRLDCore(CORE).isSolvent(marketId, address(this)), "Action causes Insolvency");
    }
    
    /* ============================================================================================ */
    /*                                        CORE INTERACTION                                      */
    /* ============================================================================================ */

    // Generic execute for Core interaction
    function modifyPosition(bytes32 rawMarketId, int256 deltaCollateral, int256 deltaDebt) external onlyOwner {
        MarketId id = MarketId.wrap(rawMarketId);
        require(MarketId.unwrap(id) == MarketId.unwrap(marketId), "Wrong Market");
        
        // Encode action for callback
        bytes memory data = abi.encode(id, deltaCollateral, deltaDebt);
        
        // Enter Lock
        IRLDCore(CORE).lock(data);
    }
    
    // Callback from Core
    function lockAcquired(bytes calldata data) external returns (bytes memory) {
        require(msg.sender == CORE, "Not Core");
        
        (MarketId id, int256 deltaCollateral, int256 deltaDebt) = abi.decode(data, (MarketId, int256, int256));
        require(MarketId.unwrap(id) == MarketId.unwrap(marketId), "Wrong Market");
        
        // Execute Modification
        IRLDCore(CORE).modifyPosition(id, deltaCollateral, deltaDebt);
        
        // Collateral: Core: `ERC20.safeTransferFrom(msg.sender, address(this), amount)`.
        if (deltaCollateral > 0) {
            ERC20(collateralToken).approve(CORE, uint256(deltaCollateral));
        }
        
        return "";
    }

    /* ============================================================================================ */
    /*                                         INTERNAL HELPERS                                     */
    /* ============================================================================================ */

    function _encodeModuleData(uint256 id, address inputToken, address outputToken) internal view returns (bytes memory) {
         // Optimization: Use cached values to avoid calling Core.
         return abi.encode(id, hook, rateOracle, inputToken, outputToken);
    }

    /* ============================================================================================ */
    /*                                        METADATA LOGIC                                        */
    /* ============================================================================================ */

    function getBondMetadata() external view override returns (BondMetadata memory) {
        return bondMetadata;
    }

    function setBondMetadata(BondMetadata calldata _metadata) external override onlyOwner {
        bondMetadata = _metadata;
    }
}
