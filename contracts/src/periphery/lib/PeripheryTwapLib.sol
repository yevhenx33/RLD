// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PrimeBroker} from "../../rld/broker/PrimeBroker.sol";
import {ITwapEngine} from "../../dex/interfaces/ITwapEngine.sol";

library PeripheryTwapLib {
    function closeActiveOrder(PrimeBroker pb, address twapEngine) internal {
        (bytes32 trackedMarketId, bytes32 orderId) = pb.activeTwammOrder();
        if (orderId == bytes32(0)) return;

        (, , , , uint256 expiration, ) = ITwapEngine(twapEngine).streamOrders(
            trackedMarketId,
            orderId
        );
        if (block.timestamp >= expiration) {
            pb.claimExpiredTwammOrder();
        } else {
            pb.cancelTwammOrder();
        }
    }
}
