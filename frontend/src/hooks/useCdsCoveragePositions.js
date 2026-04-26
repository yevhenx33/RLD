import useSWR from "swr";
import { SIM_GRAPHQL_URL } from "../api/endpoints";
import { postGraphQL } from "../api/graphqlClient";
import { queryKeys } from "../api/queryKeys";

const COVERAGE_POSITIONS_QUERY = `
  query CoveragePositions($owner: String!, $market: String) {
    coveragePositions(owner: $owner, market: $market)
  }
`;

const fetchCoveragePositions = ([url, , variables]) =>
  postGraphQL(url, { query: COVERAGE_POSITIONS_QUERY, variables });

export function useCdsCoveragePositions(owner, market, paused = false) {
  const key = !paused && owner
    ? queryKeys.coveragePositions(SIM_GRAPHQL_URL, owner, market)
    : null;

  const { data, error, isLoading, mutate } = useSWR(
    key,
    fetchCoveragePositions,
    {
      refreshInterval: 10000,
      dedupingInterval: 1500,
      revalidateOnFocus: false,
      keepPreviousData: true,
    },
  );

  return {
    positions: data?.coveragePositions || [],
    error,
    isLoading,
    refresh: mutate,
  };
}
