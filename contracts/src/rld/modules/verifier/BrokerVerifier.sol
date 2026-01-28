// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IBrokerVerifier} from "../../../shared/interfaces/IBrokerVerifier.sol";

/// @notice Minimal interface for querying broker validity from the factory.
interface IPrimeBrokerFactory {
    /// @notice Checks if an address is a valid broker deployed by this factory.
    /// @param account The address to check
    /// @return True if the address is a valid broker
    function isBroker(address account) external view returns (bool);
}

/// @title Broker Verifier
/// @author RLD Protocol
/// @notice Immutable adapter that verifies broker authenticity by delegating to PrimeBrokerFactory.
/// @dev This contract serves as a trust bridge between RLDCore and PrimeBrokerFactory.
///
/// ## Purpose
///
/// RLDCore needs to verify that an address is a legitimate PrimeBroker before:
/// 1. Allowing it to have a position (solvency checks)
/// 2. Allowing it to be liquidated
/// 3. Trusting its reported `getNetAccountValue()`
///
/// Rather than coupling RLDCore directly to PrimeBrokerFactory, this verifier provides
/// an abstraction layer that could support different broker types in the future.
///
/// ## Architecture
///
/// ```
/// RLDCore ──verify broker──▶ BrokerVerifier ──delegate──▶ PrimeBrokerFactory
///                                  │                              │
///                                  │                              ▼
///                                  │                    isBroker(addr) → bool
///                                  │
///                                  └── isValidBroker(addr) → bool
/// ```
///
/// ## Security Model
///
/// - **Immutable Factory**: Once deployed, the factory address cannot be changed
/// - **No Admin Functions**: The contract is fully immutable after deployment
/// - **Single Responsibility**: Only verifies broker validity, nothing else
///
/// ## Upgrade Path
///
/// Since the factory is immutable, upgrading requires:
/// 1. Deploy a new BrokerVerifier pointing to the new factory
/// 2. Update all market configs to use the new verifier
///
/// This tradeoff prioritizes security (immutability) over convenience (upgradeability).
contract BrokerVerifier is IBrokerVerifier {
    /* ============================================================================================ */
    /*                                          IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice The PrimeBrokerFactory this verifier trusts.
    /// @dev All broker validity checks are delegated to this factory.
    address public immutable FACTORY;

    /* ============================================================================================ */
    /*                                         CONSTRUCTOR                                          */
    /* ============================================================================================ */

    /// @notice Deploys a new BrokerVerifier trusting the specified factory.
    /// @dev Called by RLDMarketFactory during market creation.
    /// @param factory The PrimeBrokerFactory address (must be non-zero)
    constructor(address factory) {
        require(factory != address(0), "Invalid factory");
        FACTORY = factory;
    }

    /* ============================================================================================ */
    /*                                        VERIFICATION                                          */
    /* ============================================================================================ */

    /// @notice Checks if an account is a valid PrimeBroker.
    /// @dev Delegates to the factory's isBroker function.
    /// @dev This is called by RLDCore during:
    ///      - Solvency checks (_isSolvent)
    ///      - Liquidation validation (liquidate)
    /// @param account The address to verify
    /// @return True if the account is a valid broker deployed by the trusted factory
    function isValidBroker(address account) external view override returns (bool) {
        return IPrimeBrokerFactory(FACTORY).isBroker(account);
    }
}
