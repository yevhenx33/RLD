import { useCallback, useMemo } from "react";
import useSWR from "swr";
import { SIM_GRAPHQL_URL } from "../api/endpoints";
import { postGraphQL } from "../api/graphqlClient";
import { queryKeys } from "../api/queryKeys";
import { REFRESH_INTERVALS } from "../config/refreshIntervals";

const BONDS_QUERY = `
  query BondPositions($owner: String!) {
    bonds(owner: $owner)
  }
`;

const gqlFetcher = async ([url, , variables]) => {
  return postGraphQL(url, { query: BONDS_QUERY, variables });
};

/**
 * useBondPositions — Fetch bond data from the indexer GraphQL API.
 *
 * Uses the `bonds(owner)` resolver which returns raw bond rows from the
 * indexer DB. Computes display fields (notional_usd, elapsed, remaining)
 * client-side from the raw data.
 *
 * @param {string}  account        Connected wallet address
 * @param {number}  entryRate      Fallback borrow rate (for accrued calc)
 * @param {string}  bondFactoryAddr Optional bond factory address filter
 * @param {number}  pollInterval   Polling ms (default 15000)
 */
export function useBondPositions(account, entryRate, bondFactoryAddr, pollInterval = 15000, paused = false) {
  const swrKey = account
    ? queryKeys.bondPositions(SIM_GRAPHQL_URL, account)
    : null;

  const { data: gqlData, error: _error, mutate, isLoading } = useSWR(
    swrKey,
    gqlFetcher,
    {
      refreshInterval: paused ? 0 : pollInterval, // 0 = no polling while TX executes
      revalidateOnFocus: false,
      dedupingInterval: REFRESH_INTERVALS.POSITION_DEDUPE_MS,
      keepPreviousData: true,
    },
  );

  // ── Transform raw DB rows to component format ──────────────
  const bonds = useMemo(() => {
    const raw = gqlData?.bonds || [];
    return raw
      .filter((b) => b.status === "active")
      .filter((b) => !bondFactoryAddr || (b.factory_address && b.factory_address.toLowerCase() === bondFactoryAddr.toLowerCase()))
      .map((b) => {
        // Raw amounts are 6-decimal integers → divide by 1e6 for USD
        const notionalUsd = Number(b.notional) / 1e6;
        const hedgeUsd = Number(b.hedge) / 1e6;
        const durationSec = Number(b.duration);
        const durationDays = durationSec / 86400;

        // Estimate elapsed from mint_block (approximate)
        // Block time ~2s on Anvil fork — no exact timestamp available
        const elapsedDays = 0;

        // Entry rate: use indexed mark_price at mint_block (from block_states)
        // Priority: indexed entry_rate → localStorage borrowRateAPY → current market rate
        let rate = b.entry_rate || null;
        if (!rate) {
          try {
            const meta = JSON.parse(
              localStorage.getItem(`rld_bond_${b.broker_address.toLowerCase()}`) || "null",
            );
            if (meta?.borrowRateAPY) rate = meta.borrowRateAPY;
          } catch { /* ignore */ }
        }
        if (!rate) rate = entryRate || 0;
        const entryBorrowRate = rate;
        const accrued = notionalUsd * (rate / 100) * (elapsedDays / 365);

        return {
          id: parseInt(b.broker_address.slice(-4), 16) % 10000,
          brokerAddress: b.broker_address,
          principal: notionalUsd,
          debtTokens: notionalUsd,
          fixedRate: rate,
          entryBorrowRate,
          maturityDays: Math.round(durationDays),
          elapsed: elapsedDays,
          remaining: Math.round(durationDays - elapsedDays),
          maturityDate: "—",
          frozen: true, // Bonds are always frozen
          isMatured: false,
          accrued,
          freeCollateral: hedgeUsd,
          orderId: "0x" + "0".repeat(64),
          hasActiveOrder: true,
          txHash: b.mint_tx || null,
          status: b.status,
        };
      });
  }, [gqlData, entryRate, bondFactoryAddr]);

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
          };
        },
        { revalidate: true },
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
            notional: String(Math.round(notionalUsd * 1e6)),
            hedge: "0",
            duration: String(Math.round(durationHours * 3600)),
            mint_block: 0,
            mint_tx: null,
          };
          return { bonds: [newBond, ...bonds] };
        },
        { revalidate: true },
      );
    },
    [mutate, account],
  );

  return {
    bonds,
    loading: isLoading && !gqlData,
    refresh: () => mutate(),
    optimisticClose,
    optimisticCreate,
  };
}
