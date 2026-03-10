import { useCallback } from "react";
import useSWR from "swr";
import { SIM_API } from "../config/simulationConfig";

const fetcher = (url) => fetch(url).then((r) => r.json());

/**
 * useBondPositions — Fetch enriched bond data from the indexer API.
 *
 * Uses the enriched `/api/bonds?enrich=true` endpoint which returns
 * all bond data (metadata + live on-chain state) in a single API call.
 * Server-side batched RPC eliminates 20+ sequential browser calls.
 *
 * Features:
 * - SWR with keepPreviousData: bonds stay visible during refetch (no blank screen)
 * - Optimistic updates: close/create instantly update local state
 * - 15s polling for background sync
 *
 * @param {string}  account      Connected wallet address
 * @param {number}  entryRate    Fallback rate (for accrued calculation)
 * @param {string}  bondFactoryAddr Optional bond factory address filter (to separate Bonds from Basis Trades)
 * @param {number}  pollInterval Polling ms (default 15000)
 */
export function useBondPositions(account, entryRate, bondFactoryAddr, pollInterval = 15000) {
  const apiUrl = account
    ? `${SIM_API}/api/bonds?owner=${account.toLowerCase()}&status=all&enrich=true`
    : null;

  const { data, error: _error, mutate, isLoading } = useSWR(apiUrl, fetcher, {
    refreshInterval: pollInterval,
    revalidateOnFocus: false,
    dedupingInterval: 2000,
    keepPreviousData: true, // ← critical: show stale bonds during revalidation
  });

  // ── Transform server data to match existing component contract ──
  const bonds = (data?.bonds || [])
    .filter((b) => b.status === "active") // only active bonds (closed bonds hidden)
    .filter((b) => !bondFactoryAddr || (b.bond_factory && b.bond_factory.toLowerCase() === bondFactoryAddr.toLowerCase()))
    .map((b) => {
      const notional = b.notional_usd || 0;
      const rate = entryRate || 0;
      const elapsedDays = b.elapsed_days || 0;
      const accrued = notional * (rate / 100) * (elapsedDays / 365);

      // Read entry-time borrow rate from localStorage (stored at position open)
      let entryBorrowRate = rate;
      try {
        const meta = JSON.parse(localStorage.getItem(`rld_bond_${b.broker_address.toLowerCase()}`) || "null");
        if (meta?.borrowRateAPY) entryBorrowRate = meta.borrowRateAPY;
      } catch { /* ignore */ }

      return {
        id: b.bond_id,
        brokerAddress: b.broker_address,
        principal: notional,
        debtTokens: b.debt_usd || 0,
        fixedRate: rate,
        entryBorrowRate,
        maturityDays: b.maturity_days || 0,
        elapsed: elapsedDays,
        remaining: b.remaining_days || 0,
        maturityDate: b.maturity_date || "—",
        frozen: b.frozen || false,
        isMatured: b.is_matured || false,
        accrued,
        freeCollateral: b.free_collateral || 0,
        orderId: b.order_id || "0x" + "0".repeat(64),
        hasActiveOrder: b.has_active_order || false,
        txHash: b.created_tx || null,
        status: b.status,
      };
    });

  // ── Optimistic close: instantly remove bond from UI ──────────
  const optimisticClose = useCallback(
    (brokerAddress) => {
      mutate(
        (prev) => {
          if (!prev?.bonds) return prev;
          return {
            ...prev,
            bonds: prev.bonds.filter(
              (b) =>
                b.broker_address.toLowerCase() !== brokerAddress.toLowerCase(),
            ),
            count: prev.count - 1,
          };
        },
        { revalidate: true }, // revalidate in background after optimistic update
      );
    },
    [mutate],
  );

  // ── Optimistic create: add placeholder bond immediately ──────
  const optimisticCreate = useCallback(
    (brokerAddress, notionalUsd, durationHours) => {
      mutate(
        (prev) => {
          const bonds = prev?.bonds || [];
          const newBond = {
            broker_address: brokerAddress,
            owner: account?.toLowerCase(),
            status: "active",
            notional_usd: notionalUsd,
            debt_usd: notionalUsd, // approximate
            free_collateral: 0,
            remaining_days: Math.ceil(durationHours / 24),
            elapsed_days: 0,
            maturity_days: Math.ceil(durationHours / 24),
            is_matured: false,
            frozen: false,
            has_active_order: true,
            bond_id: parseInt(brokerAddress.slice(-4), 16) % 10000,
            maturity_date: "—",
            created_tx: null,
          };
          return { bonds: [newBond, ...bonds], count: bonds.length + 1 };
        },
        { revalidate: true },
      );
    },
    [mutate, account],
  );

  return {
    bonds,
    loading: isLoading && !data,
    refresh: () => mutate(),
    optimisticClose,
    optimisticCreate,
  };
}
