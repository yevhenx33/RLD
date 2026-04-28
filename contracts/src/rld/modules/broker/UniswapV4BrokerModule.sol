// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    IValuationModule
} from "../../../shared/interfaces/IValuationModule.sol";
import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";
import {
    IPositionManager
} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {LiquidityAmounts} from "../../../shared/libraries/LiquidityAmounts.sol";
import {Position} from "v4-core/src/libraries/Position.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {
    PositionInfo,
    PositionInfoLibrary
} from "v4-periphery/src/libraries/PositionInfoLibrary.sol";
import {FixedPointMathLib} from "../../../shared/utils/FixedPointMathLib.sol";

/// @title Uniswap V4 Broker Module
/// @author RLD Protocol
/// @notice Read-only valuation module for Uniswap V4 LP positions.
///
/// @dev ## Valuation Logic
///
/// For LP positions in RLD markets:
/// - **Collateral Token** (e.g. waUSDC): Valued at 1:1 (it IS the valuation currency)
/// - **Position Token** (wRLP): Valued using Aave index price
///
/// This allows proper NAV calculation for brokers holding LP positions.
///
contract UniswapV4BrokerModule is IValuationModule {
    using FixedPointMathLib for uint256;
    using PositionInfoLibrary for PositionInfo;
    using StateLibrary for IPoolManager;

    /// @notice Parameters for V4 position valuation
    /// @dev Encoded by PrimeBroker._encodeModuleData() and decoded here
    struct VerifyParams {
        uint256 tokenId; // V4 LP NFT ID
        address positionManager; // V4 Position Manager (POSM)
        address oracle; // IRLDOracle (for index price)
        address valuationToken; // Collateral token (waUSDC) - valued 1:1
        address positionToken; // Position token (wRLP) - valued via index price
        address underlyingPool; // Aave pool address
        address underlyingToken; // Underlying asset (USDC)
    }

    struct FeeParams {
        IPoolManager pm;
        PoolId poolId;
        address positionManager;
        int24 tickLower;
        int24 tickUpper;
        bytes32 tokenId;
    }

    /// @notice Returns the value of an LP Position in collateral token terms.
    /// @param data ABI-encoded VerifyParams struct
    /// @return Total value of the position in valuationToken terms
    function getValue(
        bytes calldata data
    ) external view override returns (uint256) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));

        (uint256 amount0, uint256 amount1, address currency0, address currency1) =
            _positionAmounts(params.positionManager, params.tokenId);

        uint256 value = 0;

        // Price currency0
        if (amount0 > 0) {
            value += _priceToken(currency0, amount0, params);
        }

        // Price currency1
        if (amount1 > 0) {
            value += _priceToken(currency1, amount1, params);
        }

        return value;
    }

    function _positionAmounts(address positionManager, uint256 tokenId)
        internal
        view
        returns (uint256 amount0, uint256 amount1, address currency0, address currency1)
    {
        uint128 liquidity = IPositionManager(positionManager).getPositionLiquidity(tokenId);
        if (liquidity == 0) return (0, 0, address(0), address(0));

        (PoolKey memory poolKey, PositionInfo info) = IPositionManager(positionManager).getPoolAndPositionInfo(tokenId);
        int24 tickLower = info.tickLower();
        int24 tickUpper = info.tickUpper();

        IPoolManager pm = IPositionManager(positionManager).poolManager();
        PoolId poolId = poolKey.toId();

        {
            (, int24 currentTick, , ) = pm.getSlot0(poolId);
            (amount0, amount1) = LiquidityAmounts.getAmountsForLiquidity(
                TickMath.getSqrtPriceAtTick(currentTick),
                TickMath.getSqrtPriceAtTick(tickLower),
                TickMath.getSqrtPriceAtTick(tickUpper),
                liquidity
            );
        }

        FeeParams memory feeParams = FeeParams({
            pm: pm,
            poolId: poolId,
            positionManager: positionManager,
            tickLower: tickLower,
            tickUpper: tickUpper,
            tokenId: bytes32(tokenId)
        });
        (uint256 fees0, uint256 fees1) = _uncollectedFees(feeParams);
        amount0 += fees0;
        amount1 += fees1;

        currency0 = Currency.unwrap(poolKey.currency0);
        currency1 = Currency.unwrap(poolKey.currency1);
    }

    function _positionId(
        address positionManager,
        int24 tickLower,
        int24 tickUpper,
        uint256 tokenId
    ) internal pure returns (bytes32) {
        return Position.calculatePositionKey(positionManager, tickLower, tickUpper, bytes32(tokenId));
    }

    function _uncollectedFees(FeeParams memory params) internal view returns (uint256 fees0, uint256 fees1) {
        (uint256 feeGrowthInside0, uint256 feeGrowthInside1) =
            params.pm.getFeeGrowthInside(params.poolId, params.tickLower, params.tickUpper);
        bytes32 positionId =
            Position.calculatePositionKey(params.positionManager, params.tickLower, params.tickUpper, params.tokenId);
        (uint128 posLiquidity, uint256 feeGrowthInside0Last, uint256 feeGrowthInside1Last) =
            params.pm.getPositionInfo(params.poolId, positionId);

        if (posLiquidity > 0) {
            fees0 = uint256(posLiquidity) * (feeGrowthInside0 - feeGrowthInside0Last) / (1 << 128);
            fees1 = uint256(posLiquidity) * (feeGrowthInside1 - feeGrowthInside1Last) / (1 << 128);
        }
    }

    /// @dev Prices a token amount in valuation token terms
    /// @param token The token address
    /// @param amount The token amount
    /// @param params The verification params
    /// @return The value in valuation token terms
    function _priceToken(
        address token,
        uint256 amount,
        VerifyParams memory params
    ) internal view returns (uint256) {
        if (token == params.valuationToken) {
            // Collateral token (e.g. waUSDC): 1:1 value
            return amount;
        } else if (token == params.positionToken) {
            // Position token (wRLP): use index price from Aave oracle
            uint256 indexPrice = IRLDOracle(params.oracle).getIndexPrice(
                params.underlyingPool,
                params.underlyingToken
            );
            return amount.mulWadDown(indexPrice);
        } else {
            // Unknown token - shouldn't happen in RLD pools
            return 0;
        }
    }
}
