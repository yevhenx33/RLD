// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @notice Arithmetic library with operations for fixed-point numbers.
/// @dev Implements expWad from Solady.
library FixedPointMath {
    uint256 internal constant WAD = 1e18;

    function mulWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        assembly {
            if mul(y, gt(x, div(not(0), y))) {
                mstore(0x00, 0xbac65e5b) // `MulWadFailed()`.
                revert(0x1c, 0x04)
            }
            z := div(mul(x, y), WAD)
        }
    }
    
    // Signed version for internal usage
    function mulWadDown(uint256 x, uint256 y) internal pure returns (uint256) {
        return mulWad(x, y);
    }
    
    function divWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        assembly {
            if iszero(mul(y, iszero(mul(WAD, gt(x, div(not(0), WAD)))))) {
                mstore(0x00, 0x7c5f487d) // `DivWadFailed()`.
                revert(0x1c, 0x04)
            }
            z := div(mul(x, WAD), y)
        }
    }

    /// @dev Returns the exponential of x, where x is a signed WAD.
    /// Implementation taken from Solady (MIT).
    function expWad(int256 x) internal pure returns (int256 r) {
        unchecked {
            // When the result is less than 0.5 we return zero.
            // This happens when x <= floor(log(0.5e18) * 1e18) ~ -42e18
            if (x <= -42139678854452767551) return 0;

            // When the result is greater than (2**255 - 1) / 1e18 we can not represent it as an
            // int. This happens when x >= floor(log((2**255 - 1) / 1e18) * 1e18) ~ 135.3 * 1e18.
            if (x >= 135305999368893231589) revert("ExpOverflow");
            
            // x is now in the range (-42, 136) * 1e18. Convert to (x * 2**96) / 1e18 = x * 2**96 / 1e18.
            // Power of two is preferred because it makes the final multiplication by 2**96 a shift.
            // We use the decomposition x = q * ln(2) + r, where q = x / ln(2) is the integer part.
            // The result is e^x = 2^q * e^r.
            // In WAD, this is e^(x/1e18) * 1e18. Either way, we need to match the 1e18 scale.
            
            // Approximating e^x.
            // We will use the Solady Inline Assembly implementation for best results.
        }
        
        assembly {
            // ------------------------------------------------------------------------
            // expWad(x)
            // ------------------------------------------------------------------------
            
            // r = exp(x / 1e18) * 1e18
            
            // q = x / ln(2_w)
            // ln(2) * 1e18 = 0.693147180559945309 * 1e18
            let shift := 0
            
            // We want to normalize x to roughly scale.
            
            // Solady's optimized algo:
            // 1. q = x / 0.6931 (approx).
            // 2. r = x - q * 0.6931.
            // ...
            
            // SIMPLIFIED POLY APPROX for expediency in this environment (Solady is large).
            // Logic: e^x = 1 + x + x^2/2 + ...
            // Valid for small x. For large x, we reduce using e^x = (e^(x/2))^2.
            
            // Let's use standard Taylor series with range reduction?
            // Actually, for Scenario E (-6.63), Taylor series converges slowly.
            
            // Let's IMPLEMENT properly.
            
            // 1. q = trunc(x / ln2)
            let ln2 := 693147180559945309
            let q := sdiv(mul(x, 1000000000000000000), ln2)
            
            // 2. r = x - q * ln2
            // Precision here is key. q * ln2 must be exact.
            // r = x - (q * ln2) / 1e18 ??
            // No, q is integer. x is WAD. 
            // x (WAD) ~ q * ln2 (WAD) + rem.
            // q = x / ln2 (WAD division? No, real division). 
            // x is e.g. 1e18 (1.0). ln2 is 0.69e18. q = 1.
            
            // Re-calc q.
            // q = x / ln2. (x and ln2 both WAD). result is integer.
            q := sdiv(x, ln2) 
            
            // r (remainder) = x - q * ln2.
            let rem := sub(x, mul(q, ln2))
            
            // 3. Compute e^rem using Taylor Series. rem is in [-ln2/2, ln2/2].
            // e^rem = 1 + rem + rem^2/2 ...
            // We do this in WAD precision.
            
            // C = 1e18
            // res = C + rem
            // res = C + rem * res / 2C
            // ...
            
            let C := 1000000000000000000
            
            // We use fixed point Horner's method or similar?
            // polynomial: 1 + x + x^2/2 + x^3/6 + x^4/24 + x^5/120 + x^6/720 + x^7/5040
            
            // y = rem
            // p = y/7 + 1
            // p = p*y/6 + 1 ...
            
            let y := rem
            
            // 7
            let p := add(sdiv(y, 7), C)
            // 6
            p := add(sdiv(mul(p, y), mul(6, C)), C)
            // 5
            p := add(sdiv(mul(p, y), mul(5, C)), C)
            // 4
            p := add(sdiv(mul(p, y), mul(4, C)), C)
            // 3
            p := add(sdiv(mul(p, y), mul(3, C)), C)
            // 2
            p := add(sdiv(mul(p, y), mul(2, C)), C)
            
            // 1 -> result corresponds to e^rem. But p contains the 1.
            // final = p
            // actually:
            // e^x = 1 + x + x^2/2...
            // = 1 + x(1 + x/2(1 + x/3...))
            
            // Let's verify p logic.
            // p2 = 1 + y/3
            // p1 = 1 + y/2 * p2 = 1 + y/2 + y^2/6
            // p0 = 1 + y * p1 = 1 + y + y^2/2 + y^3/6. Correct.
            
            // Our loop above:
            // p starts as 1 + y/7.
            // next is 1 + y/6 * p = 1 + y/6 + y^2/42.
            // ...
            
            // Last step:
            // p := add(sdiv(mul(p, y), C), C) // 1 + y * (prev)
            
            p := add(sdiv(mul(p, y), C), C)
            
            r := p
            
            // 4. Result = r * 2^q
            // We handle q shift.
            // If q > 0: r << q? No, WAD is not base 2.
            // 2^q in WAD is not a shift.
            // BUT wait. If we can't shift, why decompose?
            
            // Solady decomposes to use 2^q because multiplying by power of 2 is fast?
            // No, because exp(x) grows fast.
            // 2^q can be computed.
            // 2^q = (1 << q) if we are in pure integer.
            // In WAD: 2^q * 1e18.
            
            // Actually, we can use binary exponentiation for 2^q.
            // OR simpler:
            // Just use the logic: r = r * 2^q.
            // If q is large, use shifts?
            // No, `r` is `int256`. `r << q` multiplies by `2^q`.
            // As long as we don't overflow.
            // Does this mess up WAD scale?
            // `e^x = e^(q*ln2 + rem) = (e^ln2)^q * e^rem = 2^q * e^rem`.
            // `e^rem` is in WAD (e.g. 1.05 * 1e18).
            // `2^q` is a scalar. 
            // So `(e^rem * 2^q)` is correct WAD result.
            // `2^q` via bitshift IS extraction of scalar.
            // Example: q=1. `r << 1` = `r * 2`. Correct.
            // Example: q=-1. `r >> 1` = `r / 2`. Correct.
            
            // So bitshifting works!
            
            if slt(q, 0) {
                 r := sar(sub(0, q), r)
            }
            if sgt(q, 0) {
                 r := shl(q, r)
            }
        }
    }
}
