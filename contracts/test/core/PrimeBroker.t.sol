// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console2} from "forge-std/Test.sol";
import {PrimeBroker} from "../../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../../src/rld/core/PrimeBrokerFactory.sol";
import {IPrimeBroker} from "../../src/shared/interfaces/IPrimeBroker.sol";
import {IRLDCore, MarketId} from "../../src/shared/interfaces/IRLDCore.sol";
import {IValuationModule} from "../../src/shared/interfaces/IValuationModule.sol";
import {ISpotOracle} from "../../src/shared/interfaces/ISpotOracle.sol";
import {IRLDOracle} from "../../src/shared/interfaces/IRLDOracle.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {ITWAMM} from "../../src/twamm/ITWAMM.sol";

// ============================================================================
// MOCKS
// ============================================================================

/// @dev Mock ERC20 for testing
contract MockERC20 {
    string public name = "Mock Token";
    string public symbol = "MOCK";
    uint8 public decimals = 18;
    
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }
    
    function burn(address from, uint256 amount) external {
        balanceOf[from] -= amount;
    }
    
    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }
    
    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }
    
    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        if (allowance[from][msg.sender] != type(uint256).max) {
            allowance[from][msg.sender] -= amount;
        }
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

/// @dev Mock RLDCore for testing
contract MockRLDCore {
    mapping(bytes32 => IRLDCore.MarketAddresses) public marketAddresses;
    bool public solventResult = true;
    address public lockHolder;
    
    function setMarketAddresses(MarketId id, IRLDCore.MarketAddresses memory addrs) external {
        marketAddresses[MarketId.unwrap(id)] = addrs;
    }
    
    function getMarketAddresses(MarketId id) external view returns (IRLDCore.MarketAddresses memory) {
        return marketAddresses[MarketId.unwrap(id)];
    }
    
    function setSolventResult(bool _solvent) external {
        solventResult = _solvent;
    }
    
    function isSolvent(MarketId, address) external view returns (bool) {
        return solventResult;
    }
    
    function lock(bytes calldata data) external returns (bytes memory) {
        lockHolder = msg.sender;
        // Call back to broker
        bytes memory result = PrimeBroker(msg.sender).lockAcquired(data);
        lockHolder = address(0);
        return result;
    }
    
    function modifyPosition(MarketId, int256, int256) external {
        // Mock implementation - do nothing
    }
}

/// @dev Mock broker module for getValue
contract MockBrokerModule is IValuationModule {
    uint256 public mockValue = 0;
    
    function setMockValue(uint256 _value) external {
        mockValue = _value;
    }
    
    function getValue(bytes calldata) external view override returns (uint256) {
        return mockValue;
    }
}

/// @dev Mock Oracle for price feeds
contract MockOracle is IRLDOracle, ISpotOracle {
    uint256 public price = 1e18; // Default 1:1
    
    function setPrice(uint256 _price) external {
        price = _price;
    }
    
    function getIndexPrice(address, address) external view override returns (uint256) {
        return price;
    }
    
    function getSpotPrice(address, address) external view override returns (uint256) {
        return price;
    }
}

/// @dev Mock ERC721 for Position Manager
contract MockPOSM {
    mapping(uint256 => address) public owners;
    
    function setOwner(uint256 tokenId, address owner) external {
        owners[tokenId] = owner;
    }
    
    function ownerOf(uint256 tokenId) external view returns (address) {
        return owners[tokenId];
    }
}

/// @dev Mock TWAMM Hook for order tracking
contract MockTwammHook {
    mapping(bytes32 => ITWAMM.Order) public orders;
    
    function setOrder(bytes32 orderId, uint256 sellRate) external {
        orders[orderId].sellRate = sellRate;
    }
    
    function getOrder(PoolKey calldata, ITWAMM.OrderKey calldata) external view returns (ITWAMM.Order memory) {
        // Simple mock - return order based on some hash
        return ITWAMM.Order({sellRate: 1e18, earningsFactorLast: 0});
    }
    
    function cancelOrder(PoolKey calldata, ITWAMM.OrderKey calldata) external returns (uint256, uint256) {
        return (0, 0); // No tokens owed/refunded in mock
    }
}

// ============================================================================
// TEST SUITE: PRIME BROKER
// ============================================================================

contract PrimeBrokerTest is Test {
    // Core contracts
    PrimeBroker public implementation;
    PrimeBroker public broker;
    PrimeBrokerFactory public factory;
    MockRLDCore public core;
    
    // Modules
    MockBrokerModule public v4Module;
    MockBrokerModule public twammModule;
    MockPOSM public posm;
    MockTwammHook public twammHook;
    
    // Tokens
    MockERC20 public collateral;
    MockERC20 public underlying;
    MockERC20 public positionToken;
    
    // Oracle
    MockOracle public oracle;
    
    // Users
    address public owner = makeAddr("owner");
    address public operator = makeAddr("operator");
    address public attacker = makeAddr("attacker");
    address public liquidator = makeAddr("liquidator");
    
    // Market
    MarketId public marketId = MarketId.wrap(bytes32(uint256(1)));
    
    function setUp() public {
        // Deploy mocks
        core = new MockRLDCore();
        v4Module = new MockBrokerModule();
        twammModule = new MockBrokerModule();
        posm = new MockPOSM();
        twammHook = new MockTwammHook();
        oracle = new MockOracle();
        
        // Deploy tokens
        collateral = new MockERC20();
        underlying = new MockERC20();
        positionToken = new MockERC20();
        
        // Deploy implementation
        implementation = new PrimeBroker(
            address(core),
            address(v4Module),
            address(twammModule),
            address(posm)
        );
        
        // Deploy factory
        factory = new PrimeBrokerFactory(address(implementation), marketId, "RLD Bond", "BOND", address(0));
        
        // Setup market addresses in core
        IRLDCore.MarketAddresses memory addrs = IRLDCore.MarketAddresses({
            collateralToken: address(collateral),
            underlyingToken: address(underlying),
            underlyingPool: makeAddr("POOL"),
            rateOracle: address(oracle),
            spotOracle: address(oracle),
            markOracle: address(oracle),
            fundingModel: address(0),
            curator: address(0),
            liquidationModule: address(0),
            positionToken: address(positionToken)
        });
        core.setMarketAddresses(marketId, addrs);
        
        // Create broker for owner
        vm.prank(owner);
        broker = PrimeBroker(factory.createBroker(bytes32(0)));
        
        // Fund broker with collateral
        collateral.mint(address(broker), 10000e18);
    }

    // ========================================================================
    // INITIALIZATION TESTS
    // ========================================================================
    
    function test_InitializeSetsMarketId() public view {
        assertEq(MarketId.unwrap(broker.marketId()), MarketId.unwrap(marketId));
    }
    
    function test_InitializeSetsFactory() public view {
        assertEq(broker.factory(), address(factory));
    }
    
    function test_InitializeCachesTokens() public view {
        assertEq(broker.collateralToken(), address(collateral));
        assertEq(broker.underlyingToken(), address(underlying));
        assertEq(broker.positionToken(), address(positionToken));
    }
    
    function test_InitializeCachesOracle() public view {
        assertEq(broker.rateOracle(), address(oracle));
    }
    
    function test_Revert_InitializeTwice() public {
        vm.expectRevert("Initialized");
        broker.initialize(marketId, address(factory));
    }
    
    function test_ImplementationIsLocked() public view {
        // Implementation should be locked (initialized = true in constructor)
        // Can't directly test private variable, but can verify behavior
        assertEq(implementation.factory(), address(0)); // Never initialized
    }

    // ========================================================================
    // ACCESS CONTROL TESTS
    // ========================================================================
    
    function test_OnlyOwner_SetOperator() public {
        vm.prank(owner);
        broker.setOperator(operator, true);
        assertTrue(broker.operators(operator));
    }
    
    function test_Revert_NotOwner_SetOperator() public {
        vm.prank(attacker);
        vm.expectRevert("Not Owner");
        broker.setOperator(operator, true);
    }
    
    function test_Revert_OperatorCannotSetOperator() public {
        // First, owner sets operator
        vm.prank(owner);
        broker.setOperator(operator, true);
        
        // Operator cannot add another operator
        vm.prank(operator);
        vm.expectRevert("Not Owner");
        broker.setOperator(attacker, true);
    }
    
    function test_OwnerCanExecute() public {
        // No-op call to collateral (checking it doesn't revert)
        vm.prank(owner);
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }
    
    function test_OperatorCanExecute() public {
        vm.prank(owner);
        broker.setOperator(operator, true);
        
        vm.prank(operator);
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }
    
    function test_Revert_AttackerCannotExecute() public {
        vm.prank(attacker);
        vm.expectRevert("Not Authorized");
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }
    
    function test_Revert_AttackerCannotSeize() public {
        vm.prank(attacker);
        vm.expectRevert("Not Core");
        broker.seize(100e18, attacker);
    }
    
    function test_CoreCanSeize() public {
        vm.prank(address(core));
        IPrimeBroker.SeizeOutput memory output = broker.seize(100e18, liquidator);
        assertEq(output.collateralSeized, 100e18);
    }

    // ========================================================================
    // getNetAccountValue TESTS
    // ========================================================================
    
    function test_GetNetAccountValue_CashOnly() public view {
        // Broker has 10000e18 collateral from setUp
        uint256 value = broker.getNetAccountValue();
        assertEq(value, 10000e18);
    }
    
    function test_GetNetAccountValue_WithWRLP() public {
        // Give broker some wRLP tokens
        positionToken.mint(address(broker), 100e18);
        
        // Set oracle price: 1 wRLP = 2 collateral
        oracle.setPrice(2e18);
        
        // Value = 10000 collateral + (100 wRLP * 2) = 10200
        uint256 value = broker.getNetAccountValue();
        assertEq(value, 10200e18);
    }
    
    function test_GetNetAccountValue_WithTwammValue() public {
        // Set mock TWAMM value
        twammModule.setMockValue(500e18);
        
        // Create a fake TWAMM order info
        PoolKey memory poolKey;
        poolKey.hooks = IHooks(address(twammHook));
        
        ITWAMM.OrderKey memory orderKey;
        orderKey.owner = address(broker);
        
        IPrimeBroker.TwammOrderInfo memory orderInfo = IPrimeBroker.TwammOrderInfo({
            key: poolKey,
            orderKey: orderKey,
            orderId: bytes32(uint256(1))
        });
        
        // Set order via prank as owner
        vm.prank(owner);
        broker.setActiveTwammOrder(orderInfo);
        
        // Value = 10000 cash + 500 TWAMM = 10500
        uint256 value = broker.getNetAccountValue();
        assertEq(value, 10500e18);
    }
    
    function test_GetNetAccountValue_WithV4Position() public {
        // Set mock V4 value
        v4Module.setMockValue(1000e18);
        
        // Create a V4 position
        uint256 tokenId = 123;
        posm.setOwner(tokenId, address(broker));
        
        vm.prank(owner);
        broker.setActiveV4Position(tokenId);
        
        // Value = 10000 cash + 1000 V4 = 11000
        uint256 value = broker.getNetAccountValue();
        assertEq(value, 11000e18);
    }
    
    function test_GetNetAccountValue_AllAssets() public {
        // Cash: 10000
        // wRLP: 100 * 2 = 200
        // TWAMM: 500
        // V4: 1000
        // Total: 11700
        
        positionToken.mint(address(broker), 100e18);
        oracle.setPrice(2e18);
        twammModule.setMockValue(500e18);
        v4Module.setMockValue(1000e18);
        
        // Set TWAMM order
        PoolKey memory poolKey;
        poolKey.hooks = IHooks(address(twammHook));
        ITWAMM.OrderKey memory orderKey;
        orderKey.owner = address(broker);
        IPrimeBroker.TwammOrderInfo memory orderInfo = IPrimeBroker.TwammOrderInfo({
            key: poolKey,
            orderKey: orderKey,
            orderId: bytes32(uint256(1))
        });
        vm.prank(owner);
        broker.setActiveTwammOrder(orderInfo);
        
        // Set V4 position
        uint256 tokenId = 123;
        posm.setOwner(tokenId, address(broker));
        vm.prank(owner);
        broker.setActiveV4Position(tokenId);
        
        uint256 value = broker.getNetAccountValue();
        assertEq(value, 11700e18);
    }

    // ========================================================================
    // SEIZE TESTS (Two-Phase)
    // ========================================================================
    
    function test_Seize_CashOnly() public {
        vm.prank(address(core));
        IPrimeBroker.SeizeOutput memory output = broker.seize(5000e18, liquidator);
        
        assertEq(output.collateralSeized, 5000e18);
        assertEq(output.wRLPExtracted, 0);
        assertEq(collateral.balanceOf(liquidator), 5000e18);
        assertEq(collateral.balanceOf(address(broker)), 5000e18);
    }
    
    function test_Seize_ExactCash() public {
        vm.prank(address(core));
        IPrimeBroker.SeizeOutput memory output = broker.seize(10000e18, liquidator);
        
        assertEq(output.collateralSeized, 10000e18);
        assertEq(collateral.balanceOf(liquidator), 10000e18);
        assertEq(collateral.balanceOf(address(broker)), 0);
    }
    
    function test_Seize_PartialCash() public {
        // Seize more than available cash
        vm.prank(address(core));
        IPrimeBroker.SeizeOutput memory output = broker.seize(15000e18, liquidator);
        
        // Should seize all 10000 cash, remaining 5000 unsatisfied
        assertEq(output.collateralSeized, 10000e18);
        assertEq(collateral.balanceOf(liquidator), 10000e18);
    }
    
    function test_Seize_ZeroValue() public {
        vm.prank(address(core));
        IPrimeBroker.SeizeOutput memory output = broker.seize(0, liquidator);
        
        assertEq(output.collateralSeized, 0);
        assertEq(output.wRLPExtracted, 0);
        assertEq(collateral.balanceOf(liquidator), 0);
    }

    // ========================================================================
    // POSITION TRACKING TESTS
    // ========================================================================
    
    function test_SetActiveV4Position_Valid() public {
        uint256 tokenId = 456;
        posm.setOwner(tokenId, address(broker));
        
        vm.prank(owner);
        broker.setActiveV4Position(tokenId);
        
        assertEq(broker.activeTokenId(), tokenId);
    }
    
    function test_Revert_SetActiveV4Position_NotOwner() public {
        uint256 tokenId = 456;
        posm.setOwner(tokenId, attacker); // Not broker
        
        vm.prank(owner);
        vm.expectRevert("Not position owner");
        broker.setActiveV4Position(tokenId);
    }
    
    function test_SetActiveV4Position_Zero() public {
        // First set a position
        uint256 tokenId = 456;
        posm.setOwner(tokenId, address(broker));
        vm.prank(owner);
        broker.setActiveV4Position(tokenId);
        
        // Then clear it
        vm.prank(owner);
        broker.setActiveV4Position(0);
        assertEq(broker.activeTokenId(), 0);
    }
    
    function test_Revert_SetActiveV4Position_Insolvent() public {
        uint256 tokenId = 456;
        posm.setOwner(tokenId, address(broker));
        
        // Make broker insolvent
        core.setSolventResult(false);
        
        vm.prank(owner);
        vm.expectRevert("Insolvent after update");
        broker.setActiveV4Position(tokenId);
    }
    
    function test_ClearActiveV4Position() public {
        uint256 tokenId = 456;
        posm.setOwner(tokenId, address(broker));
        vm.prank(owner);
        broker.setActiveV4Position(tokenId);
        
        vm.prank(owner);
        broker.clearActiveV4Position();
        assertEq(broker.activeTokenId(), 0);
    }
    
    function test_Revert_ClearActiveV4Position_Insolvent() public {
        uint256 tokenId = 456;
        posm.setOwner(tokenId, address(broker));
        vm.prank(owner);
        broker.setActiveV4Position(tokenId);
        
        // Make insolvent
        core.setSolventResult(false);
        
        vm.prank(owner);
        vm.expectRevert("Insolvent after clear");
        broker.clearActiveV4Position();
    }
    
    function test_SetActiveTwammOrder_Valid() public {
        PoolKey memory poolKey;
        poolKey.hooks = IHooks(address(twammHook));
        
        ITWAMM.OrderKey memory orderKey;
        orderKey.owner = address(broker);
        
        IPrimeBroker.TwammOrderInfo memory orderInfo = IPrimeBroker.TwammOrderInfo({
            key: poolKey,
            orderKey: orderKey,
            orderId: bytes32(uint256(1))
        });
        
        vm.prank(owner);
        broker.setActiveTwammOrder(orderInfo);
        
        (,, bytes32 storedOrderId) = broker.activeTwammOrder();
        assertEq(storedOrderId, bytes32(uint256(1)));
    }
    
    function test_ClearActiveTwammOrder() public {
        // First set an order
        PoolKey memory poolKey;
        poolKey.hooks = IHooks(address(twammHook));
        ITWAMM.OrderKey memory orderKey;
        orderKey.owner = address(broker);
        IPrimeBroker.TwammOrderInfo memory orderInfo = IPrimeBroker.TwammOrderInfo({
            key: poolKey,
            orderKey: orderKey,
            orderId: bytes32(uint256(1))
        });
        vm.prank(owner);
        broker.setActiveTwammOrder(orderInfo);
        
        // Then clear it
        vm.prank(owner);
        broker.clearActiveTwammOrder();
        
        (,, bytes32 storedOrderId) = broker.activeTwammOrder();
        assertEq(storedOrderId, bytes32(0));
    }

    // ========================================================================
    // EXECUTE TESTS
    // ========================================================================
    
    function test_Execute_SimpleCall() public {
        vm.prank(owner);
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
        // Should not revert
    }
    
    function test_Execute_Transfer() public {
        address recipient = makeAddr("recipient");
        
        vm.prank(owner);
        broker.execute(
            address(collateral),
            abi.encodeWithSignature("transfer(address,uint256)", recipient, 100e18)
        );
        
        assertEq(collateral.balanceOf(recipient), 100e18);
        assertEq(collateral.balanceOf(address(broker)), 9900e18);
    }
    
    function test_Revert_Execute_FailedCall() public {
        vm.prank(owner);
        vm.expectRevert("Interaction Failed");
        broker.execute(address(collateral), abi.encodeWithSignature("nonexistent()"));
    }
    
    function test_Revert_Execute_CausesInsolvency() public {
        // Make broker insolvent after any action
        core.setSolventResult(false);
        
        vm.prank(owner);
        vm.expectRevert("Action causes Insolvency");
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }
    
    function test_Execute_EmitsEvent() public {
        vm.prank(owner);
        vm.expectEmit(true, false, false, true);
        emit IPrimeBroker.Execute(address(collateral), abi.encodeWithSignature("symbol()"));
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }

    // ========================================================================
    // MODIFY POSITION TESTS
    // ========================================================================
    
    function test_ModifyPosition_CallsCoreLock() public {
        vm.prank(owner);
        broker.modifyPosition(MarketId.unwrap(marketId), 100e18, 50e18);
        // Should not revert - Core's lock was called and callback processed
    }
    
    function test_Revert_ModifyPosition_WrongMarket() public {
        MarketId wrongMarket = MarketId.wrap(bytes32(uint256(999)));
        
        vm.prank(owner);
        vm.expectRevert("Wrong Market");
        broker.modifyPosition(MarketId.unwrap(wrongMarket), 100e18, 50e18);
    }
    
    function test_ModifyPosition_ApprovesCollateral() public {
        vm.prank(owner);
        broker.modifyPosition(MarketId.unwrap(marketId), 100e18, 0);
        
        // Check that broker approved Core for collateral
        // Note: In the callback, approval happens for positive deltaCollateral
        assertEq(collateral.allowance(address(broker), address(core)), 100e18);
    }

    // ========================================================================
    // LOCK ACQUIRED TESTS
    // ========================================================================
    
    function test_Revert_LockAcquired_NotCore() public {
        bytes memory data = abi.encode(marketId, int256(0), int256(0));
        
        vm.prank(attacker);
        vm.expectRevert("Not Core");
        broker.lockAcquired(data);
    }
    
    function test_Revert_LockAcquired_WrongMarket() public {
        MarketId wrongMarket = MarketId.wrap(bytes32(uint256(999)));
        bytes memory data = abi.encode(wrongMarket, int256(0), int256(0));
        
        // Call from core directly (bypassing lock mechanism)
        vm.prank(address(core));
        vm.expectRevert("Wrong Market");
        broker.lockAcquired(data);
    }

    // ========================================================================
    // BOND METADATA TESTS REMOVED (Metadata functionality deleted)

    // ========================================================================
    // OPERATOR MANAGEMENT TESTS
    // ========================================================================
    
    function test_SetOperator_True() public {
        vm.prank(owner);
        broker.setOperator(operator, true);
        assertTrue(broker.operators(operator));
    }
    
    function test_SetOperator_False() public {
        vm.prank(owner);
        broker.setOperator(operator, true);
        assertTrue(broker.operators(operator));
        
        vm.prank(owner);
        broker.setOperator(operator, false);
        assertFalse(broker.operators(operator));
    }
    
    function test_SetOperator_EmitsEvent() public {
        vm.prank(owner);
        vm.expectEmit(true, false, false, true);
        emit IPrimeBroker.OperatorUpdated(operator, true);
        broker.setOperator(operator, true);
    }
    
    function test_MultipleOperators() public {
        address op1 = makeAddr("op1");
        address op2 = makeAddr("op2");
        address op3 = makeAddr("op3");
        
        vm.startPrank(owner);
        broker.setOperator(op1, true);
        broker.setOperator(op2, true);
        broker.setOperator(op3, true);
        vm.stopPrank();
        
        assertTrue(broker.operators(op1));
        assertTrue(broker.operators(op2));
        assertTrue(broker.operators(op3));
        
        // All should be able to execute
        vm.prank(op1);
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
        
        vm.prank(op2);
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
        
        vm.prank(op3);
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }

    // ========================================================================
    // EDGE CASE & SECURITY TESTS
    // ========================================================================
    
    function test_OwnershipTransferWorksViaNFT() public {
        address newOwner = makeAddr("newOwner");
        
        // Transfer NFT
        vm.prank(owner);
        factory.transferFrom(owner, newOwner, uint256(uint160(address(broker))));
        
        // Old owner can't execute
        vm.prank(owner);
        vm.expectRevert("Not Authorized");
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
        
        // New owner can execute
        vm.prank(newOwner);
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }
    
    function test_OldOperatorsStillWorkAfterOwnershipTransfer() public {
        vm.prank(owner);
        broker.setOperator(operator, true);
        
        address newOwner = makeAddr("newOwner");
        vm.prank(owner);
        factory.transferFrom(owner, newOwner, uint256(uint160(address(broker))));
        
        // Operator still works
        vm.prank(operator);
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }
    
    function test_NewOwnerCanRemoveOldOperators() public {
        vm.prank(owner);
        broker.setOperator(operator, true);
        
        address newOwner = makeAddr("newOwner");
        vm.prank(owner);
        factory.transferFrom(owner, newOwner, uint256(uint160(address(broker))));
        
        // New owner removes operator
        vm.prank(newOwner);
        broker.setOperator(operator, false);
        
        // Operator can no longer execute
        vm.prank(operator);
        vm.expectRevert("Not Authorized");
        broker.execute(address(collateral), abi.encodeWithSignature("symbol()"));
    }
    
    function test_Fuzz_SeizeAmount(uint256 amount) public {
        amount = bound(amount, 0, 10000e18);
        
        vm.prank(address(core));
        IPrimeBroker.SeizeOutput memory output = broker.seize(amount, liquidator);
        
        assertEq(output.collateralSeized, amount);
        assertEq(collateral.balanceOf(liquidator), amount);
    }
    
    function test_Fuzz_SetActiveV4Position(uint256 tokenId) public {
        vm.assume(tokenId != 0);
        posm.setOwner(tokenId, address(broker));
        
        vm.prank(owner);
        broker.setActiveV4Position(tokenId);
        assertEq(broker.activeTokenId(), tokenId);
    }
}
