// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IBrokerModule} from "../../../shared/interfaces/IBrokerModule.sol";
import {ISpotOracle} from "../../../shared/interfaces/ISpotOracle.sol";
import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {LiquidityAmounts} from "../../../shared/libraries/LiquidityAmounts.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {PositionInfo, PositionInfoLibrary} from "v4-periphery/src/libraries/PositionInfoLibrary.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";

/// @title Uniswap V4 Broker Module
/// @notice Valuates V4 Positions via Oracle and implements Unwind-and-Send Seizure.
contract UniswapV4BrokerModule is IBrokerModule {
    using FixedPointMathLib for uint256;
    using PositionInfoLibrary for PositionInfo;
    using StateLibrary for IPoolManager;

    struct VerifyParams {
        uint256 tokenId;
        address positionManager;
        address oracle;
        address collateralToken; // Token0 or Token1
        address underlyingToken; // Token1 or Token0
    }

    /// @notice Returns the value of an LP Position using ORACLE PRICE.
    function getValue(bytes calldata data) external view returns (uint256) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));
        
        // 1. Get Position Info
        uint128 liquidity = IPositionManager(params.positionManager).getPositionLiquidity(params.tokenId);
        if (liquidity == 0) return 0;

        // 2. Get Pool Key & Ticks
        (PoolKey memory poolKey, PositionInfo info) = IPositionManager(params.positionManager).getPoolAndPositionInfo(params.tokenId);
        int24 tickLower = info.tickLower();
        int24 tickUpper = info.tickUpper();
        
        // 3. Get Current Tick (Safety: Used for composition, but priced via Oracle)
        IPoolManager pm = IPositionManager(params.positionManager).poolManager();
        (, int24 currentTick, , ) = pm.getSlot0(poolKey.toId());
        
        // 4. Calculate Token Amounts
        uint160 sqrtRatioX96 = TickMath.getSqrtPriceAtTick(currentTick);
        uint160 sqrtRatioAX96 = TickMath.getSqrtPriceAtTick(tickLower);
        uint160 sqrtRatioBX96 = TickMath.getSqrtPriceAtTick(tickUpper);
        
        (uint256 amount0, uint256 amount1) = LiquidityAmounts.getAmountsForLiquidity(
            sqrtRatioX96,
            sqrtRatioAX96,
            sqrtRatioBX96,
            liquidity
        );

        uint256 value = 0;
        if (amount0 > 0) {
             uint256 price0 = ISpotOracle(params.oracle).getSpotPrice(Currency.unwrap(poolKey.currency0), params.underlyingToken);
             value += amount0.mulWadDown(price0);
        }
        if (amount1 > 0) {
             uint256 price1 = ISpotOracle(params.oracle).getSpotPrice(Currency.unwrap(poolKey.currency1), params.underlyingToken);
             value += amount1.mulWadDown(price1);
        }
        
        return value;
    }

    /// @notice Seizes liquidity by Unwinding and transferring tokens.
    function seize(uint256 amount, address recipient, bytes calldata data) external returns (uint256 seizedValue) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));

        // 1. Calculate Liquidity to Reduce
        uint256 totalValue = this.getValue(data);
        if (totalValue == 0) return 0;
        
        
        uint128 totalLiquidity = IPositionManager(params.positionManager).getPositionLiquidity(params.tokenId);
        
        uint256 liquidityToRemove;
        if (amount >= totalValue) {
            liquidityToRemove = totalLiquidity; 
        } else {
            liquidityToRemove = uint256(totalLiquidity).mulDivUp(amount, totalValue);
        }
        
        if (liquidityToRemove == 0) return 0;

        // 2. Unwind (modifyLiquidities)
        // Actions: DECREASE_LIQUIDITY (0x01) -> TAKE_PAIR (0x11)
        bytes memory actions = abi.encodePacked(uint8(0x01), uint8(0x11));
        
        // DECREASE Params: (tokenId, liquidity, amount0Min, amount1Min, hookData)
        bytes memory decreaseParams = abi.encode(
            params.tokenId, 
            liquidityToRemove, 
            uint128(0), 
            uint128(0), 
            bytes("")
        );
        
        (PoolKey memory poolKey, ) = IPositionManager(params.positionManager).getPoolAndPositionInfo(params.tokenId);
        
        bytes memory takeParams = abi.encode(
            poolKey.currency0,
            poolKey.currency1,
            recipient
        );
        
        bytes[] memory actionParams = new bytes[](2);
        actionParams[0] = decreaseParams;
        actionParams[1] = takeParams;
        
        bytes memory unlockData = abi.encode(actions, actionParams);
        
        // CALL Unwind
        IPositionManager(params.positionManager).modifyLiquidities(unlockData, block.timestamp + 60);

        IPoolManager pm = IPositionManager(params.positionManager).poolManager();
        (, int24 currentTick, , ) = pm.getSlot0(poolKey.toId());
        
        (PoolKey memory pk, PositionInfo info) = IPositionManager(params.positionManager).getPoolAndPositionInfo(params.tokenId);
        int24 tl = info.tickLower();
        int24 tu = info.tickUpper();
        
        uint160 sqrtRatioX96 = TickMath.getSqrtPriceAtTick(currentTick);
        uint160 sqrtRatioAX96 = TickMath.getSqrtPriceAtTick(tl);
        uint160 sqrtRatioBX96 = TickMath.getSqrtPriceAtTick(tu);
        
        (uint256 amount0, uint256 amount1) = LiquidityAmounts.getAmountsForLiquidity(
            sqrtRatioX96,
            sqrtRatioAX96,
            sqrtRatioBX96,
            uint128(liquidityToRemove)
        );
        
        // Value it
        uint256 realizedValue = 0;
        if (amount0 > 0) {
             uint256 price0 = ISpotOracle(params.oracle).getSpotPrice(Currency.unwrap(pk.currency0), params.underlyingToken);
             realizedValue += amount0.mulWadDown(price0);
        }
        if (amount1 > 0) {
             uint256 price1 = ISpotOracle(params.oracle).getSpotPrice(Currency.unwrap(pk.currency1), params.underlyingToken);
             realizedValue += amount1.mulWadDown(price1);
        }
        
        return realizedValue;
    }
}
