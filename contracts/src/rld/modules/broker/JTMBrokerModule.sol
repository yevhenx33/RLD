// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IValuationModule} from "../../../shared/interfaces/IValuationModule.sol";
import {IRLDOracle} from "../../../shared/interfaces/IRLDOracle.sol";
import {FixedPointMathLib} from "../../../shared/utils/FixedPointMathLib.sol";

import {IJTM} from "../../../twamm/IJTM.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";

/// @title  JTM Broker Valuation Module
/// @author RLD Protocol
/// @notice Stateless, read-only module that computes the Net Account Value
///         (NAV) contribution of a single IJTM order held by a PrimeBroker.
///
/// @dev ## Purpose
///
///     PrimeBroker calls `getValue(data)` during every solvency check.
///     This module translates the on-chain IJTM order state into a single
///     uint256 denominated in the broker's `valuationToken` (typically
///     collateral such as waUSDC).
///
/// ## IJTM Order Lifecycle
///
///     A IJTM order streams `sellToken` (collateral) into a Uniswap V4
///     pool over a fixed duration in exchange for `buyToken` (position token,
///     e.g. wRLP).  Unlike the original TWAMM — where the hook executes
///     virtual swaps and immediately records `earningsFactor` — the JIT
///     variant accumulates sold tokens as **ghost balances** (`accrued0` /
///     `accrued1`) that remain idle until an external **clear auction**
///     converts them into buy-side earnings.
///
///     ```
///     ┌──────────── IJTM Order Lifecycle ────────────┐
///     │                                                   │
///     │  ① submit(X tokens, D seconds)                    │
///     │     ├── sellRate  = X / D                         │
///     │     └── tokens begin streaming                    │
///     │                                                   │
///     │  ② as time passes (per-block accrual)             │
///     │     ├── sellRefund  decreases                     │
///     │     └── ghost (accrued) grows                     │
///     │                                                   │
///     │  ③ clear() – Dutch auction (external arb)         │
///     │     ├── arb pays discounted buy tokens            │
///     │     ├── ghost → 0                                 │
///     │     ├── earningsFactor += earnings                │
///     │     └── buyTokensOwed visible to getCancelOrder   │
///     │                                                   │
///     │  ④ cancel / expire / forceSettle                  │
///     │     └── remaining sellRefund + buyOwed returned   │
///     └───────────────────────────────────────────────────┘
///     ```
///
/// ## Three-Term Valuation Formula
///
///     ```
///     getValue = sellRefund × sellPrice              … term 1  (unsold principal)
///              + buyOwed   × buyPrice                … term 2  (cleared earnings)
///              + ghostShare × sellPrice × (1 − d)    … term 3  (uncleared ghost)
///     ```
///
///     | Variable      | Source                                    |
///     |---------------|-------------------------------------------|
///     | sellRefund    | `getCancelOrderState()` – unsold tokens   |
///     | buyOwed       | `getCancelOrderState()` – cleared portion |
///     | ghostShare    | `getStreamState()` × pro-rata sellRate    |
///     | d (discount)  | `getStreamState().currentDiscount`        |
///     | sellPrice     | `_priceToken(sellToken, ...)`             |
///     | buyPrice      | `_priceToken(buyToken, ...)`              |
///
///     Term 3 is critical: without it, uncleared ghost is invisible and
///     PrimeBroker underestimates NAV, causing **false liquidation triggers**.
///
/// ## Ghost Attribution (Pro-Rata)
///
///     Ghost is a pool-level aggregate (`accrued0` / `accrued1` across ALL
///     orders in one stream direction).  This order's share is:
///
///         ghostShare = totalGhost × order.sellRate / stream.sellRateCurrent
///
///     This pro-rata split is **exact within an epoch** because all active
///     orders stream at constant rates between interval boundaries.  Across
///     epoch boundaries, it is an approximation that errs conservatively
///     (newer orders may be slightly over-attributed, older orders slightly
///     under-attributed).
///
/// ## Auction Discount
///
///     The discount `d` grows linearly with time since the last clear:
///
///         d = min(timeSinceLastClear × discountRateBpsPerSecond, maxDiscountBps)
///
///     This mirrors the Dutch auction in `IJTM.clear()` — a bot can
///     always buy the ghost at discount `d`, so valuing ghost at `(1 − d)`
///     of face value is the fair lower-bound.
///
/// ## Architecture
///
///     ```
///     PrimeBroker                          JTMBrokerModule
///          │                                        │
///          ├─ getNetAccountValue()                  │
///          │       │                                │
///          │       ├─ _encodeTwammData()            │
///          │       │                                │
///          │       └────► getValue(data) ──────────►│
///          │                                        ├── getCancelOrderState()  ─┐
///          │                                        ├── getStreamState()        ├─► IJTM Hook
///          │                                        ├── getOrder()              │
///          │                                        ├── getStreamPool()        ─┘
///          │                                        │
///          │◄──── totalValue ───────────────────────┤
///          │                                        │
///          ├─ _tryForceSettleGhost() ← liquidation  │
///          ├─ seize()  [cancels order internally]   │
///          └────────────────────────────────────────┘
///     ```
///
/// ## Security Notes
///
///     1. **Oracle Trust** — token prices rely on `IRLDOracle.getIndexPrice()`.
///        A manipulated oracle could overstate or understate NAV.
///     2. **Hook Trust** — the hook address is sourced from `activeTwammOrder`
///        in PrimeBroker and is set during order placement. Not user-supplied.
///     3. **Read-Only** — this contract has no storage and no mutating
///        functions; it cannot modify order or pool state.
///     4. **Ghost Dilution** — an attacker placing a large order in the same
///        stream direction would dilute this order's pro-rata ghost share.
///        This is conservative: it triggers liquidation (handled safely by
///        `forceSettle`), it does NOT inflate solvency.
///
/// ## V1 Limitations
///
///     - Only **one** IJTM order is tracked per broker (`activeTwammOrder`).
///     - Ghost pro-rata attribution is an approximation across epoch
///       boundaries (exact within any single epoch).
///     - Token pricing supports two tokens: `valuationToken` (1:1) and
///       `positionToken` (via index price).  Unknown tokens return 0.
///
contract JTMBrokerModule is IValuationModule {
    using FixedPointMathLib for uint256;

    /* ═══════════════════════════════════════════════════════════════════ */
    /*                              TYPES                                */
    /* ═══════════════════════════════════════════════════════════════════ */

    /// @notice All parameters needed to value a single IJTM order.
    /// @dev    ABI-encoded by `PrimeBroker._encodeTwammData()` and decoded
    ///         in `getValue()`.  Every field is immutable for the lifetime
    ///         of an order — they are set at order placement and never
    ///         mutated by this module.
    struct VerifyParams {
        /// @notice The IJTM hook contract (NOT this module).
        ///         All view calls (`getCancelOrderState`, `getStreamState`,
        ///         `getOrder`, `getStreamPool`) are made against this address.
        address hook;
        /// @notice Uniswap V4 PoolKey that identifies the TWAMM pool.
        ///         Contains `currency0`, `currency1`, `fee`, `tickSpacing`,
        ///         and `hooks`.
        PoolKey key;
        /// @notice Order identifier within the IJTM hook.
        ///         Contains `owner`, `expiration`, and `zeroForOne`.
        ///         `zeroForOne` determines the sell direction:
        ///           - true:  selling currency0 → buying currency1
        ///           - false: selling currency1 → buying currency0
        IJTM.OrderKey orderKey;
        /// @notice The RLD oracle contract that provides `getIndexPrice()`.
        address oracle;
        /// @notice The collateral / valuation token (e.g. waUSDC).
        ///         Priced 1:1 — 1 unit of this token = 1 unit of NAV.
        address valuationToken;
        /// @notice The position token (e.g. wRLP).
        ///         Priced via `oracle.getIndexPrice(underlyingPool, underlyingToken)`.
        address positionToken;
        /// @notice Aave lending pool address for index price lookup.
        address underlyingPool;
        /// @notice Underlying asset address (e.g. USDC) for index price.
        address underlyingToken;
    }

    /* ═══════════════════════════════════════════════════════════════════ */
    /*                         VALUATION LOGIC                           */
    /* ═══════════════════════════════════════════════════════════════════ */

    /// @inheritdoc IValuationModule
    ///
    /// @notice Returns the current NAV contribution of the broker's IJTM
    ///         order, denominated in `valuationToken`.
    ///
    /// @dev    Computes three additive terms:
    ///
    ///         1. **Sell Refund** — unsold tokens still queued in the order.
    ///            Source: `getCancelOrderState().sellTokensRefund`.
    ///            Denomination: sell token (collateral or position).
    ///
    ///         2. **Buy Owed** — earnings from past clears / forceSettles
    ///            that updated `earningsFactor`.
    ///            Source: `getCancelOrderState().buyTokensOwed`.
    ///            Denomination: buy token (position or collateral).
    ///
    ///         3. **Ghost (Discounted)** — uncleared accrued sell tokens
    ///            sitting in the hook.  These are real tokens but invisible
    ///            to `getCancelOrderState` because `earningsFactor` has not
    ///            been updated for them yet.
    ///            Source: `getStreamState()` + pro-rata attribution.
    ///            Discount: `(1 − currentDiscount)` from Dutch auction.
    ///
    ///         Each term is converted to `valuationToken` via `_priceToken`.
    ///
    /// @param  data  ABI-encoded `VerifyParams` struct.
    /// @return totalValue  The order's total value in `valuationToken` units.
    function getValue(bytes calldata data) external view override returns (uint256) {
        VerifyParams memory params = abi.decode(data, (VerifyParams));

        // ── Term 1 + 2: Cleared order state ─────────────────────────────
        //
        //  getCancelOrderState() reads:
        //    - earningsFactorCurrent − order.earningsFactorLast  →  buyOwed
        //    - remaining seconds × sellRate                      →  sellRefund
        //
        //  IMPORTANT: buyOwed only includes earnings from CLEARED ghost.
        //  Uncleared ghost is NOT reflected here.
        (uint256 buyTokensOwed, uint256 sellTokensRefund) =
            IJTM(params.hook).getCancelOrderState(params.key, params.orderKey);

        // Identify token addresses based on order direction
        address sellToken =
            params.orderKey.zeroForOne ? Currency.unwrap(params.key.currency0) : Currency.unwrap(params.key.currency1);

        address buyToken =
            params.orderKey.zeroForOne ? Currency.unwrap(params.key.currency1) : Currency.unwrap(params.key.currency0);

        uint256 totalValue = 0;

        // Term 1: Unsold principal (sell token)
        if (sellTokensRefund > 0) {
            totalValue += _priceToken(sellToken, sellTokensRefund, params);
        }

        // Term 2: Cleared earnings (buy token)
        if (buyTokensOwed > 0) {
            totalValue += _priceToken(buyToken, buyTokensOwed, params);
        }

        // ── Term 3: Uncleared ghost (discounted) ────────────────────────
        //
        //  Without this term, NAV drops to zero as ghost grows between
        //  clears — causing false liquidation triggers even when the
        //  broker holds real collateral locked in the hook.
        totalValue += _ghostValue(params, sellToken);

        return totalValue;
    }

    /* ═══════════════════════════════════════════════════════════════════ */
    /*                       INTERNAL: GHOST VALUE                       */
    /* ═══════════════════════════════════════════════════════════════════ */

    /// @notice Computes the discounted value of uncleared ghost tokens
    ///         attributable to a single order.
    ///
    /// @dev    **Three-step process:**
    ///
    ///         1. Read pool-level ghost and auction discount from
    ///            `getStreamState()`.  Ghost is aggregated across ALL
    ///            orders in one direction (e.g. all 0For1 orders).
    ///
    ///         2. Attribute this order's share via pro-rata sellRate:
    ///            `ghostShare = totalGhost × order.sellRate / stream.sellRateCurrent`
    ///
    ///            This is exact within an epoch (constant sellRate set per
    ///            interval boundary).  Across epoch boundaries where orders
    ///            were added/removed, it is an approximation that errs
    ///            conservatively.
    ///
    ///         3. Apply auction discount:
    ///            `discountedGhost = ghostShare × (10000 − discountBps) / 10000`
    ///
    ///            `discountBps` is the current Dutch auction price —
    ///            what an arb would pay right now to clear the ghost.
    ///            It is already capped at `maxDiscountBps` by the hook.
    ///
    ///         **Early-exit conditions** (returns 0):
    ///           - No ghost accrued in this direction
    ///           - Order has zero sellRate (cancelled / empty)
    ///           - Stream has zero aggregate sellRate (no active orders)
    ///           - Pro-rata share rounds to zero
    ///
    /// @param  params    The decoded `VerifyParams`.
    /// @param  sellToken The sell token address (used for pricing).
    /// @return ghostVal  The discounted ghost value in `valuationToken` units.
    function _ghostValue(VerifyParams memory params, address sellToken) internal view returns (uint256) {
        // Step 1: Pool-level ghost and discount
        (uint256 accrued0, uint256 accrued1, uint256 discountBps,) = IJTM(params.hook).getStreamState(params.key);

        // Ghost direction: zeroForOne orders sell token0 → ghost is accrued0
        uint256 totalGhost = params.orderKey.zeroForOne ? accrued0 : accrued1;
        if (totalGhost == 0) return 0;

        // Step 2: Pro-rata attribution
        IJTM.Order memory order = IJTM(params.hook).getOrder(params.key, params.orderKey);
        if (order.sellRate == 0) return 0;

        (uint256 streamSellRate,) = IJTM(params.hook).getStreamPool(params.key, params.orderKey.zeroForOne);
        if (streamSellRate == 0) return 0;

        uint256 ghostShare = (totalGhost * order.sellRate) / streamSellRate;
        if (ghostShare == 0) return 0;

        // Step 3: Apply auction discount
        uint256 discountedGhost = (ghostShare * (10000 - discountBps)) / 10000;

        return _priceToken(sellToken, discountedGhost, params);
    }

    /* ═══════════════════════════════════════════════════════════════════ */
    /*                       INTERNAL: PRICING                           */
    /* ═══════════════════════════════════════════════════════════════════ */

    /// @notice Converts a token amount into `valuationToken` terms.
    ///
    /// @dev    Handles two known token types:
    ///
    ///         - **valuationToken** (e.g. waUSDC): returned 1:1.
    ///         - **positionToken** (e.g. wRLP): multiplied by the Aave
    ///           index price (`amount × indexPrice / 1e18`).
    ///         - **unknown token**: returns 0.  This should never happen
    ///           in a correctly-configured RLD pool.
    ///
    /// @param  token   The ERC-20 address to price.
    /// @param  amount  The raw token amount.
    /// @param  params  The `VerifyParams` containing oracle and token config.
    /// @return value   The amount expressed in `valuationToken` units.
    function _priceToken(address token, uint256 amount, VerifyParams memory params) internal view returns (uint256) {
        if (token == params.valuationToken) {
            return amount;
        } else if (token == params.positionToken) {
            uint256 indexPrice = IRLDOracle(params.oracle).getIndexPrice(params.underlyingPool, params.underlyingToken);
            return amount.mulWadDown(indexPrice);
        } else {
            return 0;
        }
    }
}
