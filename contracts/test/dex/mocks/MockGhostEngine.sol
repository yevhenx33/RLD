// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IGhostEngine} from "../../../src/dex/interfaces/IGhostEngine.sol";

contract MockGhostEngine is IGhostEngine {
    error ApplyReverted();

    uint256 public ghost0;
    uint256 public ghost1;

    uint256 public lastNettingSpotPrice;
    uint256 public lastTakeSpotPrice;
    uint256 public lastTakeAmountIn;

    bool public consumeAllInput;
    bool public revertOnApply;
    uint256 public scriptedFilledOut;
    uint256 public scriptedInputConsumed;

    function setGhost(uint256 g0, uint256 g1) external {
        ghost0 = g0;
        ghost1 = g1;
    }

    function setTakeBehavior(bool _consumeAllInput, uint256 _scriptedFilledOut, uint256 _scriptedInputConsumed)
        external
    {
        consumeAllInput = _consumeAllInput;
        scriptedFilledOut = _scriptedFilledOut;
        scriptedInputConsumed = _scriptedInputConsumed;
    }

    function setRevertOnApply(bool _revertOnApply) external {
        revertOnApply = _revertOnApply;
    }

    function syncAndFetchGhost(bytes32) external view returns (uint256 outGhost0, uint256 outGhost1) {
        return (ghost0, ghost1);
    }

    function applyNettingResult(bytes32, uint256 consumed0, uint256 consumed1, uint256 spotPrice) external {
        if (revertOnApply) revert ApplyReverted();
        lastNettingSpotPrice = spotPrice;
        ghost0 -= consumed0;
        ghost1 -= consumed1;
    }

    function takeGhost(bytes32, bool zeroForOne, uint256 amountIn, uint256 spotPrice)
        external
        returns (uint256 filledOut, uint256 inputConsumed)
    {
        lastTakeSpotPrice = spotPrice;
        lastTakeAmountIn = amountIn;

        if (consumeAllInput) {
            inputConsumed = amountIn;
            filledOut = amountIn;
        } else {
            filledOut = scriptedFilledOut;
            inputConsumed = scriptedInputConsumed > amountIn ? amountIn : scriptedInputConsumed;
        }

        uint256 available = zeroForOne ? ghost1 : ghost0;
        if (filledOut > available) filledOut = available;
        if (filledOut == 0) return (0, 0);

        if (inputConsumed == 0) {
            inputConsumed = amountIn;
        }

        if (zeroForOne) {
            ghost1 -= filledOut;
        } else {
            ghost0 -= filledOut;
        }
    }
}
