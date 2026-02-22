// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {JITRLDIntegrationBase} from "../shared/JITRLDIntegrationBase.t.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {IPrimeBroker} from "../../../src/shared/interfaces/IPrimeBroker.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {PoolSwapTest} from "v4-core/src/test/PoolSwapTest.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../../../src/rld/core/PrimeBrokerFactory.sol";
import {
    BrokerVerifier
} from "../../../src/rld/modules/verifier/BrokerVerifier.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {
    IPositionManager
} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {
    IAllowanceTransfer
} from "permit2/src/interfaces/IAllowanceTransfer.sol";
import {
    LiquidityAmounts
} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import {IERC721} from "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import "forge-std/console.sol";

/// @title  Liquidation Test Suite — Shared Base
/// @author RLD Protocol
///
/// @notice Provides broker setup helpers, LP provision, oracle mocking, and
///         result logging for all liquidation integration tests.
///
/// @dev ## Test Coverage Matrix (42 tests across 8 files)
///
///     ### Tier 1: Single-Asset Liquidation  (LiquidationSingleAsset.t.sol)
///
///     | ID  | Test                    | Collateral    | Trigger    |
///     |-----|-------------------------|---------------|------------|
///     | T1  | PureCash                | Cash only     | Price ↑    |
///     | T2  | PureWRLP                | wRLP only     | Price ↑    |
///     | T3  | LPOnly                  | V4 LP only    | Price ↑    |
///
///     ### Tier 2: Multi-Asset Cascade  (LiquidationCascade.t.sol)
///
///     | ID  | Test                    | Collateral         | UW?  |
///     |-----|-------------------------|--------------------|------|
///     | T4  | CashPlusWRLP            | Cash + wRLP        | ✓    |
///     | T5  | WRLPplusLP              | wRLP + LP          | ✓    |
///     | T6  | FullCascade_NotUW       | Cash+wRLP+LP       | ✗    |
///     | T7  | FullCascade_UW          | Cash+wRLP+LP       | ✓    |
///
///     ### Tier 3: Edge Cases  (LiquidationEdgeCases.t.sol)
///
///     | ID  | Test                    | Scenario                    |
///     |-----|-------------------------|-----------------------------|
///     | T8  | CloseFactorReverts      | Over 50% → revert           |
///     | T9  | CloseFactorBypassUW     | >100%  ok when underwater   |
///     | T10 | SlippageReverts         | minSeize enforcement         |
///     | T11 | SequentialLiquidation   | Two partial liquidations     |
///
///     ### Tier 4: Out-of-Range LP  (LiquidationOutOfRange.t.sol)
///
///     | ID  | Test                    | LP Range  | Scenario       |
///     |-----|-------------------------|-----------|----------------|
///     | T12 | OOR_LP_Above            | Above     | LP only        |
///     | T13 | OOR_LP_Below            | Below     | LP only        |
///     | T14 | Cash_Plus_OOR_Above     | Above     | Cash + LP      |
///     | T15 | Cash_Plus_OOR_Below     | Below     | Cash + LP      |
///
///     ### Tier 5: TWAMM Single-Order  (LiquidationTwammSingle.t.sol)
///
///     | ID   | Test                   | Ghost State      | Clear? |
///     |------|------------------------|------------------|--------|
///     | T16  | JustPlaced             | t=0, no ghost    | ✗      |
///     | T17a | NoClearing             | 50% streamed     | ✗      |
///     | T17b | WithClearing           | 50% + cleared    | ✓      |
///     | T18a | NoClear (sellPosition) | reverse dir      | ✗      |
///     | T18b | WithClear (sellPos)    | reverse dir      | ✓      |
///
///     ### Tier 6: TWAMM + Multi-Asset Cascade  (LiquidationTwammCascade.t.sol)
///
///     | ID   | Test                   | Assets           | Clear? |
///     |------|------------------------|------------------|--------|
///     | T19  | CashPlusTWAMM          | Cash + TWAMM     | ✗      |
///     | T19b | CashPlusTWAMM_Cleared  | Cash + TWAMM     | ✓      |
///     | T20  | TWAMMplusLP            | TWAMM + LP       | ✗      |
///     | T20b | TWAMMplusLP_Cleared    | TWAMM + LP       | ✓      |
///     | T21  | FullCascade            | All assets       | ✗      |
///     | T21b | FullCascade_Cleared    | All assets       | ✓      |
///
///     ### Tier 7: TWAMM Full Stack  (LiquidationTwammFull.t.sol)
///
///     | ID   | Test                   | UW?  | Clear? |
///     |------|------------------------|------|--------|
///     | T22  | FullStack_NotUW        | ✗    | ✗      |
///     | T22b | FullStack_NotUW_Clear  | ✗    | ✓      |
///     | T23  | FullStack_UW           | ✓    | ✗      |
///     | T23b | FullStack_UW_Cleared   | ✓    | ✓      |
///
///     ### Tier 8: ForceSettle  (LiquidationForceSettle.t.sol)
///
///     | ID  | Test                    | Scenario                    |
///     |-----|-------------------------|-----------------------------|
///     | T24 | GhostToZero             | ghost→0, earnings captured  |
///     | T25 | AccessControl           | only verifier can call      |
///     | T26 | DuringLiquidation       | forceSettle mid-liquidation |
///     | T27 | ZeroGhost               | no-op when ghost=0          |
///
///     ### Ghost-Aware NAV Coverage
///
///     Tests T17a, T17b, T18a, T18b, T19/T19b, T20/T20b, T22/T22b, T23/T23b
///     exercise the `JitTwammBrokerModule.getValue()` three-term formula:
///       - "no clear" variants verify ghost is visible in NAV (term 3)
///       - "with clear" variants verify cleared earnings are visible (term 2)
///       - Oracle price shocks account for ghost-inclusive NAV
///
abstract contract LiquidationBase is JITRLDIntegrationBase {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;

    PrimeBrokerFactory public brokerFactory;
    IRLDCore.MarketAddresses public ma;
    MockERC20 public collateralMock;
    PoolKey public lpPoolKey;
    PoolSwapTest public swapRouter;
    address public liquidator;
    uint256 brokerNonce;

    uint256 constant INDEX_PRICE_WAD = 5e18;
    uint256 constant USER_DEBT = 10_000e6;
    uint256 constant LIQUIDATOR_WRLP = 50_000e6;
    uint256 constant LIQUIDATOR_CASH = 1_000_000e6;

    function _initialSqrtPrice() internal override returns (uint160) {
        uint256 p = (address(ct) > address(pt))
            ? INDEX_PRICE_WAD
            : FullMath.mulDiv(1e18, 1e18, INDEX_PRICE_WAD);
        return _computeSqrtPriceX96(p);
    }

    function _tweakSetup() internal override {
        ma = core.getMarketAddresses(marketId);
        IRLDCore.MarketConfig memory mc = core.getMarketConfig(marketId);
        brokerFactory = PrimeBrokerFactory(
            BrokerVerifier(mc.brokerVerifier).FACTORY()
        );
        collateralMock = MockERC20(ma.collateralToken);
        swapRouter = new PoolSwapTest(IPoolManager(address(poolManager)));
        vm.mockCall(
            address(v4Oracle),
            abi.encodeWithSelector(
                bytes4(keccak256("getSpotPrice(address,address)")),
                ma.positionToken,
                ma.collateralToken
            ),
            abi.encode(INDEX_PRICE_WAD)
        );
        liquidator = makeAddr("liquidator");
        _fundLiquidator();
        _initLPPool();
    }

    function _fundLiquidator() internal {
        PrimeBroker helper = _createBroker();
        collateralMock.transfer(address(helper), LIQUIDATOR_WRLP * 20);
        helper.modifyPosition(
            MarketId.unwrap(marketId),
            int256(LIQUIDATOR_WRLP * 20),
            int256(LIQUIDATOR_WRLP)
        );
        helper.withdrawPositionToken(liquidator, LIQUIDATOR_WRLP);
        collateralMock.transfer(liquidator, LIQUIDATOR_CASH);
        vm.prank(liquidator);
        ERC20(ma.positionToken).approve(address(core), type(uint256).max);
    }

    function _createBroker() internal returns (PrimeBroker) {
        bytes32 salt = keccak256(abi.encodePacked("liq", brokerNonce++));
        return PrimeBroker(payable(brokerFactory.createBroker(salt)));
    }

    function _initLPPool() internal {
        address posToken = ma.positionToken;
        address colToken = ma.collateralToken;
        (Currency c0, Currency c1) = _sortCurrencies(
            Currency.wrap(posToken),
            Currency.wrap(colToken)
        );
        lpPoolKey = PoolKey({
            currency0: c0,
            currency1: c1,
            fee: 3000,
            tickSpacing: int24(60),
            hooks: IHooks(address(0))
        });
        uint256 price = (Currency.unwrap(c0) == colToken)
            ? FullMath.mulDiv(1e18, 1e18, INDEX_PRICE_WAD)
            : INDEX_PRICE_WAD;
        poolManager.initialize(lpPoolKey, _computeSqrtPriceX96(price));
        ERC20(posToken).approve(address(swapRouter), type(uint256).max);
        ERC20(colToken).approve(address(swapRouter), type(uint256).max);
        ERC20(posToken).approve(PERMIT2_ADDRESS, type(uint256).max);
        ERC20(colToken).approve(PERMIT2_ADDRESS, type(uint256).max);
    }

    // ======================== LP PROVISION ========================

    function _provideV4LP(
        PrimeBroker broker,
        uint256 wAmt,
        uint256 cAmt
    ) internal returns (uint256 tokenId) {
        broker.withdrawPositionToken(address(this), wAmt);
        broker.withdrawCollateral(address(this), cAmt);
        vm.warp(1_700_000_000);
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            ma.positionToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            ma.collateralToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );

        (, int24 tick, , ) = poolManager.getSlot0(lpPoolKey.toId());
        int24 sp = lpPoolKey.tickSpacing;
        int24 lo = (tick / sp) * sp - 3000;
        int24 hi = lo + 6000;
        uint256 a0;
        uint256 a1;
        if (Currency.unwrap(lpPoolKey.currency0) == ma.positionToken) {
            a0 = wAmt;
            a1 = cAmt;
        } else {
            a0 = cAmt;
            a1 = wAmt;
        }
        uint128 liq = LiquidityAmounts.getLiquidityForAmounts(
            TickMath.getSqrtPriceAtTick(tick),
            TickMath.getSqrtPriceAtTick(lo),
            TickMath.getSqrtPriceAtTick(hi),
            a0,
            a1
        );
        require(liq > 0, "zero liq");
        bytes memory acts = abi.encodePacked(
            uint8(Actions.MINT_POSITION),
            uint8(Actions.SETTLE_PAIR)
        );
        bytes[] memory p = new bytes[](2);
        p[0] = abi.encode(
            lpPoolKey,
            lo,
            hi,
            uint256(liq),
            uint128((a0 * 110) / 100),
            uint128((a1 * 110) / 100),
            address(this),
            bytes("")
        );
        p[1] = abi.encode(lpPoolKey.currency0, lpPoolKey.currency1);
        positionManager.modifyLiquidities(
            abi.encode(acts, p),
            block.timestamp + 60
        );
        tokenId = positionManager.nextTokenId() - 1;
        IERC721(address(positionManager)).transferFrom(
            address(this),
            address(broker),
            tokenId
        );
        broker.setActiveV4Position(tokenId);
    }

    /// @dev Provide V4 LP at tick range entirely above or below current tick.
    ///      `above=true` → holds token0 only. `above=false` → holds token1 only.
    function _provideV4LPOutOfRange(
        PrimeBroker broker,
        uint256 amount,
        bool above
    ) internal returns (uint256 tokenId) {
        bool wRLPisC0 = Currency.unwrap(lpPoolKey.currency0) ==
            ma.positionToken;
        if (above) {
            if (wRLPisC0) broker.withdrawPositionToken(address(this), amount);
            else broker.withdrawCollateral(address(this), amount);
        } else {
            if (wRLPisC0) broker.withdrawCollateral(address(this), amount);
            else broker.withdrawPositionToken(address(this), amount);
        }

        vm.warp(1_700_000_000);
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            ma.positionToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            ma.collateralToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );

        (, int24 tick, , ) = poolManager.getSlot0(lpPoolKey.toId());
        int24 sp = lpPoolKey.tickSpacing;
        int24 lo;
        int24 hi;
        if (above) {
            lo = ((tick / sp) + 1) * sp + 600;
            hi = lo + 6000;
        } else {
            hi = ((tick / sp)) * sp - 600;
            lo = hi - 6000;
        }

        uint256 a0 = above ? amount : 0;
        uint256 a1 = above ? 0 : amount;
        uint128 liq = LiquidityAmounts.getLiquidityForAmounts(
            TickMath.getSqrtPriceAtTick(tick),
            TickMath.getSqrtPriceAtTick(lo),
            TickMath.getSqrtPriceAtTick(hi),
            a0,
            a1
        );
        require(liq > 0, "zero liq OOR");

        bytes memory acts = abi.encodePacked(
            uint8(Actions.MINT_POSITION),
            uint8(Actions.SETTLE_PAIR)
        );
        bytes[] memory p = new bytes[](2);
        p[0] = abi.encode(
            lpPoolKey,
            lo,
            hi,
            uint256(liq),
            uint128((a0 * 110) / 100),
            uint128((a1 * 110) / 100),
            address(this),
            bytes("")
        );
        p[1] = abi.encode(lpPoolKey.currency0, lpPoolKey.currency1);
        positionManager.modifyLiquidities(
            abi.encode(acts, p),
            block.timestamp + 60
        );
        tokenId = positionManager.nextTokenId() - 1;
        IERC721(address(positionManager)).transferFrom(
            address(this),
            address(broker),
            tokenId
        );
        broker.setActiveV4Position(tokenId);
    }

    // ======================== ORACLE & PRICE ========================

    function _movePoolPrice(uint256 targetPriceWad) internal {
        uint160 targetSqrt = _computeSqrtPriceX96(
            (Currency.unwrap(lpPoolKey.currency0) == ma.collateralToken)
                ? FullMath.mulDiv(1e18, 1e18, targetPriceWad)
                : targetPriceWad
        );
        bool wRLPisCurrency0 = Currency.unwrap(lpPoolKey.currency0) ==
            ma.positionToken;
        swapRouter.swap(
            lpPoolKey,
            SwapParams({
                zeroForOne: !wRLPisCurrency0,
                amountSpecified: -int256(100_000e6),
                sqrtPriceLimitX96: targetSqrt
            }),
            PoolSwapTest.TestSettings({
                takeClaims: false,
                settleUsingBurn: false
            }),
            ""
        );
    }

    function _setOraclePrice(uint256 priceWad) internal {
        testOracle.setIndexPrice(priceWad);
        testOracle.setSpotPrice(priceWad);
        vm.mockCall(
            address(v4Oracle),
            abi.encodeWithSelector(
                bytes4(keccak256("getSpotPrice(address,address)")),
                ma.positionToken,
                ma.collateralToken
            ),
            abi.encode(priceWad)
        );
    }

    // ======================== BROKER SETUP ========================

    function _setupBroker(
        uint256 targetCash,
        uint256 targetWRLP,
        uint256 lpWRLP,
        uint256 lpCol
    ) internal returns (PrimeBroker broker, uint256 tokenId) {
        broker = _createBroker();

        uint256 buffer = (lpWRLP > 0) ? 100_000e6 : 0;
        uint256 totalTransfer = targetCash + lpCol + buffer;
        collateralMock.transfer(address(broker), totalTransfer);

        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(totalTransfer),
            int256(USER_DEBT)
        );

        if (lpWRLP > 0 && lpCol > 0) {
            tokenId = _provideV4LP(broker, lpWRLP, lpCol);
        }

        uint256 currentWRLP = ERC20(ma.positionToken).balanceOf(
            address(broker)
        );
        if (currentWRLP > targetWRLP) {
            broker.withdrawPositionToken(
                address(this),
                currentWRLP - targetWRLP
            );
        }
        uint256 currentCash = ERC20(ma.collateralToken).balanceOf(
            address(broker)
        );
        if (currentCash > targetCash) {
            try
                broker.withdrawCollateral(
                    address(this),
                    currentCash - targetCash
                )
            {} catch {}
        }

        uint256 fc = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 fw = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Setup: cash:", fc / 1e6);
        console.log("  Setup: wRLP:", fw / 1e6);
    }

    function _setupBrokerOOR(
        uint256 targetCash,
        uint256 targetWRLP,
        uint256 lpAmount,
        bool above
    ) internal returns (PrimeBroker broker, uint256 tokenId) {
        broker = _createBroker();

        uint256 buffer = 50_000e6;
        uint256 totalTransfer = targetCash + lpAmount + buffer;
        collateralMock.transfer(address(broker), totalTransfer);

        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(totalTransfer),
            int256(USER_DEBT)
        );

        if (lpAmount > 0) {
            tokenId = _provideV4LPOutOfRange(broker, lpAmount, above);
        }

        uint256 currentWRLP = ERC20(ma.positionToken).balanceOf(
            address(broker)
        );
        if (currentWRLP > targetWRLP) {
            broker.withdrawPositionToken(
                address(this),
                currentWRLP - targetWRLP
            );
        }
        uint256 currentCash = ERC20(ma.collateralToken).balanceOf(
            address(broker)
        );
        if (currentCash > targetCash) {
            try
                broker.withdrawCollateral(
                    address(this),
                    currentCash - targetCash
                )
            {} catch {}
        }

        uint256 fc = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 fw = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Setup: cash:", fc / 1e6);
        console.log("  Setup: wRLP:", fw / 1e6);
        bool isC0wRLP = Currency.unwrap(lpPoolKey.currency0) ==
            ma.positionToken;
        console.log(
            "  OOR LP:",
            above ? "above (token0 only)" : "below (token1 only)"
        );
        console.log("  token0 is:", isC0wRLP ? "wRLP" : "collateral");
    }

    // ======================== SNAP & LOG ========================

    struct Snap {
        uint256 bCash;
        uint256 bWRLP;
        uint128 lpLiq;
        uint256 lCash;
        uint256 lWRLP;
    }

    function _snap(
        PrimeBroker broker,
        uint256 tid
    ) internal view returns (Snap memory s) {
        s.bCash = ERC20(ma.collateralToken).balanceOf(address(broker));
        s.bWRLP = ERC20(ma.positionToken).balanceOf(address(broker));
        s.lpLiq = tid > 0 ? positionManager.getPositionLiquidity(tid) : 0;
        s.lCash = ERC20(ma.collateralToken).balanceOf(liquidator);
        s.lWRLP = ERC20(ma.positionToken).balanceOf(liquidator);
    }

    function _logResult(Snap memory b, Snap memory a) internal pure {
        console.log("  --- RESULT ---");
        console.log("  Broker cash:", b.bCash / 1e6, "->", a.bCash / 1e6);
        console.log("  Broker wRLP:", b.bWRLP / 1e6, "->", a.bWRLP / 1e6);
        if (b.lpLiq > 0) {
            uint256 pct = (uint256(a.lpLiq) * 100) / uint256(b.lpLiq);
            console.log("  LP remaining:", pct, "%");
        }
        if (b.bWRLP > a.bWRLP)
            console.log("  wRLP extracted:", (b.bWRLP - a.bWRLP) / 1e6);
        if (b.bCash > a.bCash)
            console.log("  Cash seized:", (b.bCash - a.bCash) / 1e6);
        if (b.lWRLP > a.lWRLP)
            console.log("  Liq wRLP spent:", (b.lWRLP - a.lWRLP) / 1e6);
        if (a.lCash > b.lCash)
            console.log("  Liq cash gained:", (a.lCash - b.lCash) / 1e6);
    }

    function _liquidate(
        PrimeBroker broker,
        uint256 tokenId,
        uint256 priceWad,
        bool movePool
    ) internal {
        Snap memory b = _snap(broker, tokenId);
        if (movePool) _movePoolPrice(priceWad);
        _setOraclePrice(priceWad);

        uint256 nav = broker.getNetAccountValue();
        uint256 dv = FullMath.mulDiv(USER_DEBT, priceWad, 1e18);
        bool uw = nav < dv;
        console.log("  NAV:", nav / 1e6, "debtVal:", dv / 1e6);
        console.log("  Underwater:", uw);
        assertFalse(core.isSolvent(marketId, address(broker)), "insolvent");

        uint256 dtc = uw ? USER_DEBT : USER_DEBT / 2;
        console.log("  dtc:", dtc / 1e6);
        vm.prank(liquidator);
        core.liquidate(marketId, address(broker), dtc, 0);

        Snap memory a = _snap(broker, tokenId);
        _logResult(b, a);
        _setOraclePrice(INDEX_PRICE_WAD);
    }
}
