// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";

/// @notice BrokerRouter deposit adapter for markets whose underlying is already the collateral token.
/// @dev BrokerRouter transfers `token` here before calling `deposit`.
contract DirectDepositAdapter {
    using SafeTransferLib for ERC20;

    ERC20 public immutable token;

    constructor(address token_) {
        require(token_ != address(0), "Invalid token");
        token = ERC20(token_);
    }

    function deposit(
        uint256 amount,
        address receiver
    ) external returns (uint256 collateralAmount) {
        require(amount > 0, "amount=0");
        require(receiver != address(0), "receiver=0");

        token.safeTransfer(receiver, amount);
        return amount;
    }
}
