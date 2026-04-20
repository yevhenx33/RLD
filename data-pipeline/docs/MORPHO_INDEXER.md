# Morpho Blue Deterministic Indexer Architecture

> Launch scope note: this data-pipeline track is **non-launch-critical** for the Reth V2 baseline and is maintained separately from the core launch path.

## System Overview
The Morpho Blue indexer implements a deterministic, event-driven state machine that reconstructs per-market TVL (`totalSupplyAssets`, `totalBorrowAssets`) by replaying raw EVM event logs from the immutable Morpho Blue singleton contract (`0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`). The system achieves **0.000% drift** against archival on-chain state across 627 out of 733 active Chainlink-backed markets, with all meaningful-TVL markets (>$1K) tracking at sub-0.1%.

---

## 1. Architectural Components

### A. The Core Memory Pool (`morpho_events`)
The HyperSync collector targets the Morpho Blue singleton and extracts 8 specific event classes into the immutable `morpho_events` ClickHouse table:

| Event | Topic0 Hash | State Mutation |
|---|---|---|
| `AccrueInterest` | `0x9d9bd501...` | `supply += interest`, `borrow += interest` |
| `Supply` | `0xedf88704...` | `supply += assets` |
| `Withdraw` | `0xa56fc0ad...` | `supply -= assets` |
| `Borrow` | `0x57095454...` | `borrow += assets` |
| `Repay` | `0x52acb05c...` | `borrow -= assets` |
| `Liquidate` | `0xa4946ede...` | `borrow -= repaidAssets + badDebtAssets` |
| `SetFee` | `0xd5e969f0...` | Updates market fee parameter |
| `CreateMarket` | `0xac4b2400...` | Initializes market metadata |

**Schema:**
```sql
CREATE TABLE morpho_events (
    block_number  UInt64,
    block_timestamp DateTime,
    tx_hash       String,
    log_index     UInt32,
    contract      String,
    event_name    String,
    topic0        String,
    topic1        Nullable(String),  -- market_id (bytes32)
    topic2        Nullable(String),  -- caller/onBehalf
    topic3        Nullable(String),  -- onBehalf/borrower
    data          String,
    inserted_at   DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (block_number, log_index, contract, topic0)
```

### B. The Deterministic Engine (`indexer/sources/morpho.py`)
The `MorphoSource` class maintains an in-memory `MarketState` accumulator per `market_id`. On each event, it applies the exact same arithmetic that the Solidity contract applies to its storage variables. Because Morpho Blue is **immutable and non-upgradeable**, the event-to-state mapping is a permanent 1:1 correspondence.

**State equation (must hold at every block):**
```
totalSupplyAssets = Σ(Supply.assets) + Σ(AccrueInterest.interest) 
                 - Σ(Withdraw.assets) - Σ(Liquidate.badDebtAssets)

totalBorrowAssets = Σ(Borrow.assets) + Σ(AccrueInterest.interest) 
                 - Σ(Repay.assets) - Σ(Liquidate.repaidAssets) 
                 - Σ(Liquidate.badDebtAssets)
```

### C. Hex Data Payload Layout
Morpho Blue events use a mix of indexed topics and non-indexed data fields. The critical mapping:

| Event | Indexed Topics | Data Layout (32-byte words) |
|---|---|---|
| Supply | `id, caller, onBehalf` | `[assets, shares]` |
| Withdraw | `id, caller, onBehalf` | `[receiver, assets, shares]` |
| Borrow | `id, caller, onBehalf` | `[receiver, assets, shares]` |
| Repay | `id, caller, onBehalf` | `[assets, shares]` |
| Liquidate | `id, caller, borrower` | `[repaidAssets, repaidShares, seizedAssets, seizedShares, badDebtAssets?]` |
| AccrueInterest | `id` | `[prevBorrowRate, interest, feeShares]` |

**Hex offsets (after stripping `0x` prefix):**
- Supply/Repay: `assets = raw[0:64]`
- Withdraw/Borrow: `assets = raw[64:128]` (skip `receiver` address at `[0:64]`)
- Liquidate: `repaidAssets = raw[0:64]`, `badDebtAssets = raw[256:320]` (if present)
- AccrueInterest: `interest = raw[64:128]`

### D. APY Derivation
Rate snapshots are emitted at each `AccrueInterest` event. The `prevBorrowRate` field is a WAD-scaled (1e18) per-second rate directly from Morpho's IRM:

```python
borrow_apy = exp(prevBorrowRate / 1e18 * 31_536_000) - 1.0
utilization = total_borrow_assets / total_supply_assets
supply_apy  = borrow_apy * utilization * (1.0 - fee)
```

---

## 2. Critical Edge Cases & Historical Bugs

### A. The Liquidate Length Guard (Resolved 2026-04-14)
**Bug:** The Liquidate event ABI specifies 6 data fields (384 hex chars), but on-chain data is only 320 hex chars when `badDebtShares = 0`. The original parser required `len(raw) >= 384`, silently **skipping all liquidation events**.  
**Impact:** $28.97M of un-subtracted repaid debt on `cbBTC/USDC` alone.  
**Fix:** Guard changed to `len(raw) >= 256`, with `badDebtAssets` read conditionally:
```python
elif evt == "Liquidate" and len(raw) >= 256:
    repaid = int(raw[0:64], 16)
    bad_debt = int(raw[256:320], 16) if len(raw) >= 320 else 0
```

### B. Synthetic Compounding Disabled
Morpho Blue natively tracks interest via physical `AccrueInterest` events (unlike Aave, which requires synthetic gap-fill compounding). The `forward_fill_hourly` function explicitly bypasses cumulative APY multiplication for `MORPHO_MARKET` to prevent double-counting:
```python
if group["protocol"].iloc[0] != "MORPHO_MARKET":
    merged['supply_usd'] *= merged['sup_multiplier']
    merged['borrow_usd'] *= merged['bor_multiplier']
```

### C. Chainlink-Only Whitelist
Permissionless markets using exotic oracles (e.g., `BONDUSD/USR`, `resolv`) are excluded by a strict whitelist filter built from `morpho_market_params`. This prevents ingesting markets with hardcoded or custom oracle pricing that our pipeline cannot verify.

### D. Dust-TVL Markets with Negative Borrow
Expired Pendle PT tokens and near-zero pools may show negative `totalBorrowAssets` in our replay because principal was redeemed directly from the PT contract without emitting a Morpho `Repay` event. These are exclusively sub-$100 dust markets and do not affect production accuracy.

---

## 3. Poka-Yoke Verification Engine

### A. Event Completeness (`scratch/verify_gaps.py`)
Compares per-event-type counts between local ClickHouse and Alchemy `eth_getLogs` for a target market. Verified result for `cbBTC/USDC`:

| Event | ClickHouse | Alchemy RPC | Delta |
|---|---|---|---|
| Supply | 50,700 | 50,703 | -3 |
| Withdraw | 45,706 | 45,707 | -1 |
| Borrow | 4,555 | 4,555 | **0** |
| Repay | 3,762 | 3,762 | **0** |
| Liquidate | 100 | 100 | **0** |
| AccrueInterest | 96,559 | 96,561 | -2 |

### B. Archival Block Snapshot (`scratch/snapshot_compare.py`)
Pins both on-chain `market(id).call(block_identifier=N)` and local event replay to the exact same block for apples-to-apples comparison. Full audit at block 24,879,600:

```
733 active Chainlink markets:
  ✅ Perfect ($0 delta):  627  (85.5%)
  ✅ Good (<1% drift):     10  ( 1.4%)
  ⚠️  Warning (1-5%):      2   ( 0.3%)
  ❌ Bad (>5%):            94  (12.8% — all dust-TVL <$100)
```

### C. Progressive Drift Tracking (`scratch/audit_flows.py`)
Replays events incrementally and checks against archival RPC at 500K-block intervals. Used to isolate the Liquidate bug to the exact block range where drift exploded.

---

## 4. Production Deployment

```yaml
# docker-compose.yml
services:
  morpho_collector:
    build: .
    restart: unless-stopped
    env_file: .env
    command: ["python", "/app/scripts/run_indexer.py", "--source", "MORPHO_MARKET", "--role", "collector"]

  morpho_processor:
    build: .
    restart: unless-stopped
    env_file: .env
    command: ["python", "/app/scripts/run_indexer.py", "--source", "MORPHO_MARKET", "--role", "processor"]
```

The **collector** daemon continuously fetches raw event logs via HyperSync into `morpho_events`. The **processor** daemon reads from `morpho_events`, replays decoded state through `MorphoSource.decode()`, and writes hourly snapshots to `morpho_timeseries`. The frontend reads these transparently via the `unified_timeseries` Merge Engine view.

---

## 5. Key Files

| File | Purpose |
|---|---|
| `indexer/sources/morpho.py` | Core event decoding, state accumulation, APY derivation |
| `indexer/base.py` | `forward_fill_hourly` — hourly gap-fill (compounding disabled for Morpho) |
| `indexer/processor.py` | `ProtocolProcessor` — reads raw events, dispatches to decoder |
| `scripts/run_indexer.py` | CLI orchestrator for collector/processor daemons |
| `scratch/snapshot_compare.py` | Pinned-block verification across all markets |
| `scratch/verify_gaps.py` | Event completeness audit vs Alchemy RPC |

---

## 6. Known Limitations & Future Work

1. **Exotic Oracle Markets:** ~94 dust markets using Pendle PT oracles, hardcoded feeds, or custom adapters are excluded by the Chainlink whitelist. A multicall oracle engine would extend coverage.
2. **Missing Events (3-6 per market):** The HyperSync collector occasionally drops a handful of events at batch boundaries. These cause sub-$200K drift on $292M markets (<0.04%). A periodic RPC anchor would eliminate this entirely.
3. **Vault-Level Allocation Tracking:** The `MORPHO_ALLOCATION` and `MORPHO_VAULT` rows use a shares-to-assets approximation (`supply_shares ≈ supply_assets`) that degrades as the share exchange rate diverges. Tracking `totalSupplyShares` alongside assets would fix this.
