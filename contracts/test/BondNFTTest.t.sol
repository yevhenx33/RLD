// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";
import {BondMetadataRenderer} from "../src/utils/BondMetadataRenderer.sol";
import {IPrimeBroker} from "../src/shared/interfaces/IPrimeBroker.sol";
import {RLDCore, MarketId} from "../src/rld/core/RLDCore.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {LibString} from "solmate/src/utils/LibString.sol";

contract BondNFTTest is Test {
    PrimeBrokerFactory factory;
    BondMetadataRenderer renderer;
    PrimeBroker implementation;
    RLDCore core;
    
    address user = address(0xCAFE);
    address otherUser = address(0xBABE);
    
    function setUp() public {
        core = new RLDCore();
        implementation = new PrimeBroker(address(core), address(0), address(0), address(0));
        renderer = new BondMetadataRenderer();
        
        factory = new PrimeBrokerFactory(
            address(implementation),
            MarketId.wrap(bytes32(uint256(1))), // Mock MarketID
            "RLD Bond",
            "RLDBOND",
            address(renderer)
        );
    }
    
    function test_MintAndMetadata() public {
        vm.prank(user);
        address brokerAddr = factory.createBroker();
        uint256 tokenId = uint256(uint160(brokerAddr));
        
        // Check Ownership
        assertEq(factory.ownerOf(tokenId), user);
        assertEq(factory.balanceOf(user), 1);
        
        // Check Metadata rendering (empty initially)
        string memory uri = factory.tokenURI(tokenId);
        // Should contain base64 header
        // Check prefix manually
        bytes memory uriBytes = bytes(uri);
        bytes memory prefix = "data:application/json;base64,";
        assertTrue(uriBytes.length > prefix.length);
        for(uint i=0; i<prefix.length; i++) {
            assertEq(uriBytes[i], prefix[i]);
        }
        
        // Update Metadata via Broker
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));
        
        vm.startPrank(user);
        IPrimeBroker.BondMetadata memory meta = IPrimeBroker.BondMetadata({
            rate: 500, // 5%
            maturityDate: uint48(block.timestamp + 365 days),
            principal: 1000e18,
            bondType: IPrimeBroker.BondType.YIELD
        });
        broker.setBondMetadata(meta);
        vm.stopPrank();
        
        // Check Metadata storage
        IPrimeBroker.BondMetadata memory stored = broker.getBondMetadata();
        assertEq(stored.rate, 500);
        assertEq(uint256(stored.bondType), uint256(IPrimeBroker.BondType.YIELD));
        
        // Check Renderer Output contains key values
        // We decode base64 or just check lengths/content if possible. 
        // For simplicity, we just ensure it doesn't revert and returns data.
        string memory uriUpdated = factory.tokenURI(tokenId);
        assertTrue(bytes(uriUpdated).length > 100);
        
        // Log URI for manual inspection if needed
        console.log(uriUpdated);
    }
    
    function test_TransferOwnership() public {
        vm.prank(user);
        address brokerAddr = factory.createBroker();
        uint256 tokenId = uint256(uint160(brokerAddr));
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));

        // User can update metadata
        vm.prank(user);
        broker.setBondMetadata(IPrimeBroker.BondMetadata(100, 0, 0, IPrimeBroker.BondType.YIELD));
        
        // Transfer NFT
        vm.prank(user);
        factory.transferFrom(user, otherUser, tokenId);
        
        assertEq(factory.ownerOf(tokenId), otherUser);
        
        // Old owner cannot update
        vm.prank(user);
        vm.expectRevert("Not Owner"); 
        broker.setBondMetadata(IPrimeBroker.BondMetadata(200, 0, 0, IPrimeBroker.BondType.YIELD));
        
        // New owner CAN update
        vm.prank(otherUser);
        broker.setBondMetadata(IPrimeBroker.BondMetadata(200, 0, 0, IPrimeBroker.BondType.YIELD));
        
        assertEq(broker.getBondMetadata().rate, 200);
    }
    
    function test_DebtHedgeMetadata() public {
        vm.prank(user);
        address brokerAddr = factory.createBroker();
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));
        uint256 tokenId = uint256(uint160(brokerAddr));

        vm.prank(user);
        broker.setBondMetadata(IPrimeBroker.BondMetadata({
            rate: 750, // 7.5% Fixed Rate Borrow
            maturityDate: uint48(block.timestamp + 180 days),
            principal: 5000e18,
            bondType: IPrimeBroker.BondType.HEDGE
        }));
        
        string memory uri = factory.tokenURI(tokenId);
        // Verify it runs and produces output
        console.log("Debt Hedge URI length:", bytes(uri).length);
        assertTrue(bytes(uri).length > 0);
    }
}
