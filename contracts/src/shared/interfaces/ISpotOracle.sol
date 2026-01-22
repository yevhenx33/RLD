// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface ISpotOracle {
    /// @notice Returns the price of the Collateral Token denominated in the Underlying Token.
    /// @dev Example: If Collateral = ETH, Underlying = USDC, returns price of 1 ETH in USDC (e.g., 3000 * 1e6).
    /// @dev Decimals must be standardized (ensure caller knows if it is WAD or token decimals). 
    ///      Recommendation: Always return WAD (18 decimals).
    /// @param collateralToken The asset to price.
    /// @param underlyingToken The quote asset.
    /// @return price The price in WAD (1e18).
    function getSpotPrice(address collateralToken, address underlyingToken) external view returns (uint256 price);
}
