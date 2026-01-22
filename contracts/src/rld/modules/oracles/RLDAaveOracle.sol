// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";

interface IAavePool {
    struct ReserveData {
        uint256 configuration;
        uint128 liquidityIndex;
        uint128 currentLiquidityRate;
        uint128 variableBorrowIndex;
        uint128 currentVariableBorrowRate;
        uint128 currentStableBorrowRate;
        uint40 lastUpdateTimestamp;
        uint16 id;
        address aTokenAddress;
        address stableDebtTokenAddress;
        address variableDebtTokenAddress;
        address interestRateStrategyAddress;
        uint128 accruedToTreasury;
        uint128 unbacked;
        uint128 isolationModeTotalDebt;
    }

    function getReserveData(
        address asset
    ) external view returns (ReserveData memory);
}

/**
 * @title RLDAaveOracle
 * @notice Standardized Aave V3 Rate Oracle for the RLD Protocol.
 * @dev Implements the Index Price formula: P = K * r
 * See RLD Paper Section 2.1 & 2.2
 */
contract RLDAaveOracle is IRLDOracle {
    // --- Constants ---
    
    // RLD Paper Section 2.1: "K=100, a 5% interest rate equals a $5.00 price"
    uint256 public constant K_SCALAR = 100;

    // RLD Paper Section 2.2.2: "Ceiling r_max = 100%"
    // Aave Rates are in RAY (1e27). 100% = 1e27.
    uint256 public constant RATE_CAP = 1e27;

    // Safety Floor: $0.0001 (1e14 in WAD)
    // Prevents division by zero in AMM/Controller logic if rates drop to 0%
    uint256 public constant MIN_PRICE = 1e14;

    /**
     * @notice Returns the RLD Index Price for a specific pool/asset.
     * @param underlyingPool The Aave Pool address.
     * @param underlyingToken The asset address (e.g. USDC).
     * @return indexPrice The standardized price in WAD (18 decimals).
     */
    function getIndexPrice(address underlyingPool, address underlyingToken) external view override returns (uint256 indexPrice) {
        // 1. Fetch Raw Rate (RAY)
        IAavePool.ReserveData memory data = IAavePool(underlyingPool).getReserveData(
            underlyingToken
        );
        uint256 rawRateRay = uint256(data.currentVariableBorrowRate);

        // 2. Apply Safety Caps (Section 2.2.2)
        if (rawRateRay > RATE_CAP) {
            rawRateRay = RATE_CAP;
        }

        // 3. Calculate Index Price (Section 2.1)
        // Formula: Price = (RateRAY * K) / 1e9
        // We divide by 1e9 to convert the final result from RAY (27) to WAD (18).
        uint256 calculatedPrice = (rawRateRay * K_SCALAR) / 1e9;

        // 4. Enforce Minimum Floor
        if (calculatedPrice < MIN_PRICE) {
            indexPrice = MIN_PRICE;
        } else {
            indexPrice = calculatedPrice;
        }
    }
}
