# Asynchronous Event-Driven Data Indexing Architecture

## Collector → Processor → Pre-Aggregation Pipeline

> A generalized reference architecture for building scalable, fault-tolerant data indexing systems over append-only event sources (blockchains, message queues, CDC streams, IoT telemetry).

---

## 1. Problem Statement

Any system that must reconstruct live state from high-throughput event streams faces three fundamental tensions:

1. **Ingestion speed vs. decode complexity** — Raw event collection must keep pace with source throughput, but decoding events into domain-meaningful state requires expensive computation (lookups, math, cross-referencing).

2. **Write isolation vs. read unification** — Different event sources have incompatible schemas and failure modes. Combining them in a single table creates blast-radius problems. But downstream consumers need a single, unified read interface.

3. **Query latency vs. data freshness** — Analytical queries over raw timeseries are expensive at scale. Pre-aggregation solves latency but introduces staleness and schema coupling.

The Collector-Processor-Pre-Aggregation (CPP) pipeline resolves all three by physically separating concerns across three asynchronous tiers, each with independent failure domains, scaling characteristics, and operational concerns.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                       EVENT SOURCE                                   │
│     (Blockchain, Kafka, CDC Stream, Webhook, IoT Gateway)            │
└───────────────────────┬──────────────────────────────────────────────┘
                        │
           ┌────────────▼────────────┐
           │                         │
     ┌─────▼──────┐          ┌──────▼──────┐
     │ Collector A │          │ Collector B │        TIER 1: COLLECTION
     │ (Source α)  │          │ (Source β)  │        ─ Raw event capture
     └─────┬──────┘          └──────┬──────┘        ─ Zero decode logic
           │                        │               ─ Append-only writes
           │  INSERT raw            │  INSERT raw
           ▼                        ▼
     ┌────────────┐          ┌────────────┐
     │ raw_events │          │ raw_events │         MEMPOOL LAYER
     │   (α)      │          │   (β)      │         ─ Physically isolated
     └─────┬──────┘          └──────┬──────┘        ─ Source-specific schemas
           │                        │
     ┌─────▼──────┐          ┌──────▼──────┐
     │ Processor A │          │ Processor B │       TIER 2: PROCESSING
     │ (Decode α)  │          │ (Decode β)  │       ─ Stateful decode
     └─────┬──────┘          └──────┬──────┘       ─ Domain math
           │                        │               ─ Write to isolated tables
           │  INSERT decoded        │  INSERT decoded
           ▼                        ▼
     ┌────────────┐          ┌────────────┐
     │ timeseries │          │ timeseries │         PROTOCOL-ISOLATED
     │   (α)      │          │   (β)      │         SERVING TABLES
     └─────┬──────┘          └──────┬──────┘
           │                        │
           │  Mirror / Merge        │
           └────────┬───────────────┘
                    ▼
           ┌────────────────┐
           │   canonical    │                       CANONICAL TABLE
           │  serving_table │                       ─ Unified read layer
           └───────┬────────┘
                   │
          ┌────────▼─────────┐
          │  Materialized    │                      TIER 3: PRE-AGGREGATION
          │  Views / Rollups │                      ─ Hourly, daily, weekly
          └────────┬─────────┘                      ─ AggregatingMergeTree
                   │                                  or equivalent
                   ▼
          ┌────────────────┐
          │   API / Query  │                        SERVING LAYER
          │    Layer       │                        ─ GraphQL / REST
          └────────────────┘                        ─ Reads only from
                                                      pre-agg + canonical
```

---

## 3. Tier 1: Collection

### Responsibility
Capture raw events from external sources and persist them with zero transformation. The collector is a **dumb pipe** — it knows how to authenticate, paginate, and write, but never interprets the data.

### Design Principles

**3.1 Raw-In, Raw-Out**

The collector writes the exact bytes received from the source. No field renaming, no type casting, no filtering. This preserves the ability to replay and reprocess from the raw layer if decoder logic changes.

```
Raw Event Schema (generalized):
─────────────────────────────────
  sequence_id     UInt64          # Monotonic ordering key (block number, offset, sequence)
  source_timestamp DateTime      # Timestamp from the source, not ingestion time
  event_type      String          # Source-native event classifier
  payload         String/Bytes    # Raw event data (hex, JSON, protobuf)
  metadata_1..N   Nullable(*)     # Source-specific indexed fields (topics, keys, partitions)
```

**3.2 Idempotent Writes**

Collectors must be safely re-runnable. Use upsert semantics (e.g., `ReplacingMergeTree` keyed on `(sequence_id, event_type)`) or deduplication at the storage layer. This allows crash recovery by simply re-collecting from the last known cursor.

**3.3 Cursor Tracking**

Maintain a persistent cursor per source in a separate state table:

```
collector_state:
  source_id              String     # Unique source identifier
  last_collected_seq     UInt64     # Last successfully persisted sequence ID
  source_head_seq        UInt64     # Latest known sequence at the source
  updated_at             DateTime
```

The gap between `last_collected_seq` and `source_head_seq` is the **collector lag** — a primary health metric.

**3.4 Confirmation Depth**

For sources with reorg risk (blockchains) or eventual consistency (distributed logs), apply a confirmation buffer:

```
effective_head = source_head - CONFIRMATION_DEPTH
```

This sacrifices latency (typically 15–60 seconds) for correctness.

**3.5 Batch Sizing**

Collection runs in fixed-size batches (e.g., 100K events or 100K sequence units per cycle). This bounds:
- Memory consumption per cycle
- ClickHouse part creation frequency
- Recovery time after failures (at most one batch is lost)

### Failure Modes & Mitigations

| Failure | Impact | Mitigation |
|---------|--------|------------|
| Source API timeout | Collection pauses | Exponential backoff + client rotation |
| Duplicate delivery | Phantom events | Upsert storage engine (ReplacingMergeTree) |
| Source reorg | Stale events | Confirmation depth buffer |
| Collector crash | Gap in raw data | Cursor-based recovery from last checkpoint |
| Storage full | Write rejection | Part count monitoring + TTL policies |

---

## 4. Tier 2: Processing (Decode + State Reconstruction)

### Responsibility
Read from the raw mempool, decode events into domain-meaningful structures, reconstruct cumulative state, and write normalized timeseries to protocol-isolated output tables.

### Design Principles

**4.1 Physical Output Isolation**

Each source writes to its own physically disjoint output table. This is the single most important architectural decision in the pipeline. If source α has a bug requiring a data wipe, source β is physically untouchable.

```python
# Poka-Yoke routing table (mistake-proofing)
OUTPUT_TABLES = {
    "SOURCE_ALPHA": "alpha_timeseries",
    "SOURCE_BETA":  "beta_timeseries",
    "SOURCE_GAMMA": "gamma_timeseries",
}

class BaseProcessor:
    @property
    def output_table(self) -> str:
        return OUTPUT_TABLES[self.source_id]  # Hard route — no fallback
```

Log the routing decision before every write cycle:

```
[α-Processor] POKA-YOKE: Output table = alpha_timeseries
```

**4.2 Decode from Raw Hex / Bytes**

Do not depend on external ABI decoders or schema registries in the hot path. Decode directly from raw bytes using offset maps:

```
Event: Transfer(from, to, amount)
  from   = payload[0:64]     # First 32-byte word
  to     = payload[64:128]   # Second word
  amount = payload[128:192]  # Third word
```

This eliminates the web3/protobuf dependency in the critical path and provides 10,000x+ throughput improvement over reflection-based decoders.

**4.3 Stateful Accumulation**

Many domain models require cumulative state (running balances, index tracking, compounding). The processor maintains in-memory accumulators seeded from the database:

```
┌─────────────────────────────────────────┐
│  Accumulator State (per entity)         │
│  ─ total_supply = Σ(deposits) - Σ(withdrawals)
│  ─ total_borrow = Σ(borrows) - Σ(repayments)
│  ─ index = latest_index_update          │
│  ─ last_event_seq = cursor position     │
└─────────────────────────────────────────┘
```

**Genesis Anchoring:** For sources that launched with pre-existing state (migrations, protocol upgrades), do not initialize accumulators at zero. Query the source at a known "genesis anchor" sequence and seed from there.

**4.4 Processor Cursor Independence**

The processor maintains its own cursor, separate from the collector:

```
processor_state:
  source_id              String
  last_processed_seq     UInt64
  updated_at             DateTime
```

The gap between `collector.last_collected_seq` and `processor.last_processed_seq` is the **processing lag**. If processing lag grows unbounded, the processor is the bottleneck.

**4.5 Normalized Output Schema**

All source-specific processors emit rows conforming to a single canonical schema:

```
Normalized Timeseries Row:
──────────────────────────
  timestamp        DateTime
  source_id        String          # Which source produced this
  entity_id        String          # Unique entity within source
  metric_1         Float64         # Domain-specific metric
  metric_2         Float64
  ...
  inserted_at      DateTime        # Write timestamp for upsert resolution
```

### Failure Modes & Mitigations

| Failure | Impact | Mitigation |
|---------|--------|------------|
| Decode error (malformed event) | Single event lost | Dead-letter queue + continue processing |
| State drift (missed event) | Cumulative metrics diverge | Periodic RPC/source audit at pinned sequences |
| Cross-source contamination | Wrong table receives data | Poka-Yoke routing table (compile-time enforcement preferred) |
| Processor crash mid-batch | Partial writes | Cursor advances only after full batch commit |

---

## 5. Read Unification Layer

### The Problem
Downstream consumers (APIs, dashboards) need a single table to query across all sources. But writing all sources to a shared table creates the blast-radius problem.

### Solution: Write Isolation + Read Unification

**Option A: Merge Engine View (ClickHouse)**

```sql
CREATE TABLE canonical_serving AS alpha_timeseries
ENGINE = Merge(currentDatabase(), '^(alpha|beta|gamma)_timeseries$')
```

The Merge engine creates a virtual table that dynamically `UNION ALL`s the underlying tables. Reads see a unified table; writes are physically impossible through the view.

**Option B: Mirror-on-Write**

Each processor writes to both its isolated table AND a canonical serving table:

```python
def merge(self, ch, decoded_rows):
    # 1. Write to isolated table (source of truth)
    write_to(self.output_table, rows)
    
    # 2. Mirror to canonical serving table
    write_to("canonical_serving", rows)
```

This adds write amplification but avoids query-time UNION overhead.

**Option C: Change Data Capture**

Use CDC (e.g., ClickHouse Materialized Views on isolated tables) to automatically propagate inserts to the canonical table:

```sql
CREATE MATERIALIZED VIEW mv_mirror_alpha TO canonical_serving
AS SELECT * FROM alpha_timeseries;
```

### Recommendation

Use **Option B (Mirror-on-Write)** for production systems with high read QPS. The 2x write cost is negligible compared to query-time UNION overhead at scale. Use the isolated table as the authoritative source for audits and reprocessing.

---

## 6. Tier 3: Pre-Aggregation

### Responsibility
Maintain materialized rollups at multiple time resolutions (hourly, daily, weekly) to serve analytical queries with sub-100ms latency regardless of the raw timeseries size.

### Design Principles

**6.1 Aggregation Tiers**

```
Raw timeseries  ──▶  Hourly rollup  ──▶  Daily rollup  ──▶  Weekly rollup
(~1M rows/day)      (~24K rows/day)     (~1K rows/day)     (~150 rows/day)
```

Each tier reduces cardinality by 10–100x. API queries route to the appropriate tier based on the requested time range.

**6.2 Materialized Views (Streaming Aggregation)**

Prefer streaming MVs that trigger on INSERT, not batch rebuilds:

```sql
CREATE MATERIALIZED VIEW mv_hourly_agg
TO hourly_agg_table
AS
SELECT
    source_id,
    entity_id,
    toStartOfHour(timestamp)          AS ts,
    avgState(metric_1)                AS metric_1_avg,
    maxState(metric_2)                AS metric_2_max,
    argMaxState(metric_3, timestamp)  AS metric_3_latest
FROM canonical_serving
GROUP BY source_id, entity_id, ts
```

**6.3 AggregatingMergeTree (ClickHouse-Specific)**

Use `AggregatingMergeTree` with `-State` / `-Merge` combinator functions for incremental aggregation. This allows multiple INSERTs to the same `(entity_id, ts)` bucket to be merged at query time without re-scanning raw data.

```sql
-- Writing (via MV):  avgState(metric) → stores intermediate state
-- Reading (in API):  avgMerge(metric_state) → produces final result

SELECT
    entity_id,
    ts,
    avgMerge(metric_1_avg) AS metric_1
FROM hourly_agg_table
WHERE ts >= '2025-01-01'
GROUP BY entity_id, ts
ORDER BY ts
```

**6.4 Fallback: Full Rebuild**

Maintain a `rebuild_aggregates()` function that truncates and repopulates all pre-aggregated tables from the canonical source. This is the nuclear option for schema migrations or corruption recovery:

```python
def rebuild_aggregates(ch):
    ch.command("TRUNCATE TABLE hourly_agg_table")
    ch.command("""
        INSERT INTO hourly_agg_table
        SELECT source_id, entity_id, toStartOfHour(timestamp) AS ts,
               avgState(metric_1), ...
        FROM canonical_serving
        GROUP BY source_id, entity_id, ts
    """)
```

**6.5 TTL Policies**

Apply data retention policies at each tier:

```
Raw timeseries:      6–12 months (highest storage cost)
Hourly rollups:      18–36 months
Daily/Weekly:        36–72 months (minimal storage)
```

---

## 7. API Serving Layer

### Design Principles

**7.1 Query Routing by Time Range**

```python
def resolve_table(requested_range_hours: int) -> str:
    if requested_range_hours <= 48:
        return "canonical_serving"      # Raw resolution
    elif requested_range_hours <= 720:   # 30 days
        return "hourly_agg_table"
    elif requested_range_hours <= 8760:  # 1 year
        return "daily_agg_table"
    else:
        return "weekly_agg_table"
```

**7.2 Freshness Envelope**

Every API response includes a freshness metadata block:

```json
{
  "freshness": {
    "ready": true,
    "status": "ready",
    "generated_at": 1715087363,
    "source_lags": {
      "alpha": { "collector_lag": 3, "processing_lag": 0 },
      "beta":  { "collector_lag": 12, "processing_lag": 5 }
    }
  }
}
```

This allows clients to make informed decisions about data staleness.

**7.3 Latest-Snapshot Table**

For dashboard "current values" widgets, maintain a separate snapshot table with one row per entity, updated on every processing cycle:

```sql
CREATE TABLE entity_latest (
    source_id    String,
    entity_id    String,
    timestamp    DateTime,
    metric_1     Float64,
    ...
    inserted_at  DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (source_id, entity_id)
```

This avoids expensive `argMax` scans over the full timeseries for "what's the latest value?" queries.

---

## 8. Forward-Fill Strategy

### The Problem
Event-driven sources emit data only when state changes. Between events, the timeseries has gaps. Dashboards and charts expect contiguous hourly data.

### Solution: Gap-Aware Forward Fill

```
Physical events:     E₁ ──── gap ──── E₂ ──── gap ──── E₃
                     t=0    t=1..5    t=6    t=7..10   t=11

After forward-fill: E₁  F  F  F  F  F  E₂  F  F  F  F  E₃
                    t=0  1  2  3  4  5  t=6  7  8  9  10  11

Where F = forward-filled row (carries E's values, optionally with compounding)
```

### Implementation

```python
def forward_fill_hourly(df, source_id, compound=True):
    for entity_id in df["entity_id"].unique():
        entity_rows = df[df["entity_id"] == entity_id]
        
        # Build complete hourly range
        full_range = pd.date_range(
            start=entity_rows["timestamp"].min(),
            end=entity_rows["timestamp"].max(),
            freq="h",
        )
        
        # Merge actual data onto template
        template = pd.DataFrame({"timestamp": full_range})
        merged = template.merge(entity_rows, on="timestamp", how="left")
        
        # Forward-fill from last known values
        merged = merged.ffill()
        
        # Optional: compound interest during gaps
        if compound:
            is_gap = merged["is_physical_event"] == False
            merged.loc[is_gap, "balance"] *= (1 + merged.loc[is_gap, "apy"] / 8760)
```

### When to Skip Compounding

- **Physical accounting systems** (e.g., Morpho Blue) where interest is explicitly emitted as events — compounding would double-count.
- **Snapshot-based sources** where each event already reflects the complete state.

---

## 9. Operational Runbook

### Health Metrics

| Metric | Formula | Alert Threshold |
|--------|---------|-----------------|
| Collector lag | `source_head - last_collected_seq` | > 10 minutes of events |
| Processing lag | `last_collected_seq - last_processed_seq` | > 50K events |
| Part count (ClickHouse) | `SELECT count() FROM system.parts WHERE active` | > 3000 per table |
| Insert rate | Events per second sustained | < 50% of baseline |
| State drift | `abs(reconstructed_value - source_rpc_value)` | > 1% |

### Common Operations

**Reprocess a source from scratch:**
```sql
-- 1. Reset processor cursor
DELETE FROM processor_state WHERE source_id = 'alpha';

-- 2. Clear derived data (isolated — other sources unaffected)
TRUNCATE TABLE alpha_timeseries;

-- 3. Clear mirrored data for this source only
DELETE FROM canonical_serving WHERE source_id = 'alpha';

-- 4. Restart processor — it will replay from genesis
```

**Add a new source:**
```
1. Create raw_events_gamma table (copy schema from existing)
2. Create gamma_timeseries table (same schema as canonical_serving)
3. Implement GammaCollector (extends BaseCollector)
4. Implement GammaProcessor (extends BaseProcessor)
5. Add to OUTPUT_TABLES routing map
6. Register in daemon loop
7. If using Merge engine, update regex: '^(alpha|beta|gamma)_timeseries$'
```

**Verify state accuracy:**
```
1. Pick a random sequence checkpoint
2. Replay events from genesis to checkpoint in isolation
3. Compare reconstructed state against source's native query at that checkpoint
4. Acceptable drift: < 0.01% for financial data
```

---

## 10. Technology-Agnostic Mapping

This architecture is not ClickHouse-specific. Here's how the components map to other stacks:

| Concept | ClickHouse | PostgreSQL | Apache Kafka + Flink | AWS |
|---------|-----------|------------|---------------------|-----|
| Raw mempool | ReplacingMergeTree | Table + UPSERT | Kafka topic | Kinesis + S3 |
| Isolated timeseries | ReplacingMergeTree | Partitioned table | Keyed state | DynamoDB / Timestream |
| Merge view | Merge engine | UNION ALL view | — | Athena federated query |
| Pre-aggregation | AggregatingMergeTree + MV | Materialized view (pg_cron) | Flink windowed aggregation | Redshift MV |
| Latest snapshot | ReplacingMergeTree | Table + UPSERT | KTable | DynamoDB |
| Cursor state | ReplacingMergeTree | Table + UPSERT | Consumer group offsets | DynamoDB |

---

## 11. Decision Framework

### When to Use This Architecture

✅ **Good fit:**
- High-throughput append-only event sources (>10K events/sec)
- Multiple heterogeneous sources that must be queryable through a unified API
- Domain logic requires stateful reconstruction (running balances, indices)
- Read patterns span multiple time horizons (real-time + historical analytics)
- Sources have independent reliability characteristics and failure modes

❌ **Over-engineered for:**
- Single source with simple schema
- Sub-second latency requirements (use streaming / CQRS instead)
- Small data volumes (<1M events total)
- Sources that already provide pre-computed aggregates

### Scaling Vectors

| Bottleneck | Solution |
|-----------|----------|
| Collection throughput | Parallelize collectors per source (shard by contract/partition) |
| Processing throughput | Shard processors by entity_id range |
| Query latency | Add pre-aggregation tiers or caching (Redis, CDN) |
| Storage cost | Tiered TTL policies + cold storage offload |
| Source diversity | New source = new collector + processor + isolated table (zero impact on existing) |

---

## 12. Summary: The Three Laws

1. **Law of Separation:** Collectors never decode. Processors never fetch. The API never writes. Each tier has exactly one job.

2. **Law of Isolation:** A bug in source α can never corrupt source β's data. Physical table isolation is non-negotiable. The unified read layer is a mathematical abstraction over disjoint storage.

3. **Law of Replayability:** Any derived state can be reconstructed from the raw mempool. The raw layer is immutable and append-only. Processors are deterministic functions over raw events. Delete derived data freely; never delete raw data (until TTL).
