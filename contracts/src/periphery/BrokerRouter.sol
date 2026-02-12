// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {PrimeBroker} from "../rld/broker/PrimeBroker.sol";
import {MarketId} from "../shared/interfaces/IRLDCore.sol";
import {IERC20} from "../shared/interfaces/IERC20.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";
import {ERC721} from "solmate/src/tokens/ERC721.sol";
import {ISignatureTransfer} from "permit2/src/interfaces/ISignatureTransfer.sol";

/// @title BrokerRouter — Universal Execution Layer for PrimeBroker
/// @author RLD Protocol
/// @notice Single contract handling deposit, long, and short operations for any RLD market.
///         Replaces BrokerExecutor + LeverageShortExecutor with one unified router.
///
/// @dev Architecture:
///  ┌─────────────────────────────────────────────────────────────────┐
///  │                        BrokerRouter                             │
///  │  Pre-approved operator on every broker (set during init)        │
///  │                                                                 │
///  │  ┌───────────┐   ┌──────────────┐   ┌──────────────┐           │
///  │  │ deposit()  │   │ executeLong() │   │ executeShort()│          │
///  │  └───────────┘   └──────────────┘   └──────────────┘           │
///  │         │                │                  │                    │
///  │         └────────────────┼──────────────────┘                   │
///  │                          │                                      │
///  │               onlyBrokerAuthorized                              │
///  │          msg.sender == NFT owner OR operator                    │
///  └─────────────────────────────────────────────────────────────────┘
///
/// @dev Security Model:
///  - Router is set as a permanent operator during broker.initialize()
///  - Every function checks msg.sender is the broker NFT owner or an operator
///  - Router never holds user funds between transactions
///  - Deposit routes are admin-registered (supports multi-market)
contract BrokerRouter is ReentrancyGuard {
    using CurrencySettler for Currency;

    /* ============================================================================================ */
    /*                                          IMMUTABLES                                          */
    /* ============================================================================================ */

    /// @notice Uniswap V4 PoolManager singleton
    IPoolManager public immutable poolManager;

    /// @notice Permit2 singleton for gasless token approvals
    ISignatureTransfer public immutable permit2;

    /// @notice Protocol admin who can register deposit routes
    address public owner;

    /* ============================================================================================ */
    /*                                       DEPOSIT ROUTES                                         */
    /* ============================================================================================ */

    /// @notice Configuration for wrapping underlying tokens into broker collateral
    /// @dev Maps collateralToken → how to produce it from raw underlying
    struct DepositRoute {
        address underlying;   // e.g. USDC
        address aToken;       // e.g. aUSDC
        address wrapped;      // e.g. waUSDC (the broker's collateralToken)
        address aavePool;     // Aave V3 pool for supply()
    }

    /// @notice Registry: collateralToken → deposit route
    mapping(address => DepositRoute) public depositRoutes;

    /* ============================================================================================ */
    /*                                          SWAP STATE                                          */
    /* ============================================================================================ */

    /// @notice Callback data for V4 swap settlement
    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    /* ============================================================================================ */
    /*                                           EVENTS                                             */
    /* ============================================================================================ */

    event DepositRouteSet(address indexed collateralToken, address underlying, address aavePool);
    event LongExecuted(address indexed broker, uint256 amountIn, uint256 amountOut);
    event LongClosed(address indexed broker, uint256 amountIn, uint256 amountOut);
    event ShortExecuted(address indexed broker, uint256 debtAmount, uint256 proceeds);
    event ShortClosed(address indexed broker, uint256 debtRepaid, uint256 collateralSpent);
    event Deposited(address indexed broker, uint256 underlyingAmount, uint256 wrappedAmount);

    /* ============================================================================================ */
    /*                                           ERRORS                                             */
    /* ============================================================================================ */

    error NotAuthorized();
    error NoDepositRoute();
    error NotPoolManager();

    /* ============================================================================================ */
    /*                                          MODIFIERS                                           */
    /* ============================================================================================ */

    /// @notice Restricts to broker NFT owner or any operator of that broker
    /// @dev Mirrors PrimeBroker's own onlyAuthorized modifier
    modifier onlyBrokerAuthorized(address broker) {
        PrimeBroker pb = PrimeBroker(payable(broker));
        address brokerOwner = ERC721(pb.factory()).ownerOf(uint256(uint160(broker)));
        if (msg.sender != brokerOwner && !pb.operators(msg.sender)) revert NotAuthorized();
        _;
    }

    /// @notice Restricts to protocol admin
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    /* ============================================================================================ */
    /*                                         CONSTRUCTOR                                          */
    /* ============================================================================================ */

    /// @param _poolManager Uniswap V4 PoolManager singleton
    /// @param _permit2 Permit2 singleton address
    constructor(address _poolManager, address _permit2) {
        poolManager = IPoolManager(_poolManager);
        permit2 = ISignatureTransfer(_permit2);
        owner = msg.sender;
    }

    /* ============================================================================================ */
    /*                                         ADMIN                                                */
    /* ============================================================================================ */

    /// @notice Register a deposit route for a collateral token
    /// @dev Called once per market. Maps collateralToken → wrapping path.
    ///      Example: waUSDC → {underlying: USDC, aToken: aUSDC, wrapped: waUSDC, aavePool: ...}
    /// @param collateralToken The broker's collateral token (e.g. waUSDC)
    /// @param route The wrapping path configuration
    function setDepositRoute(address collateralToken, DepositRoute calldata route) external onlyOwner {
        require(route.underlying != address(0), "Invalid route");
        depositRoutes[collateralToken] = route;
        emit DepositRouteSet(collateralToken, route.underlying, route.aavePool);
    }

    /// @notice Transfer ownership of the router
    /// @param newOwner The new admin address
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Invalid owner");
        owner = newOwner;
    }

    /* ============================================================================================ */
    /*                                         DEPOSIT                                              */
    /* ============================================================================================ */

    /// @notice Deposit underlying tokens (e.g. USDC), auto-wrap into broker collateral (e.g. waUSDC)
    /// @dev Flow: Pull USDC via Permit2 → supply to Aave → wrap aUSDC → send to broker
    ///      Uses the deposit route registry for market-independent wrapping.
    /// @param broker The PrimeBroker to deposit into
    /// @param amount Amount of underlying tokens to deposit
    /// @param permit Permit2 transfer parameters (token, amount, nonce, deadline)
    /// @param signature User's Permit2 signature authorizing the transfer
    function deposit(
        address broker,
        uint256 amount,
        ISignatureTransfer.PermitTransferFrom calldata permit,
        bytes calldata signature
    ) external onlyBrokerAuthorized(broker) nonReentrant {
        PrimeBroker pb = PrimeBroker(payable(broker));
        address collateral = pb.collateralToken();
        DepositRoute memory route = depositRoutes[collateral];
        if (route.underlying == address(0)) revert NoDepositRoute();

        // 1. Pull underlying (e.g. USDC) from user via Permit2
        permit2.permitTransferFrom(
            permit,
            ISignatureTransfer.SignatureTransferDetails({
                to: address(this),
                requestedAmount: amount
            }),
            msg.sender,
            signature
        );

        // 2. Supply to Aave → get aToken
        IERC20(route.underlying).approve(route.aavePool, amount);
        // Aave V3 supply: supply(asset, amount, onBehalfOf, referralCode)
        (bool success,) = route.aavePool.call(
            abi.encodeWithSignature(
                "supply(address,uint256,address,uint16)",
                route.underlying, amount, address(this), 0
            )
        );
        require(success, "Aave supply failed");

        // 3. Wrap aToken → collateral token (e.g. aUSDC → waUSDC)
        uint256 aBalance = IERC20(route.aToken).balanceOf(address(this));
        IERC20(route.aToken).approve(route.wrapped, aBalance);
        // ERC4626-style deposit: deposit(assets, receiver)
        (bool wrapSuccess,) = route.wrapped.call(
            abi.encodeWithSignature(
                "deposit(uint256,address)",
                aBalance, broker  // Mint wrapped tokens directly to broker
            )
        );
        require(wrapSuccess, "Wrap failed");

        emit Deposited(broker, amount, aBalance);
    }

    /// @notice Deposit underlying tokens using a standard ERC20 approval (no Permit2)
    /// @dev Requires user to have called underlying.approve(router, amount) beforehand
    /// @param broker The PrimeBroker to deposit into
    /// @param amount Amount of underlying tokens to deposit
    function depositWithApproval(
        address broker,
        uint256 amount
    ) external onlyBrokerAuthorized(broker) nonReentrant {
        PrimeBroker pb = PrimeBroker(payable(broker));
        address collateral = pb.collateralToken();
        DepositRoute memory route = depositRoutes[collateral];
        if (route.underlying == address(0)) revert NoDepositRoute();

        // 1. Pull underlying from user via standard transferFrom
        IERC20(route.underlying).transferFrom(msg.sender, address(this), amount);

        // 2. Supply to Aave → get aToken
        IERC20(route.underlying).approve(route.aavePool, amount);
        (bool success,) = route.aavePool.call(
            abi.encodeWithSignature(
                "supply(address,uint256,address,uint16)",
                route.underlying, amount, address(this), 0
            )
        );
        require(success, "Aave supply failed");

        // 3. Wrap aToken → collateral token → send to broker
        uint256 aBalance = IERC20(route.aToken).balanceOf(address(this));
        IERC20(route.aToken).approve(route.wrapped, aBalance);
        (bool wrapSuccess,) = route.wrapped.call(
            abi.encodeWithSignature(
                "deposit(uint256,address)",
                aBalance, broker
            )
        );
        require(wrapSuccess, "Wrap failed");

        emit Deposited(broker, amount, aBalance);
    }

    /* ============================================================================================ */
    /*                                       EXECUTE LONG                                           */
    /* ============================================================================================ */

    /// @notice Buy position tokens (wRLP) with collateral (waUSDC) — simple long
    /// @dev Flow: Withdraw waUSDC from broker → swap via V4 → return wRLP to broker
    ///      Reads collateralToken and positionToken from broker (market-independent).
    /// @param broker The PrimeBroker to trade from
    /// @param amountIn Amount of collateral to swap
    /// @param poolKey V4 pool key for routing the swap
    /// @return amountOut Amount of position tokens received
    function executeLong(
        address broker,
        uint256 amountIn,
        PoolKey calldata poolKey
    ) external onlyBrokerAuthorized(broker) nonReentrant returns (uint256 amountOut) {
        PrimeBroker pb = PrimeBroker(payable(broker));
        address collateral = pb.collateralToken();
        address position = pb.positionToken();

        // 1. Withdraw collateral from broker to this router
        pb.withdrawCollateral(address(this), amountIn);

        // 2. Approve PoolManager for the swap
        IERC20(collateral).approve(address(poolManager), amountIn);

        // 3. Determine swap direction
        bool zeroForOne = collateral < position;

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(amountIn),  // negative = exact input in V4
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740  // MIN_SQRT_PRICE + 1
                : 1461446703485210103287273052203988822378723970341  // MAX_SQRT_PRICE - 1
        });

        // 4. Execute swap via PoolManager
        BalanceDelta delta = abi.decode(
            poolManager.unlock(abi.encode(SwapCallback({
                sender: address(this),
                key: poolKey,
                params: swapParams
            }))),
            (BalanceDelta)
        );

        // 5. Calculate output (the token we received — positive delta)
        amountOut = zeroForOne
            ? uint256(int256(delta.amount1()))
            : uint256(int256(delta.amount0()));

        // 6. Transfer ALL position tokens back to broker
        uint256 posBalance = IERC20(position).balanceOf(address(this));
        IERC20(position).transfer(broker, posBalance);

        emit LongExecuted(broker, amountIn, posBalance);
    }

    /* ============================================================================================ */
    /*                                       CLOSE LONG                                             */
    /* ============================================================================================ */

    /// @notice Close a long position: swap wRLP → collateral (waUSDC)
    /// @dev Flow: withdrawPositionToken → swap wRLP→waUSDC → transfer proceeds to broker
    /// @param broker The PrimeBroker to trade from
    /// @param amountIn Amount of wRLP to sell
    /// @param poolKey V4 pool key for routing the swap
    /// @return amountOut Amount of collateral received
    function closeLong(
        address broker,
        uint256 amountIn,
        PoolKey calldata poolKey
    ) external onlyBrokerAuthorized(broker) nonReentrant returns (uint256 amountOut) {
        PrimeBroker pb = PrimeBroker(payable(broker));
        address collateral = pb.collateralToken();
        address position = pb.positionToken();

        // 1. Withdraw position tokens (wRLP) from broker to this router
        pb.withdrawPositionToken(address(this), amountIn);

        // 2. Approve PoolManager for the swap
        IERC20(position).approve(address(poolManager), amountIn);

        // 3. Determine swap direction (selling position for collateral)
        bool zeroForOne = position < collateral;

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(amountIn),  // negative = exact input in V4
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740  // MIN_SQRT_PRICE + 1
                : 1461446703485210103287273052203988822378723970341  // MAX_SQRT_PRICE - 1
        });

        // 4. Execute swap via PoolManager
        BalanceDelta delta = abi.decode(
            poolManager.unlock(abi.encode(SwapCallback({
                sender: address(this),
                key: poolKey,
                params: swapParams
            }))),
            (BalanceDelta)
        );

        // 5. Calculate output (collateral received — positive delta)
        amountOut = zeroForOne
            ? uint256(int256(delta.amount1()))
            : uint256(int256(delta.amount0()));

        // 6. Transfer ALL collateral proceeds back to broker
        uint256 colBalance = IERC20(collateral).balanceOf(address(this));
        IERC20(collateral).transfer(broker, colBalance);

        emit LongClosed(broker, amountIn, colBalance);
    }

    /* ============================================================================================ */
    /*                                       EXECUTE SHORT                                          */
    /* ============================================================================================ */

    /// @notice Leveraged short: deposit collateral + mint debt → sell wRLP → deposit proceeds
    /// @dev Flow: modifyPosition(+col,+debt) → withdraw wRLP → swap → deposit proceeds
    ///      Mirrors the LeverageShortExecutor pattern in a single universal contract.
    /// @param broker The PrimeBroker to trade from
    /// @param initialCollateral Amount of collateral to use as margin
    /// @param targetDebtAmount Amount of wRLP debt to mint
    /// @param poolKey V4 pool key for routing the swap
    /// @return proceeds Amount of collateral received from selling wRLP
    function executeShort(
        address broker,
        uint256 initialCollateral,
        uint256 targetDebtAmount,
        PoolKey calldata poolKey
    ) external onlyBrokerAuthorized(broker) nonReentrant returns (uint256 proceeds) {
        PrimeBroker pb = PrimeBroker(payable(broker));
        address collateral = pb.collateralToken();
        address position = pb.positionToken();
        bytes32 rawMarketId = MarketId.unwrap(pb.marketId());

        // 1. Deposit collateral and mint wRLP debt
        pb.modifyPosition(rawMarketId, int256(initialCollateral), int256(targetDebtAmount));

        // 2. Withdraw minted wRLP to this router
        pb.withdrawPositionToken(address(this), targetDebtAmount);

        // 3. Approve PoolManager for swap
        IERC20(position).approve(address(poolManager), targetDebtAmount);

        // 4. Swap wRLP → collateral
        bool zeroForOne = position < collateral;

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(targetDebtAmount),  // negative = exact input in V4
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740
                : 1461446703485210103287273052203988822378723970341
        });

        poolManager.unlock(abi.encode(SwapCallback({
            sender: address(this),
            key: poolKey,
            params: swapParams
        })));

        // 5. Transfer ALL collateral proceeds back to broker
        proceeds = IERC20(collateral).balanceOf(address(this));
        IERC20(collateral).transfer(broker, proceeds);

        // 6. Deposit proceeds as additional collateral (no new debt)
        pb.modifyPosition(rawMarketId, int256(proceeds), int256(0));

        emit ShortExecuted(broker, targetDebtAmount, proceeds);
    }

    /* ============================================================================================ */
    /*                                       CLOSE SHORT                                            */
    /* ============================================================================================ */

    /// @notice Close (partially or fully) a short position by buying back wRLP and repaying debt
    /// @dev Flow: withdraw collateral → swap waUSDC → wRLP → transfer wRLP to broker → modifyPosition(0, -debt)
    ///      The bought wRLP is burned by Core when repaying debt.
    /// @param broker The PrimeBroker to close the short on
    /// @param collateralToSpend Amount of waUSDC to use for buying wRLP
    /// @param poolKey V4 pool key for routing the swap
    /// @return debtRepaid Amount of wRLP debt repaid
    function closeShort(
        address broker,
        uint256 collateralToSpend,
        PoolKey calldata poolKey
    ) external onlyBrokerAuthorized(broker) nonReentrant returns (uint256 debtRepaid) {
        PrimeBroker pb = PrimeBroker(payable(broker));
        address collateral = pb.collateralToken();
        address position = pb.positionToken();
        bytes32 rawMarketId = MarketId.unwrap(pb.marketId());

        // 1. Withdraw collateral (waUSDC) from broker to buy wRLP
        pb.withdrawCollateral(address(this), collateralToSpend);

        // 2. Approve PoolManager for the swap
        IERC20(collateral).approve(address(poolManager), collateralToSpend);

        // 3. Swap waUSDC → wRLP (exact input: spend all collateralToSpend)
        bool zeroForOne = collateral < position;

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(collateralToSpend),  // negative = exact input
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740  // MIN_SQRT_PRICE + 1
                : 1461446703485210103287273052203988822378723970341  // MAX_SQRT_PRICE - 1
        });

        BalanceDelta delta = abi.decode(
            poolManager.unlock(abi.encode(SwapCallback({
                sender: address(this),
                key: poolKey,
                params: swapParams
            }))),
            (BalanceDelta)
        );

        // 4. Calculate wRLP received (positive delta on the output side)
        debtRepaid = zeroForOne
            ? uint256(int256(delta.amount1()))
            : uint256(int256(delta.amount0()));

        // 5. Transfer ALL wRLP to broker (needed for burn in modifyPosition)
        uint256 posBalance = IERC20(position).balanceOf(address(this));
        IERC20(position).transfer(broker, posBalance);

        // 6. Repay debt: Core will burn wRLP from broker
        pb.modifyPosition(rawMarketId, int256(0), -int256(debtRepaid));

        // 7. Return any leftover collateral to broker
        uint256 leftover = IERC20(collateral).balanceOf(address(this));
        if (leftover > 0) {
            IERC20(collateral).transfer(broker, leftover);
        }

        emit ShortClosed(broker, debtRepaid, collateralToSpend);
    }

    /* ============================================================================================ */
    /*                                     V4 CALLBACK                                              */
    /* ============================================================================================ */

    /// @notice Uniswap V4 swap settlement callback
    /// @dev Called by PoolManager during unlock(). Settles token transfers for the swap.
    function unlockCallback(bytes calldata rawData) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert NotPoolManager();

        SwapCallback memory data = abi.decode(rawData, (SwapCallback));

        BalanceDelta delta = poolManager.swap(data.key, data.params, new bytes(0));

        // Settle: pay tokens we owe, take tokens we're owed
        if (data.params.zeroForOne) {
            if (delta.amount0() < 0) {
                data.key.currency0.settle(poolManager, data.sender, uint256(-int256(delta.amount0())), false);
            }
            if (delta.amount1() > 0) {
                data.key.currency1.take(poolManager, data.sender, uint256(int256(delta.amount1())), false);
            }
        } else {
            if (delta.amount1() < 0) {
                data.key.currency1.settle(poolManager, data.sender, uint256(-int256(delta.amount1())), false);
            }
            if (delta.amount0() > 0) {
                data.key.currency0.take(poolManager, data.sender, uint256(int256(delta.amount0())), false);
            }
        }

        return abi.encode(delta);
    }

    /* ============================================================================================ */
    /*                                        VIEW HELPERS                                          */
    /* ============================================================================================ */

    /// @notice Calculate optimal debt for target leverage (convenience helper)
    /// @param collateralAmount Initial collateral
    /// @param targetLTV Target loan-to-value (e.g., 40 = 40%)
    /// @param wRLPPriceE6 wRLP price in collateral terms (6 decimals)
    /// @return debtAmount Amount of wRLP to mint
    function calculateOptimalDebt(
        uint256 collateralAmount,
        uint256 targetLTV,
        uint256 wRLPPriceE6
    ) external pure returns (uint256 debtAmount) {
        uint256 targetDebtValue = (collateralAmount * targetLTV) / (100 - targetLTV);
        debtAmount = (targetDebtValue * 1e6) / wRLPPriceE6;
    }
}
