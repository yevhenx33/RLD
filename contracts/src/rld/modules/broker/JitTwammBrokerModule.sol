// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IValuationModule} from "../../../shared/interfaces/IValuationModule.sol";
import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";
import {FixedPointMathLib} from "../../../shared/utils/FixedPointMathLib.sol";

import {IJITTWAMM} from "../../../twamm/IJITTWAMM.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";

/// @title JITTWAMM Broker Module
/// @author RLD Protocol
/// @notice Read-only valuation module for JITTWAMM (Time-Weighted Average Market Maker) orders.
///
/// @dev ## Overview
///
/// This module calculates the current value of a JITTWAMM order for PrimeBroker solvency checks.
/// It is a **pure valuation module** - all seize/cancel logic is handled by PrimeBroker directly.
///
/// ## How JITTWAMM Orders Work
///
/// A JITTWAMM order gradually sells tokens over time:
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
/// PrimeBroker                         JitTwammBrokerModule
///      │                                      │
///      ├─ getNetAccountValue()                │
///      │       │                              │
///      │       ├─ _encodeTwammData()          │
///      │       │                              │
///      │       └──────► getValue(data) ──────►├─ getCancelOrderState()
///      │                                      │       │
///      │                                      │       ▼
///      │                                      ├─ JITTWAMM Hook
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
/// - Only ONE JITTWAMM order can be tracked per broker
/// - Pricing is in `underlyingToken` terms
///
contract JitTwammBrokerModule is IValuationModule {
    using FixedPointMathLib for uint256;

    /* ============================================================================================ */
    /*                                          TYPES                                              */
    /* ============================================================================================ */

    /// @notice Parameters for JITTWAMM order valuation
    /// @dev Encoded by PrimeBroker._encodeTwammData() and decoded here
    struct VerifyParams {
        /// @dev The JITTWAMM hook contract address (NOT this module!)
        address hook;
        
        /// @dev The Uniswap V4 PoolKey identifying the pool
        PoolKey key;
        
        /// @dev The order identifier within the JITTWAMM
        IJITTWAMM.OrderKey orderKey;
        
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

    /// @notice Calculates the current value of a JITTWAMM order
    /// @dev Called by PrimeBroker.getNetAccountValue() during solvency checks
    ///
    /// ## Process
    ///
    /// 1. Query JITTWAMM hook for cancel state (what we'd get if cancelled now)
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
        // │ STEP 1: Query JITTWAMM Hook for Cancel State                       │
        // └─────────────────────────────────────────────────────────────────┘
        // getCancelOrderState() returns what we'd get if we cancelled now:
        // - buyTokensOwed: Tokens earned from JITTWAMM execution
        // - sellTokensRefund: Unsold tokens remaining in the order
        (uint256 buyTokensOwed, uint256 sellTokensRefund) = IJITTWAMM(params.hook).getCancelOrderState(
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
