// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";

interface IGhostRouter {
    /// @notice Used strictly by the Spokes (Engines) to command the vault to push funds to users
    function pushMarketFunds(bytes32 marketId, bool zeroForOne, address to, uint256 amount) external;

    /// @notice Initialize a new Sovereign Market over a vanilla V4 pair with a dedicated oracle
    function initializeMarket(
        PoolKey calldata vanillaKey,
        address oracle
    ) external returns (bytes32 marketId);

    /// @notice Taker entrypoint for routing swaps optimally through internal Ghost Liquidity then external
    function swap(
        bytes32 marketId,
        bool zeroForOne,
        uint256 amountIn,
        uint256 amountOutMinimum
    ) external returns (uint256 amountOut);

    /// @notice Used strictly by the Spokes (Engines) to command the vault to pull user deposits
    function pullMarketFunds(bytes32 marketId, bool zeroForOne, address from, uint256 amount) external;

    /// @notice Allows Spokes to automatically settle depleted streams across V4 safely
    function settleGhost(bytes32 marketId, bool zeroForOne, uint256 amountIn) external returns (uint256 amountOut);
}
