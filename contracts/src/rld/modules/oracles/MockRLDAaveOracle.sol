// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../../shared/interfaces/ISpotOracle.sol";
import {Owned} from "solmate/src/auth/Owned.sol";

/// @title MockRLDAaveOracle
/// @notice Simulation oracle with admin-controlled Aave-like borrow rate (RAY).
/// @dev Keeps the legacy `setRate` + `RateUpdated` interface used by daemons/indexer.
contract MockRLDAaveOracle is IRLDOracle, ISpotOracle, Owned {
    uint256 public constant K_SCALAR = 100;
    uint256 public constant MIN_PRICE = 1e14;

    /// @notice Mock borrow rate in ray (1e27).
    uint256 public mockRateRay;

    /// @notice Emitted whenever admin updates the mock rate.
    /// @param newRateRay New mock rate in ray.
    /// @param timestamp Block timestamp of the update.
    event RateUpdated(uint256 newRateRay, uint256 timestamp);

    constructor() Owned(msg.sender) {
        // Default 5% APY in ray terms.
        mockRateRay = 5e25;
    }

    /// @notice Admin setter for the mock variable borrow rate.
    function setRate(uint256 newRateRay) external onlyOwner {
        mockRateRay = newRateRay;
        emit RateUpdated(newRateRay, block.timestamp);
    }

    /// @inheritdoc IRLDOracle
    function getIndexPrice(address, address) external view override returns (uint256 indexPrice) {
        indexPrice = _rateRayToPriceWad(mockRateRay);
    }

    /// @inheritdoc ISpotOracle
    /// @dev For simulation, spot oracle mirrors the same controlled price.
    function getSpotPrice(address, address) external view override returns (uint256 price) {
        price = _rateRayToPriceWad(mockRateRay);
    }

    function _rateRayToPriceWad(uint256 rateRay) internal pure returns (uint256 priceWad) {
        uint256 calculatedPrice = (rateRay * K_SCALAR) / 1e9;
        return calculatedPrice < MIN_PRICE ? MIN_PRICE : calculatedPrice;
    }
}
