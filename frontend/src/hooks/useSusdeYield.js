import useSWR from "swr";

const ETHENA_YIELD_URL = "https://ethena.fi/api/yields/protocol-and-staking-yield";

const fetcher = (url) => fetch(url).then((r) => r.json());

/**
 * Fetches real-time sUSDe staking yield from Ethena's public API.
 * Returns { stakingYield, protocolYield, avg30d, avg90d, isLoading, error }
 */
export function useSusdeYield() {
  const { data, error, isLoading } = useSWR(ETHENA_YIELD_URL, fetcher, {
    refreshInterval: 60000,    // refresh every 60s
    dedupingInterval: 30000,   // dedupe within 30s
    revalidateOnFocus: false,
  });

  return {
    stakingYield: data?.stakingYield?.value ?? null,
    protocolYield: data?.protocolYield?.value ?? null,
    avg30d: data?.avg30dSusdeYield?.value ?? null,
    avg90d: data?.avg90dSusdeYield?.value ?? null,
    lastUpdated: data?.stakingYield?.lastUpdated ?? null,
    isLoading,
    error,
  };
}
