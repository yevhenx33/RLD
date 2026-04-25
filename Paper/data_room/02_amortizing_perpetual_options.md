# Amortizing Perpetual Options (AmPO)

## Abstract
Traditional options rely on discrete expiry dates, leading to liquidity fragmentation and extensive rollover costs. Amortizing Perpetual Options (AmPO) establish a continuous-time options architecture perfectly suited for decentralized algorithmic interest rates. By utilizing state-decay mathematics, AmPO internalizes funding premiums directly into the token structure, guaranteeing convex yield invariant properties for underwriters while eliminating maturity fragmentation.

## 1. Structural Mathematics

To unbundle the complex tail risks associated with DeFi interest rates—specifically Jump-to-Default risk—without splitting liquidity across fixed-term options chains, we structure the derivative as an Amortizing Perpetual Option.

### 1.1 State Decay Mechanics
Instead of exchanging explicit, margin-consuming funding payments, the insurance premium is extracted through continuous state decay.

Let $F > 0$ represent a constant, continuous decay rate (the funding rate). The payout coverage of all minted AmPO tokens amortizes via a global Normalization Factor $NF(t)$:

$$ NF(t) = e^{-F \cdot t} $$

Where $t$ is expressed in annualized units. Assuming continuous arbitrage and frictionless AMM execution, the secondary spot price $P_{mkt}(t)$ converges to the discounted intrinsic value:

$$ P_{mkt}(t) = 100 \cdot r_t \cdot e^{-F \cdot t} $$

### 1.2 Over-Collateralization Trap
Because the liability decays continuously ($e^{-Ft}$) while the initial escrowed collateral remains statically locked within the vault, the capital backing-per-token geometrically increases over time. This structure forces underwriters into an extremely over-collateralized position if left unmanaged, necessitating specialized Just-In-Time (JIT) execution infrastructure to maintain capital efficiency (detailed in the Execution chapter).

## 2. Yield Invariance and Convex Pricing

For a continuous market equilibrium to exist, rational underwriters will only provide insurance if their expected yield $Y_{CDS}$ strictly exceeds the opportunity cost of passively supplying capital to the underlying lending pool ($r_{supply}$).

**Invariant (Supply-Side Floor):**
$$ Y_{CDS} \geq r_{supply} $$

### 2.1 The Theorem of Convex Risk Premium
We define $\delta \in (0, 1)$ as the optimal target utilization parameter of the lending protocol's Interest Rate Model (IRM), $R \in [0, 1)$ as the reserve factor, and $r_{max}$ as the absolute maximum borrow rate.

*Theorem:* Setting the decay rate to $F = -\ln(1 - \delta)$ mathematically guarantees the underwriter captures a strictly positive premium over the passive supply rate across all continuous utilization states $U_t \in [0, 1]$ if and only if:

$$ r_{max} \le \frac{-\ln(1 - \delta)}{(1 - R)} $$

### 2.2 Derivation of Underwriter Yield
Assuming a vault holds constant exogenous collateral $C_{locked}$ backing an initial token position, the NF decay unlocks collateral at a rate $dC = C_{locked} \cdot F \cdot dt$. 
By continuously sweeping this unlocked collateral to mint and sell new tokens at $P_{mkt}(t)$, the underwriter's realized continuous Return on Equity simplifies to:

$$ Y_{CDS} = F \cdot \left( \frac{r_t}{r_{max}} \right) $$

To guarantee the global invariant ($Y_{CDS} \ge r_{supply}$) holds, we evaluate the boundary condition at terminal utilization $U_t = 1$, where $r_t = r_{max}$. 
At $U_t = 1$, the passive supply rate is $r_{max}(1-R)$, and the underwriter yield is exactly $F$. Thus, the invariant holds strictly when $F \ge r_{max}(1-R)$.

### 2.3 Maclaurin Series Expansion and Tail Risk Pricing
The structural risk premium $\alpha$ is the real-time spread between the underwriter's linear payout constraint and the passive supplier's yield.
Expanding the static decay rate $F = -\ln(1 - \delta)$ via its Maclaurin series reveals the source of the premium:

$$ F = \sum_{n=1}^{\infty}\frac{\delta^{n}}{n} = \delta + \frac{\delta^{2}}{2} + \frac{\delta^{3}}{3} + \mathcal{O}(\delta^{4}) $$

Substituting this into the risk premium equation mathematically proves that as a lending protocol configures a more aggressive target utilization (e.g., $\delta \to 0.90$), the higher-order terms amplify geometrically. 

This establishes that the risk premium is not merely linear padding. It intrinsically inherits the dynamic payout convexity of the underlying pool's IRM. As utilization crosses the target, the underwriter's yield accelerates convexly precisely during liquidity shocks, capturing immense tail-risk upside without relying on complex, path-dependent Black-Scholes options math.
