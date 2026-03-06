# System Overview

RLD Protocol is built as a layered architecture where each layer has a single responsibility. All logic is immutable after deployment — once a market is created, its contracts, modules, and oracles are locked forever. Only risk parameters can change, via a 7-day timelocked curator.

## Design Philosophy

1. **Hyperstructure Core**: RLDCore is immutable after deployment. No admin keys, no upgradability. Risk parameters can only be changed via curators with a 7-day timelock.

2. **Modular Adapters, Immutable Once Deployed**: Funding models, liquidation logic, and valuation modules are designed as pluggable adapters — different markets can use different implementations at creation time, tailored to their specific use case. But once a market is deployed, its module set is locked. No hot-swapping, no upgrades.

3. **Smart Contract Wallets over EOAs**: Users interact through PrimeBroker proxies, enabling cross-margin, composable positions, and delegated access patterns.

4. **V4-Native**: Built directly on Uniswap V4's hook architecture, leveraging `beforeSwap` for JIT fills, flash accounting for atomic safety, and the singleton `PoolManager` for capital efficiency.

## Architecture Layers

```
                          +------------------+
                          |      USER        |
                          +--------+---------+
                                   |
                 +-----------------+-----------------+
                 |                                   |
                 v                                   v
  +--------------+--------------+    +---------------+--------------+
  |        PERIPHERY            |    |        JTM ENGINE            |
  |  BrokerRouter               |    |  Uniswap V4 Hook             |
  |  BondFactory                |    |  Streaming / Limit / Market  |
  |  BrokerExecutor             |    |  3-layer Ghost Balance       |
  |  LeverageShortExecutor      |    |                              |
  +--------------+--------------+    +--+---------+----+-----------+
                 |                       |         |    |
                 v                       |         |    v
  +--------------+--------------+        |         |  +-+-----------+
  |        PRIME BROKER         +--------+         |  | Uniswap V4  |
  |  Per-user smart contract    |                  |  | PoolManager |
  |  wallet (ERC-721 NFT)       |                  |  +-------------+
  |                             |                  |
  |  Holds: ERC20 + LP + JTM    |                  |
  +--------------+--------------+                  |
                 |                                 |
                 v                                 |
  +--------------+--------------+                  |
  |        RLD CORE             |                  |
  |  Immutable singleton        |                  |
  |  Positions, solvency,       |                  |
  |  funding, liquidation       |                  |
  +--+---------+----------+----+                   |
     |         |          |                        |
     v         v          v                        |
  +--+---+  +--+-------+  +--+-------+             |
  |MODULES|  | ORACLES  |  | MARKET   |            |
  |       |  |          |  | FACTORY  |            |
  |Funding|  |Aave(Idx) |  | Deploys  +------------+
  |Liquid.|  |V4  (Mark)|  | markets  |
  |JTM Val|  |CL  (Spot)|  | + pools  |
  |V4  Val|  |          |  |          |
  +-------+  +----------+  +----------+
```

### Core Layer — Immutable Accounting

The foundation of the protocol. Once deployed, the core contracts have **no admin keys** and cannot be modified.

| Contract               | Purpose                                                                                                                                                                                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **RLDCore**            | Singleton accounting engine. Tracks debt records, enforces solvency, applies funding rates, and coordinates liquidations. Does **not** hold collateral — collateral tracking is delegated to PrimeBroker, with BrokerVerifier ensuring the broker uses correct code. |
| **RLDStorage**         | Manages both permanent storage (positions, market configs) and EIP-1153 transient storage (flash accounting locks).                                                                                                                                                  |
| **RLDMarketFactory**   | Orchestrates atomic deployment of new markets — clones tokens, deploys factories, initializes V4 pools, registers oracles.                                                                                                                                           |
| **PrimeBrokerFactory** | ERC-721 contract where each token represents a PrimeBroker account. `tokenId = address`.                                                                                                                                                                             |

### Broker Layer — Smart Contract Wallets

Each user gets their own PrimeBroker — a minimal proxy clone that serves as their entire portfolio. See [Prime Broker](./prime-broker) for a deep dive.

| Contract                 | Purpose                                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------------------------- |
| **PrimeBroker**          | Holds all user assets (ERC20, LP positions, TWAMM orders). Computes unified NAV. Manages operator access. |
| **PoolLiquidityManager** | Helper for V4 concentrated liquidity operations (mint, burn, decrease). Extracted for testability.        |

### Module Layer — Adapters

Stateless modules that the core and broker contracts delegate calculation to. Each market chooses its module set at creation time — different markets can use different funding models, liquidation mechanics, or valuation logic. Once a market is deployed, its modules are **immutable** — they cannot be swapped or upgraded.

| Module                     | Purpose                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------- |
| **StandardFundingModel**   | Computes the normalization factor update: `NF × exp(-FundingRate × Δt / Period)`      |
| **DutchLiquidationModule** | Health-based liquidation auction pricing                                              |
| **JTMBrokerModule**        | Values active JTM streaming orders (3-term formula). **Critical for bonds**.          |
| **UniswapV4BrokerModule**  | Values V4 LP positions (amounts + uncollected fees)                                   |
| **BrokerVerifier**         | Validates that an address is a legitimate PrimeBroker deployed by the trusted factory |

### Oracle Layer

Three distinct oracles serving three distinct purposes:

| Oracle                       | Returns         | Source             | Used For                  |
| ---------------------------- | --------------- | ------------------ | ------------------------- |
| **RLDAaveOracle**            | Index Price     | `K × borrowRate`   | Fundamental value of wRLP |
| **UniswapV4SingletonOracle** | Mark Price      | TWAP from JTM hook | Market-determined price   |
| **ChainlinkSpotOracle**      | Collateral Spot | Chainlink feeds    | Collateral valuation      |

The divergence between index and mark price drives the [funding mechanism](../protocol/funding-mechanism).

### Periphery Layer — User Entry Points

High-level contracts that compose broker operations into user-friendly transactions. These are the contracts most users interact with directly.

| Contract           | Purpose                                                                                                        |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| **BrokerRouter**   | Unified API: deposit, go long, go short, close positions. Permanent operator on brokers.                       |
| **BondFactory**    | One-click synthetic bond lifecycle: mint bond → create broker + deposit + mint + TWAMM order + freeze account. |
| **BrokerExecutor** | Generic atomic multicall with ephemeral operator (EIP-191 signature).                                          |

### JTM Engine — Uniswap V4 Hook

The [JTM (JIT-TWAMM)](../jtm/design-evolution) is a Uniswap V4 hook that serves as the protocol's matching engine. It supports three order types — streaming (TWAP), limit, and market orders — all powered by a Ghost Balance engine with 3-layer execution. See the [JTM Engine section](../jtm/v4-hooks-architecture) for details.
