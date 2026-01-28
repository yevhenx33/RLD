// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {TransientStorage} from "../../shared/libraries/TransientStorage.sol";

/// @title RLD Storage
/// @author RLD Protocol
/// @notice Abstract contract defining the storage layout for the RLD Core singleton.
/// @dev This contract separates storage from logic to:
///      1. Ensure upgrade safety if proxy patterns are ever needed
///      2. Provide a clear overview of all protocol state
///      3. Enable cleaner code organization
///
/// ## Storage Architecture
/// 
/// The RLD Protocol uses two types of storage:
///
/// ### 1. Permanent Storage (Standard EVM Storage)
///   - `marketAddresses`: Immutable addresses defining market infrastructure
///   - `marketConfigs`: Risk parameters and market configuration
///   - `marketStates`: Dynamic state (normalization factor, timestamps)
///   - `positions`: User debt positions per market
///   - `marketExists`: Explicit existence flags for markets
///
/// ### 2. Transient Storage (EIP-1153)
///   - Used for flash accounting during the `lock()` pattern
///   - Automatically cleared at end of transaction
///   - Tracks: lock holder, touched positions, action types
///
/// ## Security Model
/// 
/// The transient storage enables atomic operations:
///   1. User acquires lock → LOCK_HOLDER set
///   2. User performs multiple operations → Positions touched
///   3. Lock released → All touched positions checked for solvency
///   4. Transaction ends → Transient storage auto-cleared
abstract contract RLDStorage is IRLDCore {
    /* ============================================================================================ */
    /*                                       PERMANENT STORAGE                                      */
    /* ============================================================================================ */

    /// @notice Maps MarketId to the addresses of all market components.
    /// @dev Includes: collateralToken, underlyingToken, oracles, funding model, position token, etc.
    /// @dev These addresses are set once at market creation and cannot be changed.
    mapping(MarketId id => MarketAddresses addresses) public marketAddresses;

    /// @notice Maps MarketId to the risk configuration parameters.
    /// @dev Includes: minColRatio, maintenanceMargin, liquidationCloseFactor, etc.
    /// @dev Some parameters may be updatable by the curator (governance).
    mapping(MarketId id => MarketConfig config) public marketConfigs;

    /// @notice Maps MarketId to the dynamic market state.
    /// @dev State includes:
    ///   - normalizationFactor: Accrued interest multiplier (starts at 1e18)
    ///   - lastUpdateTimestamp: When funding was last applied
    /// @dev Updated lazily on first interaction per block.
    mapping(MarketId id => MarketState state) public marketStates;

    /// @notice Maps (MarketId, User) to their position in that market.
    /// @dev Position only tracks `debtPrincipal` - collateral is delegated to PrimeBroker.
    /// @dev True debt = debtPrincipal * normalizationFactor
    mapping(MarketId id => mapping(address user => Position pos)) public positions;

    /// @notice Explicit existence flag for markets.
    /// @dev Used for O(1) market existence checks instead of relying on address(0) sentinel.
    mapping(MarketId id => bool) public marketExists;

    /* ============================================================================================ */
    /*                                       TRANSIENT STORAGE                                      */
    /* ============================================================================================ */

    /// @notice EIP-1153 slot key for the current lock holder address.
    /// @dev Set when `lock()` is called, cleared when lock is released.
    /// @dev Value: uint256(uint160(lockHolderAddress))
    /// @dev Derivation: keccak256("RLD.LOCK_HOLDER")
    bytes32 internal constant LOCK_HOLDER_KEY = 0x2e8f1d8c19955375494191c062804b4d68202528751351141315848733230891;
    
    /// @notice EIP-1153 slot key for the count of touched positions.
    /// @dev Incremented each time a position is modified during a lock session.
    /// @dev Derivation: keccak256("RLD.TOUCHED_COUNT")
    bytes32 internal constant TOUCHED_COUNT_KEY = 0x6e9f1d8c19955375494191c062804b4d68202528751351141315848733230892;

    /// @notice Base slot for the array of touched (MarketId, Account) pairs.
    /// @dev Storage layout per entry:
    ///   - Slot[2*i]     = MarketId (bytes32)
    ///   - Slot[2*i + 1] = Account address (uint160 stored as uint256)
    /// @dev Derivation: keccak256("RLD.TOUCHED_LIST_BASE")
    bytes32 internal constant TOUCHED_LIST_BASE = 0x8e9f1d8c19955375494191c062804b4d68202528751351141315848733230893;

    /// @notice Salt used for hashing action types per (MarketId, Account).
    /// @dev Action types distinguish between:
    ///   - Type 1: Maintenance operations (uses maintenanceMargin)
    ///   - Type 2: New minting (uses minColRatio, more strict)
    /// @dev Derivation: keccak256("RLD.ACTION_SALT")
    bytes32 internal constant ACTION_SALT = 0x1e9f1d8c19955375494191c062804b4d68202528751351141315848733230899;

    /* ============================================================================================ */
    /*                                   TRANSIENT STORAGE HELPERS                                  */
    /* ============================================================================================ */

    /// @notice Adds a (MarketId, Account) pair to the touched list.
    /// @dev Called when a position is modified during a lock session.
    /// @dev Does not check for duplicates - solvency is rechecked per entry (harmless redundancy).
    /// @dev Gas optimization: Avoiding duplicate checking saves ~2000 gas per call.
    /// @param marketId The market where the position was touched
    /// @param account The user whose position was touched
    function _addTouchedPosition(MarketId marketId, address account) internal {
        uint256 count = TransientStorage.tload(TOUCHED_COUNT_KEY);
        
        // Calculate slot positions for this entry
        bytes32 slotMarket = bytes32(uint256(TOUCHED_LIST_BASE) + (count * 2));
        bytes32 slotAccount = bytes32(uint256(TOUCHED_LIST_BASE) + (count * 2) + 1);
        
        // Store the pair
        TransientStorage.tstore(slotMarket, uint256(MarketId.unwrap(marketId)));
        TransientStorage.tstore(slotAccount, uint256(uint160(account)));
        
        // Increment count
        TransientStorage.tstore(TOUCHED_COUNT_KEY, count + 1);
    }

    /// @notice Retrieves the (MarketId, Account) pair at a specific index.
    /// @dev Used during solvency verification to iterate through all touched positions.
    /// @param index The 0-based index into the touched list
    /// @return marketId The market ID at this index
    /// @return account The account address at this index
    function _getTouchedPosition(uint256 index) internal view returns (MarketId marketId, address account) {
        bytes32 slotMarket = bytes32(uint256(TOUCHED_LIST_BASE) + (index * 2));
        bytes32 slotAccount = bytes32(uint256(TOUCHED_LIST_BASE) + (index * 2) + 1);
        
        marketId = MarketId.wrap(bytes32(TransientStorage.tload(slotMarket)));
        account = address(uint160(TransientStorage.tload(slotAccount)));
    }

    /// @notice Checks if a lock is currently active.
    /// @dev A lock is active when LOCK_HOLDER_KEY contains a non-zero value.
    /// @return True if a lock is currently held
    function _isLocked() internal view returns (bool) {
        return TransientStorage.tload(LOCK_HOLDER_KEY) != 0;
    }

    /// @notice Returns the address holding the current lock.
    /// @dev Returns address(0) if no lock is active.
    /// @return The lock holder's address
    function _getLockHolder() internal view returns (address) {
        return address(uint160(TransientStorage.tload(LOCK_HOLDER_KEY)));
    }
}
