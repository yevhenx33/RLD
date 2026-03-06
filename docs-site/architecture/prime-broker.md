# Prime Broker

The PrimeBroker is the architectural keystone of RLD. Every user gets their own PrimeBroker — a smart contract wallet that holds all their assets, computes unified solvency, and enables the protocol's flagship product: **synthetic bonds with no liquidation risk**.

## Why PrimeBroker Exists

The PrimeBroker is not just a convenience wrapper — it's a fundamental design requirement driven by the synthetic bond product.

### The Bond Problem

A bond locks in the current rate as fixed yield. At **5% APY** (index price = \$5.00):

1. User deposits **\$10,000** aUSDC as collateral
2. Mints **\$500** of wRLP debt (100 wRLP at \$5.00) — initial LTV is **5%**
3. Submits a TWAMM streaming sell to gradually sell the 100 wRLP over 90 days
4. At maturity, the short is unwound — debt cancelled, user keeps the **5% pro-rated yield** (~\$123 over 90 days)

**The problem**: the moment the bond is minted, the \$500 of wRLP leaves the user's wallet and enters the JTM hook as a streaming ghost balance. If the system only counts ERC20 balances:

| Asset                    | Value                               |
| ------------------------ | ----------------------------------- |
| aUSDC collateral         | \$10,000                            |
| wRLP in wallet           | **\$0** ← it's inside the TWAMM now |
| **Total apparent NAV**   | **\$10,000**                        |
| Debt (100 wRLP × \$5.00) | \$500                               |

This looks safe — but the \$500 of wRLP is invisible. If rates spike (5% → 10%), the index price doubles to \$10.00, and debt jumps to \$1,000. The wRLP inside the TWAMM is also worth \$1,000 now and would perfectly offset the debt increase — **except the system can't see it**.

### The Solution — Unified Asset Valuation

The PrimeBroker solves this by holding **all** user assets in one smart contract wallet and computing **total NAV** across every asset type:

| Asset Type               | Valuation Module        | How It Counts                                                         |
| ------------------------ | ----------------------- | --------------------------------------------------------------------- |
| ERC20 tokens (aUSDC)     | Direct                  | 1:1 face value                                                        |
| V4 LP positions          | `UniswapV4BrokerModule` | Decomposed amounts + uncollected fees                                 |
| **JTM streaming orders** | **`JTMBrokerModule`**   | **3-term formula: unsold tokens + earned tokens + ghost attribution** |

With all three components counted, the bond position actually looks like:

| Asset                                              | Value         |
| -------------------------------------------------- | ------------- |
| aUSDC collateral                                   | \$10,000      |
| JTM order (unsold wRLP + accrued earnings + ghost) | ~\$500        |
| **Total NAV**                                      | **~\$10,500** |
| Outstanding debt                                   | ~\$500        |
| **Health ratio**                                   | **~20:1**     |

The bond starts at ~20:1 collateralization and stays naturally over-collateralized throughout its life. **No liquidation risk** — exactly as the paper promises.

Even in an extreme scenario where rates spike from 5% to **100%** (index price \$5 → \$100), the bond survives: debt jumps to 100 wRLP × \$100 = \$10,000, but the wRLP inside the TWAMM order is also worth \$10,000. NAV = \$10,000 collateral + \$10,000 TWAMM = **\$20,000** vs \$10,000 debt — still a **2:1 health ratio**. The wRLP in the order acts as a natural hedge against rate increases.

### The 3-Term Valuation Formula

The `JTMBrokerModule` computes TWAMM order value using three components:

```
Value = SellRefund × sellPrice
      + BuyEarnings × buyPrice
      + GhostShare × sellPrice × (1 - discount)
```

| Term                   | What It Captures                                                                                                 |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **Sell Refund**        | Unsold tokens still pending in the order (they haven't been streamed yet)                                        |
| **Buy Earnings**       | Already-cleared earnings from executed portions of the order                                                     |
| **Ghost (Discounted)** | Accrued but uncleared sell tokens sitting in the JTM hook. Valued conservatively using a Dutch auction discount. |

> **Why Term 3 matters**: Without ghost attribution, there's a gap between clears where the accrued tokens are invisible. NAV would temporarily drop to near-zero between clears, creating false liquidation windows. The ghost term closes this gap.

## Cross-Margin

Because the PrimeBroker holds everything, all assets contribute to solvency simultaneously:

- **LP as collateral**: You can provide liquidity on the V4 wRLP/USDC pool AND use that LP position as margin for your short. The fees you earn also count.
- **TWAMM as collateral**: Your streaming order's accrued value protects your position. As the order fills, collateral value increases while wRLP debt decreases.
- **Stacked strategies**: Deposit collateral → mint debt → sell some wRLP → LP with the rest → place a TWAMM order for gradual exit → everything counts as one unified position.

### Solvency Check Flow

Every time a position is modified, RLDCore validates solvency via a callback:

```
RLDCore.lock()
  → PrimeBroker.lockAcquired(data)
    → user operations (deposit, trade, LP, TWAMM)
  → RLDCore._checkSolvencyOfTouched()
    → broker.getNetAccountValue()
       = Σ ERC20 balances
       + Σ V4 LP values  (via UniswapV4BrokerModule)
       + Σ JTM order values  (via JTMBrokerModule)
    → must satisfy: NAV ≥ debtPrincipal × NF × indexPrice × maintenanceMargin
```

If the check fails, the entire transaction reverts — no partial states.

## NFT Ownership Model

Each PrimeBroker is identified by an NFT. The `PrimeBrokerFactory` contract is itself an ERC-721 token where:

```
tokenId = uint256(uint160(brokerAddress))
```

The broker's contract address IS its token ID.

### What This Enables

| Feature                        | How                                                                 |
| ------------------------------ | ------------------------------------------------------------------- |
| **Account trading**            | Transfer the NFT on any marketplace → transfer the entire account   |
| **Institutional sub-accounts** | One entity owns multiple broker NFTs with isolated positions        |
| **Protocol composability**     | Other protocols can hold broker NFTs, enabling automated strategies |
| **Ownership verification**     | Cheap on-chain lookup: `factory.ownerOf(uint160(broker))`           |

### Frozen Bonds as Tradeable Assets

When `BondFactory` mints a bond, it **freezes** the broker account — no deposits, withdrawals, or position modifications are allowed until maturity. This makes the NFT a self-contained, non-modifiable fixed-yield instrument that can be freely traded on any NFT marketplace (OpenSea, Blur, etc.).

**Example**: You open a bond at **10% APY** when rates are high. A month later, rates drop to **3%**. Your bond is still locked in at 10% — it's now significantly more valuable than a new bond. You can list the broker NFT on a marketplace at a premium, and the buyer receives the remaining yield stream simply by holding the NFT.

This creates a **secondary bond market** with no additional infrastructure:

- Bond value is fully on-chain (collateral + TWAMM order + locked yield)
- Frozen accounts can't be drained before transfer
- Buyer gets a clean operator set (all operators revoked on transfer)
- The yield continues accruing to whoever holds the NFT at maturity

### Security on Transfer

When a broker NFT is transferred:

- All existing operators are **automatically revoked**
- The new owner starts with a clean operator set
- No lingering access from the previous owner's delegates

## Operator System

PrimeBroker supports delegated access via operators — addresses authorized to act on behalf of the owner.

### Operator Types

| Type                   | Example                     | Lifecycle                          | Use Case                                                       |
| ---------------------- | --------------------------- | ---------------------------------- | -------------------------------------------------------------- |
| **Owner**              | NFT holder                  | Permanent until NFT transfer       | Full control                                                   |
| **Permanent Operator** | BrokerRouter                | Set once during `initialize()`     | Everyday trading (deposit, long, short)                        |
| **Ephemeral Operator** | BrokerExecutor, BondFactory | Set + revoked atomically in one tx | Complex multi-step operations (bond minting, leveraged shorts) |

### Ephemeral Operators — How They Work

For complex operations like bond minting or leveraged shorts, the user signs an EIP-191 message authorizing the executor contract as a temporary operator:

1. Executor calls `setOperatorWithSignature(self, true, signature)`
2. Executor performs the multi-step operation (deposit → mint → swap → TWAMM → etc.)
3. Executor calls `setOperator(self, false)` — revokes itself
4. All within one atomic transaction — if any step fails, everything reverts

This eliminates the need for the user to sign each step individually while maintaining security — the operator only exists for the duration of one transaction.

## Deployment Model

PrimeBrokers are deployed as **EIP-1167 minimal proxy clones** of a template implementation. This means:

- **Cheap deployment**: ~\$2 in gas vs ~\$100+ for a full contract deployment
- **Identical logic**: All brokers share the same battle-tested code
- **Immutable reference**: The template implementation is locked (`CORE = address(1)`) to prevent hijacking

Anyone can create a broker — `PrimeBrokerFactory.createBroker()` is **permissionless**. The factory mints the NFT and returns the broker address. RLDCore verifies broker legitimacy via `BrokerVerifier` before any financial operations.
