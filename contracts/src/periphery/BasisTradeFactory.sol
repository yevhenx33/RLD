// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {PrimeBroker} from "../rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../rld/core/PrimeBrokerFactory.sol";
import {IERC20} from "../shared/interfaces/IERC20.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";
import {IJTM} from "../twamm/IJTM.sol";

/* ═══════════════════════════════════════════════════════════════════════════════
   EXTERNAL INTERFACES
   ═══════════════════════════════════════════════════════════════════════════════ */

/// @dev Aave V3 Pool — supply, borrow, repay, withdraw
interface IAavePool {
    function supply(
        address asset,
        uint256 amount,
        address onBehalfOf,
        uint16 referralCode
    ) external;

    function borrow(
        address asset,
        uint256 amount,
        uint256 interestRateMode,
        uint16 referralCode,
        address onBehalfOf
    ) external;

    function repay(
        address asset,
        uint256 amount,
        uint256 interestRateMode,
        address onBehalfOf
    ) external returns (uint256);

    function withdraw(
        address asset,
        uint256 amount,
        address to
    ) external returns (uint256);
}

/// @dev Curve StableSwap pool — exchange sUSDe ↔ USDC
interface ICurvePool {
    function exchange(
        int128 i,
        int128 j,
        uint256 dx,
        uint256 min_dy
    ) external returns (uint256);

    function get_dy(
        int128 i,
        int128 j,
        uint256 dx
    ) external view returns (uint256);
}

/// @dev Aave wrapped aToken (e.g. waUSDC)
interface IWrappedAToken {
    function aToken() external view returns (address);
    function wrap(uint256 aTokenAmount) external returns (uint256 shares);
    function unwrap(uint256 shares) external returns (uint256 aTokenAmount);
    function deposit(
        uint256 assets,
        address receiver
    ) external returns (uint256 shares);
}

/// @dev Minimal aToken interface
interface IAToken {
    function UNDERLYING_ASSET_ADDRESS() external view returns (address);
    function POOL() external view returns (address);
}

/* ═══════════════════════════════════════════════════════════════════════════════
   BASIS TRADE FACTORY
   ═══════════════════════════════════════════════════════════════════════════════ */

/// @title  BasisTradeFactory — sUSDe Carry Trade with Rate Hedging
/// @author RLD Protocol
/// @notice Opens leveraged sUSDe positions on Aave V3 with wRLP rate hedging.
///
/// @dev ## Strategy
///
///   User deposits sUSDe (or USDC) → leverage loop on Aave V3 →
///   buy wRLP for rate hedge → TWAMM sell wRLP → waUSDC over duration.
///
///   The PrimeBroker acts as a smart wallet holding:
///   - Aave aTokens (sUSDe collateral)
///   - Aave variable debt (USDC borrow)
///   - wRLP tokens → TWAMM streaming order
///
///   From RLD's perspective, the user only buys wRLP.
///   Aave positions are Aave's responsibility.
///
/// @dev ## User Flow
///
///   ### Open (sUSDe entry):
///   1. approve sUSDe → BasisTradeFactory
///   2. openBasisTrade(amount, leverage, duration)
///
///   ### Open (USDC entry):
///   1. approve USDC → BasisTradeFactory
///   2. openBasisTradeWithUSDC(amount, leverage, duration)
///
///   ### Close:
///   1. closeBasisTrade(broker) — unwinding is permissionless for the owner
///
contract BasisTradeFactory is ReentrancyGuard {
    using CurrencySettler for Currency;

    /* ═══════════════════════════════════════════════════ IMMUTABLES ═══════════════════════════════ */

    PrimeBrokerFactory public immutable BROKER_FACTORY;
    address public immutable TWAMM_HOOK;
    address public immutable COLLATERAL; // waUSDC
    IPoolManager public immutable POOL_MANAGER;

    // External protocols
    address public immutable AAVE_POOL;
    address public immutable SUSDE; // sUSDe token
    address public immutable USDC; // USDC token
    address public immutable CURVE_POOL; // Curve sUSDe/USDC pool

    // Curve pool coin indices (set in constructor)
    int128 public immutable CURVE_SUSDE_INDEX;
    int128 public immutable CURVE_USDC_INDEX;

    /* ═══════════════════════════════════════════════════ STATE ════════════════════════════════════ */

    uint256 public nonce;

    /// @notice Tracks basis trade ownership: broker → user
    mapping(address => address) public tradeOwner;

    /* ═══════════════════════════════════════════════════ EVENTS ═══════════════════════════════════ */

    event BasisTradeOpened(
        address indexed user,
        address indexed broker,
        uint256 sUsdeDeposited,
        uint256 leverage,
        uint256 duration
    );

    event BasisTradeClosed(
        address indexed user,
        address indexed broker,
        uint256 sUsdeReturned
    );

    /* ═══════════════════════════════════════════════════ SWAP CALLBACK ════════════════════════════ */

    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    /* ═══════════════════════════════════════════════════ CONSTRUCTOR ══════════════════════════════ */

    constructor(
        address brokerFactory_,
        address twammHook_,
        address collateral_,
        address poolManager_,
        address aavePool_,
        address sUsde_,
        address usdc_,
        address curvePool_,
        int128 curveSUsdeIndex_,
        int128 curveUsdcIndex_
    ) {
        require(brokerFactory_ != address(0), "!factory");
        require(twammHook_ != address(0), "!twamm");
        require(collateral_ != address(0), "!collateral");
        require(poolManager_ != address(0), "!pm");
        require(aavePool_ != address(0), "!aave");
        require(sUsde_ != address(0), "!susde");
        require(usdc_ != address(0), "!usdc");
        require(curvePool_ != address(0), "!curve");

        BROKER_FACTORY = PrimeBrokerFactory(brokerFactory_);
        TWAMM_HOOK = twammHook_;
        COLLATERAL = collateral_;
        POOL_MANAGER = IPoolManager(poolManager_);
        AAVE_POOL = aavePool_;
        SUSDE = sUsde_;
        USDC = usdc_;
        CURVE_POOL = curvePool_;
        CURVE_SUSDE_INDEX = curveSUsdeIndex_;
        CURVE_USDC_INDEX = curveUsdcIndex_;
    }

    /* ═══════════════════════════════════════════ OPEN — sUSDe ENTRY ══════════════════════════════ */

    /// @notice Open a basis trade with sUSDe as the entry token.
    ///
    /// @param sUsdeAmount   Amount of sUSDe to deposit
    /// @param leverage      Target leverage multiplier (e.g. 3 = 3x)
    /// @param hedgeAmount   waUSDC to spend buying wRLP for the hedge
    /// @param duration      TWAMM order duration in seconds
    /// @param poolKey       V4 pool key for waUSDC/wRLP swaps
    /// @return broker       The deployed PrimeBroker address
    function openBasisTrade(
        uint256 sUsdeAmount,
        uint256 leverage,
        uint256 hedgeAmount,
        uint256 duration,
        PoolKey calldata poolKey
    ) external nonReentrant returns (address broker) {
        require(sUsdeAmount > 0, "Zero amount");
        require(leverage >= 2, "Min 2x");
        require(duration > 0, "Zero duration");

        // ── 1. Create fresh PrimeBroker ─────────────────────────────────
        bytes32 salt = keccak256(abi.encodePacked(msg.sender, nonce++));
        broker = BROKER_FACTORY.createBroker(salt);
        PrimeBroker pb = PrimeBroker(payable(broker));

        // ── 2. Pull sUSDe from user → this contract ────────────────────
        IERC20(SUSDE).transferFrom(msg.sender, address(this), sUsdeAmount);

        // ── 3. Leverage loop via broker.execute() ───────────────────────
        _executeLeverageLoop(pb, broker, sUsdeAmount, leverage);

        // ── 4. Borrow USDC for hedge, wrap to waUSDC ────────────────────
        _borrowAndWrapForHedge(pb, broker, hedgeAmount);

        // ── 5. Buy wRLP with waUSDC on V4 ───────────────────────────────
        _buyWRLP(pb, broker, hedgeAmount, poolKey);

        // ── 6. Submit TWAMM order: sell wRLP → waUSDC ───────────────────
        _submitTwammHedge(pb, broker, duration, poolKey);

        // ── 7. Track ownership ──────────────────────────────────────────
        tradeOwner[broker] = msg.sender;

        emit BasisTradeOpened(
            msg.sender,
            broker,
            sUsdeAmount,
            leverage,
            duration
        );
    }

    /* ═══════════════════════════════════════════ OPEN — USDC ENTRY ═══════════════════════════════ */

    /// @notice Open a basis trade with USDC as the entry token.
    /// @dev Swaps USDC → sUSDe on Curve first, then follows sUSDe flow.
    function openBasisTradeWithUSDC(
        uint256 usdcAmount,
        uint256 leverage,
        uint256 hedgeAmount,
        uint256 duration,
        PoolKey calldata poolKey
    ) external nonReentrant returns (address broker) {
        require(usdcAmount > 0, "Zero amount");
        require(leverage >= 2, "Min 2x");
        require(duration > 0, "Zero duration");

        // ── 0. Pull USDC from user → swap to sUSDe ─────────────────────
        IERC20(USDC).transferFrom(msg.sender, address(this), usdcAmount);
        IERC20(USDC).approve(CURVE_POOL, usdcAmount);
        uint256 sUsdeAmount = ICurvePool(CURVE_POOL).exchange(
            CURVE_USDC_INDEX,
            CURVE_SUSDE_INDEX,
            usdcAmount,
            0 // TODO: add min_dy for slippage protection
        );

        // ── 1. Create fresh PrimeBroker ─────────────────────────────────
        bytes32 salt = keccak256(abi.encodePacked(msg.sender, nonce++));
        broker = BROKER_FACTORY.createBroker(salt);
        PrimeBroker pb = PrimeBroker(payable(broker));

        // ── 2–6. Same as sUSDe flow ────────────────────────────────────
        _executeLeverageLoop(pb, broker, sUsdeAmount, leverage);
        _borrowAndWrapForHedge(pb, broker, hedgeAmount);
        _buyWRLP(pb, broker, hedgeAmount, poolKey);
        _submitTwammHedge(pb, broker, duration, poolKey);

        tradeOwner[broker] = msg.sender;

        emit BasisTradeOpened(
            msg.sender,
            broker,
            sUsdeAmount,
            leverage,
            duration
        );
    }

    /* ═══════════════════════════════════════════ CLOSE ═══════════════════════════════════════════ */

    /// @notice Close a basis trade — unwind all positions, return sUSDe to user.
    ///
    /// @param broker The PrimeBroker address to close
    /// @param poolKey V4 pool key for wRLP/waUSDC swaps
    function closeBasisTrade(
        address broker,
        PoolKey calldata poolKey
    ) external nonReentrant {
        PrimeBroker pb = PrimeBroker(payable(broker));

        // ── 1. Verify ownership ─────────────────────────────────────────
        require(tradeOwner[broker] == msg.sender, "Not owner");
        delete tradeOwner[broker];

        // ── 2. Cancel/claim TWAMM order ─────────────────────────────────
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

        // ── 3. Sell any leftover wRLP → waUSDC via V4 ───────────────────
        {
            address positionToken = pb.positionToken();
            uint256 wrlpBal = ERC20(positionToken).balanceOf(broker);
            if (wrlpBal > 0) {
                // Withdraw wRLP from broker → this contract
                pb.withdrawPositionToken(address(this), wrlpBal);
                // Swap wRLP → waUSDC
                uint256 waUsdcReceived = _swapExactInput(
                    positionToken,
                    COLLATERAL,
                    wrlpBal,
                    poolKey
                );
                // Send waUSDC to broker for unwrapping
                IERC20(COLLATERAL).transfer(broker, waUsdcReceived);
            }
        }

        // ── 4. Unwrap waUSDC → USDC (inside broker) ─────────────────────
        {
            uint256 waUsdcBal = ERC20(COLLATERAL).balanceOf(broker);
            if (waUsdcBal > 0) {
                // Broker unwraps waUSDC → USDC
                pb.execute(
                    COLLATERAL,
                    abi.encodeCall(IWrappedAToken.unwrap, (waUsdcBal))
                );
            }
        }

        // ── 5. Repay Aave USDC debt ─────────────────────────────────────
        {
            // Get broker's variable debt balance
            uint256 usdcBal = ERC20(USDC).balanceOf(broker);
            if (usdcBal > 0) {
                // Approve USDC → Aave pool (from broker)
                pb.execute(
                    USDC,
                    abi.encodeCall(IERC20.approve, (AAVE_POOL, usdcBal))
                );
                // Repay debt
                pb.execute(
                    AAVE_POOL,
                    abi.encodeCall(IAavePool.repay, (USDC, usdcBal, 2, broker))
                );
            }
        }

        // ── 6. Withdraw sUSDe from Aave ─────────────────────────────────
        {
            // Withdraw max sUSDe from Aave → broker
            pb.execute(
                AAVE_POOL,
                abi.encodeCall(
                    IAavePool.withdraw,
                    (SUSDE, type(uint256).max, broker)
                )
            );
        }

        // ── 7. Transfer sUSDe from broker → user ────────────────────────
        {
            uint256 susdeBal = ERC20(SUSDE).balanceOf(broker);
            if (susdeBal > 0) {
                // Use execute to transfer sUSDe from broker to user
                pb.execute(
                    SUSDE,
                    abi.encodeCall(IERC20.transfer, (msg.sender, susdeBal))
                );
            }
        }

        // ── 8. Sweep any remaining USDC to user ─────────────────────────
        {
            uint256 usdcLeft = ERC20(USDC).balanceOf(broker);
            if (usdcLeft > 0) {
                pb.execute(
                    USDC,
                    abi.encodeCall(IERC20.transfer, (msg.sender, usdcLeft))
                );
            }
        }

        emit BasisTradeClosed(
            msg.sender,
            broker,
            ERC20(SUSDE).balanceOf(msg.sender)
        );
    }

    /* ═══════════════════════════════════════ INTERNAL — LEVERAGE LOOP ════════════════════════════ */

    /// @dev Executes the Aave leverage loop:
    ///   1. Send sUSDe to broker
    ///   2. Broker supplies sUSDe to Aave
    ///   3. Broker borrows USDC from Aave
    ///   4. Swap USDC → sUSDe on Curve
    ///   5. Repeat steps 2-4 for leverage iterations
    ///
    /// For simplicity, this uses iterative looping.
    /// Flash loan optimization can be added as a V2 upgrade.
    function _executeLeverageLoop(
        PrimeBroker pb,
        address broker,
        uint256 initialSUsde,
        uint256 leverage
    ) internal {
        // Transfer initial sUSDe to broker
        IERC20(SUSDE).transfer(broker, initialSUsde);

        // First supply: deposit initial sUSDe to Aave
        pb.execute(
            SUSDE,
            abi.encodeCall(IERC20.approve, (AAVE_POOL, type(uint256).max))
        );
        pb.execute(
            AAVE_POOL,
            abi.encodeCall(IAavePool.supply, (SUSDE, initialSUsde, broker, 0))
        );

        // Loop: borrow USDC → swap to sUSDe → supply to Aave
        // Each iteration borrows ~(1/leverage) of collateral value
        uint256 totalSupplied = initialSUsde;
        for (uint256 i = 1; i < leverage; i++) {
            // Estimate USDC to borrow (conservative: 70% of new collateral value)
            // sUSDe ≈ $1, USDC ≈ $1, so amounts are roughly 1:1
            // Aave LTV for sUSDe is typically ~75%
            uint256 borrowAmount = (initialSUsde * 7) / 10; // 70% LTV per loop

            // Borrow USDC from Aave
            pb.execute(
                AAVE_POOL,
                abi.encodeCall(
                    IAavePool.borrow,
                    (USDC, borrowAmount, 2, 0, broker)
                )
            );

            // Withdraw USDC from broker → this contract for Curve swap
            pb.execute(
                USDC,
                abi.encodeCall(IERC20.transfer, (address(this), borrowAmount))
            );

            // Swap USDC → sUSDe on Curve
            IERC20(USDC).approve(CURVE_POOL, borrowAmount);
            uint256 sUsdeReceived = ICurvePool(CURVE_POOL).exchange(
                CURVE_USDC_INDEX,
                CURVE_SUSDE_INDEX,
                borrowAmount,
                0 // TODO: add min_dy for production
            );

            // Send sUSDe back to broker
            IERC20(SUSDE).transfer(broker, sUsdeReceived);

            // Supply new sUSDe to Aave
            pb.execute(
                AAVE_POOL,
                abi.encodeCall(
                    IAavePool.supply,
                    (SUSDE, sUsdeReceived, broker, 0)
                )
            );

            totalSupplied += sUsdeReceived;
        }
    }

    /// @dev Borrows USDC for the hedge portion and wraps to waUSDC
    function _borrowAndWrapForHedge(
        PrimeBroker pb,
        address broker,
        uint256 hedgeAmount
    ) internal {
        if (hedgeAmount == 0) return;

        // Borrow USDC from Aave (hedge portion)
        pb.execute(
            AAVE_POOL,
            abi.encodeCall(IAavePool.borrow, (USDC, hedgeAmount, 2, 0, broker))
        );

        // Wrap USDC → waUSDC: broker approves Aave pool, supplies USDC, wraps aToken
        // Step 1: Send USDC from broker → this contract
        pb.execute(
            USDC,
            abi.encodeCall(IERC20.transfer, (address(this), hedgeAmount))
        );

        // Step 2: This contract supplies USDC to Aave → gets aUSDC, wraps to waUSDC
        IWrappedAToken wrapper = IWrappedAToken(COLLATERAL);
        address aTokenAddr = wrapper.aToken();
        IAToken aToken = IAToken(aTokenAddr);
        address underlying = aToken.UNDERLYING_ASSET_ADDRESS();
        address pool = aToken.POOL();

        IERC20(underlying).approve(pool, hedgeAmount);
        IAavePool(pool).supply(underlying, hedgeAmount, address(this), 0);

        uint256 aBalance = ERC20(aTokenAddr).balanceOf(address(this));
        IERC20(aTokenAddr).approve(COLLATERAL, aBalance);
        uint256 waUsdcShares = wrapper.wrap(aBalance);

        // Send waUSDC to broker
        IERC20(COLLATERAL).transfer(broker, waUsdcShares);
    }

    /// @dev Buys wRLP with waUSDC on V4
    function _buyWRLP(
        PrimeBroker pb,
        address broker,
        uint256 waUsdcAmount,
        PoolKey calldata poolKey
    ) internal {
        if (waUsdcAmount == 0) return;

        // Withdraw waUSDC from broker → this contract
        pb.withdrawCollateral(address(this), waUsdcAmount);

        // Swap waUSDC → wRLP on V4
        address positionToken = pb.positionToken();
        uint256 wrlpReceived = _swapExactInput(
            COLLATERAL,
            positionToken,
            waUsdcAmount,
            poolKey
        );

        // Send wRLP to broker
        IERC20(positionToken).transfer(broker, wrlpReceived);
    }

    /// @dev Submits TWAMM order to sell wRLP → waUSDC
    function _submitTwammHedge(
        PrimeBroker pb,
        address broker,
        uint256 duration,
        PoolKey calldata poolKey
    ) internal {
        address positionToken = pb.positionToken();
        uint256 wrlpBalance = ERC20(positionToken).balanceOf(broker);

        if (wrlpBalance == 0) return;

        // Determine sell direction: wRLP → waUSDC
        bool zeroForOne = positionToken < COLLATERAL;

        IJTM.SubmitOrderParams memory params = IJTM.SubmitOrderParams({
            key: poolKey,
            zeroForOne: zeroForOne,
            duration: duration,
            amountIn: wrlpBalance
        });

        pb.submitTwammOrder(TWAMM_HOOK, params);
    }

    /* ═══════════════════════════════════════ V4 SWAP HELPERS ═════════════════════════════════════ */

    /// @dev Swap exact input on V4 pool
    function _swapExactInput(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        PoolKey calldata poolKey
    ) internal returns (uint256 amountOut) {
        bool zeroForOne = tokenIn < tokenOut;

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(amountIn),
            sqrtPriceLimitX96: zeroForOne
                ? uint160(4295128740)
                : uint160(1461446703485210103287273052203988822378723970341)
        });

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

        amountOut = zeroForOne
            ? uint256(int256(delta.amount1()))
            : uint256(int256(delta.amount0()));
    }

    /// @notice V4 swap settlement callback
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
}
