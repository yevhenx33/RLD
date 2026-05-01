// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";

interface IAavePoolLike {
    function supply(
        address asset,
        uint256 amount,
        address onBehalfOf,
        uint16 referralCode
    ) external;
}

interface IWrappedATokenLike {
    function wrap(uint256 aTokenAmount) external returns (uint256 shares);
}

/// @notice BrokerRouter deposit adapter for underlying -> Aave aToken -> wrapped aToken.
/// @dev BrokerRouter transfers `underlying` here before calling `deposit`.
contract AaveWrappedDepositAdapter {
    using SafeTransferLib for ERC20;

    ERC20 public immutable underlying;
    ERC20 public immutable aToken;
    ERC20 public immutable wrappedToken;
    IAavePoolLike public immutable aavePool;

    constructor(
        address underlying_,
        address aToken_,
        address wrappedToken_,
        address aavePool_
    ) {
        require(underlying_ != address(0), "Invalid underlying");
        require(aToken_ != address(0), "Invalid aToken");
        require(wrappedToken_ != address(0), "Invalid wrapped");
        require(aavePool_ != address(0), "Invalid pool");

        underlying = ERC20(underlying_);
        aToken = ERC20(aToken_);
        wrappedToken = ERC20(wrappedToken_);
        aavePool = IAavePoolLike(aavePool_);
    }

    function deposit(
        uint256 amount,
        address receiver
    ) external returns (uint256 collateralAmount) {
        require(amount > 0, "amount=0");
        require(receiver != address(0), "receiver=0");

        uint256 aBefore = aToken.balanceOf(address(this));
        underlying.safeApprove(address(aavePool), amount);
        aavePool.supply(address(underlying), amount, address(this), 0);
        uint256 aReceived = aToken.balanceOf(address(this)) - aBefore;

        uint256 wrappedBefore = wrappedToken.balanceOf(address(this));
        aToken.safeApprove(address(wrappedToken), aReceived);
        IWrappedATokenLike(address(wrappedToken)).wrap(aReceived);
        collateralAmount =
            wrappedToken.balanceOf(address(this)) -
            wrappedBefore;

        wrappedToken.safeTransfer(receiver, collateralAmount);
    }
}
