import { useState, useMemo } from "react";
import useSWR from "swr";
import { ENVIO_GQL_URL, getPastDate, getToday } from "../utils/helpers";

const MAX_POINTS = 17520;

const fetchMarketRates = async ([url, resolution, startDate, endDate]) => {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: `{
        historicalRates(symbols: ["USDC"], resolution: "${resolution}", limit: ${MAX_POINTS}) {
          timestamp
          symbol
          apy
          price
        }
      }`,
    }),
  });

  const json = await res.json();
  if (json.errors) throw new Error(json.errors[0]?.message || "GraphQL query failed");

  const nodes = json?.data?.historicalRates || [];
  const startUnix = Math.floor(new Date(startDate).getTime() / 1000);
  const endDateObj = new Date(endDate);
  endDateObj.setUTCHours(23, 59, 59, 999);
  const endUnix = Math.floor(endDateObj.getTime() / 1000);

  return nodes
    .filter((node) => node.symbol === "USDC" && node.timestamp >= startUnix && node.timestamp <= endUnix)
    .map((node) => ({
      timestamp: node.timestamp,
      apy: node.apy,
      eth_price: node.price,
    }))
    .sort((a, b) => a.timestamp - b.timestamp);
};

export function useMarketData(resolution = "4H") {
  const [dates] = useState({ start: getPastDate(90), end: getToday() });
  const {
    data: rates,
    error,
    isLoading,
  } = useSWR(
    [ENVIO_GQL_URL, resolution, dates.start, dates.end],
    fetchMarketRates,
    { refreshInterval: 10000, revalidateOnFocus: false },
  );

  const stats = useMemo(() => {
    if (!rates || rates.length === 0)
      return { min: 0, max: 0, mean: 0, vol: 0 };
    const apys = rates.map((r) => r.apy);
    const mean = apys.reduce((a, b) => a + b, 0) / apys.length;
    const variance =
      apys.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / apys.length;
    return {
      min: Math.min(...apys),
      max: Math.max(...apys),
      mean,
      vol: Math.sqrt(variance),
    };
  }, [rates]);

  const swrLatest =
    rates && rates.length > 0
      ? rates[rates.length - 1]
      : { apy: 0, block_number: 0, timestamp: 0 };

  const latest = swrLatest;

  const dailyChange = useMemo(() => {
    if (!rates || rates.length < 2) return 0;

    // Find ~24h ago from historical data
    const latestTs = latest.timestamp || rates[rates.length - 1].timestamp;
    const targetTs = latestTs - 86400;

    const closest = rates.reduce((prev, curr) =>
      Math.abs(curr.timestamp - targetTs) < Math.abs(prev.timestamp - targetTs)
        ? curr
        : prev,
    );

    // Compare Live Latest vs Historical
    return latest.apy - closest.apy;
  }, [rates, latest]);

  const isCappedRaw = resolution === "RAW" && rates && rates.length >= 30000;
  return { rates, error, isLoading, stats, dailyChange, latest, isCappedRaw };
}
