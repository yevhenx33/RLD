// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {GhostPoolState, FillResult} from "../types/GhostTypes.sol";
import {PriceMath} from "./PriceMath.sol";

/// @title JitFillLib — Layer 2 Fill Engine
/// @notice Computes fills from ghost pool at TWAP, pro-rata ghost consumption.
///         Direct port of Python jit_fill.py.
library JitFillLib {
    // ─── Compute Fill ─────────────────────────────────────────────────

    /// @notice Given a taker swap, compute how much ghost can fill at TWAP
    /// @param amountSpecified The swap amount (negative = exact input)
    /// @param zeroForOne The swap direction
    /// @param twap The current TWAP sqrtPriceX96
    /// @param availableGhost Ghost available on the output side
    /// @return fillOut Amount going out from ghost
    /// @return fillIn Amount coming in from taker
    function computeFill(int256 amountSpecified, bool zeroForOne, uint160 twap, uint256 availableGhost)
        internal
        pure
        returns (uint256 fillOut, uint256 fillIn)
    {
        uint256 takerAmount = amountSpecified < 0 ? uint256(-amountSpecified) : uint256(amountSpecified);

        if (amountSpecified < 0) {
            // Exact input
            fillIn = takerAmount;
            fillOut = PriceMath.convertAtPrice(fillIn, twap, zeroForOne);
            if (fillOut > availableGhost) {
                fillOut = availableGhost;
                fillIn = PriceMath.convertAtPrice(fillOut, twap, !zeroForOne);
            }
        } else {
            // Exact output
            fillOut = takerAmount < availableGhost ? takerAmount : availableGhost;
            fillIn = PriceMath.convertAtPrice(fillOut, twap, !zeroForOne);
        }
    }

    // ─── Pro-Rata Ghost Consumption ───────────────────────────────────

    /// @notice Consume ghost pro-rata from stream and limit
    /// @return streamConsumed The amount consumed from stream ghost
    /// @return limitConsumed The amount consumed from limit ghost
    function consumeGhostProRata(GhostPoolState storage pool, bool consumingGhostT0, uint256 fillOut)
        internal
        returns (uint256 streamConsumed, uint256 limitConsumed)
    {
        if (consumingGhostT0) {
            uint256 total = pool.streamGhostT0 + pool.limitGhostT0;
            if (total == 0) return (0, 0);
            streamConsumed = (fillOut * pool.streamGhostT0) / total;
            limitConsumed = (fillOut * pool.limitGhostT0) / total;
            pool.streamGhostT0 -= streamConsumed;
            pool.limitGhostT0 -= limitConsumed;
        } else {
            uint256 total = pool.streamGhostT1 + pool.limitGhostT1;
            if (total == 0) return (0, 0);
            streamConsumed = (fillOut * pool.streamGhostT1) / total;
            limitConsumed = (fillOut * pool.limitGhostT1) / total;
            pool.streamGhostT1 -= streamConsumed;
            pool.limitGhostT1 -= limitConsumed;
        }
    }
}
