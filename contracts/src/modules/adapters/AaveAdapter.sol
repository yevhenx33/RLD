// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ILendingAdapter} from "../../interfaces/ILendingAdapter.sol";
import {IERC20} from "../../interfaces/IERC20.sol"; // Use OpenZeppelin or custom interface

// Minimal Aave Interface needed for Adapter
interface IAavePoolSimple {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
    function withdraw(address asset, uint256 amount, address to) external returns (uint256);
    function borrow(address asset, uint256 amount, uint256 interestRateMode, uint16 referralCode, address onBehalfOf) external;
    function repay(address asset, uint256 amount, uint256 interestRateMode, address onBehalfOf) external returns (uint256);
    function getReserveData(address asset) external view returns (uint256, uint128, uint128, uint128, uint128, uint128, uint40, uint16, address aTokenAddress, address, address);
}

/// @title AaveAdapter
/// @notice Implements ILendingAdapter for Aave V3.
/// @dev Stateless adapter.
contract AaveAdapter is ILendingAdapter {
    address public immutable POOL; // Aave Pool Address

    constructor(address _pool) {
        POOL = _pool;
    }

    function supply(address asset, uint256 amount) external override returns (address collateralToken, uint256 collateralAmount) {
        // 1. Transfer Asset from User to Adapter (if not already here)
        // Note: Adapter usually called via delegatecall or user approves adapter?
        // Architecture: Vault calls Adapter. Vault approves Adapter.
        IERC20(asset).transferFrom(msg.sender, address(this), amount);
        
        // 2. Approve Pool
        IERC20(asset).approve(POOL, amount);
        
        // 3. Supply
        IAavePoolSimple(POOL).supply(asset, amount, msg.sender, 0);
        
        // 4. Return Output Info
        // Fetch aToken address
        (,,,,,,,, collateralToken, , ) = IAavePoolSimple(POOL).getReserveData(asset);
        collateralAmount = amount; // 1:1 for aTokens usually (ignoring index growth for principal?)
        // Actually aToken balance grows. Principal supplied is amount.
    }

    function withdraw(address asset, uint256 amount) external override returns (uint256 receivedAmount) {
        // Vault holds aToken. Vault needs to approve Pool to burn aToken?
        // Actually `withdraw` pulls aToken from msg.sender (Vault) and sends Asset to to.
        // Vault must approve Aave Pool to spend aToken? No, Aave V3 withdraw burns from msg.sender.
        // Wait, if Adapter is a separate contract, msg.sender for Aave is Adapter.
        // Vault should pull aToken to Adapter?
        // Efficient way: Vault delegatecalls Adapter? 
        // If Adapter is standard contract:
        // Vault transfers aToken to Adapter. Adapter withdraws. Adapter sends asset back.
        
        // Let's assume standard call.
        // 1. Determine aToken
        (,,,,,,,, address aToken, , ) = IAavePoolSimple(POOL).getReserveData(asset);
        
        // 2. Transfer aToken from Vault
        IERC20(aToken).transferFrom(msg.sender, address(this), amount);
        
        // 3. Withdraw
        receivedAmount = IAavePoolSimple(POOL).withdraw(asset, amount, msg.sender);
    }
    
    function borrow(address asset, uint256 amount) external override {
        // Vault calls this. Vault must be credit solvent on Aave.
        // But Vault is msg.sender. Adapter is intermediary.
        // Aave Borrow: `onBehalfOf`.
        // Adapter calls borrow(asset, amount, ..., onBehalfOf=msg.sender).
        // Requires User (Vault) to approve Credit Delegation to Adapter?
        // Or simpler: Adapter is ONLY used if Vault Delegatecalls it?
        // If delegatecall, then address(this) is Vault. POOL is immutable in logic contract.
        
        // DECISION: Adapters should be Libraries or Delegatecalled?
        // If standard contract, `borrow` requires credit delegation.
        // "Credit Delegation" is complex features.
        // Simpler: Vault interacts with Aave directly?
        // But we want "Protocol Agnostic".
        // Solution: Vault DELEGATECALLS the Adapter logic.
        
        // If delegatecall:
        IERC20(asset).approve(POOL, amount); // Optional if needed for repay?
        IAavePoolSimple(POOL).borrow(asset, amount, 2, 0, address(this));
    }

    function repay(address asset, uint256 amount) external override {
        IERC20(asset).approve(POOL, amount);
        IAavePoolSimple(POOL).repay(asset, amount, 2, address(this));
    }

    function getDebt(address asset, address user) external view override returns (uint256) {
        // Aave debt balance
        (,,,,,,,, , , address vDebtToken) = IAavePoolSimple(POOL).getReserveData(asset);
        return IERC20(vDebtToken).balanceOf(user);
    }
}
