// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {PrimeBroker} from "../rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../rld/core/PrimeBrokerFactory.sol";
import {IERC20} from "../shared/interfaces/IERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";
import {IJTM} from "../twamm/IJTM.sol";
import {IRLDCore, MarketId} from "../shared/interfaces/IRLDCore.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {IV4Quoter} from "v4-periphery/src/interfaces/IV4Quoter.sol";

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
///   1. One-time: `USDC.approve(bondFactory, type(uint256).max)` (or waUSDC)
///   2. Per bond: `bondFactory.mintBond(notional, hedge, duration, poolKey, useUnderlying)`
///
///   ### Close:
///   1. `bondFactory.closeBond(broker, poolKey, useUnderlying)` — no approvals needed
///
///   ### Optional NFT custody:
///   - `claimBond(broker)` — transfer NFT to your wallet
///   - `returnBond(broker)` — return NFT to BondFactory custody
///
///   Total: 1 wallet popup per action. Zero approvals for close.
///
contract BondFactory is ReentrancyGuard {
    using CurrencySettler for Currency;
    /* ============================= IMMUTABLES ============================= */

    /// @notice The PrimeBrokerFactory that deploys broker clones and mints NFTs
    PrimeBrokerFactory public immutable BROKER_FACTORY;

    /// @notice The BrokerRouter for executing short positions (mint only)
    address public immutable ROUTER;

    /// @notice The TWAMM hook for streaming orders
    address public immutable TWAMM_HOOK;

    /// @notice The collateral token (waUSDC)
    address public immutable COLLATERAL;

    /// @notice Uniswap V4 PoolManager for direct swaps
    IPoolManager public immutable POOL_MANAGER;

    /// @notice V4 Quoter for exact output quotes
    IV4Quoter public immutable QUOTER;

    /* =============================== STATE ================================ */

    /// @notice Monotonic nonce for deterministic salt generation
    uint256 public nonce;

    /// @notice Tracks bond ownership when NFT is custodied by BondFactory
    /// @dev broker address → user address. Zero when NFT has been claimed.
    mapping(address => address) public bondOwner;

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

    /// @notice Emitted when user claims NFT to their wallet
    event BondClaimed(address indexed user, address indexed broker);

    /// @notice Emitted when user returns NFT to BondFactory custody
    event BondReturned(address indexed user, address indexed broker);

    /* ========================= SWAP CALLBACK ============================== */

    /// @dev Data passed through PoolManager.unlock() for swap settlement
    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    /* ============================ CONSTRUCTOR ============================= */

    constructor(
        address brokerFactory_,
        address router_,
        address twammHook_,
        address collateral_,
        address poolManager_,
        address quoter_
    ) {
        require(brokerFactory_ != address(0), "Invalid factory");
        require(router_ != address(0), "Invalid router");
        require(twammHook_ != address(0), "Invalid twamm");
        require(collateral_ != address(0), "Invalid collateral");
        require(poolManager_ != address(0), "Invalid poolManager");
        require(quoter_ != address(0), "Invalid quoter");

        BROKER_FACTORY = PrimeBrokerFactory(brokerFactory_);
        ROUTER = router_;
        TWAMM_HOOK = twammHook_;
        COLLATERAL = collateral_;
        POOL_MANAGER = IPoolManager(poolManager_);
        QUOTER = IV4Quoter(quoter_);
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
    ///   6. Track ownership in bondOwner mapping
    ///
    /// @param notional       Collateral for the short position (waUSDC, 6 decimals)
    /// @param hedgeAmount    Amount to allocate for TWAMM buy-back (waUSDC, 6 decimals)
    /// @param duration       TWAMM order duration in seconds
    /// @param poolKey        The Uniswap V4 pool key (wRLP/waUSDC)
    /// @param useUnderlying If true, pull underlying (e.g. USDC) from user and
    ///                     auto-wrap to collateral (e.g. waUSDC). If false, pull
    ///                     collateral directly.
    /// @return broker        The deployed broker address (also the NFT token)
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
        bytes32 salt = keccak256(abi.encodePacked(address(this), msg.sender, nonce++));
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
        // Determine direction: selling COLLATERAL (waUSDC) → buying position token (wRLP)
        // zeroForOne = true when COLLATERAL is currency0 (lower address)
        bool sellCollateral = Currency.unwrap(poolKey.currency0) == COLLATERAL;

        IJTM.SubmitOrderParams memory params = IJTM.SubmitOrderParams({
            key: poolKey,
            zeroForOne: sellCollateral,
            duration: duration,
            amountIn: hedgeAmount
        });
        pb.submitTwammOrder(TWAMM_HOOK, params);

        // ── 5. Freeze broker ────────────────────────────────────────────
        pb.freeze();

        // ── 6. Track ownership (NFT stays on BondFactory) ────────────────
        bondOwner[broker] = msg.sender;

        emit BondMinted(msg.sender, broker, notional, hedgeAmount, duration);
    }

    /* =========================== CLOSE BOND ============================== */

    /// @notice Close a bond in a single transaction.
    ///
    /// @dev Self-contained flow — no BrokerRouter dependency:
    ///   1. Verify ownership (custodied or claimed)
    ///   2. Unfreeze broker
    ///   3. Cancel/claim TWAMM order
    ///   4. If wRLP shortfall: quote exact amount → withdraw waUSDC → swap on V4 → repay debt
    ///   5. Convert leftover wRLP → waUSDC via V4 swap
    ///   6. Withdraw collateral to user (optionally unwrap to USDC)
    ///
    /// @param broker         The bond's PrimeBroker address
    /// @param poolKey        The Uniswap V4 pool key (needed for swaps)
    /// @param useUnderlying  If true, unwrap collateral to underlying (e.g. USDC)
    ///                       before returning to user
    function closeBond(
        address broker,
        PoolKey calldata poolKey,
        bool useUnderlying
    ) external nonReentrant {
        PrimeBroker pb = PrimeBroker(payable(broker));
        uint256 tokenId = uint256(uint160(broker));

        // ── 1. Verify ownership ─────────────────────────────────────────
        if (bondOwner[broker] == msg.sender) {
            // Custodied: BondFactory already owns NFT, clear mapping
            delete bondOwner[broker];
        } else {
            // Claimed: pull NFT from user (requires prior approval)
            BROKER_FACTORY.transferFrom(msg.sender, address(this), tokenId);
        }

        // ── 2. Unfreeze ─────────────────────────────────────────────────
        if (pb.frozen()) {
            pb.unfreeze();
        }

        // ── 3. Handle TWAMM order ───────────────────────────────────────
        (, , bytes32 orderId) = pb.activeTwammOrder();
        if (orderId != bytes32(0)) {
            (, IJTM.OrderKey memory orderKey, ) = pb.activeTwammOrder();
            uint256 expiration = uint256(orderKey.expiration);

            if (block.timestamp >= expiration) {
                pb.claimExpiredTwammOrder();
            } else {
                pb.cancelTwammOrder();
            }
        }

        // ── 4. Repay wRLP debt ──────────────────────────────────────────
        address coreAddr = pb.CORE();
        MarketId mktId = pb.marketId();
        bytes32 rawMarketId = MarketId.unwrap(mktId);

        IRLDCore.Position memory pos = IRLDCore(coreAddr).getPosition(
            mktId,
            broker
        );
        uint128 debtPrincipal = pos.debtPrincipal;

        if (debtPrincipal > 0) {
            address positionToken = pb.positionToken();
            uint256 wrlpBalance = ERC20(positionToken).balanceOf(broker);

            // If wRLP insufficient, buy the exact shortfall via V4 swap
            if (wrlpBalance < debtPrincipal) {
                uint256 shortfall = debtPrincipal - wrlpBalance;
                _buyExactWRLP(pb, shortfall, positionToken, poolKey);
            }

            // Re-fetch wRLP balance after swap
            uint256 availableWRLP = ERC20(positionToken).balanceOf(broker);
            uint256 repayAmount = availableWRLP < debtPrincipal
                ? availableWRLP
                : debtPrincipal;

            if (repayAmount > 0) {
                pb.modifyPosition(rawMarketId, int256(0), -int256(repayAmount));
            }
        }

        // ── 5. Convert leftover wRLP → waUSDC ───────────────────────────
        {
            address positionToken = pb.positionToken();
            uint256 leftoverWRLP = ERC20(positionToken).balanceOf(broker);
            if (leftoverWRLP > 0) {
                // Withdraw wRLP from broker → this contract
                pb.withdrawPositionToken(address(this), leftoverWRLP);
                // Swap wRLP → waUSDC on V4
                uint256 waUSDCReceived = _swapExactInput(
                    positionToken,
                    COLLATERAL,
                    leftoverWRLP,
                    poolKey
                );
                // Send waUSDC to broker so it's included in final withdrawal
                IERC20(COLLATERAL).transfer(broker, waUSDCReceived);
            }
        }

        // ── 6. Withdraw collateral ───────────────────────────────────────
        address collateralToken = pb.collateralToken();
        uint256 collBal = ERC20(collateralToken).balanceOf(broker);

        if (collBal > 0) {
            if (useUnderlying) {
                pb.withdrawCollateral(address(this), collBal);
                _unwrapAndSend(collBal, msg.sender);
            } else {
                pb.withdrawCollateral(msg.sender, collBal);
            }
        }

        emit BondClosed(msg.sender, broker, collBal, 0);
    }

    /* ========================= CLAIM / RETURN ============================= */

    /// @notice Claim bond NFT to your wallet (for trading/transfer)
    /// @dev Moves NFT from BondFactory to caller. Clears bondOwner mapping.
    ///      After claiming, closeBond requires NFT approval.
    function claimBond(address broker) external {
        require(bondOwner[broker] == msg.sender, "Not bond owner");
        delete bondOwner[broker];
        uint256 tokenId = uint256(uint160(broker));
        BROKER_FACTORY.transferFrom(address(this), msg.sender, tokenId);
        emit BondClaimed(msg.sender, broker);
    }

    /// @notice Return a claimed bond NFT back to BondFactory custody
    /// @dev Moves NFT from caller to BondFactory. Restores bondOwner mapping.
    ///      After returning, closeBond no longer requires approval.
    function returnBond(address broker) external {
        uint256 tokenId = uint256(uint160(broker));
        BROKER_FACTORY.transferFrom(msg.sender, address(this), tokenId);
        bondOwner[broker] = msg.sender;
        emit BondReturned(msg.sender, broker);
    }

    /* ========================= V4 SWAP HELPERS ============================ */

    /// @dev Buy exact amount of wRLP by swapping waUSDC from the broker.
    ///      Uses quoter to determine exact waUSDC needed, then swaps on V4.
    function _buyExactWRLP(
        PrimeBroker pb,
        uint256 wrlpNeeded,
        address positionToken,
        PoolKey calldata poolKey
    ) internal {
        address collToken = pb.collateralToken();

        // 1. Quote exact waUSDC input needed for wrlpNeeded output
        bool zeroForOne = collToken < positionToken;
        (uint256 amountIn, ) = QUOTER.quoteExactOutputSingle(
            IV4Quoter.QuoteExactSingleParams({
                poolKey: poolKey,
                zeroForOne: zeroForOne,
                exactAmount: uint128(wrlpNeeded),
                hookData: new bytes(0)
            })
        );

        // 2. Withdraw exactly amountIn waUSDC from broker to this contract
        pb.withdrawCollateral(address(this), amountIn);

        // 3. Swap waUSDC → wRLP on V4 (exact input = amountIn)
        uint256 wrlpReceived = _swapExactInput(
            collToken,
            positionToken,
            amountIn,
            poolKey
        );

        // 4. Transfer wRLP to broker for debt repayment
        IERC20(positionToken).transfer(address(pb), wrlpReceived);
    }

    /// @dev Swap exact input amount on V4 pool. Returns output amount.
    function _swapExactInput(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        PoolKey calldata poolKey
    ) internal returns (uint256 amountOut) {
        bool zeroForOne = tokenIn < tokenOut;

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(amountIn), // negative = exact input
            sqrtPriceLimitX96: zeroForOne
                ? uint160(4295128740) // MIN_SQRT_PRICE + 1
                : uint160(1461446703485210103287273052203988822378723970341) // MAX_SQRT_PRICE - 1
        });

        // Approve PoolManager to pull input tokens
        IERC20(tokenIn).approve(address(POOL_MANAGER), amountIn);

        BalanceDelta delta = abi.decode(
            POOL_MANAGER.unlock(
                abi.encode(
                    SwapCallback({
                        sender: address(this),
                        key: poolKey,
                        params: swapParams
                    })
                )
            ),
            (BalanceDelta)
        );

        // Output is the positive delta on the output side
        amountOut = zeroForOne
            ? uint256(int256(delta.amount1()))
            : uint256(int256(delta.amount0()));
    }

    /// @notice Uniswap V4 swap settlement callback
    /// @dev Called by PoolManager during unlock(). Settles token transfers.
    function unlockCallback(
        bytes calldata rawData
    ) external returns (bytes memory) {
        require(msg.sender == address(POOL_MANAGER), "Not PoolManager");

        SwapCallback memory data = abi.decode(rawData, (SwapCallback));

        BalanceDelta delta = POOL_MANAGER.swap(
            data.key,
            data.params,
            new bytes(0)
        );

        // Settle: pay tokens we owe, take tokens we're owed
        if (data.params.zeroForOne) {
            if (delta.amount0() < 0) {
                data.key.currency0.settle(
                    POOL_MANAGER,
                    data.sender,
                    uint256(-int256(delta.amount0())),
                    false
                );
            }
            if (delta.amount1() > 0) {
                data.key.currency1.take(
                    POOL_MANAGER,
                    data.sender,
                    uint256(int256(delta.amount1())),
                    false
                );
            }
        } else {
            if (delta.amount1() < 0) {
                data.key.currency1.settle(
                    POOL_MANAGER,
                    data.sender,
                    uint256(-int256(delta.amount1())),
                    false
                );
            }
            if (delta.amount0() > 0) {
                data.key.currency0.take(
                    POOL_MANAGER,
                    data.sender,
                    uint256(int256(delta.amount0())),
                    false
                );
            }
        }

        return abi.encode(delta);
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
