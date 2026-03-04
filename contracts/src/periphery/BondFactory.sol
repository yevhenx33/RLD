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
import {IRLDCore, MarketId} from "../shared/interfaces/IRLDCore.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

/// @dev Minimal interface for WrappedAToken (waUSDC, waUSDT, etc.)
interface IWrappedAToken {
    function aToken() external view returns (address);
    function wrap(uint256 aTokenAmount) external returns (uint256 shares);
    function unwrap(uint256 shares) external returns (uint256 aTokenAmount);
}

/// @dev Minimal interface for Aave aTokens
interface IAToken {
    function UNDERLYING_ASSET_ADDRESS() external view returns (address);
    function POOL() external view returns (address);
}

/// @dev Minimal interface for Aave V3 Pool
interface IAavePool {
    function supply(
        address asset,
        uint256 amount,
        address onBehalfOf,
        uint16 referralCode
    ) external;
    function withdraw(
        address asset,
        uint256 amount,
        address to
    ) external returns (uint256);
}

/// @title  BondFactory — Single-TX Bond Minting & Closing
/// @author RLD Protocol
/// @notice Creates and closes frozen, isolated bonds in one transaction.
///
/// @dev ## Architecture
///
///   A "bond" is a PrimeBroker clone with:
///     1. A short position (debt + collateral in Core)
///     2. A TWAMM buy-back order hedging the debt
///     3. Frozen state (no further mutations)
///     4. An ERC721 NFT representing ownership
///
///   BondFactory temporarily owns the NFT during minting/closing, which gives
///   it unrestricted access (owner passes all access checks).
///
/// @dev ## User Flow
///
///   ### Create:
///   1. One-time: `waUSDC.approve(bondFactory, type(uint256).max)`
///   2. Per bond: `bondFactory.mintBond(notional, hedge, duration, poolKey)`
///
///   ### Close:
///   1. One-time: `BROKER_FACTORY.approve(bondFactory, tokenId)`
///   2. Per bond: `bondFactory.closeBond(broker, poolKey)`
///
///   Total: 1 wallet popup per action (after initial approvals).
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
    event BondMinted(
        address indexed user,
        address indexed broker,
        uint256 notional,
        uint256 hedge,
        uint256 duration
    );

    /// @notice Emitted when a bond is closed
    event BondClosed(
        address indexed user,
        address indexed broker,
        uint256 collateralReturned,
        uint256 positionReturned
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

    /* =========================== MINT BOND ================================ */

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
    /// @param useUnderlying If true, pull underlying (e.g. USDC) from user and
    ///                     auto-wrap to collateral (e.g. waUSDC). If false, pull
    ///                     collateral directly.
    function mintBond(
        uint256 notional,
        uint256 hedgeAmount,
        uint256 duration,
        PoolKey calldata poolKey,
        bool useUnderlying
    ) external nonReentrant returns (address broker) {
        require(notional > 0, "Zero notional");
        require(duration > 0, "Zero duration");

        // ── 1. Create fresh broker ──────────────────────────────────────
        bytes32 salt = keccak256(abi.encodePacked(msg.sender, nonce++));
        broker = BROKER_FACTORY.createBroker(salt);

        // ── 2. Fund broker with collateral ──────────────────────────────
        uint256 total = notional + hedgeAmount;

        if (useUnderlying) {
            // Pull underlying (USDC) → wrap → send waUSDC to broker
            _wrapAndSend(total, broker);
        } else {
            // Pull waUSDC directly from user → broker
            IERC20(COLLATERAL).transferFrom(msg.sender, broker, total);
        }

        // ── 3. Execute short ────────────────────────────────────────────
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
        PrimeBroker pb = PrimeBroker(payable(broker));
        IJTM.SubmitOrderParams memory params = IJTM.SubmitOrderParams({
            key: poolKey,
            zeroForOne: false, // sell currency1 (waUSDC) → buy currency0 (wRLP)
            duration: duration,
            amountIn: hedgeAmount
        });
        pb.submitTwammOrder(TWAMM_HOOK, params);

        // ── 5. Freeze broker ────────────────────────────────────────────
        pb.freeze();

        // ── 6. Transfer NFT to user ─────────────────────────────────────
        uint256 tokenId = uint256(uint160(broker));
        BROKER_FACTORY.transferFrom(address(this), msg.sender, tokenId);

        emit BondMinted(msg.sender, broker, notional, hedgeAmount, duration);
    }

    /* =========================== CLOSE BOND ============================== */

    /// @notice Close a bond in a single transaction.
    ///
    /// @dev Flow:
    ///   1. Pull NFT from user (user must approve this contract first)
    ///   2. Unfreeze broker (we are now NFT owner → onlyOwner passes)
    ///   3. Handle TWAMM: claim expired order or cancel active order
    ///   4. If wRLP < debt: buy shortfall via BrokerRouter.closeShort()
    ///   5. Repay all debt via modifyPosition
    ///   6. Withdraw remaining tokens to user
    ///   7. Transfer NFT back to user
    ///
    /// @param broker         The bond's PrimeBroker address
    /// @param poolKey        The Uniswap V4 pool key (needed for potential closeShort)
    /// @param useUnderlying  If true, unwrap collateral to underlying (e.g. USDC)
    ///                       before returning to user
    function closeBond(
        address broker,
        PoolKey calldata poolKey,
        bool useUnderlying
    ) external nonReentrant {
        PrimeBroker pb = PrimeBroker(payable(broker));
        uint256 tokenId = uint256(uint160(broker));

        // ── 1. Pull NFT from user → this contract ──────────────────────
        BROKER_FACTORY.transferFrom(msg.sender, address(this), tokenId);

        // ── 2. Unfreeze ─────────────────────────────────────────────────
        if (pb.frozen()) {
            pb.unfreeze();
        }

        // ── 3. Handle TWAMM order ───────────────────────────────────────
        (, , bytes32 orderId) = pb.activeTwammOrder();
        if (orderId != bytes32(0)) {
            // Read order expiration
            (, IJTM.OrderKey memory orderKey, ) = pb.activeTwammOrder();
            uint256 expiration = uint256(orderKey.expiration);

            if (block.timestamp >= expiration) {
                // Expired → claim tokens (no whenNotFrozen needed)
                pb.claimExpiredTwammOrder();
            } else {
                // Active → cancel (returns earned + refund)
                pb.cancelTwammOrder();
            }
        }

        // ── 4. Check wRLP balance vs debt ───────────────────────────────
        address coreAddr = pb.CORE();
        MarketId mktId = pb.marketId();
        bytes32 rawMarketId = MarketId.unwrap(mktId);

        IRLDCore.Position memory pos = IRLDCore(coreAddr).getPosition(
            mktId,
            broker
        );
        uint128 debtPrincipal = pos.debtPrincipal;

        // Grant Router operator access for closeShort/closeLong calls
        pb.setOperator(ROUTER, true);

        if (debtPrincipal > 0) {
            address positionToken = pb.positionToken();
            uint256 wrlpBalance = ERC20(positionToken).balanceOf(broker);

            // If wRLP insufficient, buy the shortfall via BrokerRouter
            if (wrlpBalance < debtPrincipal) {
                address collToken = pb.collateralToken();
                uint256 waUSDCBal = ERC20(collToken).balanceOf(broker);

                if (waUSDCBal > 0) {
                    (bool ok, ) = ROUTER.call(
                        abi.encodeWithSignature(
                            "closeShort(address,uint256,(address,address,uint24,int24,address))",
                            broker,
                            waUSDCBal,
                            poolKey
                        )
                    );
                    if (!ok) {
                        (ok, ) = ROUTER.call(
                            abi.encodeWithSignature(
                                "closeShort(address,uint256,(address,address,uint24,int24,address))",
                                broker,
                                waUSDCBal / 2,
                                poolKey
                            )
                        );
                    }
                }
            }

            // Re-fetch debt and balance after closeShort
            pos = IRLDCore(coreAddr).getPosition(mktId, broker);
            debtPrincipal = pos.debtPrincipal;

            // ── 5. Repay remaining debt (capped to available wRLP) ──────
            if (debtPrincipal > 0) {
                address posToken2 = pb.positionToken();
                uint256 availableWRLP = ERC20(posToken2).balanceOf(broker);
                uint256 repayAmount = availableWRLP < debtPrincipal
                    ? availableWRLP
                    : debtPrincipal;

                if (repayAmount > 0) {
                    pb.modifyPosition(
                        rawMarketId,
                        int256(0),
                        -int256(repayAmount)
                    );
                }
            }
        }

        // ── 6. Convert any leftover wRLP → waUSDC ───────────────────────
        {
            address positionToken = pb.positionToken();
            uint256 leftoverWRLP = ERC20(positionToken).balanceOf(broker);
            if (leftoverWRLP > 0) {
                // closeLong swaps wRLP → waUSDC, proceeds stay in broker
                ROUTER.call(
                    abi.encodeWithSignature(
                        "closeLong(address,uint256,(address,address,uint24,int24,address))",
                        broker,
                        leftoverWRLP,
                        poolKey
                    )
                );
            }
        }

        // Revoke Router operator access
        pb.setOperator(ROUTER, false);

        // ── 7. Withdraw collateral ───────────────────────────────────────
        address collateralToken = pb.collateralToken();
        uint256 collBal = ERC20(collateralToken).balanceOf(broker);

        if (collBal > 0) {
            if (useUnderlying) {
                // Withdraw waUSDC to this contract, then unwrap → USDC to user
                pb.withdrawCollateral(address(this), collBal);
                _unwrapAndSend(collBal, msg.sender);
            } else {
                // Withdraw waUSDC directly to user
                pb.withdrawCollateral(msg.sender, collBal);
            }
        }

        // ── 8. Transfer NFT back to user ────────────────────────────────
        BROKER_FACTORY.transferFrom(address(this), msg.sender, tokenId);

        emit BondClosed(msg.sender, broker, collBal, 0);
    }

    /* =========================== INTERNAL ================================= */

    /// @dev Pull underlying from user → Aave supply → wrap to collateral → send to recipient
    ///      Derives aToken/underlying/pool from the WrappedAToken contract (market-agnostic)
    function _wrapAndSend(uint256 amount, address recipient) internal {
        IWrappedAToken wrapper = IWrappedAToken(COLLATERAL);
        address aTokenAddr = wrapper.aToken();
        IAToken aToken = IAToken(aTokenAddr);
        address underlying = aToken.UNDERLYING_ASSET_ADDRESS();
        address pool = aToken.POOL();

        // 1. Pull underlying (e.g. USDC) from user
        IERC20(underlying).transferFrom(msg.sender, address(this), amount);

        // 2. Supply to Aave → this contract receives aTokens
        IERC20(underlying).approve(pool, amount);
        IAavePool(pool).supply(underlying, amount, address(this), 0);

        // 3. Wrap aToken → collateral token (e.g. aUSDC → waUSDC)
        uint256 aBalance = ERC20(aTokenAddr).balanceOf(address(this));
        IERC20(aTokenAddr).approve(COLLATERAL, aBalance);
        uint256 shares = wrapper.wrap(aBalance);

        // 4. Send wrapped tokens to recipient
        IERC20(COLLATERAL).transfer(recipient, shares);
    }

    /// @dev Unwrap collateral → withdraw from Aave → send underlying to recipient
    ///      Assumes this contract already holds the collateral tokens
    function _unwrapAndSend(uint256 collAmount, address recipient) internal {
        IWrappedAToken wrapper = IWrappedAToken(COLLATERAL);
        address aTokenAddr = wrapper.aToken();
        IAToken aToken = IAToken(aTokenAddr);
        address underlying = aToken.UNDERLYING_ASSET_ADDRESS();
        address pool = aToken.POOL();

        // 1. Unwrap collateral → this contract receives aTokens
        uint256 aAmount = wrapper.unwrap(collAmount);

        // 2. Withdraw from Aave → underlying (e.g. USDC) sent to recipient
        IERC20(aTokenAddr).approve(pool, aAmount);
        IAavePool(pool).withdraw(underlying, aAmount, recipient);
    }

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
