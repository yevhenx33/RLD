// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {ITWAMM} from "../../twamm/ITWAMM.sol";

/// @title Prime Broker Interface
/// @notice Interface for the "Smart Margin Account" that holds assets.
interface IPrimeBroker {
    struct TwammOrderInfo {
        PoolKey key;
        ITWAMM.OrderKey orderKey;
        bytes32 orderId;
    }

    /// @notice Submits a TWAMM order on behalf of the user and registers it as collateral.
    /// @param params The order parameters.
    function submitTwammOrder(ITWAMM.SubmitOrderParams calldata params) external;
    /// @notice Returns the total Net Asset Value of the account in Underlying terms.
    /// @dev Used by RLDCore for solvency checks.
    function getNetAccountValue() external view returns (uint256);

    /// @notice Seizes assets from the account to pay a Liquidator.
    /// @dev Only callable by RLDCore during liquidation.
    /// @param value The value (in Underlying terms) to seize.
    /// @param recipient The liquidator address to receive the seized assets.
    function seize(uint256 value, address recipient) external;
    
    /// @notice Emitted when a generic execution is performed.
    event Execute(address indexed target, bytes data);

    /* ============================================================================================ */
    /*                                        NFT METADATA                                          */
    /* ============================================================================================ */

    enum BondType { YIELD, HEDGE }

    struct BondMetadata {
        uint256 rate;           // The fixed rate/yield (WAD, e.g., 0.05e18 = 5%)
        uint256 maturityDate;   // Timestamp of bond expiration
        uint256 principal;      // The size of the bond (in Underlying tokens)
        BondType bondType;      // YIELD (Lender) or HEDGE (Borrower)
    }

    /// @notice Sets the metadata for the Bond NFT.
    /// @dev Callable only by the Owner.
    function setBondMetadata(BondMetadata calldata metadata) external;

    /// @notice Returns the metadata for the Bond NFT.
    function getBondMetadata() external view returns (BondMetadata memory);
}
