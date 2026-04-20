// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IGhostRouter} from "../../../src/dex/interfaces/IGhostRouter.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";

interface IERC20Like {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

interface IMintableERC20Like {
    function mint(address to, uint256 amount) external;
}

contract MockGhostRouterForEngine is IGhostRouter {
    struct Market {
        address token0;
        address token1;
        uint256 spotPrice;
        uint256 settleOutOverride;
    }

    mapping(bytes32 => Market) public markets;
    mapping(bytes32 => uint16) public marketTradingFeeBps;
    mapping(bytes32 => address) public marketFeeController;
    mapping(bytes32 => mapping(address => uint256)) public accruedTradingFees;

    function setMarket(bytes32 marketId, address token0, address token1) external {
        markets[marketId].token0 = token0;
        markets[marketId].token1 = token1;
    }

    function setSpotPrice(bytes32 marketId, uint256 price) external {
        markets[marketId].spotPrice = price;
    }

    function setSettleOutOverride(bytes32 marketId, uint256 amountOut) external {
        markets[marketId].settleOutOverride = amountOut;
    }

    function pushMarketFunds(bytes32 marketId, bool zeroForOne, address to, uint256 amount) external override {
        Market storage market = markets[marketId];
        address token = zeroForOne ? market.token0 : market.token1;
        IERC20Like(token).transfer(to, amount);
    }

    function initializeMarket(PoolKey calldata, address) external pure override returns (bytes32 marketId) {
        return marketId;
    }

    function initializeMarketWithUniswapOracle(PoolKey calldata) external pure override returns (bytes32 marketId) {
        return marketId;
    }

    function setExternalOracle(bytes32, address) external pure override {}

    function setUniswapOracle(bytes32) external pure override {}

    function setMarketFeeController(bytes32 marketId, address controller) external override {
        marketFeeController[marketId] = controller;
    }

    function setMarketTradingFeeBps(bytes32 marketId, uint16 feeBps) external override {
        marketTradingFeeBps[marketId] = feeBps;
    }

    function claimTradingFees(bytes32 marketId, address token, address to, uint256 amount) external override {
        uint256 accrued = accruedTradingFees[marketId][token];
        if (amount > accrued) amount = accrued;
        if (amount > 0) {
            accruedTradingFees[marketId][token] = accrued - amount;
            IERC20Like(token).transfer(to, amount);
        }
    }

    function getSpotPrice(bytes32 marketId) external view override returns (uint256 price) {
        return markets[marketId].spotPrice;
    }

    function swap(bytes32, bool, uint256, uint256) external pure override returns (uint256 amountOut) {
        return amountOut;
    }

    function pullMarketFunds(bytes32 marketId, bool zeroForOne, address from, uint256 amount) external override {
        Market storage market = markets[marketId];
        address token = zeroForOne ? market.token0 : market.token1;
        IERC20Like(token).transferFrom(from, address(this), amount);
    }

    function settleGhost(bytes32 marketId, bool zeroForOne, uint256 amountIn) external override returns (uint256 amountOut) {
        uint256 overrideOut = markets[marketId].settleOutOverride;
        amountOut = overrideOut == 0 ? amountIn : overrideOut;
        address buyToken = zeroForOne ? markets[marketId].token1 : markets[marketId].token0;
        IMintableERC20Like(buyToken).mint(address(this), amountOut);
    }

    function observe(bytes32, uint32[] calldata secondsAgos) external pure override returns (uint256[] memory priceCumulatives) {
        priceCumulatives = new uint256[](secondsAgos.length);
    }
}
