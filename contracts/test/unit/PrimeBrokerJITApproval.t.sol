// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import "../../lib/solady/test/utils/mocks/MockERC20.sol";

/// @title JIT Approval Pattern Security Tests
/// @notice Focused tests for the executeWithApproval security model
contract JITApprovalSecurityTest is Test {
    MockERC20 USDC;
    MockERC20 wRLP;
    MockERC20 wETH;
    
    MockBroker broker;
    
    address user = address(0x1);
    address attacker = address(0x666);
    
    function setUp() public {
        // Deploy tokens
        USDC = new MockERC20("USDC", "USDC", 6);
        wRLP = new MockERC20("wRLP", "wRLP", 18);
        wETH = new MockERC20("wETH", "wETH", 18);
        
        // Deploy simplified broker
        broker = new MockBroker(address(USDC), address(wRLP));
        
        // Setup balances
        USDC.mint(address(broker), 10000e6);
        wRLP.mint(address(broker), 1000e18);
        wETH.mint(address(broker), 10 ether);
    }
    
    /* ============================================ */
    /*         ATTACK VECTOR TESTS (SHOULD FAIL)   */
    /* ============================================ */
    
    function test_RevertWhen_CallingCollateralToken() public {
        vm.expectRevert("Cannot call token contracts");
        broker.executeWithApproval(
            address(USDC),
            abi.encodeCall(USDC.approve, (attacker, type(uint256).max)),
            address(0),
            0
        );
    }
    
    function test_RevertWhen_CallingPositionToken() public {
        vm.expectRevert("Cannot call token contracts");
        broker.executeWithApproval(
            address(wRLP),
            abi.encodeCall(wRLP.approve, (attacker, type(uint256).max)),
            address(0),
            0
        );
    }
    
    function test_WrapperContractSafe() public {
        MaliciousWrapper wrapper = new MaliciousWrapper();
        
        uint256 balanceBefore = USDC.balanceOf(address(broker));
        
        // Try exploit
        broker.executeWithApproval(
            address(wrapper),
            abi.encodeCall(wrapper.exploit, (address(USDC), attacker)),
            address(0),
            0
        );
        
        // Broker balance unchanged
        assertEq(USDC.balanceOf(address(broker)), balanceBefore);
        
        // Attacker has no allowance
        assertEq(USDC.allowance(address(broker), attacker), 0);
    }
    
    /* ============================================ */
    /*         JIT APPROVAL TESTS (SHOULD PASS)    */
    /* ============================================ */
    
    function test_JITApprovalGrantedAndRevoked() public {
        MockRouter router = new MockRouter(address(wRLP), address(USDC));
        USDC.mint(address(router), 500e6);
        
        // Before: no allowance
        assertEq(wRLP.allowance(address(broker), address(router)), 0);
        
        // Execute swap
        broker.executeWithApproval(
            address(router),
            abi.encodeCall(router.swap, (500e18, 490e6)),
            address(wRLP),
            500e18
        );
        
        // After: allowance revoked
        assertEq(wRLP.allowance(address(broker), address(router)), 0);
    }
    
    function test_ApprovalRevokedEvenIfUnused() public {
        DoNothingContract dummy = new DoNothingContract();
        
        broker.executeWithApproval(
            address(dummy),
            abi.encodeCall(dummy.doNothing, ()),
            address(wETH),
            type(uint256).max
        );
        
        // Allowance revoked even though never used
        assertEq(wETH.allowance(address(broker), address(dummy)), 0);
    }
    
    function test_MultipleApprovalsInMulticall() public {
        MockRouter router = new MockRouter(address(wRLP), address(USDC));
        USDC.mint(address(router), 1000e6);
        
        bytes[] memory calls = new bytes[](2);
        calls[0] = abi.encodeCall(broker.executeWithApproval, (
            address(router),
            abi.encodeCall(router.swap, (100e18, 95e6)),
            address(wRLP),
            100e18
        ));
        calls[1] = abi.encodeCall(broker.executeWithApproval, (
            address(router),
            abi.encodeCall(router.swap, (200e18, 190e6)),
            address(wRLP),
            200e18
        ));
        
        broker.multicall(calls);
        
        // All approvals revoked
        assertEq(wRLP.allowance(address(broker), address(router)), 0);
    }
    
    /* ============================================ */
    /*         WITHDRAWAL TESTS                    */
    /* ============================================ */
    
    function test_WithdrawCollateral() public {
        broker.withdrawCollateral(user, 1000e6);
        assertEq(USDC.balanceOf(user), 1000e6);
    }
    
    function test_WithdrawPositionToken() public {
        broker.withdrawPositionToken(user, 500e18);
        assertEq(wRLP.balanceOf(user), 500e18);
    }
    
    function test_WithdrawNonCriticalTokenViaExecute() public {
        // wETH not in blacklist
        broker.executeWithApproval(
            address(wETH),
            abi.encodeCall(wETH.transfer, (user, 5 ether)),
            address(0),
            0
        );
        
        assertEq(wETH.balanceOf(user), 5 ether);
    }
}

/* ============================================ */
/*              MOCK BROKER                    */
/* ============================================ */

contract MockBroker {
    address public collateralToken;
    address public positionToken;
    
    constructor(address _collateral, address _position) {
        collateralToken = _collateral;
        positionToken = _position;
    }
    
    function executeWithApproval(
        address target,
        bytes calldata data,
        address approvalToken,
        uint256 approvalAmount
    ) external {
        // Token blacklist
        require(
            target != collateralToken &&
            target != positionToken,
            "Cannot call token contracts"
        );
        
        // Grant approval
        if (approvalToken != address(0) && approvalAmount > 0) {
            (bool success,) = approvalToken.call(
                abi.encodeCall(IERC20.approve, (target, approvalAmount))
            );
            require(success);
        }
        
        // Execute
        (bool success,) = target.call(data);
        require(success, "Execution failed");
        
        // Revoke approval
        if (approvalToken != address(0) && approvalAmount > 0) {
            (success,) = approvalToken.call(
                abi.encodeCall(IERC20.approve, (target, 0))
            );
            require(success);
        }
    }
    
    function multicall(bytes[] calldata data) external returns (bytes[] memory results) {
        results = new bytes[](data.length);
        for (uint256 i = 0; i < data.length; i++) {
            (bool success, bytes memory result) = address(this).delegatecall(data[i]);
            require(success);
            results[i] = result;
        }
    }
    
    function withdrawCollateral(address recipient, uint256 amount) external {
        IERC20(collateralToken).transfer(recipient, amount);
    }
    
    function withdrawPositionToken(address recipient, uint256 amount) external {
        IERC20(positionToken).transfer(recipient, amount);
    }
}

/* ============================================ */
/*              HELPER CONTRACTS               */
/* ============================================ */

contract MaliciousWrapper {
    function exploit(address token, address spender) external {
        IERC20(token).approve(spender, type(uint256).max);
    }
}

contract DoNothingContract {
    function doNothing() external pure {}
}

contract MockRouter {
    address public tokenIn;
    address public tokenOut;
    
    constructor(address _in, address _out) {
        tokenIn = _in;
        tokenOut = _out;
    }
    
    function swap(uint256 amountIn, uint256 amountOut) external {
        IERC20(tokenIn).transferFrom(msg.sender, address(this), amountIn);
        IERC20(tokenOut).transfer(msg.sender, amountOut);
    }
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function allowance(address owner, address spender) external view returns (uint256);
}
