// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {MarketId} from "./IRLDCore.sol";

interface IRLDHook {
    function beforeModifyPosition(
        MarketId id, 
        address sender, 
        int256 deltaCollateral, 
        int256 deltaDebt
    ) external;
}
