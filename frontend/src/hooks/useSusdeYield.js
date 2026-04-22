import useSWR from "swr";

import { ENVIO_GRAPHQL_URL } from "../api/endpoints";
import { postGraphQL } from "../api/graphqlClient";
import { queryKeys } from "../api/queryKeys";

const SUSDE_QUERY = `{ latestRates { susde } }`;

const gqlFetcher = async ([url]) => {
  return postGraphQL(url, { query: SUSDE_QUERY });
};

/**
 * Fetches real-time sUSDe staking yield from the Envio GraphQL API.
 * Returns { stakingYield, protocolYield, avg30d, avg90d, isLoading, error }
 */
export function useSusdeYield() {
  const { data, error, isLoading } = useSWR(
    queryKeys.envioSusdeLatest(ENVIO_GRAPHQL_URL),
    gqlFetcher,
    {
    refreshInterval: 60000,
    dedupingInterval: 30000,
    revalidateOnFocus: false,
    },
  );

  const yieldPct = data?.latestRates?.susde ?? null;

  return {
    stakingYield: yieldPct,
    protocolYield: yieldPct,
    avg30d: yieldPct, // single latest value used as fallback
    avg90d: yieldPct,
    lastUpdated: null,
    isLoading,
    error,
  };
}
