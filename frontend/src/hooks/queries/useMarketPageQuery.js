import useSWR from "swr";
import { apiGraphQL } from "../../api/apiClient";
import { MARKET_PAGE_QUERY } from "../../api/apiQueries";
import { API_GRAPHQL_URL } from "../../api/endpoints";
import { queryKeys } from "../../api/queryKeys";
import { REFRESH_INTERVALS } from "../../config/refreshIntervals";

export function useMarketPageQuery({
  protocol,
  marketId,
  timeseriesLimit,
  flowLimit,
  allocationLimit = 0,
}) {
  return useSWR(
    queryKeys.apiMarketPage(API_GRAPHQL_URL, protocol, marketId),
    ([, , variables]) =>
      apiGraphQL("MarketPage", {
        query: MARKET_PAGE_QUERY,
        variables: {
          protocol: variables.protocol,
          marketId: variables.marketId,
          timeseriesLimit,
          flowLimit,
          allocationLimit,
        },
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );
}
