// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ISpotOracle} from "../../../shared/interfaces/ISpotOracle.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolId} from "@uniswap/v4-core/src/types/PoolId.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {ITWAMM} from "../../../twamm/ITWAMM.sol";

/// @title UniswapV4SingletonOracle
/// @notice Single Oracle Contract that manages rate queries for multiple V4 Pools.
/// @dev Optimized to save gas by avoiding per-market Oracle Adapter deployment.
contract UniswapV4SingletonOracle is ISpotOracle {
    
    struct PoolSettings {
        PoolKey key;
        PoolId poolId;
        ITWAMM twamm;
        uint32 period;
        bool set;
    }
    
    // Mapping from Position Token (wRLP) -> Pool Settings
    mapping(address => PoolSettings) public poolSettings;
    
    error InvalidTokens();
    error PoolNotRegistered();
    error InvalidPool();

    function registerPool(
        address positionToken,
        PoolKey memory key,
        address twammAddr,
        uint32 period
    ) external {
        if (address(key.hooks) != twammAddr) revert InvalidPool(); // basic check
        
        poolSettings[positionToken] = PoolSettings({
            key: key,
            poolId: key.toId(),
            twamm: ITWAMM(twammAddr),
            period: period,
            set: true
        });
    }

    function getSpotPrice(address collateralToken, address underlyingToken) external view override returns (uint256 price) {
        // collateralToken here serves as the key (Position Token)
        // Check if settings exist for this token
        PoolSettings storage settings = poolSettings[collateralToken];
        
        // If not found, maybe parameters were swapped or user passed collateral as base?
        // In RLDCore, we prioritized passing PositionToken as the first argument (collateralToken param).
        // So `collateralToken` should be our wRLP address.
        
        if (!settings.set) {
            // Fallback: If querying purely collateral vs underlying (not debt), this might be wrong oracle usage.
            // But we only use this for MARK PRICE of the DEBT.
            revert PoolNotRegistered();
        }
        
        address token0 = Currency.unwrap(settings.key.currency0);
        address token1 = Currency.unwrap(settings.key.currency1);
        
        // Check if query matches the pool's tokens
        bool zeroForOne = (collateralToken == token0 && underlyingToken == token1);
        bool oneForZero = (collateralToken == token1 && underlyingToken == token0);

        if (!zeroForOne && !oneForZero) {
            revert InvalidTokens();
        }

        uint32[] memory secondsAgos = new uint32[](2);
        secondsAgos[0] = settings.period;
        secondsAgos[1] = 0;

        int56[] memory tickCumulatives = settings.twamm.observe(settings.poolId, secondsAgos);
        
        int56 tickCumulativesDelta = tickCumulatives[1] - tickCumulatives[0];
        
        int24 arithmeticMeanTick = int24(tickCumulativesDelta / int56(uint56(settings.period)));
        
        if (tickCumulativesDelta < 0 && (tickCumulativesDelta % int56(uint56(settings.period)) != 0)) {
             arithmeticMeanTick--;
        }

        uint8 decimals0 = ERC20(token0).decimals();
        uint8 decimals1 = ERC20(token1).decimals();
        
        uint128 baseAmount = uint128(10 ** (zeroForOne ? decimals0 : decimals1));
        uint256 quoteAmount = getQuoteAtTick(arithmeticMeanTick, baseAmount, collateralToken, underlyingToken, token0);
        
        uint8 quoteDecimals = zeroForOne ? decimals1 : decimals0;
        price = (quoteAmount * 1e18) / (10 ** quoteDecimals);
    }
    
    function getQuoteAtTick(
        int24 tick,
        uint128 baseAmount,
        address baseToken,
        address /*quoteToken*/,
        address token0
    ) internal pure returns (uint256 quoteAmount) {
        uint160 sqrtRatioX96 = TickMath.getSqrtPriceAtTick(tick);

        if (sqrtRatioX96 <= type(uint128).max) {
             uint256 ratioX192 = uint256(sqrtRatioX96) * sqrtRatioX96;
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
