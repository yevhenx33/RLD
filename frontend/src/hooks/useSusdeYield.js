import useSWR from "swr";
import { API_URL } from "../utils/helpers";

const SUSDE_YIELD_URL = `${API_URL}/yields/susde`;

const fetcher = (url) => fetch(url).then((r) => r.json());

/**
 * Fetches real-time sUSDe staking yield from the backend.
 * The rates daemon polls Ethena hourly and stores in DB.
 * Returns { stakingYield, protocolYield, avg30d, avg90d, isLoading, error }
 */
export function useSusdeYield() {
  const { data, error, isLoading } = useSWR(SUSDE_YIELD_URL, fetcher, {
    refreshInterval: 60000, // refresh every 60s
    dedupingInterval: 30000, // dedupe within 30s
    revalidateOnFocus: false,
  });

  return {
    stakingYield: data?.stakingYield ?? null,
    protocolYield: data?.protocolYield ?? null,
    avg30d: data?.avg30d ?? null,
    avg90d: data?.avg90d ?? null,
    lastUpdated: data?.lastUpdated ?? null,
    isLoading,
    error,
  };
}
