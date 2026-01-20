// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {CDSHook} from "../src/modules/hooks/CDSHook.sol";
import {MarketId} from "../src/interfaces/IRLDCore.sol";

contract CDSHookTest is Test {
    CDSHook hook;
    
    MarketId constant ID_1 = MarketId.wrap(bytes32(uint256(1)));
    MarketId constant ID_2 = MarketId.wrap(bytes32(uint256(2)));
    
    address user1 = address(0x1);
    address user2 = address(0x2);

    function setUp() public {
        hook = new CDSHook();
    }

    function test_RequestWithdrawal_IndependentMarkets() public {
        vm.startPrank(user1);
        
        // Request for Market 1
        hook.requestWithdrawal(ID_1);
        
        uint256 unlockTime1 = hook.withdrawalUnlockTime(ID_1, user1);
        uint256 expected = block.timestamp + 7 days;
        assertEq(unlockTime1, expected);
        
        // Check Market 2 is empty
        uint256 unlockTime2 = hook.withdrawalUnlockTime(ID_2, user1);
        assertEq(unlockTime2, 0);
        
        // Request for Market 2
        hook.requestWithdrawal(ID_2);
        unlockTime2 = hook.withdrawalUnlockTime(ID_2, user1);
        assertEq(unlockTime2, expected);
        
        vm.stopPrank();
    }
    
    function test_BeforeModifyPosition_EnforceLock() public {
        vm.startPrank(user1);
        
        // Case 1: No request -> Revert
        // withdrawing collateral (delta < 0)
        vm.expectRevert(abi.encodeWithSelector(CDSHook.Locked.selector, 0));
        hook.beforeModifyPosition(ID_1, user1, -100, 0);
        
        // Case 2: Adding collateral -> Pass
        hook.beforeModifyPosition(ID_1, user1, 100, 0);
        
        // Case 3: Request made, but time not passed
        hook.requestWithdrawal(ID_1);
        uint256 unlockTime = block.timestamp + 7 days;
        
        vm.expectRevert(abi.encodeWithSelector(CDSHook.Locked.selector, unlockTime));
        hook.beforeModifyPosition(ID_1, user1, -100, 0);
        
        // Case 4: Time passed -> Pass
        vm.warp(block.timestamp + 7 days + 1 seconds);
        hook.beforeModifyPosition(ID_1, user1, -100, 0);
        
        vm.stopPrank();
    }
    
    function test_BeforeModifyPosition_IndependentUsers() public {
        vm.startPrank(user1);
        hook.requestWithdrawal(ID_1);
        vm.stopPrank();
        
        vm.startPrank(user2);
        // User 2 hasn't requested
        vm.expectRevert(abi.encodeWithSelector(CDSHook.Locked.selector, 0));
        hook.beforeModifyPosition(ID_1, user2, -100, 0);
        vm.stopPrank();
    }
}
