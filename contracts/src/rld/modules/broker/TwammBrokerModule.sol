// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IValuationModule} from "../../../shared/interfaces/IValuationModule.sol";
import {ISpotOracle} from "../../../shared/interfaces/ISpotOracle.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";

import {ITWAMM} from "../../../twamm/ITWAMM.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";

/// @title TWAMM Broker Module
/// @author RLD Protocol
/// @notice Read-only valuation module for TWAMM (Time-Weighted Average Market Maker) orders.
///
/// @dev ## Overview
///
/// This module calculates the current value of a TWAMM order for PrimeBroker solvency checks.
/// It is a **pure valuation module** - all seize/cancel logic is handled by PrimeBroker directly.
///
/// ## How TWAMM Orders Work
///
/// A TWAMM order gradually sells tokens over time:
/// ```
/// User deposits X tokens to sell over duration D
/// Order executes at sellRate = X / D tokens per second
/// At any time, order has:
///   - sellTokensRefund: Unsold tokens remaining
///   - buyTokensOwed: Tokens earned from executed swaps
/// ```
///
/// ## Valuation Formula
///
/// ```
/// totalValue = (sellTokensRefund × sellTokenPrice) + (buyTokensOwed × buyTokenPrice)
/// ```
///
/// Where prices are fetched from the oracle in terms of `underlyingToken`.
///
/// ## Architecture
///
/// ```
/// PrimeBroker                         TwammBrokerModule
///      │                                      │
///      ├─ getNetAccountValue()                │
///      │       │                              │
///      │       ├─ _encodeTwammData()          │
///      │       │                              │
///      │       └──────► getValue(data) ──────►├─ getCancelOrderState()
///      │                                      │       │
///      │                                      │       ▼
///      │                                      ├─ TWAMM Hook
///      │                                      │       │
///      │◄──── totalValue ─────────────────────┤◄──────┘
///      │                                      │
///      ├─ seize() [HANDLED DIRECTLY]          │
///      │   (cancels order, routes tokens)     │
///      │                                      │
///      └──────────────────────────────────────┘
/// ```
///
/// ## Security Notes
///
/// 1. **Oracle Trust**: Values depend on the oracle price feed
/// 2. **Hook Trust**: The hook address comes from verified order info in PrimeBroker
/// 3. **Read-Only**: This module only reads state, cannot modify anything
///
/// ## V1 Limitations
///
/// - Only ONE TWAMM order can be tracked per broker
/// - Pricing is in `underlyingToken` terms
///
contract TwammBrokerModule is IValuationModule {
    using FixedPointMathLib for uint256;

    /* ============================================================================================ */
    /*                                          TYPES                                              */
    /* ============================================================================================ */

    /// @notice Parameters for TWAMM order valuation
    /// @dev Encoded by PrimeBroker._encodeTwammData() and decoded here
    struct VerifyParams {
        /// @dev The TWAMM hook contract address (NOT this module!)
        /// The hook stores order state and executes swaps.
        address hook;
        
        /// @dev The Uniswap V4 PoolKey identifying the pool
        /// Contains: currency0, currency1, fee, tickSpacing, hooks
        PoolKey key;
        
        /// @dev The order identifier within the TWAMM
        /// Contains: owner, expiration, zeroForOne (trade direction)
        ITWAMM.OrderKey orderKey;
        
        /// @dev The price oracle for valuation (ISpotOracle)
        address oracle;
        
        /// @dev The base token for pricing (e.g., collateralToken like aUSDC)
        /// All token values are converted to this denomination
        address valuationToken;
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
    function getValue(bytes calldata data) external view override returns (uint256) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));
        
        // ┌─────────────────────────────────────────────────────────────────┐
        // │ STEP 1: Query TWAMM Hook for Cancel State                       │
        // └─────────────────────────────────────────────────────────────────┘
        // getCancelOrderState() returns what we'd get if we cancelled now:
        // - buyTokensOwed: Tokens earned from TWAMM execution
        // - sellTokensRefund: Unsold tokens remaining in the order
        (uint256 buyTokensOwed, uint256 sellTokensRefund) = ITWAMM(params.hook).getCancelOrderState(
            params.key, 
            params.orderKey
        );
        
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
        // Convert sellTokensRefund to valuationToken value
        // Formula: value = amount × price
        if (sellTokensRefund > 0) {
            uint256 price = ISpotOracle(params.oracle).getSpotPrice(sellToken, params.valuationToken);
            totalValue += sellTokensRefund.mulWadDown(price);
        }
        
        // ┌─────────────────────────────────────────────────────────────────┐
        // │ STEP 4: Value the Earnings (Bought Tokens)                      │
        // └─────────────────────────────────────────────────────────────────┘
        // Convert buyTokensOwed to valuationToken value
        if (buyTokensOwed > 0) {
            uint256 price = ISpotOracle(params.oracle).getSpotPrice(buyToken, params.valuationToken);
            totalValue += buyTokensOwed.mulWadDown(price);
        }
        
        return totalValue;
    }
}
