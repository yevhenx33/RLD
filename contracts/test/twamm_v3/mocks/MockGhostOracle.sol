// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IGhostOracle} from "../../../src/twamm_v3/interfaces/IGhostOracle.sol";

contract MockGhostOracle is IGhostOracle {
    mapping(bytes32 => uint256) public prices;

    function setPrice(bytes32 marketId, uint256 price) external {
        prices[marketId] = price;
    }

    function getSpotPrice(bytes32 marketId) external view returns (uint256 price) {
        return prices[marketId];
    }
}
