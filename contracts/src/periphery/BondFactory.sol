// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {PrimeBroker} from "../rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../rld/core/PrimeBrokerFactory.sol";
import {IERC20} from "../shared/interfaces/IERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {IJTM} from "../twamm/IJTM.sol";

/// @title  BondFactory — Single-TX Bond Minting
/// @author RLD Protocol
/// @notice Creates frozen, isolated bonds in one transaction.
///
/// @dev ## Architecture
///
///   A "bond" is a PrimeBroker clone with:
///     1. A short position (debt + collateral in Core)
///     2. A TWAMM buy-back order hedging the debt
///     3. Frozen state (no further mutations)
///     4. An ERC721 NFT representing ownership
///
///   BondFactory temporarily owns the NFT during minting, which gives it
///   unrestricted access (owner passes all access checks). At the end of
///   mintBond(), the NFT is transferred to the user.
///
/// @dev ## User Flow
///
///   1. One-time: `waUSDC.approve(bondFactory, type(uint256).max)`
///   2. Per bond: `bondFactory.mintBond(notional, hedge, duration, poolKey)`
///
///   Total: 1 wallet popup per bond (after initial approval).
///
contract BondFactory is ReentrancyGuard {
    /* ============================= IMMUTABLES ============================= */

    /// @notice The PrimeBrokerFactory that deploys broker clones and mints NFTs
    PrimeBrokerFactory public immutable BROKER_FACTORY;

    /// @notice The BrokerRouter for executing short positions
    address public immutable ROUTER;

    /// @notice The TWAMM hook for streaming orders
    address public immutable TWAMM_HOOK;

    /// @notice The collateral token (waUSDC)
    address public immutable COLLATERAL;

    /* =============================== STATE ================================ */

    /// @notice Monotonic nonce for deterministic salt generation
    uint256 public nonce;

    /* =============================== EVENTS =============================== */

    /// @notice Emitted when a new bond is minted
    /// @param user      The bond owner (receives the NFT)
    /// @param broker    The new PrimeBroker clone address
    /// @param notional  Collateral deposited (waUSDC, 6 decimals)
    /// @param hedge     Amount allocated to TWAMM buy-back
    /// @param duration  TWAMM order duration in seconds
    event BondMinted(
        address indexed user,
        address indexed broker,
        uint256 notional,
        uint256 hedge,
        uint256 duration
    );

    /* ============================ CONSTRUCTOR ============================= */

    constructor(
        address brokerFactory_,
        address router_,
        address twammHook_,
        address collateral_
    ) {
        require(brokerFactory_ != address(0), "Invalid factory");
        require(router_ != address(0), "Invalid router");
        require(twammHook_ != address(0), "Invalid twamm");
        require(collateral_ != address(0), "Invalid collateral");

        BROKER_FACTORY = PrimeBrokerFactory(brokerFactory_);
        ROUTER = router_;
        TWAMM_HOOK = twammHook_;
        COLLATERAL = collateral_;
    }

    /* =========================== CORE FUNCTION ============================ */

    /// @notice Mint a bond in a single transaction.
    ///
    /// @dev Flow:
    ///   1. Create fresh broker (NFT → this contract)
    ///   2. Pull waUSDC from user → broker
    ///   3. Execute short via BrokerRouter (we are NFT owner → authorized)
    ///   4. Submit TWAMM buy-back order (we are owner → authorized)
    ///   5. Freeze broker (we are owner → passes onlyOwner)
    ///   6. Transfer NFT to user
    ///
    /// @param notional       Collateral for the short position (waUSDC, 6 decimals)
    /// @param hedgeAmount    Amount to allocate for TWAMM buy-back (waUSDC, 6 decimals)
    /// @param duration       TWAMM order duration in seconds
    /// @param poolKey        The Uniswap V4 pool key (wRLP/waUSDC)
    /// @return broker        The deployed broker address (also the NFT token)
    function mintBond(
        uint256 notional,
        uint256 hedgeAmount,
        uint256 duration,
        PoolKey calldata poolKey
    ) external nonReentrant returns (address broker) {
        require(notional > 0, "Zero notional");
        require(duration > 0, "Zero duration");

        // ── 1. Create fresh broker ──────────────────────────────────────
        // NFT is minted to address(this), making us the owner.
        bytes32 salt = keccak256(abi.encodePacked(msg.sender, nonce++));
        broker = BROKER_FACTORY.createBroker(salt);

        // ── 2. Pull waUSDC from user → broker ──────────────────────────
        uint256 total = notional + hedgeAmount;
        IERC20(COLLATERAL).transferFrom(msg.sender, broker, total);

        // ── 3. Execute short ────────────────────────────────────────────
        // BrokerRouter.executeShort checks onlyBrokerAuthorized(broker):
        //   msg.sender == NFT owner ✓ (we own the NFT)
        // This deposits `notional` as collateral, mints `hedgeAmount` wRLP
        // as debt, spot-swaps wRLP → waUSDC, and returns proceeds to broker.
        (bool ok, bytes memory ret) = ROUTER.call(
            abi.encodeWithSignature(
                "executeShort(address,uint256,uint256,(address,address,uint24,int24,address))",
                broker,
                notional,
                hedgeAmount,
                poolKey
            )
        );
        require(ok, _getRevertMsg(ret));

        // ── 4. Submit TWAMM buy-back order ──────────────────────────────
        // PrimeBroker.submitTwammOrder checks onlyAuthorized:
        //   msg.sender == NFT owner ✓
        // Sells waUSDC (currency1) → buys wRLP (currency0) to hedge.
        PrimeBroker pb = PrimeBroker(payable(broker));
        IJTM.SubmitOrderParams memory params = IJTM.SubmitOrderParams({
            key: poolKey,
            zeroForOne: false, // sell currency1 (waUSDC) → buy currency0 (wRLP)
            duration: duration,
            amountIn: hedgeAmount
        });
        pb.submitTwammOrder(TWAMM_HOOK, params);

        // ── 5. Freeze broker ────────────────────────────────────────────
        // Locks all state mutations. TWAMM continues at hook level.
        pb.freeze();

        // ── 6. Transfer NFT to user ─────────────────────────────────────
        // Factory.transferFrom calls revokeAllOperators (no-op since frozen).
        uint256 tokenId = uint256(uint160(broker));
        BROKER_FACTORY.transferFrom(address(this), msg.sender, tokenId);

        emit BondMinted(msg.sender, broker, notional, hedgeAmount, duration);
    }

    /* =========================== INTERNAL ================================= */

    /// @dev Extract revert reason from failed low-level call
    function _getRevertMsg(
        bytes memory returnData
    ) internal pure returns (string memory) {
        if (returnData.length < 68) return "BondFactory: call failed";
        assembly {
            returnData := add(returnData, 0x04)
        }
        return abi.decode(returnData, (string));
    }
}
