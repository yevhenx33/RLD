// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IValuationModule} from "../../../shared/interfaces/IValuationModule.sol";
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
/// @author RLD Protocol
/// @notice Read-only valuation module for Uniswap V4 LP positions.
///
/// @dev ## Overview
///
/// This module calculates the current value of a V4 LP position for PrimeBroker solvency checks.
/// It is a **pure valuation module** - all seize/unwind logic is handled by PrimeBroker directly
/// to ensure proper authorization (Broker owns the NFT, not this module).
///
/// ## Valuation Logic
///
/// 1. Fetch position liquidity and ticks from V4 PositionManager
/// 2. Fetch current pool tick from V4 PoolManager
/// 3. Calculate underlying token amounts (amount0, amount1)
/// 4. Price tokens using Oracle in terms of `valuationToken` (e.g. USDC)
///
/// ## Security Notes
///
/// 1. **Oracle Trust**: Values depend on the oracle price feed
/// 2. **V4 State**: Relies on V4 contracts (PositionManager, PoolManager)
/// 3. **Read-Only**: This module only reads state, cannot modify anything
///
contract UniswapV4BrokerModule is IValuationModule {
    using FixedPointMathLib for uint256;
    using PositionInfoLibrary for PositionInfo;
    using StateLibrary for IPoolManager;

    /// @notice Parameters for V4 position valuation
    /// @dev Encoded by PrimeBroker._encodeModuleData() and decoded here
    struct VerifyParams {
        uint256 tokenId;        // V4 LP NFT ID
        address positionManager;// V4 Position Manager (POSM)
        address oracle;         // ISpotOracle
        address valuationToken; // Target currency (e.g. collateralToken aka USDC)
    }

    /// @notice Returns the value of an LP Position using ORACLE PRICE.
    /// @param data ABI-encoded VerifyParams struct
    /// @return Total value of the position in valuationToken terms
    function getValue(bytes calldata data) external view override returns (uint256) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));
        
        // 1. Get Position Liquidity
        // If 0 liquidity, value is 0
        uint128 liquidity = IPositionManager(params.positionManager).getPositionLiquidity(params.tokenId);
        if (liquidity == 0) return 0;

        // 2. Get Pool Key & Ticks
        (PoolKey memory poolKey, PositionInfo info) = IPositionManager(params.positionManager).getPoolAndPositionInfo(params.tokenId);
        int24 tickLower = info.tickLower();
        int24 tickUpper = info.tickUpper();
        
        // 3. Get Current Tick
        // Note: Using spot tick for valuation. Impermanent loss check is implicit in token amounts.
        IPoolManager pm = IPositionManager(params.positionManager).poolManager();
        (, int24 currentTick, , ) = pm.getSlot0(poolKey.toId());
        
        // 4. Calculate Token Amounts
        (uint256 amount0, uint256 amount1) = LiquidityAmounts.getAmountsForLiquidity(
            TickMath.getSqrtPriceAtTick(currentTick),
            TickMath.getSqrtPriceAtTick(tickLower),
            TickMath.getSqrtPriceAtTick(tickUpper),
            liquidity
        );

        uint256 value = 0;
        
        // 5. Price Token0
        if (amount0 > 0) {
             uint256 price0 = ISpotOracle(params.oracle).getSpotPrice(Currency.unwrap(poolKey.currency0), params.valuationToken);
             value += amount0.mulWadDown(price0);
        }
        
        // 6. Price Token1
        if (amount1 > 0) {
             uint256 price1 = ISpotOracle(params.oracle).getSpotPrice(Currency.unwrap(poolKey.currency1), params.valuationToken);
             value += amount1.mulWadDown(price1);
        }
        
        return value;
    }
}
