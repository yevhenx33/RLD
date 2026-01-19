// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../../interfaces/IRLDCore.sol"; // Adjust path if needed
import {IDefaultOracle} from "../../interfaces/IDefaultOracle.sol";

/// @title CDSHook
/// @notice Enforces a 7-Day Withdrawal Delay for Underwriters (Shorts).
/// @dev Linked to specific Markets. Prevents "Run on the Bank" by locking capital during crises.
contract CDSHook {
    /* ============================================================================================ */
    /*                                           STATE                                              */
    /* ============================================================================================ */

    // Mapping: User -> Timestamp when they can withdraw
    mapping(address => uint256) public withdrawalUnlockTime;
    
    // Constant: 7 Days
    uint256 public constant LOCK_PERIOD = 7 days;

    // Error definitions
    error Locked(uint256 unlockTime);
    error Defaulted();

    /* ============================================================================================ */
    /*                                          LOGIC                                               */
    /* ============================================================================================ */

    /// @notice User calls this to start the cooldown timer.
    function requestWithdrawal() external {
        withdrawalUnlockTime[msg.sender] = block.timestamp + LOCK_PERIOD;
    }

    /// @notice Called by RLDCore before modifying a position.
    /// @dev If withdrawing collateral (deltaCollateral < 0), Checks the lock.
    /// @param sender The user modifying the position (or the lock holder).
    /// @param deltaCollateral Content of the change.
    function beforeModifyPosition(
        MarketId /*id*/, 
        address sender, 
        int256 deltaCollateral, 
        int256 /*deltaDebt*/
    ) external view {
        // 1. If Adding Collateral or Neutral -> OK
        if (deltaCollateral >= 0) return;

        // 2. If Withdrawing -> Check Lock
        uint256 unlockTime = withdrawalUnlockTime[sender];
        
        // If unlockTime is 0, they never requested.
        // If block.timestamp < unlockTime, they are still waiting.
        if (unlockTime == 0 || block.timestamp < unlockTime) {
            revert Locked(unlockTime);
        }
        
        // Note: Once unlocked, they stay unlocked? Or strictly one-time?
        // "Rolling Window" or "One-Time"? 
        // Simplest: If timestamp > unlockTime, they can withdraw.
        // But we might want them to re-lock after? 
        // For now, let's keep it simple: Once passed, they are free to leave until they deposit again?
        // Better security: Reset on Deposit? No, too annoying.
    }
    
    // TODO: Add logic to check DefaultOracle and revert EVERYTHING if defaulted?
    // Actually, Core checks Settlement. Hook just enforces user-level locks.
}
