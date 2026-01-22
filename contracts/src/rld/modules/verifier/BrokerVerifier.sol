// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IBrokerVerifier} from "../../../shared/interfaces/IBrokerVerifier.sol";

interface IPrimeBrokerFactory {
    function isBroker(address account) external view returns (bool);
}

/// @title Broker Verifier
/// @notice Immutable Registry that trusts the PrimeBrokerFactory.
contract BrokerVerifier is IBrokerVerifier {
    address public immutable FACTORY;

    constructor(address factory) {
        FACTORY = factory;
    }

    function isValidBroker(address account) external view override returns (bool) {
        return IPrimeBrokerFactory(FACTORY).isBroker(account);
    }
}
