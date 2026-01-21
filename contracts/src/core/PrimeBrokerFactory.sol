// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Clones} from "openzeppelin-v5/contracts/proxy/Clones.sol";
import {PrimeBroker} from "./PrimeBroker.sol";
import {MarketId} from "../interfaces/IRLDCore.sol";
import {ERC721} from "solmate/src/tokens/ERC721.sol";

interface IBondMetadataRenderer {
    function render(uint256 tokenId, address broker) external view returns (string memory);
}

/// @title Prime Broker Factory (The "Bond" NFT)
/// @notice Deploys PrimeBrokers and tracks ownership via ERC721.
/// @dev TokenID = Broker Address. Transferring NFT = Transferring Account.
contract PrimeBrokerFactory is ERC721 {
    using Clones for address;

    address public immutable IMPLEMENTATION;
    MarketId public immutable MARKET_ID;
    address public immutable RENDERER;

    event BrokerCreated(address indexed broker, address indexed owner, uint256 tokenId);

    constructor(
        address implementation,
        MarketId marketId,
        string memory name,
        string memory symbol,
        address renderer
    ) ERC721(name, symbol) {
        IMPLEMENTATION = implementation;
        MARKET_ID = marketId;
        RENDERER = renderer;
    }

    function createBroker() external returns (address broker) {
        broker = IMPLEMENTATION.clone();
        
        // Initialize with Market ID + Factory Address
        PrimeBroker(payable(broker)).initialize(
            MARKET_ID,
            address(this)
        );
        
        // Mint NFT using Broker Address as ID
        uint256 tokenId = uint256(uint160(broker));
        _mint(msg.sender, tokenId);
        
        emit BrokerCreated(broker, msg.sender, tokenId);
    }
    
    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        require(ownerOf(tokenId) != address(0), "NOT_MINTED");

        if (RENDERER == address(0)) return "";
        return IBondMetadataRenderer(RENDERER).render(tokenId, address(uint160(tokenId)));
    }
    
    /// @notice Returns the Broker address associated with a Token ID.
    function account(uint256 tokenId) external pure returns (address) {
        return address(uint160(tokenId));
    }

    /// @notice Returns true if the address is a valid broker minted by this factory.
    function isBroker(address broker) external view returns (bool) {
        return _ownerOf[uint256(uint160(broker))] != address(0);
    }
}
