// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";

interface IGhostOracle {
    /// @notice Fetches the spot price for a given PoolKey in Q64.96 or standard precision format.
    /// @dev For the mock, we can return a hardcoded 1:1 price or configurable.
    function getSpotPrice(PoolKey calldata key) external view returns (uint256 price);
}
