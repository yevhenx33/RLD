# Ghost Balance Limit Orders: Technical Specification

This document details the architecture and mechanics of the Ghost Balance Limit Order protocol. Designed as a Uniswap V4 Hook, it provides an on-chain limit order execution engine that is gas-efficient, manipulation-resistant, and entirely immune to "bounce-back" unfilling risks.

---

## 1. Executive Summary: The Bounce-Back Problem

Traditional on-chain limit orders (such as those natively possible via Uniswap V4 Range Orders) operate by placing single-tick concentrated liquidity (LP). A user wishing to sell `Token0` at price `P` deposits `Token0` into an LP position at tick `T` (where `T` corresponds to `P`).

When the market price sweeps across tick `T`, the AMM automatically converts the user's `Token0` into `Token1`. The order is "filled".

**The Fatal Flaw:** The V4 AMM is inherently symmetric. If the market price subsequently drops back down across tick `T`, the AMM will automatically re-convert the user's `Token1` back into `Token0`. The limit order "un-fills" itself.

To prevent this "bounce-back", traditional hook designs attempt to aggressively withdraw the LP position the instant it is crossed. However, executing multi-tick LP withdrawals during a swapper's transaction is prohibitively gas-intensive, shifting unacceptable costs onto organic takers and breaking pool routing.

### The Solution

Our architecture abandons the LP paradigm entirely. It utilizes a **Ghost Balance** system coupled with **Just-In-Time (JIT) `beforeSwap` interception** and **Time-Weighted Average Price (TWAP) gating**.

Tokens physically reside in the Hook contract, invisible to the AMM pool. They are only matched against incoming swappers when the TWAP confirms the price condition has been met, guaranteeing zero bounce-back risk, zero flash-loan manipulation risk, and bounded $O(1)$ gas overhead for swappers.

---

## 2. Architectural Components

The system relies on five core states:

1. **User Deposits (`userDepositAtTick`)**: Tracks the individual token amounts deposited by users, mapped by the exact price tick they specify.
2. **Aggregate Deposits (`totalDepositAtTick`)**: Tracks the total volume of tokens waiting to be sold at a specific tick.
3. **Tick Bitmap (`activeTicks`)**: A highly gas-efficient bitmap tracking which ticks contain active limit orders.
4. **Accumulators (`accumulatorAtTick`)**: Tracks the tokens received _after_ a limit order bucket has been filled.
5. **Epoch Log (`swapLog`)**: A permanent, monotonic record of the TWAP price recorded after every external swap.

---

## 3. The Execution Lifecycle

### Step 1: Placing the Limit Order

Alice wishes to sell 10 ETH (`Token0`) when the ETH price reaches $3,000 (Tick `T_3000`).

1. Alice calls `placeLimitOrder(10 ETH, T_3000)`.
2. 10 ETH is transferred from Alice directly into the Hook contract.
3. The Hook increments `userDepositAtTick[T_3000][Alice] += 10 ETH`.
4. The Hook increments `totalDepositAtTick[T_3000] += 10 ETH`.
5. The Hook sets the corresponding bit in the `activeTicks` bitmap.

**Outcome:** Alice's ETH sits safely in the Hook's custody. It is not exposed to the V4 AMM, does not earn LP fees, and cannot be accidentally converted.

### Step 2: A Massive (but temporary) Spot Price Move

A whale submits an massive, illiquid swap on Uniswap V4, pushing the instantaneous spot price of ETH from $2,800 to $3,200.

1. Uniswap triggers the Hook's `beforeSwap()` callback prior to executing the whale's swap.
2. The Hook reads the pool's 5-minute **TWAP** from the native V4 oracle. Because the whale's swap hasn't happened yet (and even if it had, it's a single moment in time), the 5-minute TWAP sits at **$2,810**.
3. The Hook checks if the TWAP ($2,810) satisfies any active limit order ticks (e.g., Alice's $3,000). It does not.
4. The Hook returns `ZERO_DELTA`, allowing the whale's swap to execute normally via the AMM.

**Outcome:** The limit order was safely ignored. Flash-loan manipulation or temporary spot-price wicks cannot trigger limit orders. The trigger condition requires sustained market consensus (the TWAP).

### Step 3: Sustained Price Discovery (The Execution)

Over the next 10 minutes, sustained buying pressure pushes the 5-minute TWAP up to **$3,010**.

A retail trader submits a swap to buy 5 ETH with USDC.

1. Uniswap triggers the Hook's `beforeSwap()` callback.
2. The Hook reads the TWAP: **$3,010**.
3. Utilizing the `activeTicks` bitmap, the Hook efficiently identifies that Alice's limit order at $3,000 is now eligible for execution (TWAP $3,010 $\ge$ Trigger $3,000).
4. The Hook intercepts the retail trader's requested 5 ETH volume.
5. It fills the 5 ETH directly from Alice's waiting deposit, executing the trade at the exact **TWAP price ($3,010)**.
6. The retail trader's USDC payment is routed into the Hook.
7. The Hook updates states:
   - `totalDepositAtTick[T_3000] -= 5 ETH`
   - `accumulatorAtTick[T_3000] += 15,050 USDC` (5 ETH $\times$ $3,010)
8. The Hook returns a `BeforeSwapDelta` to Uniswap, instructing the core AMM: _"I handled 5 ETH internally. Proceed with the remainder of the swap natively."_

**Outcome:** 50% of Alice's order is definitively filled at $3,010. The V4 AMM was bypassed entirely for this volume. The swap required $O(1)$ gas overhead for the retail trader.

### Step 4: The Market Crashes

The price of ETH violently crashes back down to $2,000.

Because Alice's filled volume was never deposited as an LP position, there is zero risk of "bounce-back." The 15,050 USDC sits safely inside the Hook's accumulator. The remaining 5 ETH in her deposit simply becomes inactive again, waiting for the TWAP to recover above $3,000.

### Step 5: Claiming the Spoils

Alice calls `claimLimitOrder(T_3000)`.

1. The Hook calculates her proportional share of the tick's bucket. Since she was the only depositor, she owns 100% of the accumulator.
2. The Hook transfers 15,050 USDC to Alice.
3. She can optionally cancel her un-filled 5 ETH order and withdraw it back to her wallet.

---

## 4. Internal Netting (The "Spread Cross")

The protocol naturally functions as an internal, zero-fee crossing engine when opposing limit orders overlap.

Suppose:

- **Bob** places a limit order to Sell ETH at \$2,900.
- **Charlie** places a limit order to Buy ETH at \$3,000.

The spread is crossed. Bob is willing to sell for less than Charlie is willing to pay.

Whenever the protocol's global state is updated (which happens routinely on any user interaction), the engine checks for crossed active ticks against the current TWAP.

If the TWAP is **$2,950**:

- Bob's condition is met (TWAP $\ge$ $2,900)
- Charlie's condition is met (TWAP $\le$ $3,000)

The Hook will instantaneously match Bob and Charlie's volume directly against each other at exactly the $2,950 TWAP price.

- Bob receives $2,950 USDC per ETH.
- Charlie receives 1 ETH and is refunded $50 USDC to his unspent balance.
- **Outcome:** Zero AMM impact, zero slippage, zero LP fees.

---

## 5. Security & Gas Analysis

### Security Matrix

| Vector          | Defense                                                                                     | Attacker Cost                                              | Practical Impact                                               |
| --------------- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | -------------------------------------------------------------- |
| Flash Loan      | Execution is strictly TWAP-gated. A single-block manipulation barely moves a 5-minute TWAP. | Hundreds of millions in real capital to sustain the price. | None. Order ignores the wick.                                  |
| Bounce-Back     | Tokens are held in Hook custody ("Ghost Balances"), not in V4 Range Orders.                 | N/A                                                        | Absolute zero risk.                                            |
| Malicious Maker | Attacker placing orders to block routing in `beforeSwap`.                                   | Gas fees.                                                  | None. Un-used volume degrades gracefully to native V4 routing. |

### Gas Profile

The system aggressively shields the external organic swapper from iteration costs. The swapper pays for the premium JIT execution service, while the limit-order placer pays for their own cleanup.

**Swapper Overhead (Paid during V4 Swap):**

- Read TWAP: ~5,000 gas
- Bitmap lookup: ~2,500 gas
- JIT Delta Match: ~10,000 gas per matched tick.
- **Total Overhead:** ~20,000 gas. Bounded and constant regardless of orderbook depth.

**Claim Overhead (Paid by Limit Order owner):**

- Proportional share math & State updates: ~8,000 gas
- Native ERC20 Transfers: ~30,000 gas
- **Total Overhead:** ~40,000 gas per claim.

---

## 6. Open Engineering Considerations

1. **Capital Idle Time:** Under the current spec, unmatched tokens sit completely idle in the Hook contract. Future iterations could involve deploying this idle capital into ultra-safe yield venues (e.g., Aave or underlying yield-bearing tokens like sDAI) to provide baseline yield while the order awaits execution.
2. **Tick Spacing Limitations:** To prevent state bloat, limit orders should not be permitted at every single individual tick size. They should be constrained to standard tick intervals (e.g., `tickSpacing * 10`) to enforce liquidity aggregation.
3. **Partial Fills Across Multi-Tick Spans:** When a swap bridges multiple active limit order ticks, execution routing logic must define whether it sweeps the best-priced tick entirely before moving to the next, or fills all triggered ticks proportionally. Best-price-first execution is the default AMM standard and strongly recommended here.
