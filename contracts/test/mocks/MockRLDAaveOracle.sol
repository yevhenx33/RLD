// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDOracle} from "../../src/shared/interfaces/IRLDOracle.sol";

/**
 * @title MockRLDAaveOracle
 * @notice Mock oracle for testnet with admin-settable rate.
 * @dev Used for local Anvil testing with API-driven rate updates.
 *      For production, use RLDAaveOracle which reads from real Aave V3.
 */
contract MockRLDAaveOracle is IRLDOracle {
    // --- Constants (same as RLDAaveOracle) ---

    /// @notice RLD Paper Section 2.1: "K=100, a 5% interest rate equals a $5.00 price"
    uint256 public constant K_SCALAR = 100;

    /// @notice Safety Floor: $0.0001 (1e14 in WAD)
    uint256 public constant MIN_PRICE = 1e14;

    // --- State ---

    /// @notice Current mock rate in RAY (1e27 = 100%)
    uint256 public mockRateRay;

    /// @notice Admin address that can update the rate
    address public admin;

    /// @notice Last update timestamp
    uint256 public lastUpdate;

    // --- Events ---

    event RateUpdated(uint256 newRateRay, uint256 timestamp);
    event AdminTransferred(address oldAdmin, address newAdmin);

    // --- Constructor ---

    constructor() {
        admin = msg.sender;
        // Default to 5% APY (0.05 * 1e27 = 5e25)
        mockRateRay = 5e25;
        lastUpdate = block.timestamp;
    }

    // --- Admin Functions ---

    /**
     * @notice Set the mock borrow rate.
     * @param newRateRay New rate in RAY format (1e27 = 100%)
     * @dev Called by sync daemon when API has new rate
     */
    function setRate(uint256 newRateRay) external {
        require(msg.sender == admin, "MockOracle: not admin");
        mockRateRay = newRateRay;
        lastUpdate = block.timestamp;
        emit RateUpdated(newRateRay, block.timestamp);
    }

    /**
     * @notice Transfer admin rights.
     * @param newAdmin New admin address
     */
    function transferAdmin(address newAdmin) external {
        require(msg.sender == admin, "MockOracle: not admin");
        require(newAdmin != address(0), "MockOracle: zero address");
        emit AdminTransferred(admin, newAdmin);
        admin = newAdmin;
    }

    // --- IRLDOracle Implementation ---

    /**
     * @notice Returns the RLD Index Price based on mock rate.
     * @dev Same formula as RLDAaveOracle: Price = (Rate * K) / 1e9
     * @param underlyingPool Ignored (for interface compatibility)
     * @param underlyingToken Ignored (for interface compatibility)
     * @return indexPrice The standardized price in WAD (18 decimals)
     */
    function getIndexPrice(
        address underlyingPool,
        address underlyingToken
    ) external view override returns (uint256 indexPrice) {
        // Silence unused variable warnings
        underlyingPool;
        underlyingToken;

        // Calculate Index Price (Section 2.1)
        // Formula: Price = (RateRAY * K) / 1e9
        uint256 calculatedPrice = (mockRateRay * K_SCALAR) / 1e9;

        // Enforce Minimum Floor
        if (calculatedPrice < MIN_PRICE) {
            indexPrice = MIN_PRICE;
        } else {
            indexPrice = calculatedPrice;
        }
    }

    // --- View Helpers ---

    /**
     * @notice Get current rate as APY percentage (for debugging).
     * @return apyPercent Rate as percentage (e.g., 500 = 5.00%)
     */
    function getRatePercent() external view returns (uint256 apyPercent) {
        // Convert RAY to percentage with 2 decimal places
        // 5e25 RAY = 5% = 500 (in basis points * 100)
        return mockRateRay / 1e23;
    }
}
