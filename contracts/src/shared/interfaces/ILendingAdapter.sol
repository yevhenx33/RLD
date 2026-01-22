// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface ILendingAdapter {
    /// @notice Supplies an asset to the lending protocol.
    /// @dev Used by SyntheticBond to earn yield + collateralize.
    /// @param asset The asset to supply (e.g., USDC).
    /// @param amount The amount to supply.
    /// @return collateralToken The address of the received collateral (e.g., aUSDC).
    /// @return collateralAmount The amount of collateral received.
    function supply(address asset, uint256 amount) external returns (address collateralToken, uint256 collateralAmount);
    
    /// @notice Withdraws an asset from the lending protocol.
    /// @param asset The underlying asset to withdraw.
    /// @param amount The amount of underlying to withdraw.
    /// @return receivedAmount The actual amount withdrawn.
    function withdraw(address asset, uint256 amount) external returns (uint256 receivedAmount);
    
    /// @notice Borrows an asset from the lending protocol.
    /// @dev Used by LeveragedBasisVault for looping.
    /// @param asset The asset to borrow.
    /// @param amount The amount to borrow.
    function borrow(address asset, uint256 amount) external;

    /// @notice Repays a borrowed asset.
    /// @param asset The asset to repay.
    /// @param amount The amount to repay.
    function repay(address asset, uint256 amount) external;

    /// @notice Returns the user's current debt in the lending protocol.
    /// @param asset The borrowed asset.
    /// @param user The user whose debt is being checked.
    /// @return debt The debt amount.
    function getDebt(address asset, address user) external view returns (uint256 debt);
}
