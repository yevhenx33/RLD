import useSWR from "swr";
import { ENVIO_GRAPHQL_URL } from "../../api/endpoints";
import { queryKeys } from "../../api/queryKeys";
import { postGraphQL } from "../../api/graphqlClient";

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
      refreshInterval: 15000,
      dedupingInterval: 5000,
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
