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
}
