// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import "../src/core/RLDCore.sol";
import "../src/core/RLDMarketFactory.sol";
import "./RLDCore.t.sol"; // Use Mocks from here

contract RLDFactoryTest is Test {
    RLDCore core;
    RLDMarketFactory factory;
    
    MockERC20 collateral;
    MockERC20 underlying;
    MockOracle oracle;
    MockFunding funding;
    
    address pool = address(0x123);

    function setUp() public {
        core = new RLDCore();
        collateral = new MockERC20();
        underlying = new MockERC20();
        oracle = new MockOracle();
        funding = new MockFunding();
        
        factory = new RLDMarketFactory(
            address(core),
            address(funding),
            address(oracle), // Spot
            address(oracle), // Rate
            address(oracle)  // Default
        );
    }

    function test_DeployMarket_RLP() public {
        (MarketId id, , , , ) = factory.deployMarket(
            pool,
            address(underlying),
            address(collateral),
            IRLDCore.MarketType.RLP,
            address(this), // feeRecipient
            0, 0,
            1.5e18, 
            1.1e18,
            address(0),
            bytes32(uint256(1.05e18))
        );
        
        IRLDCore.MarketConfig memory p = core.getMarketConfig(id);
        
        // Verify Type
        assertTrue(p.marketType == IRLDCore.MarketType.RLP);
        
        // Verify Registry
        assertTrue(MarketId.unwrap(factory.canonicalMarkets(pool, address(funding), IRLDCore.MarketType.RLP)) != bytes32(0));
    }

    function test_DeployMarket_CDS() public {
        (MarketId id, , , , ) = factory.deployMarket(
            pool,
            address(underlying),
            address(collateral),
            IRLDCore.MarketType.CDS,
            address(this),
            0, 0,
            1.5e18, 
            1.1e18,
            address(0),
            bytes32(uint256(1.05e18))
        );
        
        assertTrue(MarketId.unwrap(factory.canonicalMarkets(pool, address(funding), IRLDCore.MarketType.CDS)) != bytes32(0));
    }

    function test_Revert_Duplicate() public {
        factory.deployMarket(
            pool,
            address(underlying),
            address(collateral),
            IRLDCore.MarketType.RLP,
            address(this),
            0, 0,
            1.5e18, 
            1.1e18,
            address(0),
            bytes32(uint256(1.05e18))
        );

        vm.expectRevert("Market Already Exists");
        factory.deployMarket(
            pool,
            address(underlying),
            address(collateral),
            IRLDCore.MarketType.RLP,
            address(this),
            0, 0,
            1.5e18, 
            1.1e18,
            address(0),
            bytes32(uint256(1.05e18))
        );
        
        // Different Type should succeed
        factory.deployMarket(
            pool,
            address(underlying),
            address(collateral),
            IRLDCore.MarketType.CDS,
            address(this),
            0, 0,
            1.5e18, 
            1.1e18,
            address(0),
            bytes32(uint256(1.05e18))
        );
    }
}
