// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ISpotOracle} from "../../interfaces/ISpotOracle.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolId} from "@uniswap/v4-core/src/types/PoolId.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {ITWAMM} from "v4-twamm-hook/src/ITWAMM.sol";

contract UniswapV4OracleAdapter is ISpotOracle {
    IPoolManager public immutable manager;
    ITWAMM public immutable twamm;
    PoolId public immutable poolId;
    PoolKey public poolKey;
    uint32 public immutable period;
    
    address public immutable token0;
    address public immutable token1;
    uint8 public immutable decimals0;
    uint8 public immutable decimals1;

    error InvalidTokens();
    error OracleNotReady();

    constructor(
        IPoolManager _manager,
        ITWAMM _twamm,
        PoolKey memory _key,
        uint32 _period
    ) {
        manager = _manager;
        twamm = _twamm;
        poolKey = _key;
        poolId = _key.toId();
        period = _period;
        
        token0 = Currency.unwrap(_key.currency0);
        token1 = Currency.unwrap(_key.currency1);
        decimals0 = ERC20(token0).decimals();
        decimals1 = ERC20(token1).decimals();
    }

    function getSpotPrice(address collateralToken, address underlyingToken) external view override returns (uint256 price) {
        // Check if query matches the pool's tokens
        bool zeroForOne = (collateralToken == token0 && underlyingToken == token1);
        bool oneForZero = (collateralToken == token1 && underlyingToken == token0);

        if (!zeroForOne && !oneForZero) {
            revert InvalidTokens();
        }

        uint32[] memory secondsAgos = new uint32[](2);
        secondsAgos[0] = period;
        secondsAgos[1] = 0;

        int56[] memory tickCumulatives = twamm.observe(poolId, secondsAgos);
        
        int56 tickCumulativesDelta = tickCumulatives[1] - tickCumulatives[0];
        
        // Calculate arithmetic mean tick
        int24 arithmeticMeanTick = int24(tickCumulativesDelta / int56(uint56(period)));
        
        // Adjust round down for negative ticks
        if (tickCumulativesDelta < 0 && (tickCumulativesDelta % int56(uint56(period)) != 0)) {
             arithmeticMeanTick--;
        }

        // Get quote amount for 1 Unit of Base Token
        // Base = collateralToken
        // Quote = underlyingToken
        
        // If Base == Token0, price = ratio
        // If Base == Token1, price = 1/ratio
        
        uint128 baseAmount = uint128(10 ** (zeroForOne ? decimals0 : decimals1));
        uint256 quoteAmount = getQuoteAtTick(arithmeticMeanTick, baseAmount, collateralToken, underlyingToken);
        
        // Convert to WAD (1e18)
        uint8 quoteDecimals = zeroForOne ? decimals1 : decimals0;
        price = (quoteAmount * 1e18) / (10 ** quoteDecimals);
    }
    
    function getQuoteAtTick(
        int24 tick,
        uint128 baseAmount,
        address baseToken,
        address quoteToken
    ) internal view returns (uint256 quoteAmount) {
        uint160 sqrtRatioX96 = TickMath.getSqrtPriceAtTick(tick);

        // Calculate quoteAmount with better precision if possible
        if (sqrtRatioX96 <= type(uint128).max) {
             uint256 ratioX192 = uint256(sqrtRatioX96) * sqrtRatioX96;
             // baseToken < quoteToken corresponds to token0 < token1.
             // If baseToken == token0, then baseToken < quoteToken (assuming pair is sorted).
             // token0 is always < token1 in PoolKey (if created correctly).
             // So if baseToken == token0, mulDiv(ratio, amount, unit).
             // If baseToken == token1, mulDiv(unit, amount, ratio).
             
             quoteAmount = baseToken == token0
                ? FullMath.mulDiv(ratioX192, baseAmount, 1 << 192)
                : FullMath.mulDiv(1 << 192, baseAmount, ratioX192);
        } else {
             uint256 ratioX128 = FullMath.mulDiv(sqrtRatioX96, sqrtRatioX96, 1 << 64);
             quoteAmount = baseToken == token0
                ? FullMath.mulDiv(ratioX128, baseAmount, 1 << 128)
                : FullMath.mulDiv(1 << 128, baseAmount, ratioX128);
        }
    }
}
