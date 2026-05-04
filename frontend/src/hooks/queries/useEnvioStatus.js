import useSWR from "swr";
import { ENVIO_GRAPHQL_URL } from "../../api/endpoints";
import { queryKeys } from "../../api/queryKeys";
import { postGraphQL } from "../../api/graphqlClient";
import { REFRESH_INTERVALS } from "../../config/refreshIntervals";

const ENVIO_STATUS_QUERY = `
  query EnvioStatus {
    historicalRates(symbols: ["USDC"], resolution: "1D", limit: 1) {
      timestamp
    }
  }
`;

async function fetchEnvioStatus([url]) {
  return postGraphQL(url, { query: ENVIO_STATUS_QUERY });
}

export function useEnvioStatus() {
  const { data, error, isLoading } = useSWR(
    queryKeys.envioStatus(ENVIO_GRAPHQL_URL),
    fetchEnvioStatus,
    {
      refreshInterval: REFRESH_INTERVALS.ANALYTICS_STATUS_MS,
      dedupingInterval: REFRESH_INTERVALS.ANALYTICS_STATUS_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const latestTs = data?.historicalRates?.[0]?.timestamp || 0;
  return {
    latestTimestamp: latestTs,
    ratesLoaded: Boolean(latestTs),
    isCapped: false,
    isLoading,
    error,
  };
}
