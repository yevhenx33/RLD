// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {PrimeBroker} from "../rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../rld/core/PrimeBrokerFactory.sol";
import {IERC20} from "../shared/interfaces/IERC20.sol";
import {IRLDCore, MarketId} from "../shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../shared/interfaces/IRLDOracle.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId} from "v4-core/src/types/PoolId.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";
import {ITwapEngine} from "../dex/interfaces/ITwapEngine.sol";
import {IV4Quoter} from "v4-periphery/src/interfaces/IV4Quoter.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

/// @title CDSCoverageFactory
/// @notice Single-transaction fixed-coverage CDS opener.
/// @dev Strategy wrapper over isolated CDS PrimeBrokers. It is deliberately not
///      the primitive itself: independent underwriters/fiduciaries can still use
///      brokers, AMMs, TWAMMs, RFQs, or OTC execution directly.
contract CDSCoverageFactory is ReentrancyGuard {
    using CurrencySettler for Currency;

    uint256 public constant WAD = 1e18;
    uint256 public constant SECONDS_PER_YEAR = 365 days;
    uint256 public constant INDEX_SCALAR = 100;
    uint256 public constant MAX_CDS_R_MAX_WAD = 1e18; // 100%

    PrimeBrokerFactory public immutable BROKER_FACTORY;
    IRLDCore public immutable CORE;
    address public immutable TWAP_ENGINE;
    address public immutable COLLATERAL;
    IPoolManager public immutable POOL_MANAGER;
    IV4Quoter public immutable QUOTER;
    MarketId public immutable MARKET_ID;

    /// @notice Snapshotted effective CDS r_max, capped at 100%.
    uint256 public immutable R_MAX_CDS_WAD;

    uint256 public nonce;
    mapping(address => address) public coverageOwner;

    event CoverageOpened(
        address indexed user,
        address indexed broker,
        uint256 coverage,
        uint256 initialCost,
        uint256 premiumBudget,
        uint256 initialPositionTokens,
        uint256 duration
    );

    event CoverageClosed(
        address indexed user,
        address indexed broker,
        uint256 collateralReturned,
        uint256 positionReturned
    );

    event CoverageClaimed(address indexed user, address indexed broker);
    event CoverageReturned(address indexed user, address indexed broker);

    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    struct OpenCoverageVars {
        address positionToken;
        uint256 initialPositionTokens;
        uint256 initialCost;
        uint256 premiumBudget;
        uint256 totalRequired;
        uint256 positionReceived;
        bool sellCollateral;
    }

    constructor(
        address brokerFactory_,
        address core_,
        address twapEngine_,
        address collateral_,
        address poolManager_,
        address quoter_,
        uint256 rMaxSnapshotWad_
    ) {
        require(brokerFactory_ != address(0), "Invalid factory");
        require(core_ != address(0), "Invalid core");
        require(twapEngine_ != address(0), "Invalid twapEngine");
        require(collateral_ != address(0), "Invalid collateral");
        require(poolManager_ != address(0), "Invalid poolManager");
        require(quoter_ != address(0), "Invalid quoter");
        require(rMaxSnapshotWad_ > 0, "Invalid rMax");

        BROKER_FACTORY = PrimeBrokerFactory(brokerFactory_);
        CORE = IRLDCore(core_);
        TWAP_ENGINE = twapEngine_;
        COLLATERAL = collateral_;
        POOL_MANAGER = IPoolManager(poolManager_);
        QUOTER = IV4Quoter(quoter_);
        MARKET_ID = PrimeBrokerFactory(brokerFactory_).MARKET_ID();
        R_MAX_CDS_WAD = rMaxSnapshotWad_ > MAX_CDS_R_MAX_WAD
            ? MAX_CDS_R_MAX_WAD
            : rMaxSnapshotWad_;
    }

    /// @notice Open fixed CDS coverage in one transaction.
    /// @dev Pulls initial buy cost + premium stream budget from the user.
    ///      The initial buy establishes coverage now; TWAMM budget maintains
    ///      coverage as NF decays over the selected duration.
    function openCoverage(
        uint256 coverage,
        uint256 duration,
        PoolKey calldata poolKey
    ) external nonReentrant returns (address broker) {
        require(coverage > 0, "Zero coverage");
        require(duration > 0, "Zero duration");

        OpenCoverageVars memory vars;
        vars.positionToken = CORE.getMarketAddresses(MARKET_ID).positionToken;
        _validatePoolKey(poolKey, COLLATERAL, vars.positionToken);

        vars.initialPositionTokens = initialTokensForCoverage(coverage);
        vars.initialCost = _quoteExactPositionOut(
            vars.initialPositionTokens,
            vars.positionToken,
            poolKey
        );
        vars.premiumBudget = premiumBudgetForCoverage(coverage, duration);
        vars.totalRequired = vars.initialCost + vars.premiumBudget;

        broker = BROKER_FACTORY.createBroker(
            keccak256(abi.encodePacked(address(this), msg.sender, nonce++))
        );

        IERC20(COLLATERAL).transferFrom(msg.sender, address(this), vars.totalRequired);

        vars.positionReceived = _swapExactInput(
            COLLATERAL,
            vars.positionToken,
            vars.initialCost,
            poolKey
        );
        IERC20(vars.positionToken).transfer(broker, vars.positionReceived);

        IERC20(COLLATERAL).transfer(broker, vars.premiumBudget);

        vars.sellCollateral = Currency.unwrap(poolKey.currency0) == COLLATERAL;
        PrimeBroker(payable(broker)).submitTwammOrder(
            TWAP_ENGINE,
            _ghostPoolId(poolKey),
            vars.sellCollateral,
            duration,
            vars.premiumBudget
        );

        PrimeBroker(payable(broker)).freeze();
        coverageOwner[broker] = msg.sender;

        emit CoverageOpened(
            msg.sender,
            broker,
            coverage,
            vars.initialCost,
            vars.premiumBudget,
            vars.initialPositionTokens,
            duration
        );
    }

    /// @notice Close fixed coverage and return residual collateral to user.
    /// @dev Cancels/claims TWAMM, liquidates remaining position tokens to
    ///      collateral, and returns all collateral. Settlement payout flows are
    ///      intentionally handled by the market settlement module.
    function closeCoverage(address broker, PoolKey calldata poolKey) external nonReentrant {
        PrimeBroker pb = PrimeBroker(payable(broker));
        uint256 tokenId = uint256(uint160(broker));

        if (coverageOwner[broker] == msg.sender) {
            delete coverageOwner[broker];
        } else {
            BROKER_FACTORY.transferFrom(msg.sender, address(this), tokenId);
        }

        if (pb.frozen()) {
            pb.unfreeze();
        }

        _handleTwammClose(pb);

        address positionToken = pb.positionToken();
        _validatePoolKey(poolKey, pb.collateralToken(), positionToken);

        uint256 positionBalance = ERC20(positionToken).balanceOf(broker);
        if (positionBalance > 0) {
            pb.withdrawToken(positionToken, address(this), positionBalance);
            uint256 collateralReceived = _swapExactInput(
                positionToken,
                pb.collateralToken(),
                positionBalance,
                poolKey
            );
            IERC20(pb.collateralToken()).transfer(broker, collateralReceived);
        }

        uint256 collateralBalance = ERC20(pb.collateralToken()).balanceOf(broker);
        if (collateralBalance > 0) {
            pb.withdrawToken(pb.collateralToken(), msg.sender, collateralBalance);
        }

        emit CoverageClosed(msg.sender, broker, collateralBalance, positionBalance);
    }

    function claimCoverage(address broker) external {
        require(coverageOwner[broker] == msg.sender, "Not coverage owner");
        delete coverageOwner[broker];
        uint256 tokenId = uint256(uint160(broker));
        BROKER_FACTORY.transferFrom(address(this), msg.sender, tokenId);
        emit CoverageClaimed(msg.sender, broker);
    }

    function returnCoverage(address broker) external {
        uint256 tokenId = uint256(uint160(broker));
        BROKER_FACTORY.transferFrom(msg.sender, address(this), tokenId);
        coverageOwner[broker] = msg.sender;
        emit CoverageReturned(msg.sender, broker);
    }

    function initialTokensForCoverage(uint256 coverage) public view returns (uint256) {
        // pMaxWad = 100 * rMax, expressed as USDC per wCDS in WAD.
        uint256 pMaxWad = INDEX_SCALAR * R_MAX_CDS_WAD;
        return (coverage * WAD) / pMaxWad;
    }

    function premiumBudgetForCoverage(
        uint256 coverage,
        uint256 duration
    ) public view returns (uint256) {
        uint256 indexPriceWad = _indexPriceWad();
        uint256 rateWad = indexPriceWad / INDEX_SCALAR;
        return (coverage * rateWad * duration) / (WAD * SECONDS_PER_YEAR);
    }

    function quoteOpenCoverage(
        uint256 coverage,
        uint256 duration,
        PoolKey calldata poolKey
    )
        external
        returns (
            uint256 initialPositionTokens,
            uint256 initialCost,
            uint256 premiumBudget,
            uint256 totalRequired
        )
    {
        IRLDCore.MarketAddresses memory addrs = CORE.getMarketAddresses(MARKET_ID);
        _validatePoolKey(poolKey, COLLATERAL, addrs.positionToken);
        initialPositionTokens = initialTokensForCoverage(coverage);
        initialCost = _quoteExactPositionOut(initialPositionTokens, addrs.positionToken, poolKey);
        premiumBudget = premiumBudgetForCoverage(coverage, duration);
        totalRequired = initialCost + premiumBudget;
    }

    function _indexPriceWad() internal view returns (uint256) {
        IRLDCore.MarketAddresses memory addrs = CORE.getMarketAddresses(MARKET_ID);
        return IRLDOracle(addrs.rateOracle).getIndexPrice(
            addrs.underlyingPool,
            addrs.underlyingToken
        );
    }

    function _quoteExactPositionOut(
        uint256 positionAmount,
        address positionToken,
        PoolKey calldata poolKey
    ) internal returns (uint256 amountIn) {
        bool zeroForOne = COLLATERAL < positionToken;
        (amountIn, ) = QUOTER.quoteExactOutputSingle(
            IV4Quoter.QuoteExactSingleParams({
                poolKey: poolKey,
                zeroForOne: zeroForOne,
                exactAmount: uint128(positionAmount),
                hookData: new bytes(0)
            })
        );
    }

    function _ghostPoolId(PoolKey calldata poolKey) internal pure returns (bytes32) {
        PoolKey memory k = poolKey;
        k.hooks = IHooks(address(0));
        return PoolId.unwrap(k.toId());
    }

    function _handleTwammClose(PrimeBroker pb) internal {
        (bytes32 trackedMarketId, bytes32 orderId) = pb.activeTwammOrder();
        if (orderId != bytes32(0)) {
            (, , , , uint256 expiration, ) =
                ITwapEngine(TWAP_ENGINE).streamOrders(trackedMarketId, orderId);
            if (block.timestamp >= expiration) {
                pb.claimExpiredTwammOrder();
            } else {
                pb.cancelTwammOrder();
            }
        }
    }

    function _validatePoolKey(
        PoolKey calldata poolKey,
        address collateral,
        address position
    ) internal pure {
        address currency0 = Currency.unwrap(poolKey.currency0);
        address currency1 = Currency.unwrap(poolKey.currency1);
        require(poolKey.hooks == IHooks(address(0)), "Unexpected hooks");
        require(
            (currency0 == collateral && currency1 == position) ||
                (currency0 == position && currency1 == collateral),
            "Wrong pool"
        );
    }

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

    function unlockCallback(bytes calldata rawData) external returns (bytes memory) {
        require(msg.sender == address(POOL_MANAGER), "Not PoolManager");

        SwapCallback memory data = abi.decode(rawData, (SwapCallback));
        BalanceDelta delta = POOL_MANAGER.swap(
            data.key,
            data.params,
            new bytes(0)
        );

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
