// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Clones} from "openzeppelin-v5/contracts/proxy/Clones.sol";
import {PrimeBroker} from "../broker/PrimeBroker.sol";
import {MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {ERC721} from "solmate/src/tokens/ERC721.sol";
import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Interface for rendering Bond NFT metadata.
/// @dev Implementations should return a valid data URI or URL.
interface IBondMetadataRenderer {
    /// @notice Generates the tokenURI for a given Bond NFT.
    /// @param tokenId The token ID (equals broker address cast to uint256)
    /// @param broker The broker contract address
    /// @return A data URI or URL string for the token metadata
    function render(
        uint256 tokenId,
        address broker
    ) external view returns (string memory);
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
contract PrimeBrokerFactory is ERC721, ReentrancyGuard {
    using Clones for address;

    uint8 public constant MAX_DEFAULT_OPERATORS = 8;

    /* ============================================================================================ */
    /*                                          IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice The PrimeBroker implementation contract to clone.
    /// @dev All deployed brokers are minimal proxies pointing to this address.
    address public immutable IMPLEMENTATION;

    /// @notice The market ID that all brokers from this factory will serve.
    /// @dev Each market has its own factory, ensuring market isolation.
    MarketId public immutable MARKET_ID;

    /// @notice Optional metadata renderer for generating Bond NFT artwork (CURRENTLY UNUSED)
    /// @dev Can be address(0). Reserved for future on-chain metadata rendering.
    ///      Currently, tokenURI() returns empty string regardless of this value.
    ///      Metadata is handled off-chain or by frontend dynamic rendering.
    ///      Stored for future extensibility without requiring factory redeployment.
    address public immutable RENDERER;

    /// @notice The RLDCore singleton address
    /// @dev Passed to each broker during initialization so clones can call Core
    address public immutable CORE;

    /// @notice Admin allowed to update default operators for future brokers
    address public immutable DEFAULT_OPERATOR_ADMIN;

    /// @notice Default operators set on every new broker (e.g., BrokerRouter)
    /// @dev Set at factory deployment. Every createBroker() call passes these
    ///      to PrimeBroker.initialize() as pre-approved operators.
    address[] public defaultOperators;

    /* ============================================================================================ */
    /*                                           EVENTS                                             */
    /* ============================================================================================ */

    /// @notice Emitted when a new broker is created.
    /// @param broker The deployed broker contract address
    /// @param owner The initial owner (who called createBroker)
    /// @param tokenId The NFT token ID (equals uint256(uint160(broker)))
    event BrokerCreated(
        address indexed broker,
        address indexed owner,
        uint256 tokenId
    );
    event DefaultOperatorsUpdated(address[] operators);

    modifier onlyDefaultOperatorAdmin() {
        require(msg.sender == DEFAULT_OPERATOR_ADMIN, "Not admin");
        _;
    }

    /* ============================================================================================ */
    /*                                         CONSTRUCTOR                                          */
    /* ============================================================================================ */

    /// @notice Deploys a new PrimeBrokerFactory for a specific market.
    /// @dev Called by RLDMarketFactory during market creation.
    /// @param implementation The PrimeBroker implementation to clone (must be non-zero)
    /// @param marketId The market ID these brokers will serve (must be non-zero)
    /// @param name The ERC721 collection name (e.g., "RLD: aUSDC")
    /// @param symbol The ERC721 collection symbol (e.g., "RLD-aUSDC")
    /// @param renderer Optional metadata renderer (currently unused, can be address(0))
    /// @param core The RLDCore singleton address (passed to brokers during init)
    /// @param defaultOperatorAdmin Admin allowed to update default operators
    /// @param _defaultOperators Addresses to pre-approve on every new broker (e.g., BrokerRouter)
    constructor(
        address implementation,
        MarketId marketId,
        string memory name,
        string memory symbol,
        address renderer,
        address core,
        address defaultOperatorAdmin,
        address[] memory _defaultOperators
    ) ERC721(name, symbol) {
        require(implementation != address(0), "Invalid implementation");
        require(MarketId.unwrap(marketId) != bytes32(0), "Invalid marketId");
        require(core != address(0), "Invalid core");
        require(defaultOperatorAdmin != address(0), "Invalid admin");
        IMPLEMENTATION = implementation;
        MARKET_ID = marketId;
        RENDERER = renderer; // Stored but currently unused (see RENDERER docs)
        CORE = core;
        DEFAULT_OPERATOR_ADMIN = defaultOperatorAdmin;
        defaultOperators = _defaultOperators;
    }

    /// @notice Updates default operators applied to brokers created after this call.
    function setDefaultOperators(
        address[] calldata operators
    ) external onlyDefaultOperatorAdmin {
        require(operators.length <= MAX_DEFAULT_OPERATORS, "Too many operators");
        delete defaultOperators;
        for (uint256 i = 0; i < operators.length; i++) {
            require(operators[i] != address(0), "Invalid operator");
            defaultOperators.push(operators[i]);
        }
        emit DefaultOperatorsUpdated(defaultOperators);
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
    function createBroker(
        bytes32 salt
    ) external nonReentrant returns (address broker) {
        // 1. Deploy minimal proxy clone using CREATE2
        broker = IMPLEMENTATION.cloneDeterministic(salt);

        // 2. Initialize the clone with market context + CORE address
        // The broker needs to know which market it serves, who can verify ownership,
        // and the CORE address for solvency checks and position management
        PrimeBroker(payable(broker)).initialize(
            MARKET_ID,
            address(this),
            CORE,
            defaultOperators
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

    /// @notice Returns the metadata URI for a Bond NFT token
    /// @dev Currently returns empty string - metadata is handled off-chain.
    ///      RENDERER is ignored but stored for future on-chain metadata functionality.
    ///      Frontend/indexers should generate metadata dynamically based on broker state.
    /// @param tokenId The token ID (equals broker address cast to uint256)
    /// @return Empty string (metadata handled off-chain)
    function tokenURI(
        uint256 tokenId
    ) public view override returns (string memory) {
        require(ownerOf(tokenId) != address(0), "NOT_MINTED");
        return ""; // Metadata is handled off-chain or by frontend dynamic rendering
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

    /* ============================================================================================ */
    /*                                    TRANSFER HOOK (H-3 FIX)                                   */
    /* ============================================================================================ */

    /// @notice Override ERC721 transferFrom to revoke all broker operators on ownership transfer
    /// @dev H-3 FIX: Prevents previous owner's operators from retaining access after sale.
    ///      Since solmate's safeTransferFrom calls transferFrom internally, all transfer
    ///      paths (transferFrom + both safeTransferFrom variants) trigger this hook.
    ///      Gas cost: bounded by MAX_OPERATORS (8 SSTOREs + 8 events + 1 SSTORE for delete)
    function transferFrom(
        address from,
        address to,
        uint256 id
    ) public override {
        // Revoke all operators BEFORE transfer (while `from` still owns the NFT)
        address broker = address(uint160(id));
        PrimeBroker(payable(broker)).revokeAllOperators();

        // Execute standard ERC721 transfer
        super.transferFrom(from, to, id);
    }
}
