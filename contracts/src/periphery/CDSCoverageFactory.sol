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
import {IV4Quoter} from "v4-periphery/src/interfaces/IV4Quoter.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PeripheryGhostLib} from "./lib/PeripheryGhostLib.sol";
import {PeripheryTwapLib} from "./lib/PeripheryTwapLib.sol";

/// @title CDSCoverageFactory
/// @notice Single-transaction fixed-coverage CDS opener.
/// @dev Strategy wrapper over isolated CDS PrimeBrokers. It is deliberately not
///      the primitive itself: independent underwriters/fiduciaries can still use
///      brokers, AMMs, TWAMMs, RFQs, or OTC execution directly.
contract CDSCoverageFactory is ReentrancyGuard {
    uint256 public constant WAD = 1e18;
    uint256 public constant SECONDS_PER_YEAR = 365 days;
    uint256 public constant INDEX_SCALAR = 100;
    uint256 public constant MAX_CDS_R_MAX_WAD = 1e18; // 100%

    PrimeBrokerFactory public immutable BROKER_FACTORY;
    IRLDCore public immutable CORE;
    address public immutable TWAP_ENGINE;
    address public immutable COLLATERAL;
    address public immutable GHOST_ROUTER;
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
        address ghostRouter_,
        address quoter_,
        uint256 rMaxSnapshotWad_
    ) {
        require(brokerFactory_ != address(0), "Invalid factory");
        require(core_ != address(0), "Invalid core");
        require(twapEngine_ != address(0), "Invalid twapEngine");
        require(collateral_ != address(0), "Invalid collateral");
        require(ghostRouter_ != address(0), "Invalid ghostRouter");
        require(quoter_ != address(0), "Invalid quoter");
        require(rMaxSnapshotWad_ > 0, "Invalid rMax");

        BROKER_FACTORY = PrimeBrokerFactory(brokerFactory_);
        CORE = IRLDCore(core_);
        TWAP_ENGINE = twapEngine_;
        COLLATERAL = collateral_;
        GHOST_ROUTER = ghostRouter_;
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
        PeripheryGhostLib.validatePoolKey(poolKey, COLLATERAL, vars.positionToken);

        vars.initialPositionTokens = initialTokensForCoverage(coverage);
        vars.initialCost = _quoteExactPositionOut(
            vars.initialPositionTokens,
            poolKey
        );
        vars.premiumBudget = premiumBudgetForCoverage(coverage, duration);
        vars.totalRequired = vars.initialCost + vars.premiumBudget;

        broker = BROKER_FACTORY.createBroker(
            keccak256(abi.encodePacked(address(this), msg.sender, nonce++))
        );

        IERC20(COLLATERAL).transferFrom(msg.sender, address(this), vars.totalRequired);

        vars.positionReceived = PeripheryGhostLib.swapExactInput(
            GHOST_ROUTER,
            poolKey,
            COLLATERAL,
            vars.positionToken,
            vars.initialCost,
            0
        );
        IERC20(vars.positionToken).transfer(broker, vars.positionReceived);

        IERC20(COLLATERAL).transfer(broker, vars.premiumBudget);

        vars.sellCollateral = PeripheryGhostLib.zeroForOne(poolKey, COLLATERAL);
        PrimeBroker(payable(broker)).submitTwammOrder(
            TWAP_ENGINE,
            PeripheryGhostLib.ghostPoolId(poolKey),
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

        PeripheryTwapLib.closeActiveOrder(pb, TWAP_ENGINE);

        address positionToken = pb.positionToken();
        PeripheryGhostLib.validatePoolKey(poolKey, pb.collateralToken(), positionToken);

        uint256 positionBalance = ERC20(positionToken).balanceOf(broker);
        if (positionBalance > 0) {
            pb.withdrawToken(positionToken, address(this), positionBalance);
            uint256 collateralReceived = PeripheryGhostLib.swapExactInput(
                GHOST_ROUTER,
                poolKey,
                positionToken,
                pb.collateralToken(),
                positionBalance,
                0
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
        PeripheryGhostLib.validatePoolKey(poolKey, COLLATERAL, addrs.positionToken);
        initialPositionTokens = initialTokensForCoverage(coverage);
        initialCost = _quoteExactPositionOut(initialPositionTokens, poolKey);
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
        PoolKey calldata poolKey
    ) internal returns (uint256 amountIn) {
        bool zeroForOne = PeripheryGhostLib.zeroForOne(poolKey, COLLATERAL);
        (amountIn, ) = QUOTER.quoteExactOutputSingle(
            IV4Quoter.QuoteExactSingleParams({
                poolKey: poolKey,
                zeroForOne: zeroForOne,
                exactAmount: uint128(positionAmount),
                hookData: new bytes(0)
            })
        );
    }

}
