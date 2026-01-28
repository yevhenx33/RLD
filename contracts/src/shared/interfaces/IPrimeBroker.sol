// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {ITWAMM} from "../../twamm/ITWAMM.sol";

/// @title Prime Broker Interface
/// @notice Interface for the "Smart Margin Account" that holds assets.
/// @dev The PrimeBroker uses execute() for all DeFi interactions (LP, TWAMM, etc.)
///      and tracking functions (setActiveV4Position, setActiveTwammOrder) to register
///      positions for solvency calculations.
interface IPrimeBroker {
    struct TwammOrderInfo {
        PoolKey key;
        ITWAMM.OrderKey orderKey;
        bytes32 orderId;
    }

    /// @notice Output from seize() during liquidation
    /// @dev Enables two-phase seize: wRLP extracted is burned to offset debt,
    ///      collateral goes to liquidator as bonus.
    struct SeizeOutput {
        uint256 collateralSeized;     // collateralToken transferred to recipient (liquidator bonus)
        uint256 wRLPExtracted;        // positionToken (wRLP) sent to Core for burning
    }

    /// @notice Returns the total Net Asset Value of the account in collateral terms.
    /// @dev Used by RLDCore for solvency checks.
    /// @dev Includes: cash + wRLP tokens + tracked TWAMM + tracked V4 LP
    function getNetAccountValue() external view returns (uint256);

    /// @notice Seizes assets from the account during liquidation.
    /// @dev Only callable by RLDCore during liquidation.
    /// @dev Priority order: Cash → TWAMM → V4 LP
    /// @dev Design: collateralToken goes to recipient, wRLP goes to caller (Core) for burning,
    ///      other tokens stay in broker.
    /// @param value The value (in collateral terms) to seize.
    /// @param recipient The liquidator address to receive collateral.
    /// @return output The amounts of collateral and wRLP extracted.
    function seize(uint256 value, address recipient) external returns (SeizeOutput memory output);
    
    /// @notice Emitted when a generic execution is performed.
    event Execute(address indexed target, bytes data);

    /// @notice Emitted when an operator is updated.
    event OperatorUpdated(address indexed operator, bool active);

    /// @notice Sets an operator for the Prime Broker.
    /// @dev Operators can perform all actions except ownership transfer.
    /// @param operator The address to set as operator.
    /// @param active True to authorize, false to deauthorize.
    function setOperator(address operator, bool active) external;

    /* ============================================================================================ */
    /*                                        NFT METADATA                                          */
    /* ============================================================================================ */

    // BondMetadata removed - rendering is now dynamic based on chain state


}
