// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Clones} from "openzeppelin-v5/contracts/proxy/Clones.sol";
import {PrimeBroker} from "../broker/PrimeBroker.sol";
import {MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {ERC721} from "solmate/src/tokens/ERC721.sol";

/// @notice Interface for rendering Bond NFT metadata.
/// @dev Implementations should return a valid data URI or URL.
interface IBondMetadataRenderer {
    /// @notice Generates the tokenURI for a given Bond NFT.
    /// @param tokenId The token ID (equals broker address cast to uint256)
    /// @param broker The broker contract address
    /// @return A data URI or URL string for the token metadata
    function render(uint256 tokenId, address broker) external view returns (string memory);
}

/// @title Prime Broker Factory (NFT)
/// @author RLD Protocol
/// @notice Factory contract that deploys PrimeBroker instances and tracks ownership via ERC721 NFTs.
/// @dev This contract serves two core purposes:
///
/// ## 1. Clone Factory (EIP-1167 Minimal Proxies)
/// Deploys lightweight proxy clones of the PrimeBroker implementation contract.
/// Each clone is initialized with the market ID and this factory's address.
///
/// ## 2. Bond NFT (ERC721)
/// Each broker account is represented as an NFT where:
/// - **TokenId = uint256(uint160(brokerAddress))**
/// - Transferring the NFT transfers ownership of the broker account
/// - This enables account trading on secondary markets (OpenSea, etc.)
///
/// ## Architecture
///
/// ```
/// PrimeBrokerFactory (ERC721)
///         │
///         ├── IMPLEMENTATION (immutable) → PrimeBroker template
///         │
///         ├── MARKET_ID (immutable) → Which market these brokers serve
///         │
///         └── createBroker(salt) ─────────────────────────┐
///                                                         │
///              ┌──────────────────────────────────────────┘
///              │
///              ▼
///         Clone Deployment (EIP-1167)
///              │
///              ├── broker.initialize(MARKET_ID, factory)
///              │
///              └── _mint(msg.sender, uint256(uint160(broker)))
/// ```
///
/// ## Security Model
///
/// - **Permissionless**: Anyone can create a broker for any supported market
/// - **Deterministic**: Broker addresses are predictable via CREATE2
/// - **Ownership via NFT**: Account ownership follows ERC721 standard
/// - **Used by BrokerVerifier**: RLDCore checks broker validity via this factory
contract PrimeBrokerFactory is ERC721 {
    using Clones for address;

    /* ============================================================================================ */
    /*                                          IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice The PrimeBroker implementation contract to clone.
    /// @dev All deployed brokers are minimal proxies pointing to this address.
    address public immutable IMPLEMENTATION;

    /// @notice The market ID that all brokers from this factory will serve.
    /// @dev Each market has its own factory, ensuring market isolation.
    MarketId public immutable MARKET_ID;

    /// @notice Optional metadata renderer for generating Bond NFT artwork.
    /// @dev Can be address(0), in which case tokenURI returns empty string.
    address public immutable RENDERER;

    /* ============================================================================================ */
    /*                                           EVENTS                                             */
    /* ============================================================================================ */

    /// @notice Emitted when a new broker is created.
    /// @param broker The deployed broker contract address
    /// @param owner The initial owner (who called createBroker)
    /// @param tokenId The NFT token ID (equals uint256(uint160(broker)))
    event BrokerCreated(address indexed broker, address indexed owner, uint256 tokenId);

    /* ============================================================================================ */
    /*                                         CONSTRUCTOR                                          */
    /* ============================================================================================ */

    /// @notice Deploys a new PrimeBrokerFactory for a specific market.
    /// @dev Called by RLDMarketFactory during market creation.
    /// @param implementation The PrimeBroker implementation to clone (must be non-zero)
    /// @param marketId The market ID these brokers will serve (must be non-zero)
    /// @param name The ERC721 collection name (e.g., "RLD: aUSDC")
    /// @param symbol The ERC721 collection symbol (e.g., "RLD-aUSDC")
    /// @param renderer Optional metadata renderer (can be address(0))
    constructor(
        address implementation,
        MarketId marketId,
        string memory name,
        string memory symbol,
        address renderer
    ) ERC721(name, symbol) {
        require(implementation != address(0), "Invalid implementation");
        require(MarketId.unwrap(marketId) != bytes32(0), "Invalid marketId");
        IMPLEMENTATION = implementation;
        MARKET_ID = marketId;
        RENDERER = renderer; // Can be 0, handled in tokenURI
    }

    /* ============================================================================================ */
    /*                                      BROKER DEPLOYMENT                                       */
    /* ============================================================================================ */

    /// @notice Deploys a new PrimeBroker clone and mints an ownership NFT.
    /// @dev Uses EIP-1167 minimal proxies for gas-efficient deployment.
    ///
    /// ## Deterministic Addressing
    /// The broker address is determined by:
    /// - This factory's address
    /// - The implementation address
    /// - The provided salt
    ///
    /// This allows users to predict their broker address before deployment.
    ///
    /// ## Salt Collision
    /// If the same salt is used twice, the deployment will revert because
    /// a contract already exists at the computed address.
    ///
    /// @param salt Unique salt for deterministic CREATE2 address generation
    /// @return broker The deployed broker contract address
    function createBroker(bytes32 salt) external returns (address broker) {
        // 1. Deploy minimal proxy clone using CREATE2
        broker = IMPLEMENTATION.cloneDeterministic(salt);
        
        // 2. Initialize the clone with market context
        // The broker needs to know which market it serves and who can verify ownership
        PrimeBroker(payable(broker)).initialize(
            MARKET_ID,
            address(this)
        );
        
        // 3. Mint NFT with tokenId = broker address
        // This establishes ownership and enables transfer via ERC721
        uint256 tokenId = uint256(uint160(broker));
        _mint(msg.sender, tokenId);
        
        emit BrokerCreated(broker, msg.sender, tokenId);
    }

    /* ============================================================================================ */
    /*                                       ERC721 METADATA                                        */
    /* ============================================================================================ */

    /// @notice Returns the metadata URI for a Bond NFT.
    /// @dev Delegates to the RENDERER contract if set.
    /// @param tokenId The token ID to get metadata for
    /// @return The metadata URI (data URI or URL), or empty string if no renderer
    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        require(ownerOf(tokenId) != address(0), "NOT_MINTED");

        if (RENDERER == address(0)) return "";
        return IBondMetadataRenderer(RENDERER).render(tokenId, address(uint160(tokenId)));
    }

    /* ============================================================================================ */
    /*                                        VIEW FUNCTIONS                                        */
    /* ============================================================================================ */

    /// @notice Converts a token ID to its corresponding broker address.
    /// @dev Reverts if the token has not been minted (no broker at that address).
    /// @param tokenId The token ID (which equals the broker address cast to uint256)
    /// @return The broker contract address
    function account(uint256 tokenId) external view returns (address) {
        require(_ownerOf[tokenId] != address(0), "NOT_MINTED");
        return address(uint160(tokenId));
    }

    /// @notice Checks if an address is a valid broker deployed by this factory.
    /// @dev Used by BrokerVerifier to validate broker authenticity.
    /// @dev A broker is valid if its corresponding NFT exists (was minted).
    /// @param broker The address to check
    /// @return True if the address is a valid broker from this factory
    function isBroker(address broker) external view returns (bool) {
        return _ownerOf[uint256(uint160(broker))] != address(0);
    }
}
