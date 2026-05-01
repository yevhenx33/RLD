# Frontend Data Fetching Patterns

Standards for loading, caching, and displaying data across all RLD frontend pages.

---

## Core Principles

1. **Single source of truth** — one hook per page owns all data
2. **Atomic UI updates** — toast + data appear at the same time
3. **Stale-while-refresh** — previous data persists until new data arrives (no flicker)

---

## Pattern A: `useBrokerData` (Perps Page)

> **Used by:** `/markets/perps/:address`

### Architecture

```
ONE GQL query → broker profile, TWAMM orders, pool snapshot, operations
  ↓
N RPC calls  → getCancelOrderState per active TWAMM order
  ↓
Client math  → sellRate, NAV, colRatio, isSolvent, priceLower/Upper
  ↓
ONE setState → atomic UI update
```

### Key Mechanism: Merge-on-Update

```js
// ✅ Correct — preserves previous data as baseline
setData((prev) => ({
  ...prev,
  brokerAddress: profile?.address || null,
  twammOrders: enrichedOrders.length > 0 || rawTwamm.length === 0
    ? enrichedOrders : prev?.twammOrders ?? [],
  // ... all other fields
}));
```

**Why?** When `fetchAll` runs after a TX, the indexer may not have processed the new block yet. Without merge, TWAMM orders briefly flash to `[]` for ~1 second.

**TWAMM special case:** Only overwrite `twammOrders` when:
- `enrichedOrders.length > 0` (real new data), OR
- `rawTwamm.length === 0` (GQL explicitly confirmed zero orders)

Otherwise, keep `prev.twammOrders`.

### Error Handling

```js
} catch (e) {
  console.warn("[BrokerData] fetchAll failed:", e);
  // On error, keep stale data — don't clear
}
```

Never reset state on fetch failure.

### Refresh After TX

```js
const refresh = useCallback(async () => {
  await new Promise((r) => setTimeout(r, 500)); // let indexer process block
  await fetchAll();
}, [fetchAll]);
```

---

## Pattern B: `useBondPositions` + `_syncAndNotify`

> **Used by:** transaction flows that need the bond list refreshed before showing success UI.

### Architecture

```
SWR fetch   → bonds(owner) GQL query
  ↓
Client math → notional, elapsed, remaining, accrued
  ↓
SWR cache   → keepPreviousData: true (no flicker)
```

### Key Mechanism: SWR with `keepPreviousData`

```js
const { data, mutate } = useSWR(swrKey, gqlFetcher, {
  refreshInterval: 15000,
  keepPreviousData: true,  // ← prevents flicker during re-fetch
  dedupingInterval: 2000,
});
```

### Key Mechanism: `_syncAndNotify` (Toast After State)

Execution hooks can accept `onRefreshComplete` callbacks:

```js
const { refresh: refreshBonds } = useBondPositions(...);

const { executeAction } = useExecutionHook(
  account, infrastructure,
  { onRefreshComplete: [refreshBonds] },  // ← toast waits for this
);
```

Inside the execution hook, `_syncAndNotify` runs after TX receipt:

```js
const _syncAndNotify = async (successStep, onSuccess, result) => {
  setStep("Syncing...");
  await new Promise((r) => setTimeout(r, 500));          // 1. wait for indexer
  await Promise.all(onRefreshComplete.map(fn => fn?.())); // 2. refresh data
  setStep(successStep);                                    // 3. update step
  if (onSuccess) onSuccess(result);                        // 4. fire toast
};
```

**Result:** Toast + bond list update appear simultaneously.

### Optimistic Updates

SWR's `mutate` supports optimistic writes for instant UI feedback:

```js
// Instant removal on close
const optimisticClose = (brokerAddress) => {
  mutate((prev) => ({
    ...prev,
    bonds: prev.bonds.filter(b => b.broker_address !== brokerAddress),
  }), { revalidate: true });  // still revalidate from server
};
```

---

## Anti-Patterns

| ❌ Don't | ✅ Do |
|---|---|
| `setData({...newData})` (replace) | `setData(prev => ({...prev, ...newData}))` (merge) |
| Toast immediately after `tx.wait()` | Toast after `await refresh()` completes |
| Clear state on fetch error | Keep stale data on error |
| Separate `useState` per field | Single `data` object with atomic updates |
| Multiple independent polling intervals | One `fetchAll` with one interval |

---

## Hook Call Order

When a refresh function from one hook is needed by another, declare the data hook first:

```js
// ✅ Correct order — refreshBonds available for onRefreshComplete
const { refresh: refreshBonds } = useBondPositions(...);
const { executeAction } = useExecutionHook(..., { onRefreshComplete: [refreshBonds] });

// ❌ Wrong — refreshBonds not yet defined
const { executeAction } = useExecutionHook(..., { onRefreshComplete: [refreshBonds] });
const { refresh: refreshBonds } = useBondPositions(...);
```

---

## Checklist for New Pages

- [ ] Single data hook with one GQL query + client-side math
- [ ] `setData(prev => ({...prev, ...}))` merge pattern
- [ ] Error catch preserves stale data
- [ ] 500ms delay before refresh after TX
- [ ] Toast fires AFTER `onRefreshComplete` resolves
- [ ] SWR option `keepPreviousData: true` if using SWR
- [ ] Optimistic updates for instant UI feedback where applicable
