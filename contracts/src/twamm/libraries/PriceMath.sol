// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title PriceMath — sqrtPriceX96 conversion utilities
/// @notice Matches the Python price_math.py module exactly.

library PriceMath {
    uint256 internal constant Q96 = 1 << 96;

    /// @notice Convert an amount at a given sqrtPriceX96
    /// @param amount The input amount
    /// @param sqrtPrice The sqrtPriceX96 value
    /// @param zeroForOne True = T0→T1, False = T1→T0
    /// @return result The converted amount
    function convertAtPrice(uint256 amount, uint160 sqrtPrice, bool zeroForOne) internal pure returns (uint256 result) {
        if (amount == 0 || sqrtPrice == 0) return 0;

        if (zeroForOne) {
            // T0 → T1: multiply by price
            uint256 intermediate = (amount * uint256(sqrtPrice)) / Q96;
            result = (intermediate * uint256(sqrtPrice)) / Q96;
        } else {
            // T1 → T0: divide by price
            uint256 intermediate = (amount * Q96) / uint256(sqrtPrice);
            result = (intermediate * Q96) / uint256(sqrtPrice);
        }
    }
}
