# Morpho Blue Indexer — Session Walkthrough

> **Date:** April 5, 2026 | **DB:** `/home/ubuntu/RLD/backend/morpho/data/morpho.db` (70MB)

---

## What Was Built

### Indexer Pipeline (`/home/ubuntu/RLD/backend/morpho/`)

| Module | Purpose |
|--------|---------|
| [config.py](file:///home/ubuntu/RLD/backend/morpho/config.py) | Selectors, addresses, constants |
| [db.py](file:///home/ubuntu/RLD/backend/morpho/db.py) | SQLite schema — markets, vaults, snapshots, allocations |
| [rpc.py](file:///home/ubuntu/RLD/backend/morpho/rpc.py) | Batch RPC client with retry (Alchemy) |
| [discovery.py](file:///home/ubuntu/RLD/backend/morpho/discovery.py) | Syncs market/vault metadata from Morpho GraphQL API |
| [collector.py](file:///home/ubuntu/RLD/backend/morpho/collector.py) | Full snapshot collector (markets + oracles + vaults + positions) |
| [backfill_fast.py](file:///home/ubuntu/RLD/backend/morpho/backfill_fast.py) | Optimized historical backfill — 4 workers, batch 500, ~9 snapshots/sec |
| [indexer.py](file:///home/ubuntu/RLD/backend/morpho/indexer.py) | FastAPI service (not needed for historical — run backfill directly) |

### How to Run

```bash
# Set env
export MAINNET_RPC_URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"
export PYTHONPATH=/home/ubuntu/RLD/backend
export DB_DIR=/home/ubuntu/RLD/backend/morpho/data

# Backfill (already done — 11K hourly snapshots Jan 2025 → Apr 2026)
python3 -m morpho.backfill_fast --start 2025-01-01 --workers 4

# Single live snapshot
python3 -c "from morpho.db import init_db; from morpho.discovery import discover_markets_and_vaults; from morpho.collector import collect_snapshot; init_db(); discover_markets_and_vaults(); collect_snapshot()"
```

### RPC Key
- Stored in `/home/ubuntu/RLD/docker/.env` as `MAINNET_RPC_URL`
- Alchemy free tier is sufficient (~7.7M calls for full backfill)

---

## Data Collected

| Metric | Value |
|--------|-------|
| Date range | 2025-01-01 → 2026-04-05 |
| Hourly timestamps | 1,553 |
| Market snapshots | 169,163 rows |
| Vault snapshots | 65,109 rows |
| Vault allocations | 4,142 rows (last ~2 weeks only) |
| Markets tracked | 242 |
| Vaults tracked | 124 |
| Oracle coverage | 37% (many early markets had no oracle) |
| Collection time | 20.9 minutes for full backfill |

### Schema (key tables)

- `market_params` — static: market_id, collateral/loan tokens, LLTV, oracle, IRM
- `market_snapshots` — per hour: supply, borrow, utilization, oracle price, block number
- `vault_meta` — static: address, name, asset symbol, curator
- `vault_snapshots` — per hour: totalAssets, totalSupply, share_price
- `vault_allocations` — per hour: which vault supplies how much to which market

---

## Key Findings

### 1. Market Stress
- **17 markets at 100% utilization** right now — capital trapped
- **PAXG/USDC**: 746 consecutive hours at 100% (31 days) — longest stress
- **amphrETH/WETH**: 710 hours
- Once at 100%, markets stay there for **weeks/months** — adaptive IRM fails for illiquid assets

### 2. Vault Performance
- **Zero negative share price drops** across all 124 vaults in 15 months
- **USUAL Vault**: 29.6% APY (best)
- Average stablecoin vault: 5-8% APY
- Gauntlet DAI Core: 5.8% APY

### 3. Flash Events
- **cbBTC/USDS** on Sep 22, 2025: 1.9% → 100% utilization in 1 hour
- **USR/USDC** on Dec 14, 2025: 11.6% → 90.8% in 1 hour

### 4. Curator Market Share
| Curator | Share |
|---------|-------|
| **Sentora** | 59.3% |
| **Steakhouse** | 34.4% |
| Metronome | 5.1% |
| Smokehouse | 0.6% |

> Sentora + Steakhouse = 93.7% of all vaulted supply

### 5. Gauntlet Deep Dive
- **$320M AUM** across 14 vaults, 44 markets
- Top position: cbBTC/USDC at $85.7M (24.3% market share)
- $38.5M (12.1%) in stressed markets >90% utilization
- Key dominance: 97.1% of wstUSR/USDC, 99.6% of sUSDD/USDT, 58.6% of weETH/WETH
- Consistently #2 behind Steakhouse in major markets
- **$5M stuck in wstUSR/USDC at 100% util** (Resolv aftermath)

---

## Resolv USR Exploit — March 22, 2026

### What Happened
- Attacker compromised Resolv's off-chain signing infrastructure via supply chain attack
- **02:21:35 UTC**: First mint — 50M USR via Counter contract
- **03:41 UTC**: Second mint — 30M more USR
- Attacker swapped 80M USR → staked USR → USDC/ETH, extracting **~$25M**
- **05:16 UTC**: Contracts paused

### Impact on Morpho Markets (from our data)

**USR/USDC market:**
- Before: supply=$87.1M, util=74.3%
- After: supply=$74.1M, util=87.9%
- **$13M in supply withdrawn by lenders**

**RLP/USDC market:**
- Before: supply=$13.9M, util=66.7%
- After: supply=$10.7M, util=85.8%
- **$3.1M withdrawn by curators**

### Curator Exposure to RLP at Exploit Time (Mar 9 data)

| Curator | RLP Exposure | Post-Exploit | Action |
|---------|-------------|--------------|--------|
| Gauntlet USDC Frontier | $1.13M (58.5%) | $376K | Cut $750K |
| Resolv USDC | $614K (31.7%) | $680K | Stayed |
| MEV Capital USDC | $158K (8.2%) | $47K | Cut $111K |
| kpk USDC Yield | **$16** (0.0%) | $0 | Already out |
| Apostro Resolv USDC | $20K (1.0%) | $694 | Nearly exited |

### kpk "Same Block" Claim
- kpk had **negligible RLP exposure** ($16) well before the exploit
- "Zero loss to depositors" is verified — they essentially weren't exposed
- Their claim of "same block, no manual intervention" is about **blocking new allocations**, not withdrawing from a position
- Our hourly data has a **45-hour gap** over the exploit window (Mar 21 15:00 → Mar 23 13:00) — we can't verify intra-block timing

### Gauntlet Got Hit
- **$5M stuck in wstUSR/USDC at 100% utilization** — still there today
- $1.1M in RLP reduced to $377K
- Total Resolv exposure: ~$2.2M (RLP + wstUSR)

---

## Next Steps: Reth ExEx Architecture

### The Problem
Hourly snapshots show state changes but NOT:
- Transaction ordering within blocks
- Who withdrew before whom ("same block" claims)
- Per-tx utilization changes
- MEV/frontrunning patterns

### Proposed Architecture

```
┌─────────┐    ┌──────────────────┐    ┌───────────────┐
│  Reth    │───►│ ExEx Plugin      │───►│ Event Store   │
│  Node    │    │ (Rust)           │    │ (SQLite/PG)   │
│          │    │ • Decode Morpho  │    │ • block_num   │
│  Synced  │    │   events         │    │ • tx_index    │
│  to head │    │ • Track state    │    │ • event_type  │
│          │    │   per tx         │    │ • caller      │
└─────────┘    │ • Emit to store  │    │ • market_id   │
               └──────────────────┘    │ • amounts     │
                                       │ • state_after │
                                       └───────┬───────┘
                                               │
                                       ┌───────▼───────┐
                                       │ Analytics     │
                                       │ (Python)      │
                                       │ • Forensics   │
                                       │ • Vault PnL   │
                                       │ • Alerting    │
                                       └───────────────┘
```

### ExEx Plugin Would Capture
1. **Morpho Blue events**: Supply, Withdraw, Borrow, Repay, Liquidate
2. **MetaMorpho events**: ReallocateSupply, ReallocateWithdraw
3. **Per-tx state**: market utilization after each transaction
4. **Caller identity**: trace back to vault or EOA

### Killer Queries This Enables
- "Which vault withdrew from RLP/USDC within ±5 blocks of USR Counter.mint()?"
- "What was utilization between tx[3] and tx[4] in block X?"
- "Did kpk's withdrawal come before or after the mint in the same block?"
- "Show all MetaMorpho reallocations within 60 seconds of any oracle price drop >5%"

### Why This Matters
This is the only way to distinguish between:
1. **Genuinely impressive automation** — monitoring bot detected Counter.mint() and auto-triggered withdrawal in same block
2. **Information asymmetry** — curator knew something before the block was built

**This is the product: forensic-grade intra-block DeFi intelligence that nobody else has.**

---

## Files Reference

| Path | Description |
|------|-------------|
| `/home/ubuntu/RLD/backend/morpho/` | All indexer code |
| `/home/ubuntu/RLD/backend/morpho/data/morpho.db` | SQLite database (70MB) |
| `/home/ubuntu/RLD/backend/morpho/backfill_fast.log` | Last backfill run log |
| `/home/ubuntu/RLD/docker/.env` | Contains `MAINNET_RPC_URL` (Alchemy key) |
