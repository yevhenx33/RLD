// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    IValuationModule
} from "../../../shared/interfaces/IValuationModule.sol";
import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";
import {FixedPointMathLib} from "../../../shared/utils/FixedPointMathLib.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

import {ITwapEngine} from "../../../dex/interfaces/ITwapEngine.sol";

/// @title  TWAP Broker Valuation Module
/// @author RLD Protocol
/// @notice Stateless, read-only module that computes the Net Account Value
///         (NAV) contribution of a single TWAP order held by a PrimeBroker.
contract TWAPBrokerModule is IValuationModule {
    using FixedPointMathLib for uint256;

    uint256 public constant DISCOUNT_RATE_PRECISION = 1e12;

    struct VerifyParams {
        address twapEngine;
        bytes32 marketId;
        bytes32 orderId;
        address oracle;
        address valuationToken;
        address positionToken;
        address underlyingPool;
        address underlyingToken;
    }

    function getValue(
        bytes calldata data
    ) external view override returns (uint256) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));

        (uint256 buyTokensOwed, uint256 sellTokensRefund) = ITwapEngine(params.twapEngine)
            .getCancelOrderStateExact(params.marketId, params.orderId);

        (, , , , , bool zeroForOne) = ITwapEngine(params.twapEngine).streamOrders(
            params.marketId,
            params.orderId
        );

        (address c0, address c1) = params.positionToken < params.valuationToken
            ? (params.positionToken, params.valuationToken)
            : (params.valuationToken, params.positionToken);

        address sellToken = zeroForOne ? c0 : c1;
        address buyToken = zeroForOne ? c1 : c0;

        uint256 totalValue = 0;

        if (sellTokensRefund > 0) {
            totalValue += _priceToken(sellToken, sellTokensRefund, params);
        }

        if (buyTokensOwed > 0) {
            totalValue += _priceToken(buyToken, buyTokensOwed, params);
        }

        totalValue += _ghostValue(params, sellToken, zeroForOne);

        return totalValue;
    }

    function _ghostValue(
        VerifyParams memory params,
        address sellToken,
        bool zeroForOne
    ) internal view returns (uint256) {
        (uint256 accrued0, uint256 accrued1, , uint256 lastClearTime, ) = ITwapEngine(
            params.twapEngine
        ).states(params.marketId);

        uint256 totalGhost = zeroForOne ? accrued0 : accrued1;
        if (totalGhost == 0) return 0;

        (, uint256 orderSellRate, , , , ) = ITwapEngine(params.twapEngine).streamOrders(
            params.marketId,
            params.orderId
        );
        if (orderSellRate == 0) return 0;

        (uint256 streamSellRate, ) = ITwapEngine(params.twapEngine).streamPools(
            params.marketId,
            zeroForOne
        );
        if (streamSellRate == 0) return 0;

        uint256 ghostShare = (totalGhost * orderSellRate) / streamSellRate;
        if (ghostShare == 0) return 0;

        uint256 discountBps;
        if (block.timestamp <= lastClearTime) {
            discountBps = 0;
        } else {
            uint256 timeSinceClear = block.timestamp - lastClearTime;
            uint256 discountRateScaled = ITwapEngine(params.twapEngine).discountRateScaled();
            uint256 maxDiscount = ITwapEngine(params.twapEngine).maxDiscountBps();
            discountBps = Math.min((timeSinceClear * discountRateScaled) / DISCOUNT_RATE_PRECISION, maxDiscount);
        }

        uint256 discountedGhost = (ghostShare * (10000 - discountBps)) / 10000;

        return _priceToken(sellToken, discountedGhost, params);
    }

    function _priceToken(
        address token,
        uint256 amount,
        VerifyParams memory params
    ) internal view returns (uint256) {
        if (token == params.valuationToken) {
            return amount;
        } else if (token == params.positionToken) {
            uint256 indexPrice = IRLDOracle(params.oracle).getIndexPrice(
                params.underlyingPool,
                params.underlyingToken
            );
            return amount.mulWadDown(indexPrice);
        } else {
            return 0;
        }
    }
}
