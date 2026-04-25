# Ghost Router and Execution

## Abstract
The execution of continuous-time derivatives faces immense friction due to Impermanent Loss, inventory decay, and liquidity fragmentation. The Ghost Router addresses these market microstructure challenges by deploying a highly specialized execution engine that couples Just-In-Time (JIT) vault management with Time-Weighted Average Market Maker (TWAMM) logic. This effectively internalizes Loss-Versus-Rebalancing (LVR) and guarantees deterministic duration execution.

## 1. Market Microstructure Challenges

Holding static balances of an Amortizing Perpetual Option (AmPO) or Rate-Level Perp induces two critical forms of execution drag:
1.  **Inventory Decay Drag**: As the Normalization Factor ($NF(t)$) continuously decays, a static holding of minted debt becomes heavily over-collateralized, destroying the underwriter's capital efficiency.
2.  **Adverse Selection (LVR)**: During jump-to-default events or sudden interest rate spikes, passive $x \cdot y = k$ Automated Market Maker (AMM) liquidity providers suffer deterministic adverse selection against informed arbitrageurs, resulting in severe Loss-Versus-Rebalancing (LVR).

## 2. Fiduciary Execution via TWAMM

To manage duration risk perfectly without manual intervention, the protocol mandates a deterministic microstructure.

### 2.1 The Unwind Mathematics
To maintain a constant absolute coverage $C$ (or to linearly reduce exposure in a Synthetic Bond), a fiduciary must systematically execute orders.
To precisely counteract the $e^{-Ft}$ decay of an AmPO, the fiduciary must grow their token balance exponentially:

$$ N(t) = \frac{C}{P_{max}} e^{Ft} $$

The continuous acquisition rate dictates a cash stream equal to:
$$ \text{Stream} = C \cdot F \cdot \left(\frac{r_t}{r_{max}}\right) $$

By routing this execution through a Time-Weighted Average Market Maker (TWAMM), the exponential growth and decay factors mathematically cancel each other out. The TWAMM strictly outputs constant-dollar coverage at a duration-neutral continuous premium, smoothing out localized volatility and ensuring optimal execution pricing.

## 3. Just-In-Time (JIT) Underwriter Vaults

If underwriters hold pre-minted inventory, the continuous amortization erodes their structural risk premium ($\alpha$). To achieve maximum theoretical capital efficiency, underwriters deploy into specialized ERC-4626 Vaults that perform Just-In-Time (JIT) underwriting.

### 3.1 The Hub and Spoke Architecture
The Ghost Router functions as a central custody and routing hub, coordinating execution across specialized "spoke" engines (e.g., `TwapEngine` for continuous streams and `LimitEngine` for discrete price triggers). 

As these engines accumulate unexecuted inventory ("ghost balances"), the Ghost Router dynamically processes incoming taker swaps through a three-layer execution hierarchy:
1.  **Global Netting (Layer 1):** The router aggregates ghost balances from all registered engines and executes pro-rata netting of opposing sides.
2.  **Taker Intercept (Layer 2):** Any remaining directional ghost balance is directly intercepted by the taker flow.
3.  **AMM Fallback (Layer 3):** Any residual taker volume not satisfied by the passive ghost inventory falls back to the underlying Uniswap V4 pool.

### 3.2 LVR Internalization and the Coincidence of Wants (CoW)
Heavy-tailed jumps in the underlying rate ($r_t$) introduce toxic flow to standard AMMs. 

By prioritizing Layer 1 and Layer 2 execution, the Ghost Router physically circumvents the passive AMM curve when opposing organic flow exists. This internalizes the Coincidence of Wants (CoW). Peer-to-peer trades execute exactly at the oracle-derived spot price, saving takers from AMM spread/slippage while mathematically neutralizing Loss-Versus-Rebalancing (LVR) bleed for passive liquidity providers.

## 4. EVM Discrete Integration

A common vulnerability in continuous-time DeFi protocols is Euler drift caused by discrete recursive integration. The Ghost Router execution environment eliminates this by evaluating the analytical solution:

$$ NF(t_k) = \exp(-F \cdot t_k) $$

By calculating the exact value at discrete block timestamps using high-precision fixed-point math, execution error is strictly confined to IEEE-754 equivalent precision truncation (e.g., 18-decimal `WAD`), rendering time-discretization drift mathematically non-existent across the entire duration of the contract.
