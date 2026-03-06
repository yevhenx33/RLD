# FAQ

## General

### What is RLD in one sentence?

RLD lets you trade DeFi lending rates as if they were stocks — buy when you think rates go up, sell when you think they go down, or mint a synthetic bond for fixed yield.

### How is RLD different from Pendle, Notional, or Sense?

These protocols use **fixed-term** instruments that expire on specific dates, fragmenting liquidity across maturities. RLD uses a **perpetual** design — one pool, no expiry dates. Bonds are created by combining a short position with a JTM streaming order, not by splitting tokens into principal and yield components.

### What chains is RLD deployed on?

See [Deployed Addresses](./reference/deployed-addresses) for current deployment information.

## Trading

### Can I lose more than my collateral?

**No.** If you go long (buy wRLP), your maximum loss is the purchase amount. If you go short (mint wRLP against collateral), your position gets liquidated before your collateral is fully consumed. The protocol's bad debt waterfall handles any residual loss.

### What are the fees?

| Fee Type             | Amount                       | Paid To           |
| -------------------- | ---------------------------- | ----------------- |
| V4 swap fee          | Set per pool at creation     | LPs               |
| JTM streaming/limit  | 0 bps (Layer 1 & 2 are free) | —                 |
| JTM Layer 3 clearing | 0-500 bps (time-decayed)     | Arbitrageurs      |
| Protocol fee         | 0 bps (configurable)         | Protocol treasury |

### How do I calculate my effective rate?

If you're short and earning funding:

```
Effective Rate = IndexRate × (1 + FundingYield)
```

Where FundingYield depends on the mark-index divergence. Check the dashboard for real-time funding rates.

## Bonds

### Is the fixed yield guaranteed?

The yield is **approximately** fixed. It depends on:

- JTM execution quality (average fill price over the bond duration)
- Funding rate (adds or subtracts from base yield)
- Rate movements during the bond period

Monte Carlo simulations show ~11% mean yield on a 10% target, with 95% of outcomes between 6-16% for unhedged variable rates.

### Can my bond get liquidated?

**Extremely unlikely.** Bonds start at ~8% LTV (12:1 health ratio). The rate would need to increase ~12× to approach liquidation. The PrimeBroker's cross-margin design ensures the JTM streaming order always counts as collateral.

### Can I exit a bond early?

Yes, via `closeBond()`. You receive proportional yield for time held but lose the fixed-yield guarantee — you may need to buy back wRLP at market price.

## Security

### Is RLD audited?

**No external audit has been completed yet.** The protocol has undergone extensive internal testing with **429 tests** across 26 test suites:

- **171 JTM/TWAMM tests** — streaming orders, netting, JIT fills, clearing, oracle, epoch handling
- **103 broker tests** — router trading, executor multicall, leverage shorts, valuation modules, ACL
- **75 factory tests** — market creation, configuration, deployment
- **49 liquidation tests** — cascades, bad debt, force-settle, permutations, edge cases
- **22 pool + 9 oracle tests** — liquidity management, Aave oracle integration

See [Security Model](./risk/security-model) for details on the threat model and known limitations.

### Who controls the protocol?

- **Core contracts**: No admin keys. Immutable after deployment.
- **Risk parameters**: Curator-controlled with mandatory 7-day timelock.
- **JTM tunables**: Hook owner can adjust discount rates and TWAP window.
- **Market creation**: Factory owner only.

### What happens if Aave goes down?

The index oracle reads Aave's borrow rate. If Aave is unreachable, the oracle returns the last known rate. Existing positions continue to function — funding and solvency calculations use the stale price. The market effectively pauses funding convergence until the oracle updates.

## Technical

### What is a PrimeBroker?

A smart contract wallet (EIP-1167 minimal proxy) that holds all your assets — ERC20 tokens, V4 LP positions, and JTM orders. It computes unified NAV for cross-margin solvency. It's also an NFT — transfer the NFT to transfer your entire account. See [Prime Broker](./architecture/prime-broker).

### What is the JTM?

JIT-TWAMM — a Uniswap V4 hook that serves as a unified order engine supporting streaming (TWAP), limit, and market orders. It uses Ghost Balances and a 3-layer matching engine. See [JTM Engine](./jtm/design-evolution).

### What does "Ghost Balance" mean?

Tokens that have been committed to orders but haven't been settled yet. They exist inside the JTM hook, invisible to the AMM pool. They're matched via netting, JIT fills, or Dutch auction clearing. See [Glossary](./introduction/glossary#g).
