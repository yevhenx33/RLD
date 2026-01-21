// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Clones} from "openzeppelin-v5/contracts/proxy/Clones.sol";
import {PrimeBroker} from "./PrimeBroker.sol";
import {MarketId} from "../interfaces/IRLDCore.sol";

/// @title Prime Broker Factory
/// @notice Deploys and tracks verified PrimeBroker instances.
/// @dev Binded to a specific MarketId to enable Thin Broker pattern.
contract PrimeBrokerFactory {
    using Clones for address;

    address public immutable IMPLEMENTATION;
    MarketId public immutable MARKET_ID;

    mapping(address => bool) public isBroker;

    event BrokerCreated(address indexed broker, address indexed owner);

    constructor(
        address implementation,
        MarketId marketId
    ) {
        IMPLEMENTATION = implementation;
        MARKET_ID = marketId;
    }

    function createBroker() external returns (address broker) {
        broker = IMPLEMENTATION.clone();
        
        // Initialize with Market ID + Owner
        PrimeBroker(payable(broker)).initialize(
            msg.sender,
            MARKET_ID
        );
        
        isBroker[broker] = true;
        emit BrokerCreated(broker, msg.sender);
    }
}
