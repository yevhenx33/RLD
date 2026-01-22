// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @notice Arithmetic library with operations for fixed-point numbers.
/// @dev Inspired by Solady (https://github.com/Vectorized/solady/blob/main/src/utils/FixedPointMathLib.sol)
/// @author Modified from Solady (https://github.com/vectorized/solady)
library FixedPointMath {
    uint256 internal constant WAD = 1e18;
    uint256 internal constant RAY = 1e27;

    function mulWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        assembly {
            // Equivalent to require(y == 0 || x <= type(uint256).max / y)
            if mul(y, gt(x, div(not(0), y))) {
                mstore(0x00, 0xbac65e5b) // `MulWadFailed()`.
                revert(0x1c, 0x04)
            }
            z := div(mul(x, y), WAD)
        }
    }

    function divWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        assembly {
            // Equivalent to require(y != 0 && (x == 0 || WAD <= type(uint256).max / x))
            if iszero(mul(y, iszero(mul(WAD, gt(x, div(not(0), WAD)))))) {
                mstore(0x00, 0x7c5f487d) // `DivWadFailed()`.
                revert(0x1c, 0x04)
            }
            z := div(mul(x, WAD), y)
        }
    }
}
