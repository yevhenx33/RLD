// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ILendingAdapter} from "../../../shared/interfaces/ILendingAdapter.sol";
import {IERC20} from "../../../shared/interfaces/IERC20.sol"; // Use OpenZeppelin or custom interface

// Minimal Aave Interface needed for Adapter
interface IAavePoolSimple {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
    function withdraw(address asset, uint256 amount, address to) external returns (uint256);
    function borrow(address asset, uint256 amount, uint256 interestRateMode, uint16 referralCode, address onBehalfOf) external;
    function repay(address asset, uint256 amount, uint256 interestRateMode, address onBehalfOf) external returns (uint256);
    function getReserveData(address asset) external view returns (uint256, uint128, uint128, uint128, uint128, uint128, uint40, uint16, address aTokenAddress, address, address);
}

/// @title AaveAdapter
/// @notice Implements ILendingAdapter for Aave V3 using normal calls, not delegatecall.
/// @dev Users/vaults must approve this adapter for supplied, repaid, or withdrawn tokens.
///      Borrowing requires Aave credit delegation from the borrower to this adapter.
contract AaveAdapter is ILendingAdapter {
    address public immutable POOL; // Aave Pool Address

    constructor(address _pool) {
        POOL = _pool;
    }

    function supply(address asset, uint256 amount) external override returns (address collateralToken, uint256 collateralAmount) {
        require(amount > 0, "amount=0");

        // 1. Transfer asset from caller to adapter.
        require(IERC20(asset).transferFrom(msg.sender, address(this), amount), "transferFrom failed");
        
        // 2. Approve Pool
        require(IERC20(asset).approve(POOL, amount), "approve failed");
        
        // 3. Supply on behalf of caller so the caller receives aTokens.
        IAavePoolSimple(POOL).supply(asset, amount, msg.sender, 0);
        
        // 4. Return Output Info
        // Fetch aToken address
        (,,,,,,,, collateralToken, , ) = IAavePoolSimple(POOL).getReserveData(asset);
        collateralAmount = amount; // 1:1 for aTokens usually (ignoring index growth for principal?)
        // Actually aToken balance grows. Principal supplied is amount.
    }

    function withdraw(address asset, uint256 amount) external override returns (uint256 receivedAmount) {
        require(amount > 0, "amount=0");

        // 1. Determine aToken
        (,,,,,,,, address aToken, , ) = IAavePoolSimple(POOL).getReserveData(asset);
        
        // 2. Transfer aToken from caller to adapter.
        require(IERC20(aToken).transferFrom(msg.sender, address(this), amount), "transferFrom failed");
        require(IERC20(aToken).approve(POOL, amount), "approve failed");
        
        // 3. Withdraw underlying to caller.
        receivedAmount = IAavePoolSimple(POOL).withdraw(asset, amount, msg.sender);
    }
    
    function borrow(address asset, uint256 amount) external override {
        require(amount > 0, "amount=0");
        uint256 beforeBalance = IERC20(asset).balanceOf(address(this));
        IAavePoolSimple(POOL).borrow(asset, amount, 2, 0, msg.sender);
        uint256 borrowed = IERC20(asset).balanceOf(address(this)) - beforeBalance;
        require(IERC20(asset).transfer(msg.sender, borrowed), "transfer failed");
    }

    function repay(address asset, uint256 amount) external override {
        require(amount > 0, "amount=0");
        require(IERC20(asset).transferFrom(msg.sender, address(this), amount), "transferFrom failed");
        require(IERC20(asset).approve(POOL, amount), "approve failed");
        uint256 repaid = IAavePoolSimple(POOL).repay(asset, amount, 2, msg.sender);
        if (repaid < amount) {
            require(IERC20(asset).transfer(msg.sender, amount - repaid), "refund failed");
        }
    }

    function getDebt(address asset, address user) external view override returns (uint256) {
        // Aave debt balance
        (,,,,,,,, , , address vDebtToken) = IAavePoolSimple(POOL).getReserveData(asset);
        return IERC20(vDebtToken).balanceOf(user);
    }
}
