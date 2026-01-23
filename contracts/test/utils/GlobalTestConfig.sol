// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {RLDMarketFactory} from "../../src/rld/core/RLDMarketFactory.sol";

/// @title GlobalTestConfig
/// @notice Centralized configuration for all RLD tests. 
/// Change values here to propagate across the entire test suite.
contract GlobalTestConfig {
    
    // --- Default Risk Parameters ---
    uint64 constant TEST_MIN_COL_RATIO = 150e16;       // 150%
    uint64 constant TEST_MAINTENANCE_MARGIN = 109e16;  // 109%
    uint64 constant TEST_LIQ_CLOSE_FACTOR = 50e16;     // 50%
    
    // --- Default V4 Config ---
    uint24 constant TEST_POOL_FEE = 3000;              // 0.3%
    int24 constant TEST_TICK_SPACING = 60;
    uint32 constant TEST_ORACLE_PERIOD = 3600;         // 1 Hour
    uint32 constant TEST_FUNDING_PERIOD = 30 days;     // 30 Days (Per user request)

    // --- Asset Names ---
    string constant TEST_TOKEN_NAME = "Wrapped RLP Position: aUSDC";
    string constant TEST_TOKEN_SYMBOL = "wRLPaUSDC";

    // --- Liquidation Params (Standard Dutch Auction) ---
    // Packed: [Slope 16b][Max 16b][Base 16b]
    
    uint256 constant LIQ_BASE_DISCOUNT = 0;  // 0% (500 bps)
    uint256 constant LIQ_MAX_DISCOUNT  = 1000; // 10% (2000 bps)
    uint256 constant LIQ_SLOPE         = 100;  // 1.0x (Scaled by 100)

    bytes32 constant TEST_LIQ_PARAMS = bytes32(
        (LIQ_SLOPE << 32) | (LIQ_MAX_DISCOUNT << 16) | LIQ_BASE_DISCOUNT
    );

    function getGlobalDeployParams(
        address _underlyingPool,
        address _underlyingToken,
        address _collateralToken,
        address _curator,
        address _spotOracle,
        address _rateOracle,
        address _liquidationModule
    ) internal pure returns (RLDMarketFactory.DeployParams memory) {
        return RLDMarketFactory.DeployParams({
            underlyingPool: _underlyingPool,
            underlyingToken: _underlyingToken,
            collateralToken: _collateralToken,
            curator: _curator,

            positionTokenName: TEST_TOKEN_NAME,
            positionTokenSymbol: TEST_TOKEN_SYMBOL,

            minColRatio: TEST_MIN_COL_RATIO,
            maintenanceMargin: TEST_MAINTENANCE_MARGIN,
            liquidationCloseFactor: TEST_LIQ_CLOSE_FACTOR,
            liquidationModule: _liquidationModule,
            
            // @notice using Real Params instead of Zero to ensure Liquidation logic triggers.
            liquidationParams: TEST_LIQ_PARAMS, 


            spotOracle: _spotOracle,
            rateOracle: _rateOracle,
            oraclePeriod: TEST_ORACLE_PERIOD,
            
            poolFee: TEST_POOL_FEE,
            tickSpacing: TEST_TICK_SPACING
        });
    }
}
