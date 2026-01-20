// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {Owned} from "solmate/src/auth/Owned.sol";
import {MarketId} from "../interfaces/IRLDCore.sol";

contract WrappedRLP is ERC20, Owned {
    MarketId public marketId;
    address public immutable underlying;

    error MarketIdAlreadySet();
    error NotMarket();

    constructor(address _underlying) ERC20("Wrapped RLP", "wRLP", 18) Owned(msg.sender) {
        underlying = _underlying;
    }

    function setMarketId(MarketId _id) external onlyOwner {
        if (MarketId.unwrap(marketId) != bytes32(0)) revert MarketIdAlreadySet();
        marketId = _id;
    }

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    function burn(address from, uint256 amount) external onlyOwner {
        _burn(from, amount);
    }
}
