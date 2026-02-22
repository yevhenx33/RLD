// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    IValuationModule
} from "../../../shared/interfaces/IValuationModule.sol";
import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";
import {FixedPointMathLib} from "../../../shared/utils/FixedPointMathLib.sol";

import {ITWAMM} from "../../../twamm/ITWAMM.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";

/// @title  TWAMM Broker Module (DEPRECATED)
/// @author RLD Protocol
///
/// @notice ⚠️  DEPRECATED — use `JitTwammBrokerModule` instead.
///
/// @dev    This module implements a **two-term** valuation formula:
///
///             totalValue = sellRefund × sellPrice + buyOwed × buyPrice
///
///         It does NOT account for uncleared ghost balances, which means NAV
///         is underestimated between clears.  This causes false liquidation
///         triggers when ghost holds significant value.
///
///         `JitTwammBrokerModule` supersedes this module with a **three-term**
///         formula that includes discounted ghost:
///
///             totalValue = sellRefund × sellPrice
///                        + buyOwed   × buyPrice
///                        + ghostShare × sellPrice × (1 − auctionDiscount)
///
///         See `JitTwammBrokerModule.sol` for full documentation.
///
/// @custom:deprecated Replaced by JitTwammBrokerModule with ghost-aware NAV.
contract TwammBrokerModule is IValuationModule {
    using FixedPointMathLib for uint256;

    /* ============================================================================================ */
    /*                                          TYPES                                              */
    /* ============================================================================================ */

    /// @notice Parameters for TWAMM order valuation
    /// @dev Encoded by PrimeBroker._encodeTwammData() and decoded here
    struct VerifyParams {
        /// @dev The TWAMM hook contract address (NOT this module!)
        address hook;
        /// @dev The Uniswap V4 PoolKey identifying the pool
        PoolKey key;
        /// @dev The order identifier within the TWAMM
        ITWAMM.OrderKey orderKey;
        /// @dev The RLD oracle for valuation (IRLDOracle for getIndexPrice)
        address oracle;
        /// @dev The collateral token (e.g., waUSDC) - valued 1:1
        address valuationToken;
        /// @dev The position token (e.g., wRLP) - valued via index price
        address positionToken;
        /// @dev Aave pool address for index price lookup
        address underlyingPool;
        /// @dev Underlying asset address (e.g., USDC)
        address underlyingToken;
    }

    /* ============================================================================================ */
    /*                                     VALUATION LOGIC                                         */
    /* ============================================================================================ */

    /// @notice Calculates the current value of a TWAMM order
    /// @dev Called by PrimeBroker.getNetAccountValue() during solvency checks
    ///
    /// ## Process
    ///
    /// 1. Query TWAMM hook for cancel state (what we'd get if cancelled now)
    /// 2. Identify sell/buy tokens based on order direction
    /// 3. Price both token amounts using oracle
    /// 4. Return sum as total value
    ///
    /// ## Edge Cases
    ///
    /// - **Expired Order**: refund = 0, earnings = max earned
    /// - **Fresh Order**: refund ≈ principal, earnings ≈ 0
    /// - **Empty Order**: Returns 0
    ///
    /// @param data ABI-encoded VerifyParams struct
    /// @return Total value of the order in valuationToken terms (usually collateralToken)
    function getValue(
        bytes calldata data
    ) external view override returns (uint256) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ STEP 1: Query TWAMM Hook for Cancel State                       │
        // └─────────────────────────────────────────────────────────────────┘
        // getCancelOrderState() returns what we'd get if we cancelled now:
        // - buyTokensOwed: Tokens earned from TWAMM execution
        // - sellTokensRefund: Unsold tokens remaining in the order
        (uint256 buyTokensOwed, uint256 sellTokensRefund) = ITWAMM(params.hook)
            .getCancelOrderState(params.key, params.orderKey);

        // Early return if order is empty
        if (buyTokensOwed == 0 && sellTokensRefund == 0) {
            return 0;
        }

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ STEP 2: Identify Sell and Buy Tokens                            │
        // └─────────────────────────────────────────────────────────────────┘
        // zeroForOne determines trade direction:
        // - true: selling token0 → buying token1
        // - false: selling token1 → buying token0
        address sellToken = params.orderKey.zeroForOne
            ? Currency.unwrap(params.key.currency0)
            : Currency.unwrap(params.key.currency1);

        address buyToken = params.orderKey.zeroForOne
            ? Currency.unwrap(params.key.currency1)
            : Currency.unwrap(params.key.currency0);

        uint256 totalValue = 0;

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ STEP 3: Value the Refund (Unsold Principal)                     │
        // └─────────────────────────────────────────────────────────────────┘
        if (sellTokensRefund > 0) {
            totalValue += _priceToken(sellToken, sellTokensRefund, params);
        }

        // ┌─────────────────────────────────────────────────────────────────┐
        // │ STEP 4: Value the Earnings (Bought Tokens)                      │
        // └─────────────────────────────────────────────────────────────────┘
        if (buyTokensOwed > 0) {
            totalValue += _priceToken(buyToken, buyTokensOwed, params);
        }

        return totalValue;
    }

    /// @dev Prices a token amount in valuation token terms
    /// @param token The token address
    /// @param amount The token amount
    /// @param params The verification params
    /// @return The value in valuation token terms
    function _priceToken(
        address token,
        uint256 amount,
        VerifyParams memory params
    ) internal view returns (uint256) {
        if (token == params.valuationToken) {
            // Collateral token: 1:1 value
            return amount;
        } else if (token == params.positionToken) {
            // Position token: use index price from Aave oracle
            uint256 indexPrice = IRLDOracle(params.oracle).getIndexPrice(
                params.underlyingPool,
                params.underlyingToken
            );
            return amount.mulWadDown(indexPrice);
        } else {
            // Unknown token - shouldn't happen in RLD pools
            return 0;
        }
    }
}
