// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {RLDCore} from "../../../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../../../src/rld/core/RLDMarketFactory.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {IPrimeBroker} from "../../../src/shared/interfaces/IPrimeBroker.sol";
import {PositionToken} from "../../../src/rld/tokens/PositionToken.sol";
import {ILiquidationModule} from "../../../src/shared/interfaces/ILiquidationModule.sol";
import {IRLDOracle} from "../../../src/shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../../src/shared/interfaces/ISpotOracle.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

/**
 * @title Liquidation Test Base
 * @notice Base contract with common setup and utilities for liquidation tests
 */
abstract contract LiquidationTestBase is Test {
    // Core contracts
    RLDCore public core;
    address public broker;
    address public positionToken;
    
    // Mock tokens
    MockERC20 public collateralToken;
    MockERC20 public underlyingToken;
    
    // Test actors
    address public borrower = makeAddr("borrower");
    address public liquidator = makeAddr("liquidator");
    address public curator = makeAddr("curator");
    
    // Market ID
    MarketId public marketId;
    
    // Test constants
    uint256 constant INITIAL_COLLATERAL = 10_000e18;
    uint256 constant INITIAL_DEBT = 5_000e18;
    uint256 constant WAD = 1e18;
    
    // Mock contracts
    MockLiquidationModule public liquidationModule;
    MockRLDOracle public rldOracle;
    MockSpotOracle public spotOracle;
    
    function setUp() public virtual {
        // Deploy mock tokens
        collateralToken = new MockERC20("Collateral", "COLL", 18);
        underlyingToken = new MockERC20("Underlying", "UND", 18);
        
        // Deploy mocks
        liquidationModule = new MockLiquidationModule();
        rldOracle = new MockRLDOracle();
        spotOracle = new MockSpotOracle();
        
        // Set default prices
        rldOracle.setIndexPrice(1e18); // 1:1
        spotOracle.setSpotPrice(1e18); // 1:1
        
        // Set default liquidation bonus (5%)
        liquidationModule.setBonus(1.05e18);
        
        // Fund test actors
        collateralToken.mint(borrower, INITIAL_COLLATERAL);
        collateralToken.mint(liquidator, INITIAL_COLLATERAL);
        underlyingToken.mint(borrower, INITIAL_COLLATERAL);
        underlyingToken.mint(liquidator, INITIAL_COLLATERAL);
    }
    
    // Helper: Create a position with specified debt and collateral
    function _createPosition(
        address user,
        uint256 collateral,
        uint256 debt
    ) internal returns (address brokerAddr) {
        vm.startPrank(user);
        
        // Transfer collateral to broker
        collateralToken.transfer(brokerAddr, collateral);
        
        // Mint debt (wRLP)
        // This would normally go through RLDCore.modifyPosition
        // For testing, we'll set up the state directly
        
        vm.stopPrank();
        return brokerAddr;
    }
    
    // Helper: Make position liquidatable by changing price
    function _makeLiquidatable(address brokerAddr, uint256 newPrice) internal {
        rldOracle.setIndexPrice(newPrice);
    }
    
    // Helper: Calculate expected seize amount
    function _calculateExpectedSeize(
        uint256 debtToCover,
        uint256 bonus
    ) internal pure returns (uint256) {
        return (debtToCover * bonus) / WAD;
    }
    
    // Helper: Get position health
    function _getHealth(address brokerAddr) internal view returns (uint256) {
        // Health = collateral / (debt * price)
        // Implementation depends on actual broker interface
        return 0; // Placeholder
    }
}

/**
 * @title Mock Liquidation Module
 * @notice Simple mock for testing liquidation calculations
 */
contract MockLiquidationModule is ILiquidationModule {
    uint256 public bonus = 1.05e18; // 5% default
    
    function setBonus(uint256 _bonus) external {
        bonus = _bonus;
    }
    
    function calculateSeizeAmount(
        uint256 debtToCover,
        uint256 userCollateral,
        uint256 userDebt,
        PriceData calldata priceData,
        IRLDCore.MarketConfig calldata config,
        bytes32 liquidationParams
    ) external view override returns (uint256 bonusCollateral, uint256 seizeAmount) {
        // Simple calculation: seizeAmount = debtToCover * indexPrice * bonus
        uint256 debtValue = (debtToCover * priceData.indexPrice) / 1e18;
        seizeAmount = (debtValue * bonus) / 1e18;
        bonusCollateral = seizeAmount - debtValue;
    }
}

/**
 * @title Mock RLD Oracle
 * @notice Simple mock for index price
 */
contract MockRLDOracle is IRLDOracle {
    uint256 public indexPrice = 1e18;
    
    function setIndexPrice(uint256 _price) external {
        indexPrice = _price;
    }
    
    function getIndexPrice(address pool, address token) external view override returns (uint256) {
        return indexPrice;
    }
}


/**
 * @title Mock Spot Oracle
 * @notice Simple mock for spot price
 */
contract MockSpotOracle is ISpotOracle {
    uint256 public spotPrice = 1e18;
    
    function setSpotPrice(uint256 _price) external {
        spotPrice = _price;
    }
    
    function getSpotPrice(address tokenIn, address tokenOut) external view override returns (uint256) {
        return spotPrice;
    }
}

/**
 * @title Mock Broker
 * @notice Simplified broker for testing liquidation flows
 */
contract MockBroker {
    address public immutable CORE;
    address public collateralToken;
    address public positionToken;
    
    uint256 public collateralBalance;
    uint256 public wRLPBalance;
    
    constructor(address _core, address _collateral, address _position) {
        CORE = _core;
        collateralToken = _collateral;
        positionToken = _position;
    }
    
    function setBalances(uint256 _collateral, uint256 _wRLP) external {
        collateralBalance = _collateral;
        wRLPBalance = _wRLP;
    }
    
    function getNetAccountValue() external view returns (uint256) {
        // Simplified: just return collateral + wRLP value
        return collateralBalance + wRLPBalance;
    }
    
    function seize(uint256 value, address recipient) external returns (IPrimeBroker.SeizeOutput memory output) {
        require(msg.sender == CORE, "Only core");
        
        // Simplified seize logic
        uint256 remaining = value;
        
        // Priority 1: wRLP
        if (wRLPBalance > 0) {
            uint256 take = wRLPBalance > remaining ? remaining : wRLPBalance;
            ERC20(positionToken).transfer(CORE, take);
            output.wRLPExtracted = take;
            wRLPBalance -= take;
            remaining -= take;
        }
        
        // Priority 2: Collateral
        if (remaining > 0 && collateralBalance > 0) {
            uint256 take = collateralBalance > remaining ? remaining : collateralBalance;
            ERC20(collateralToken).transfer(recipient, take);
            output.collateralSeized = take;
            collateralBalance -= take;
        }
    }
}
