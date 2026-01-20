// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "./IRLDCore.sol"; // Use relative path

interface IRLDMarketFactory {
    /// @notice Deploys a new RLD Market with all necessary components.
    /// @param underlyingPool The external lending pool (e.g., Aave Pool).
    /// @param underlyingToken The asset to track.
    /// @param collateralToken The asset used as collateral.
    /// @return marketId The ID of the created market.
    /// @return oracle The deployed Rate Oracle address.
    /// @return spotOracle The deployed Spot Oracle address.
    /// @return defaultOracle The deployed Default Oracle address.
    /// @return poolId The deployed Uniswap V4 Pool ID.
    function deployMarket(
        address underlyingPool,
        address underlyingToken,
        address collateralToken,
        IRLDCore.MarketType marketType,

        uint64 minColRatio,
        uint64 maintenanceMargin,
        address liquidationModule,
        bytes32 liquidationParams
    ) external returns (MarketId marketId, address oracle, address spotOracle, address defaultOracle, bytes32 poolId);

    function deployMarketV4(
        address underlyingPool,
        address underlyingToken,
        address collateralToken,
        IRLDCore.MarketType marketType,

        uint64 minColRatio,
        uint64 maintenanceMargin,
        address liquidationModule,
        bytes32 liquidationParams,
        uint160 initSqrtPrice,
        uint32 oraclePeriod
    ) external returns (MarketId marketId, address oracle, address spotOracle, address defaultOracle, bytes32 poolId);

    /// @notice Deploys a Synthetic Bond Vault for an existing market.
    /// @param marketId The market to bond against.
    /// @return vault The address of the deployed ERC-4626 Vault.
    function deployBondVault(MarketId marketId) external returns (address vault);
}
