// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ISpotOracle} from "../../../shared/interfaces/ISpotOracle.sol";

interface IChainlinkFeed {
    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80);
    function decimals() external view returns (uint8);
}

/// @title ChainlinkSpotOracle
/// @notice Returns the price of Collateral in terms of Underlying (using Chainlink Feeds).
/// @dev Expects Collateral/USD and Underlying/USD feeds to derive Cross Rate if direct not available.
///      For simplicity, this example assumes we configure feeds per pair in a mapping.
contract ChainlinkSpotOracle is ISpotOracle {
    
    // Mapping: keccak256(collateral, underlying) -> Feed Address
    mapping(bytes32 => address) public feeds;
    // Mapping: keccak256(collateral, underlying) -> Inverse? (if feed is Underlying/Collateral)
    mapping(bytes32 => bool) public isInverse;

    // Owner only setup (or factory)
    // function setFeed(...) ...

    /// @notice Returns price in WAD (1e18).
    /// @dev NOTE: Production version needs robustness (stale checks, sequencer uptime).
    function getSpotPrice(address collateralToken, address underlyingToken) external view returns (uint256 price) {
        // 1. Get Feed
        bytes32 key = keccak256(abi.encodePacked(collateralToken, underlyingToken));
        address feed = feeds[key];
        
        if (feed == address(0)) {
            // Check inverse
            key = keccak256(abi.encodePacked(underlyingToken, collateralToken));
            feed = feeds[key];
            if (feed == address(0)) revert("Feed Not Found");
            
            // Calc inverse logic if needed
            // For MVP, lets assume we strictly set feeds.
            // Or maybe we use ETH/USD and USDC/USD? 
            // Simplifying: Assume we manually registered a feed that gives Price of Collateral in Underlying.
            // e.g., ETH/USDC feed.
            // 2. Read Feed (Inverse)
            (, int256 answer,,,) = IChainlinkFeed(feed).latestRoundData();
            if (answer <= 0) revert("Invalid Price");
            
            uint8 decimals = IChainlinkFeed(feed).decimals();
            uint256 directPrice;
            
            // Normalize direct price to 1e18 first
            if (decimals < 18) {
                directPrice = uint256(answer) * (10 ** (18 - decimals));
            } else if (decimals > 18) {
                directPrice = uint256(answer) / (10 ** (decimals - 18));
            } else {
                directPrice = uint256(answer);
            }
            
            // 3. Invert Price
            // Price = 1 / DirectPrice. 
            // Since DirectPrice is WAD (1e18), Inverse = 1e36 / DirectPrice
            price = 1e36 / directPrice;
        } else {
            // 2. Read Feed (Direct)
            (, int256 answer,,,) = IChainlinkFeed(feed).latestRoundData();
            if (answer <= 0) revert("Invalid Price");

            uint8 decimals = IChainlinkFeed(feed).decimals();
            
            // 3. Normalize to 1e18
            if (decimals < 18) {
                price = uint256(answer) * (10 ** (18 - decimals));
            } else if (decimals > 18) {
                price = uint256(answer) / (10 ** (decimals - 18));
            } else {
                price = uint256(answer);
            }
        }
    }
}
