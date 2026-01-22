// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IBrokerModule} from "../../interfaces/IBrokerModule.sol";
import {ISpotOracle} from "../../interfaces/ISpotOracle.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";

import {ITWAMM} from "v4-twamm-hook/src/ITWAMM.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";

/// @title TWAMM Broker Module
/// @notice Valuates active TWAMM orders and implements Cancel-and-Seize logic.
contract TwammBrokerModule is IBrokerModule {
    using FixedPointMathLib for uint256;
    using SafeTransferLib for ERC20;

    struct VerifyParams {
        address hook; // TWAMM Address
        PoolKey key;
        ITWAMM.OrderKey orderKey;
        address oracle;
        address collateralToken;
        address underlyingToken;
    }

    /// @notice Returns the value of a TWAMM Order.
    function getValue(bytes calldata data) external view returns (uint256) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));
        
        // 1. Get Cancel State (Earnings + Refund)
        // This calculates exactly what we would get if we cancelled right now.
        // It handles expiration correctly (refund = 0, earnings = max) and active orders.
        (uint256 buyTokensOwed, uint256 sellTokensRefund) = ITWAMM(params.hook).getCancelOrderState(params.key, params.orderKey);
        
        uint256 totalValue = 0;
        
        // 2. Identify Tokens
        address sellToken = params.orderKey.zeroForOne 
            ? Currency.unwrap(params.key.currency0) 
            : Currency.unwrap(params.key.currency1);
            
        address buyToken = params.orderKey.zeroForOne 
            ? Currency.unwrap(params.key.currency1) 
            : Currency.unwrap(params.key.currency0);
            
        // 3. Value the Refund (Unsold Principal)
        if (sellTokensRefund > 0) {
            uint256 price = ISpotOracle(params.oracle).getSpotPrice(sellToken, params.underlyingToken);
            totalValue += sellTokensRefund.mulWadDown(price);
        }
        
        // 4. Value the Earnings (Bought Tokens)
        if (buyTokensOwed > 0) {
             uint256 price = ISpotOracle(params.oracle).getSpotPrice(buyToken, params.underlyingToken);
             totalValue += buyTokensOwed.mulWadDown(price);
        }
        
        return totalValue;
    }

    /// @notice Seizes assets by Cancel & Claim.
    /// @notice Seizes assets by Cancel & Claim.
    /// @dev Logic moved to PrimeBroker to handle ownership/authorization. This module is now Read-Only for TWAMM.
    function seize(uint256, address, bytes calldata) external pure returns (uint256) {
        return 0; // Handled by Broker directly
    }
}
