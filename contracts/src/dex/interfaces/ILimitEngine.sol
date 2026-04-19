// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IGhostEngine} from "./IGhostEngine.sol";

/// @title ILimitEngine
/// @notice Limit-order spoke with demand-triggered activation and global active pools.
interface ILimitEngine is IGhostEngine {
    struct ActivePool {
        uint256 remainingInput;
        uint256 totalShares;
        uint256 earningsFactor;
        uint256 depletionFactor;
    }

    struct LimitBucket {
        uint256 totalShares;
        bool activated;
        uint256 activationEarningsFactor;
        uint256 activationDepletionFactor;
    }

    struct LimitOrder {
        address owner;
        uint256 shares;
        uint256 triggerPrice;
        bool zeroForOne;
        uint256 earningsFactorLast;
        uint256 depletionFactorAtActivation;
        bool activationSnapshotSet;
    }

    function submitLimitOrder(bytes32 marketId, bool zeroForOne, uint256 triggerPrice, uint256 amountIn)
        external
        returns (bytes32 orderId);

    function claimTokens(bytes32 marketId, bytes32 orderId) external returns (uint256 earningsOut);

    function cancelOrder(bytes32 marketId, bytes32 orderId) external returns (uint256 refund, uint256 earningsOut);

    function getOrderState(bytes32 marketId, bytes32 orderId)
        external
        view
        returns (bool activated, uint256 claimableOut, uint256 refundableIn);
}
