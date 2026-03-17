// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationBase} from "./LiquidationBase.t.sol";
import {IJTM} from "../../../src/twamm/IJTM.sol";
import {IJTM} from "../../../src/twamm/IJTM.sol";
import {IPrimeBroker} from "../../../src/shared/interfaces/IPrimeBroker.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import "forge-std/console.sol";

/// @title Shared base for TWAMM liquidation tests
/// @dev Adds TWAMM order placement, auction clearing, and registration helpers
///      on top of LiquidationBase.
///
/// ## Production Pool Architecture
///
/// The factory creates a production pool: positionToken(wRLP) / collateralToken(ct)
/// with the JTM hook. This is the pool used for TWAMM orders. It trades the
/// ACTUAL market tokens — not raw pt/ct mocks. This means:
///   - Selling collateral via TWAMM → earns positionToken (wRLP)
///   - wRLP is recognized by the seize pipeline → can be swept
///
/// ## Ghost Balance Warning
///
/// TWAMM accrual creates "ghost balances" — tokens that have been streamed
/// but NOT yet matched. On cancel, only the UNSOLD portion is refunded.
/// The SOLD portion (ghost balance) is LOST unless matched via:
///   - Layer 1: Internal netting (opposing orders)
///   - Layer 2: JIT fill (external swaps)
///   - Layer 3: Auction clear (clear() function)
///
/// Without matching, canceled orders lose accrued tokens permanently.
/// Tests that need buyTokensOut > 0 MUST call clear() first.
abstract contract LiquidationTwammBase is LiquidationBase {
    uint256 constant TWAMM_INTERVAL = 3600; // 1-hour interval (matches JTM)

    /// @dev The production TWAMM pool key: positionToken/collateralToken + JTM hook.
    ///      Constructed in setUp from market addresses. Unlike twammPoolKey (raw pt/ct),
    ///      this key trades the actual wrapped tokens visible to the seize pipeline.
    PoolKey public marketTwammKey;

    /// @dev Override setUp to:
    ///      1. Warp to a valid TWAMM interval BEFORE pool init (avoids NotInitialized)
    ///      2. Construct the market's TWAMM pool key from actual token addresses
    function setUp() public override {
        vm.warp(TWAMM_INTERVAL); // Warp to 3600 so _getIntervalTime(3600) = 3600 ≠ 0
        super.setUp();

        // Build the production pool key: positionToken / collateralToken + hook
        // Same pool that the factory initialized during createMarket()
        (Currency c0, Currency c1) = _sortCurrencies(
            Currency.wrap(ma.positionToken),
            Currency.wrap(ma.collateralToken)
        );
        marketTwammKey = PoolKey({
            currency0: c0,
            currency1: c1,
            fee: 3000,
            tickSpacing: int24(60),
            hooks: IHooks(address(twammHook))
        });

        // Fund test contract with wRLP so it can act as auction clearer.
        // clear() requires paying positionToken(wRLP) for the accrued collateral.
        _fundClearer();
    }

    /// @dev Mint wRLP for this contract so it can call clear() as the arb.
    function _fundClearer() internal {
        uint256 clearerWRLP = 500_000e6;
        PrimeBroker helper = _createBroker();
        collateralMock.transfer(address(helper), clearerWRLP * 20);
        helper.modifyPosition(
            MarketId.unwrap(marketId),
            int256(clearerWRLP * 20),
            int256(clearerWRLP)
        );
        helper.withdrawPositionToken(address(this), clearerWRLP);
        // Approve TWAMM hook to pull wRLP during clear
        ERC20(ma.positionToken).approve(address(twammHook), type(uint256).max);
    }

    /// @dev Place a TWAMM order on behalf of the broker using the PRODUCTION pool.
    ///      Uses vm.prank to submit directly via JTM, then registers
    ///      via broker.setActiveTwammOrder().
    ///
    ///      For sellCollateral=true:
    ///        - Broker sells collateralToken, earns positionToken (wRLP)
    ///        - zeroForOne depends on currency sorting
    ///
    ///      Earned wRLP is directly visible to seize pipeline's _sweepAssets()
    function _placeTwammOrder(
        PrimeBroker broker,
        uint256 amountIn,
        bool sellCollateral,
        uint256 duration
    ) internal returns (bytes32 orderId, IJTM.OrderKey memory orderKey) {
        // Determine direction based on token ordering
        bool colIsC0 = Currency.unwrap(marketTwammKey.currency0) ==
            ma.collateralToken;
        bool zeroForOne = sellCollateral ? colIsC0 : !colIsC0;

        // Determine which token the broker sells
        address sellToken = zeroForOne
            ? Currency.unwrap(marketTwammKey.currency0)
            : Currency.unwrap(marketTwammKey.currency1);

        // Broker approves TWAMM hook to pull tokens
        vm.prank(address(broker));
        ERC20(sellToken).approve(address(twammHook), amountIn);

        // Submit order as broker
        vm.prank(address(broker));
        (orderId, orderKey) = twammHook.submitOrder(
            IJTM.SubmitOrderParams({
                key: marketTwammKey,
                zeroForOne: zeroForOne,
                duration: duration,
                amountIn: amountIn
            })
        );

        // Register order in broker for solvency tracking
        broker.setActiveTwammOrder(
            IPrimeBroker.TwammOrderInfo({
                key: marketTwammKey,
                orderKey: orderKey,
                orderId: orderId
            })
        );

        console.log("  TWAMM order placed on MARKET pool:");
        console.log("    sellCollateral:", sellCollateral);
        console.log("    zeroForOne:", zeroForOne);
        console.log("    amountIn:", amountIn / 1e6);
        console.log("    duration:", duration);
        console.log("    c0 is:", colIsC0 ? "collateral" : "positionToken");
    }

    /// @dev Execute a Layer 3 auction clear on accrued ghost balance.
    ///      This converts ghost balance into real earnings for the streamer.
    ///
    ///      For our orders (selling collateral):
    ///        - accrued collateral sits as ghost balance
    ///        - Clearer pays positionToken(wRLP) at TWAP minus discount
    ///        - Clearer receives collateral at discount
    ///        - _recordEarnings updates earningsFactorCurrent
    ///        - On cancel, broker receives buyTokensOut in positionToken(wRLP)
    ///
    /// @param zeroForOne Direction of clear (true=buy accrued0, false=buy accrued1)
    /// @param maxAmount Maximum amount to clear
    function _clearTwammAuction(bool zeroForOne, uint256 maxAmount) internal {
        (uint256 accrued0, uint256 accrued1, uint256 discount, ) = twammHook
            .getStreamState(marketTwammKey);
        console.log("  Clear auction:");
        console.log("    accrued0:", accrued0 / 1e6);
        console.log("    accrued1:", accrued1 / 1e6);
        console.log("    discount bps:", discount);

        // Determine payment token (clearer pays the opposite currency)
        address payToken = zeroForOne
            ? Currency.unwrap(marketTwammKey.currency1)
            : Currency.unwrap(marketTwammKey.currency0);

        // Approve a large amount for the clearer's payment
        ERC20(payToken).approve(address(twammHook), type(uint256).max);

        // Execute clear (test contract is the clearer)
        twammHook.clear(marketTwammKey, zeroForOne, maxAmount, 0);

        (accrued0, accrued1, , ) = twammHook.getStreamState(marketTwammKey);
        console.log("    After clear - accrued0:", accrued0 / 1e6);
        console.log("    After clear - accrued1:", accrued1 / 1e6);
    }

    /// @dev Set up broker with a TWAMM order on the PRODUCTION pool.
    ///      Broker gets cash + debt, places TWAMM order selling collateral.
    ///
    ///      KEY INVARIANT: After setup, broker.wRLP = 0 and broker.cash = targetCash.
    ///      This ensures _getLiquidValue() < seize target, forcing _cancelTwammOrder()
    ///      during liquidation.
    function _setupBrokerTwamm(
        uint256 targetCash,
        uint256 twammAmount,
        bool sellCollateral,
        uint256 duration
    ) internal returns (PrimeBroker broker) {
        broker = _createBroker();

        // Large buffer for solvency during setup
        uint256 buffer = 100_000e6;
        uint256 totalTransfer = targetCash + twammAmount + buffer;
        collateralMock.transfer(address(broker), totalTransfer);

        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(totalTransfer),
            int256(USER_DEBT)
        );

        // Align time to TWAMM interval
        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL) + 1) *
            TWAMM_INTERVAL;
        vm.warp(nextInterval);
        twammHook.executeJTMOrders(marketTwammKey);

        _placeTwammOrder(broker, twammAmount, sellCollateral, duration);

        // Drain excess cash
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
        // Drain ALL wRLP — TWAMM order value keeps broker solvent
        uint256 currentWRLP = ERC20(ma.positionToken).balanceOf(
            address(broker)
        );
        if (currentWRLP > 0) {
            try
                broker.withdrawPositionToken(address(this), currentWRLP)
            {} catch {}
        }

        uint256 fc = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 fw = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Setup: cash:", fc / 1e6, "wRLP:", fw / 1e6);
    }

    /// @dev Set up broker with Cash + TWAMM + optional V4 LP.
    ///      Handles time coordination: LP is provisioned first (warps),
    ///      then TWAMM interval alignment + order placement.
    ///
    ///      Final state: cash=targetCash, wRLP=targetWRLP, TWAMM active.
    ///      If lpWRLP > 0, LP position is also active.
    function _setupBrokerTwammCascade(
        uint256 targetCash,
        uint256 targetWRLP,
        uint256 twammAmount,
        uint256 lpWRLP,
        uint256 lpCol
    ) internal returns (PrimeBroker broker, uint256 tokenId) {
        broker = _createBroker();

        uint256 buffer = 200_000e6;
        uint256 totalTransfer = targetCash + twammAmount + buffer + lpCol;
        collateralMock.transfer(address(broker), totalTransfer);

        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(totalTransfer),
            int256(USER_DEBT)
        );

        // Step 1: Align time. We need to be at a TWAMM interval boundary
        //         for order placement. Don't call executeJTMOrders
        //         because no TWAMM orders exist yet (crossing 472k empty
        //         intervals would cause OutOfGas).
        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL) + 1) *
            TWAMM_INTERVAL;
        vm.warp(nextInterval);

        // Step 2: Provision LP (if requested).
        //         _provideV4LP warps to 1_700_000_000 internally.
        if (lpWRLP > 0 && lpCol > 0) {
            tokenId = _provideV4LP(broker, lpWRLP, lpCol);
        }

        // Step 3: Re-align to TWAMM interval after LP warp.
        //         Still no orders, so skip executeJTMOrders.
        nextInterval =
            ((block.timestamp / TWAMM_INTERVAL) + 1) *
            TWAMM_INTERVAL;
        vm.warp(nextInterval);

        // Step 4: Place TWAMM order (now we're at a clean interval)
        _placeTwammOrder(broker, twammAmount, true, TWAMM_INTERVAL);

        // Step 4: Drain to targets
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
        uint256 currentWRLP = ERC20(ma.positionToken).balanceOf(
            address(broker)
        );
        if (currentWRLP > targetWRLP) {
            try
                broker.withdrawPositionToken(
                    address(this),
                    currentWRLP - targetWRLP
                )
            {} catch {}
        }

        uint256 fc = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 fw = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Setup: cash:", fc / 1e6, "wRLP:", fw / 1e6);
    }

    /// @dev Set up broker with TWAMM order + out-of-range V4 LP.
    ///      Combines _placeTwammOrder() with _provideV4LPOutOfRange().
    ///
    ///      OOR LP returns only one token on unwind:
    ///        above=true  → token0 only (wRLP or collateral, depends on sorting)
    ///        above=false → token1 only
    ///
    ///      Final state: cash=targetCash, wRLP=targetWRLP, TWAMM active, OOR LP active.
    function _setupBrokerTwammOOR(
        uint256 targetCash,
        uint256 targetWRLP,
        uint256 twammAmount,
        uint256 lpAmount,
        bool sellCollateral,
        bool lpAbove
    ) internal returns (PrimeBroker broker, uint256 tokenId) {
        broker = _createBroker();

        uint256 buffer = 200_000e6;
        uint256 totalTransfer = targetCash + twammAmount + lpAmount + buffer;
        collateralMock.transfer(address(broker), totalTransfer);

        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(totalTransfer),
            int256(USER_DEBT)
        );

        // Step 1: Align time to TWAMM interval (no orders yet, skip execute)
        uint256 nextInterval = ((block.timestamp / TWAMM_INTERVAL) + 1) *
            TWAMM_INTERVAL;
        vm.warp(nextInterval);

        // Step 2: Provision OOR LP (warps to 1_700_000_000 internally)
        tokenId = _provideV4LPOutOfRange(broker, lpAmount, lpAbove);

        // Step 3: Re-align to TWAMM interval after LP warp
        nextInterval =
            ((block.timestamp / TWAMM_INTERVAL) + 1) *
            TWAMM_INTERVAL;
        vm.warp(nextInterval);

        // Step 4: Place TWAMM order
        _placeTwammOrder(broker, twammAmount, sellCollateral, TWAMM_INTERVAL);

        // Step 5: Drain to targets
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
        uint256 currentWRLP = ERC20(ma.positionToken).balanceOf(
            address(broker)
        );
        if (currentWRLP > targetWRLP) {
            try
                broker.withdrawPositionToken(
                    address(this),
                    currentWRLP - targetWRLP
                )
            {} catch {}
        }

        uint256 fc = ERC20(ma.collateralToken).balanceOf(address(broker));
        uint256 fw = ERC20(ma.positionToken).balanceOf(address(broker));
        console.log("  Setup: cash:", fc / 1e6, "wRLP:", fw / 1e6);
        console.log(
            "  OOR LP:",
            lpAbove ? "above (token0 only)" : "below (token1 only)"
        );
    }
}
