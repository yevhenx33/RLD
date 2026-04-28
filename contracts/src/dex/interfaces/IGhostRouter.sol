// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";

interface IGhostRouter {
    /// @notice Used strictly by the Spokes (Engines) to push market-direction sell tokens.
    /// @dev zeroForOne=true maps to token0, zeroForOne=false maps to token1.
    function pushMarketFunds(bytes32 marketId, bool zeroForOne, address to, uint256 amount) external;

    /// @notice Initialize a new Sovereign Market over a vanilla V4 pair with an external oracle.
    function initializeMarket(PoolKey calldata vanillaKey, address oracle) external returns (bytes32 marketId);

    /// @notice Initialize a new market that uses Uniswap V4 pool price as oracle source.
    function initializeMarketWithUniswapOracle(PoolKey calldata vanillaKey) external returns (bytes32 marketId);

    /// @notice Set the market oracle to an external oracle implementation.
    function setExternalOracle(bytes32 marketId, address oracle) external;

    /// @notice Set the market oracle to Uniswap V4 pool price.
    function setUniswapOracle(bytes32 marketId) external;

    /// @notice Set the maximum observation heartbeat gap before TWAP history is considered stale.
    function setOracleMaxStaleness(bytes32 marketId, uint32 maxStaleness) external;

    /// @notice Sets who can manage fees for a market (owner can always override).
    function setMarketFeeController(bytes32 marketId, address controller) external;

    /// @notice Updates the taker fee for a market in basis points.
    function setMarketTradingFeeBps(bytes32 marketId, uint16 feeBps) external;

    /// @notice Claims accrued fees for a market/token pair.
    function claimTradingFees(bytes32 marketId, address token, address to, uint256 amount) external;

    /// @notice Returns current taker fee in bps for a market.
    function marketTradingFeeBps(bytes32 marketId) external view returns (uint16);

    /// @notice Returns configured fee controller for a market.
    function marketFeeController(bytes32 marketId) external view returns (address);

    /// @notice Returns accrued trading fees for a market/token pair.
    function accruedTradingFees(bytes32 marketId, address token) external view returns (uint256);

    /// @notice Read spot price for a market (token1 per token0, scaled by 1e18).
    function getSpotPrice(bytes32 marketId) external view returns (uint256 price);

    /// @notice Taker entrypoint for routing swaps optimally through internal Ghost Liquidity then external
    function swap(bytes32 marketId, bool zeroForOne, uint256 amountIn, uint256 amountOutMinimum)
        external
        returns (uint256 amountOut);

    /// @notice Used strictly by the Spokes (Engines) to pull market-direction sell tokens.
    /// @dev zeroForOne=true maps to token0, zeroForOne=false maps to token1.
    function pullMarketFunds(bytes32 marketId, bool zeroForOne, address from, uint256 amount) external;

    /// @notice Allows Spokes to automatically settle depleted streams across V4 safely
    function settleGhost(bytes32 marketId, bool zeroForOne, uint256 amountIn) external returns (uint256 amountOut);

    /// @notice Permissionless router entrypoint for registered engines that expose force settlement.
    function forceSettleEngine(address engine, bytes32 marketId, bool zeroForOne) external;

    /// @notice Permissionless heartbeat to keep the native price accumulator fresh between swaps.
    function pokeOracle(bytes32 marketId) external;

    /// @notice Query the native price accumulator for TWAP computation.
    /// @dev Returns price cumulatives (Σ price×Δt, price in 1e18) at each requested point.
    ///      Price is token1-per-token0, matching getSpotPrice() convention.
    ///      Consumer computes TWAP: (cum[1] - cum[0]) / (secondsAgos[0] - secondsAgos[1]).
    /// @param marketId The sovereign market to query.
    /// @param secondsAgos Array of lookback offsets from block.timestamp (e.g., [1800, 0]).
    /// @return priceCumulatives Array of price cumulatives at each requested point.
    function observe(bytes32 marketId, uint32[] calldata secondsAgos)
        external
        view
        returns (uint256[] memory priceCumulatives);
}
