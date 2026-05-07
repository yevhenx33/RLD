import useSWR from "swr";
import { API_STATUS_URL } from "../../api/endpoints";
import { queryKeys } from "../../api/queryKeys";
import { REFRESH_INTERVALS } from "../../config/refreshIntervals";

async function fetchApiStatus([url]) {
  const res = await fetch(url, {
    headers: {
      Accept: "application/json",
    },
  });
  const json = await res.json().catch(() => null);
  if (!res.ok) {
    const reason = json?.reason || json?.status || `HTTP ${res.status}`;
    throw new Error(`API status unavailable: ${reason}`);
  }
  return json;
}

export function useApiStatus() {
  const { data, error, isLoading } = useSWR(
    queryKeys.apiStatus(API_STATUS_URL),
    fetchApiStatus,
    {
      refreshInterval: REFRESH_INTERVALS.API_STATUS_MS,
      dedupingInterval: REFRESH_INTERVALS.API_STATUS_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const latestTs = data?.generatedAt || 0;
  return {
    latestTimestamp: latestTs,
    ratesLoaded: Boolean(data?.ready),
    isCapped: false,
    isLoading,
    error,
    status: data?.status || "unknown",
    protocols: data?.protocols || [],
  };
}
