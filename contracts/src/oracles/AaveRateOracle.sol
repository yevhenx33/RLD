// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20; // UPDATED

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

contract AaveRateOracle {
    // Aave V3 Pool Address (Ethereum Mainnet)
    address public constant POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;

    // USDC Address
    address public constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;

    function getUsdcBorrowRate() external view returns (uint256) {
        IAavePool.ReserveData memory data = IAavePool(POOL).getReserveData(
            USDC
        );
        return uint256(data.currentVariableBorrowRate);
    }
}
