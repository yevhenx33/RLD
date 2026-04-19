// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IGhostEngine} from "../../../src/twamm_v3/interfaces/IGhostEngine.sol";

contract MockRevertingGhostEngine is IGhostEngine {
    error SyncReverted();
    error ApplyReverted();
    error TakeReverted();

    function syncAndFetchGhost(bytes32) external pure returns (uint256, uint256) {
        revert SyncReverted();
    }

    function applyNettingResult(bytes32, uint256, uint256, uint256) external pure {
        revert ApplyReverted();
    }

    function takeGhost(bytes32, bool, uint256, uint256) external pure returns (uint256, uint256) {
        revert TakeReverted();
    }
}
