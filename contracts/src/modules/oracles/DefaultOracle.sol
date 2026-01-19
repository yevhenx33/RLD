// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IDefaultOracle} from "../../interfaces/IDefaultOracle.sol";

/// @title DefaultOracle
/// @notice Detects if an underlying lending protocol is in a "Default" state.
/// @dev "Default" = High Utilization (>99%) AND High Rates (indicating liquidity crisis).
contract DefaultOracle is IDefaultOracle {
    
    uint256 public constant UTILIZATION_THRESHOLD = 99e16; // 99%
    uint256 public constant RATE_THRESHOLD = 80e16; // 80% APY
    
    // Safety buffer: Protocol must be failing for X time? 
    // Or instant? Instant is dangerous (manipulation).
    // Better: Rate Oracle already uses TWAP/TWAMM. We should use Spot Util?
    
    function isDefaulted(address underlyingPool, address underlyingToken) external view returns (bool) {
        // Placeholder for Aave interaction.
        // Real implementation requires casting pool to IAavePool and fetching data.
        
        // Pseudo-code logic:
        // (,,,,, uint256 utilization, uint256 borrowRate,,) = IAavePool(underlyingPool).getReserveData(underlyingToken);
        // return (utilization > UTILIZATION_THRESHOLD && borrowRate > RATE_THRESHOLD);
        
        return false; // Safe default for now.
    }
}
