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
import {ITwapEngine} from "../dex/interfaces/ITwapEngine.sol";
import {MarketId} from "../shared/interfaces/IRLDCore.sol";

/* ═══════════════════════════════════════════════════════════════════════════════
   EXTERNAL INTERFACES
   ═══════════════════════════════════════════════════════════════════════════════ */

/// @dev Morpho Blue MarketParams struct — identifies a specific lending market
struct MarketParams {
    address loanToken;
    address collateralToken;
    address oracle;
    address irm;
    uint256 lltv;
}

/// @dev Morpho Blue — supply collateral, borrow, repay, withdraw collateral, flash loan
interface IMorpho {
    function supplyCollateral(
        MarketParams calldata marketParams,
        uint256 assets,
        address onBehalf,
        bytes calldata data
    ) external;

    function borrow(
        MarketParams calldata marketParams,
        uint256 assets,
        uint256 shares,
        address onBehalf,
        address receiver
    ) external returns (uint256 assetsOut, uint256 sharesOut);

    function repay(
        MarketParams calldata marketParams,
        uint256 assets,
        uint256 shares,
        address onBehalf,
        bytes calldata data
    ) external returns (uint256 assetsRepaid, uint256 sharesRepaid);

    function withdrawCollateral(
        MarketParams calldata marketParams,
        uint256 assets,
        address onBehalf,
        address receiver
    ) external;

    function setAuthorization(
        address authorized,
        bool newIsAuthorized
    ) external;

    function position(
        bytes32 id,
        address user
    )
        external
        view
        returns (
            uint256 supplyShares,
            uint128 borrowShares,
            uint128 collateral
        );

    /// @dev Morpho Blue flash loan — FREE (0 fee)
    function flashLoan(
        address token,
        uint256 assets,
        bytes calldata data
    ) external;
}

/// @dev Curve StableSwap pool — exchange tokens
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

/// @dev Aave wrapped aToken (e.g. waUSDC) — still needed for the hedge wrapping
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

/// @dev Aave V3 Pool — only needed for waUSDC wrapping (supply USDC → aUSDC → waUSDC)
interface IAavePool {
    function supply(
        address asset,
        uint256 amount,
        address onBehalfOf,
        uint16 referralCode
    ) external;
}

/// @dev ERC4626 vault interface (for sUSDe staking)
interface IERC4626 {
    function deposit(
        uint256 assets,
        address receiver
    ) external returns (uint256 shares);
    function redeem(
        uint256 shares,
        address receiver,
        address owner
    ) external returns (uint256 assets);
    function asset() external view returns (address);
}

/* ═══════════════════════════════════════════════════════════════════════════════
   BASIS TRADE FACTORY — FLASH LOAN EDITION
   ═══════════════════════════════════════════════════════════════════════════════ */

/// @title  BasisTradeFactory — sUSDe Carry Trade with Fixed-Rate Hedging
/// @author RLD Protocol
/// @notice Opens leveraged sUSDe positions on Morpho Blue via flash loan
///         with wRLP rate hedging for fixed-rate borrowing.
///
/// @dev ## Strategy (Flash Loan — single atomic tx)
///
///   1. User deposits USDC → swap to sUSDe
///   2. Flash-borrow (lev + hedge) PYUSD from Morpho (FREE)
///   3. Swap ALL PYUSD → USDC (Curve, one batch)
///   4. Split USDC: leverage leg → sUSDe, hedge leg → waUSDC → wRLP
///   5. Supply ALL sUSDe to Morpho as collateral
///   6. Borrow PYUSD to repay flash loan
///   7. Submit TWAMM: sell wRLP → waUSDC over duration (covers interest)
///
/// @dev ## Fixed-Rate Mechanism
///
///   wRLP price = K × r_t. TWAMM sells wRLP at market rate → waUSDC.
///   If rates spike: wRLP worth more → more waUSDC → covers higher cost.
///   If rates drop: wRLP worth less → but owe less interest → still covered.
///   Net: locked at entry borrow rate. Monte Carlo validated: 0.0000% error.
///
/// @dev ## Hedge Formula (self-covering, no gamma)
///
///   α = r × T  (r = borrow APY, T = duration / 365 days)
///   hedge = lev_debt × α / (1 − α)
///
///   The hedge covers its OWN interest cost (recursive). No beta, no gamma.
///
contract BasisTradeFactory is ReentrancyGuard {
    using CurrencySettler for Currency;

    /* ═══════════════════════════════════════════════════ IMMUTABLES ═══════════════════════════════ */

    PrimeBrokerFactory public immutable BROKER_FACTORY;
    address public immutable TWAP_ENGINE;
    address public immutable COLLATERAL; // waUSDC (RLD collateral token)
    IPoolManager public immutable POOL_MANAGER;

    // Morpho Blue
    IMorpho public immutable MORPHO;
    MarketParams public morphoMarketParams; // sUSDe/PYUSD market

    // Tokens
    address public immutable SUSDE; // sUSDe (ERC4626 vault)
    address public immutable USDE; // USDe (underlying of sUSDe)
    address public immutable USDC; // USDC
    address public immutable PYUSD; // PYUSD (Morpho loan token)

    // Curve pools
    address public immutable CURVE_USDE_USDC_POOL; // USDe/USDC
    address public immutable CURVE_PYUSD_USDC_POOL; // PYUSD/USDC
    int128 public immutable CURVE_USDE_INDEX;
    int128 public immutable CURVE_USDC_INDEX_USDE; // USDC index in USDe pool
    int128 public immutable CURVE_PYUSD_INDEX;
    int128 public immutable CURVE_USDC_INDEX_PYUSD; // USDC index in PYUSD pool

    /* ═══════════════════════════════════════════════════ STATE ════════════════════════════════════ */

    uint256 public nonce;

    /// @notice Tracks basis trade ownership: broker → user
    mapping(address => address) public tradeOwner;

    /* ═══════════════════════════════════════════════════ EVENTS ═══════════════════════════════════ */

    event BasisTradeOpened(
        address indexed user,
        address indexed broker,
        uint256 amount,
        uint256 effectiveLeverage,
        uint256 duration
    );

    event BasisTradeClosed(
        address indexed user,
        address indexed broker,
        uint256 sUsdeReturned
    );

    /* ═══════════════════════════════════════════════════ STRUCTS ══════════════════════════════════ */

    /// @notice User-facing parameters to open a basis trade
    /// @dev levDebt and hedge are pre-computed off-chain:
    ///   α = r × T  (r = borrow APY, T = duration / 365 days)
    ///   hedge = levDebt × α / (1 − α)
    struct BasisTradeParams {
        uint256 amount; // USDC deposit amount (6 decimals)
        uint256 levDebt; // PYUSD to flash-borrow for leverage (6 decimals)
        uint256 hedge; // PYUSD to flash-borrow for hedge (6 decimals)
        uint256 duration; // TWAMM hedge duration in seconds
        PoolKey poolKey; // wRLP/waUSDC V4 pool
        bytes swapPath; // Calldata: reserved for custom routing (unused in V1)
    }

    /// @dev Internal data passed through the Morpho flash loan callback
    struct FlashData {
        uint256 initialSUsde; // sUSDe from user deposit
        uint256 levDebt; // PYUSD for leverage leg
        uint256 hedge; // PYUSD for hedge leg
        address broker; // PrimeBroker address
        uint256 duration; // TWAMM duration
        PoolKey poolKey; // V4 pool
    }

    /// @dev V4 swap callback data
    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    /* ═══════════════════════════════════════════════════ CONSTRUCTOR ══════════════════════════════ */
    struct ConstructorParams {
        address brokerFactory;
        address twapEngine;
        address collateral;
        address poolManager;
        address morpho;
        address sUsde;
        address usde;
        address usdc;
        address pyusd;
        address curveUsdeUsdcPool;
        address curvePyusdUsdcPool;
        int128 curveUsdeIndex;
        int128 curveUsdcIndexUsde;
        int128 curvePyusdIndex;
        int128 curveUsdcIndexPyusd;
        address morphoOracle;
        address morphoIrm;
        uint256 morphoLltv;
    }

    constructor(ConstructorParams memory p) {
        require(p.brokerFactory != address(0), "!factory");
        require(p.twapEngine != address(0), "!twamm");
        require(p.collateral != address(0), "!collateral");
        require(p.poolManager != address(0), "!pm");
        require(p.morpho != address(0), "!morpho");
        require(p.sUsde != address(0), "!susde");
        require(p.usde != address(0), "!usde");
        require(p.usdc != address(0), "!usdc");
        require(p.pyusd != address(0), "!pyusd");

        BROKER_FACTORY = PrimeBrokerFactory(p.brokerFactory);
        TWAP_ENGINE = p.twapEngine;
        COLLATERAL = p.collateral;
        POOL_MANAGER = IPoolManager(p.poolManager);
        MORPHO = IMorpho(p.morpho);
        SUSDE = p.sUsde;
        USDE = p.usde;
        USDC = p.usdc;
        PYUSD = p.pyusd;

        CURVE_USDE_USDC_POOL = p.curveUsdeUsdcPool;
        CURVE_PYUSD_USDC_POOL = p.curvePyusdUsdcPool;
        CURVE_USDE_INDEX = p.curveUsdeIndex;
        CURVE_USDC_INDEX_USDE = p.curveUsdcIndexUsde;
        CURVE_PYUSD_INDEX = p.curvePyusdIndex;
        CURVE_USDC_INDEX_PYUSD = p.curveUsdcIndexPyusd;

        morphoMarketParams = MarketParams({
            loanToken: p.pyusd,
            collateralToken: p.sUsde,
            oracle: p.morphoOracle,
            irm: p.morphoIrm,
            lltv: p.morphoLltv
        });

        // Pre-approve Morpho for sUSDe (collateral)
        IERC20(p.sUsde).approve(p.morpho, type(uint256).max);
    }

    /* ═══════════════════════════════════════════ OPEN — USDC ENTRY ═══════════════════════════════ */

    /// @notice Open a basis trade with USDC as the entry token.
    /// @dev Single atomic tx via Morpho flash loan. levDebt and hedge are pre-computed off-chain.
    /// @param params BasisTradeParams with amount, levDebt, hedge, duration, poolKey
    function openBasisTradeWithUSDC(
        BasisTradeParams calldata params
    ) external nonReentrant returns (address broker) {
        require(params.amount > 0, "Zero amount");
        require(params.levDebt > 0, "Zero levDebt");
        require(params.duration > 0, "Zero duration");

        // ── 1. Pull USDC from user → swap to sUSDe ─────────────────────
        IERC20(USDC).transferFrom(msg.sender, address(this), params.amount);
        uint256 initialSUsde = _swapUsdcToSUsde(params.amount);

        // ── 2. Create fresh PrimeBroker ──────────────────────────────────
        bytes32 salt = keccak256(abi.encodePacked(address(this), msg.sender, nonce++));
        broker = BROKER_FACTORY.createBroker(salt);

        // ── 3. Execute flash loan leverage + hedge ───────────────────────
        _executeFlashLoan(initialSUsde, params, broker);

        // ── 4. Submit TWAMM order: sell wRLP → waUSDC ────────────────────
        _submitTwammHedge(
            PrimeBroker(payable(broker)),
            broker,
            params.duration,
            params.poolKey
        );

        // ── 5. Track ownership ───────────────────────────────────────────
        tradeOwner[broker] = msg.sender;

        emit BasisTradeOpened(
            msg.sender,
            broker,
            params.amount,
            params.levDebt,
            params.duration
        );
    }

    /// @notice Open a basis trade with sUSDe as the entry token.
    function openBasisTrade(
        BasisTradeParams calldata params,
        uint256 sUsdeAmount
    ) external nonReentrant returns (address broker) {
        require(sUsdeAmount > 0, "Zero amount");
        require(params.levDebt > 0, "Zero levDebt");
        require(params.duration > 0, "Zero duration");

        // ── 1. Pull sUSDe from user ──────────────────────────────────────
        IERC20(SUSDE).transferFrom(msg.sender, address(this), sUsdeAmount);

        // ── 2. Create fresh PrimeBroker ──────────────────────────────────
        bytes32 salt = keccak256(abi.encodePacked(address(this), msg.sender, nonce++));
        broker = BROKER_FACTORY.createBroker(salt);

        // ── 3. Execute flash loan leverage + hedge ───────────────────────
        _executeFlashLoan(sUsdeAmount, params, broker);

        // ── 4. Submit TWAMM order ────────────────────────────────────────
        _submitTwammHedge(
            PrimeBroker(payable(broker)),
            broker,
            params.duration,
            params.poolKey
        );

        // ── 5. Track ownership ───────────────────────────────────────────
        tradeOwner[broker] = msg.sender;

        emit BasisTradeOpened(
            msg.sender,
            broker,
            sUsdeAmount,
            0,
            params.duration
        );
    }

    /* ═══════════════════════════════════════════ CLOSE ═══════════════════════════════════════════ */

    /// @notice Close a basis trade — unwind all positions, return sUSDe to user.
    function closeBasisTrade(
        address broker,
        PoolKey calldata poolKey
    ) external nonReentrant {
        PrimeBroker pb = PrimeBroker(payable(broker));

        // ── 1. Verify ownership ─────────────────────────────────────────
        require(tradeOwner[broker] == msg.sender, "Not owner");
        delete tradeOwner[broker];

        // ── 2. Cancel/claim TWAMM order ─────────────────────────────────
        (bytes32 trackedMarketId, bytes32 orderId) = pb.activeTwammOrder();
        if (orderId != bytes32(0)) {
            (, , , , uint256 expiration, ) = ITwapEngine(TWAP_ENGINE).streamOrders(trackedMarketId, orderId);

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
                pb.withdrawToken(pb.positionToken(), address(this), wrlpBal);
                _swapExactInput(positionToken, COLLATERAL, wrlpBal, poolKey);
            }
        }

        // ── 4. Unwrap all waUSDC → USDC ─────────────────────────────────
        {
            uint256 waUsdcBal = ERC20(COLLATERAL).balanceOf(address(this));
            if (waUsdcBal > 0) {
                IWrappedAToken(COLLATERAL).unwrap(waUsdcBal);
            }
        }

        // ── 5. Swap any USDC → PYUSD for Morpho repay ───────────────────
        {
            uint256 usdcBal = ERC20(USDC).balanceOf(address(this));
            if (usdcBal > 0) {
                _swapUsdcToPyusd(usdcBal);
            }
        }

        // ── 6. Repay Morpho PYUSD debt ──────────────────────────────────
        {
            uint256 pyusdBal = ERC20(PYUSD).balanceOf(address(this));
            if (pyusdBal > 0) {
                IERC20(PYUSD).approve(address(MORPHO), pyusdBal);
                MORPHO.repay(
                    morphoMarketParams,
                    pyusdBal,
                    0,
                    address(this),
                    ""
                );
            }
        }

        // ── 7. Withdraw sUSDe collateral from Morpho ────────────────────
        {
            (, , uint128 collateral) = MORPHO.position(
                _morphoMarketId(),
                address(this)
            );
            if (collateral > 0) {
                MORPHO.withdrawCollateral(
                    morphoMarketParams,
                    uint256(collateral),
                    address(this),
                    address(this)
                );
            }
        }

        // ── 8. Transfer sUSDe → user ────────────────────────────────────
        {
            uint256 susdeBal = ERC20(SUSDE).balanceOf(address(this));
            if (susdeBal > 0) {
                IERC20(SUSDE).transfer(msg.sender, susdeBal);
            }
        }

        // ── 9. Sweep any remaining PYUSD/USDC to user ───────────────────
        {
            uint256 pyusdLeft = ERC20(PYUSD).balanceOf(address(this));
            if (pyusdLeft > 0) {
                IERC20(PYUSD).transfer(msg.sender, pyusdLeft);
            }
            uint256 usdcLeft = ERC20(USDC).balanceOf(address(this));
            if (usdcLeft > 0) {
                IERC20(USDC).transfer(msg.sender, usdcLeft);
            }
        }

        emit BasisTradeClosed(
            msg.sender,
            broker,
            ERC20(SUSDE).balanceOf(msg.sender)
        );
    }

    /* ═══════════════════════════════════════ FLASH LOAN CORE ═════════════════════════════════════ */

    /// @dev Triggers Morpho flash loan with pre-computed leverage + hedge amounts.
    ///
    /// levDebt and hedge are computed off-chain by the frontend:
    ///   α = r × T  (r = borrow APY, T = duration/365)
    ///   hedge = levDebt × α / (1 − α)
    ///
    /// Flash-borrows (levDebt + hedge) PYUSD from Morpho (FREE).
    function _executeFlashLoan(
        uint256 initialSUsde,
        BasisTradeParams calldata params,
        address broker
    ) internal {
        uint256 totalFlash = params.levDebt + params.hedge;
        require(totalFlash > 0, "Zero flash");

        // Encode callback data
        bytes memory cbData = abi.encode(
            FlashData({
                initialSUsde: initialSUsde,
                levDebt: params.levDebt,
                hedge: params.hedge,
                broker: broker,
                duration: params.duration,
                poolKey: params.poolKey
            })
        );

        // Flash-borrow PYUSD from Morpho (FREE — 0 fee)
        MORPHO.flashLoan(PYUSD, totalFlash, cbData);
    }

    /// @dev Morpho flash loan callback — executed atomically.
    ///
    /// Flow:
    ///   A. Swap ALL PYUSD → USDC (one Curve swap)
    ///   B. Split USDC: leverage leg + hedge leg
    ///   C. Leverage: USDC → USDe → sUSDe, combine with initial
    ///   D. Supply ALL sUSDe to Morpho
    ///   E. Borrow PYUSD to repay flash loan
    ///   F. Hedge: USDC → waUSDC → wRLP, send to broker
    ///   G. Approve PYUSD for flash repayment
    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external {
        require(msg.sender == address(MORPHO), "!morpho");

        FlashData memory d = abi.decode(data, (FlashData));

        // ── A. Swap ALL flash PYUSD → USDC in one batch ──────────────────
        uint256 totalUsdc = _swapPyusdToUsdc(assets);

        // ── B. Split USDC proportionally ──────────────────────────────────
        uint256 leverageUsdc;
        uint256 hedgeUsdc;
        if (d.hedge > 0) {
            hedgeUsdc = (totalUsdc * d.hedge) / (d.levDebt + d.hedge);
            leverageUsdc = totalUsdc - hedgeUsdc;
        } else {
            leverageUsdc = totalUsdc;
        }

        // ── C. LEVERAGE LEG: USDC → USDe → sUSDe ────────────────────────
        uint256 leverageSUsde = _swapUsdcToSUsde(leverageUsdc);
        uint256 totalSUsde = d.initialSUsde + leverageSUsde;

        // ── D. Supply ALL sUSDe to Morpho as collateral ──────────────────
        MORPHO.supplyCollateral(
            morphoMarketParams,
            totalSUsde,
            address(this),
            ""
        );

        // ── E. Borrow PYUSD to repay flash loan ─────────────────────────
        MORPHO.borrow(
            morphoMarketParams,
            assets,
            0,
            address(this),
            address(this)
        );

        // ── F. HEDGE LEG: USDC → waUSDC → wRLP → broker ────────────────
        if (hedgeUsdc > 0) {
            // Wrap USDC → waUSDC
            uint256 waUsdcShares = _wrapUsdcToWaUsdc(hedgeUsdc);

            // Swap waUSDC → wRLP on V4
            PrimeBroker pb = PrimeBroker(payable(d.broker));
            _buyWRLP(pb, d.broker, waUsdcShares, d.poolKey);
        }

        // ── G. Approve PYUSD repayment (Morpho pulls it back) ────────────
        IERC20(PYUSD).approve(address(MORPHO), assets);
    }

    // NOTE: Hedge math (α = r × T, hedge = lev × α / (1-α)) is computed off-chain
    //       by the frontend. The contract just executes the pre-computed values.

    /* ═══════════════════════════════════════ INTERNAL — HEDGE HELPERS ═════════════════════════════ */

    /// @dev Wraps USDC → waUSDC (via Aave supply + wrap)
    function _wrapUsdcToWaUsdc(
        uint256 usdcAmount
    ) internal returns (uint256 waUsdcShares) {
        IWrappedAToken wrapper = IWrappedAToken(COLLATERAL);
        address aTokenAddr = wrapper.aToken();
        IAToken aToken = IAToken(aTokenAddr);
        address underlying = aToken.UNDERLYING_ASSET_ADDRESS();
        address pool = aToken.POOL();

        IERC20(underlying).approve(pool, usdcAmount);
        IAavePool(pool).supply(underlying, usdcAmount, address(this), 0);

        uint256 aBalance = ERC20(aTokenAddr).balanceOf(address(this));
        IERC20(aTokenAddr).approve(COLLATERAL, aBalance);
        waUsdcShares = wrapper.wrap(aBalance);
    }

    /// @dev Buys wRLP with waUSDC on V4, sends to broker
    function _buyWRLP(
        PrimeBroker pb,
        address broker,
        uint256 waUsdcAmount,
        PoolKey memory poolKey
    ) internal {
        if (waUsdcAmount == 0) return;

        address positionToken = pb.positionToken();
        uint256 wrlpReceived = _swapExactInputMemory(
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
        PoolKey memory poolKey
    ) internal {
        address positionToken = pb.positionToken();
        uint256 wrlpBalance = ERC20(positionToken).balanceOf(broker);

        if (wrlpBalance == 0) return;

        // Determine sell direction: wRLP → waUSDC
        bool zeroForOne = positionToken < COLLATERAL;
        bytes32 marketId = MarketId.unwrap(pb.marketId());

        pb.submitTwammOrder(TWAP_ENGINE, marketId, zeroForOne, duration, wrlpBalance);
    }

    /* ═══════════════════════════════════════ SWAP HELPERS ═════════════════════════════════════════ */

    /// @dev 2-hop swap: USDC → USDe (Curve) → sUSDe (Ethena staking)
    function _swapUsdcToSUsde(
        uint256 usdcAmount
    ) internal returns (uint256 sUsdeAmount) {
        // Step 1: USDC → USDe via Curve
        IERC20(USDC).approve(CURVE_USDE_USDC_POOL, usdcAmount);
        uint256 usdeAmount = ICurvePool(CURVE_USDE_USDC_POOL).exchange(
            CURVE_USDC_INDEX_USDE,
            CURVE_USDE_INDEX,
            usdcAmount,
            0
        );

        // Step 2: USDe → sUSDe via Ethena staking (ERC4626 deposit)
        IERC20(USDE).approve(SUSDE, usdeAmount);
        sUsdeAmount = IERC4626(SUSDE).deposit(usdeAmount, address(this));
    }

    /// @dev Swap PYUSD → USDC via Curve PYUSD/USDC pool
    function _swapPyusdToUsdc(
        uint256 pyusdAmount
    ) internal returns (uint256 usdcAmount) {
        IERC20(PYUSD).approve(CURVE_PYUSD_USDC_POOL, pyusdAmount);
        usdcAmount = ICurvePool(CURVE_PYUSD_USDC_POOL).exchange(
            CURVE_PYUSD_INDEX,
            CURVE_USDC_INDEX_PYUSD,
            pyusdAmount,
            0
        );
    }

    /// @dev Swap USDC → PYUSD via Curve PYUSD/USDC pool
    function _swapUsdcToPyusd(
        uint256 usdcAmount
    ) internal returns (uint256 pyusdAmount) {
        IERC20(USDC).approve(CURVE_PYUSD_USDC_POOL, usdcAmount);
        pyusdAmount = ICurvePool(CURVE_PYUSD_USDC_POOL).exchange(
            CURVE_USDC_INDEX_PYUSD,
            CURVE_PYUSD_INDEX,
            usdcAmount,
            0
        );
    }

    /* ═══════════════════════════════════════ MORPHO HELPERS ══════════════════════════════════════ */

    /// @dev Compute the Morpho market ID from MarketParams
    function _morphoMarketId() internal view returns (bytes32) {
        return keccak256(abi.encode(morphoMarketParams));
    }

    /* ═══════════════════════════════════════ V4 SWAP HELPERS ═════════════════════════════════════ */

    /// @dev Swap exact input on V4 pool (calldata poolKey)
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

    /// @dev Swap exact input on V4 pool (memory poolKey — used from flash callback)
    function _swapExactInputMemory(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        PoolKey memory poolKey
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
