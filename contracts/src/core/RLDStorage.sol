// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../interfaces/IRLDCore.sol";
import {TransientStorage} from "../libraries/TransientStorage.sol";

/// @title RLD Storage
/// @notice Defines the storage layout and Transient Storage keys for the RLD Singleton.
/// @dev Separated from logic to ensure upgrade safety (if ever needed) and cleaner code.
abstract contract RLDStorage is IRLDCore {
    /* ============================================================================================ */
    /*                                       PERMANENT STORAGE                                      */
    /* ============================================================================================ */

    /// @notice Params Defines the immutable DNA of a market.
    mapping(MarketId => MarketAddresses) public marketAddresses;
    mapping(MarketId => MarketConfig) public marketConfigs;

    /// @notice State Tracks the dynamic variables of a market.
    mapping(MarketId => MarketState) public marketStates;

    /// @notice Positions Tracks user debt and collateral per market.
    mapping(MarketId => mapping(address => Position)) public positions;

    /* ============================================================================================ */
    /*                                       TRANSIENT STORAGE                                      */
    /* ============================================================================================ */

    // Slots for EIP-1153
    // keccak256("RLD.LOCK_HOLDER")
    bytes32 internal constant LOCK_HOLDER_KEY = 0x2e8f1d8c19955375494191c062804b4d68202528751351141315848733230891;
    
    // keccak256("RLD.TOUCHED_COUNT")
    bytes32 internal constant TOUCHED_COUNT_KEY = 0x6e9f1d8c19955375494191c062804b4d68202528751351141315848733230892;

    // Base slot for the array of touched account data.
    // Structure:
    // Slot[2*i]     = MarketId
    // Slot[2*i + 1] = Account
    bytes32 internal constant TOUCHED_LIST_BASE = 0x8e9f1d8c19955375494191c062804b4d68202528751351141315848733230893;

    /// @notice Adds a (MarketId, Account) pair to the touched list.
    /// @dev Does not check for duplicates to save gas (optimistic). Solvency check handles redundant checks harmlessly.
    function _addTouchedPosition(MarketId marketId, address account) internal {
        uint256 count = TransientStorage.tload(TOUCHED_COUNT_KEY);
        
        // Calculate slots
        bytes32 slotMarket = bytes32(uint256(TOUCHED_LIST_BASE) + (count * 2));
        bytes32 slotAccount = bytes32(uint256(TOUCHED_LIST_BASE) + (count * 2) + 1);
        
        TransientStorage.tstore(slotMarket, uint256(MarketId.unwrap(marketId)));
        TransientStorage.tstore(slotAccount, uint256(uint160(account)));
        
        TransientStorage.tstore(TOUCHED_COUNT_KEY, count + 1);
    }

    /// @notice Retrieves the (MarketId, Account) at a specific index.
    function _getTouchedPosition(uint256 index) internal view returns (MarketId marketId, address account) {
        bytes32 slotMarket = bytes32(uint256(TOUCHED_LIST_BASE) + (index * 2));
        bytes32 slotAccount = bytes32(uint256(TOUCHED_LIST_BASE) + (index * 2) + 1);
        
        marketId = MarketId.wrap(bytes32(TransientStorage.tload(slotMarket)));
        account = address(uint160(TransientStorage.tload(slotAccount)));
    }

    /// @notice Returns true if a lock is currently active.
    function _isLocked() internal view returns (bool) {
        return TransientStorage.tload(LOCK_HOLDER_KEY) != 0;
    }

    /// @notice Returns the address holding the current lock.
    function _getLockHolder() internal view returns (address) {
        return address(uint160(TransientStorage.tload(LOCK_HOLDER_KEY)));
    }
}
