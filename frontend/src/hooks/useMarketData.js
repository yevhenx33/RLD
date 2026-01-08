import { useState, useMemo } from "react";
import useSWR from "swr";
import { API_URL, fetcher, getPastDate, getToday } from "../utils/helpers";

export function useMarketData(resolution = "4H") {
  const [dates] = useState({ start: getPastDate(90), end: getToday() });
  const getUrl = () =>
    `${API_URL}/rates?resolution=${resolution}&start_date=${dates.start}&end_date=${dates.end}`;
  const {
    data: rates,
    error,
    isLoading,
  } = useSWR(getUrl(), fetcher, { refreshInterval: 10000 });

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

  const dailyChange = useMemo(() => {
    if (!rates || rates.length < 2) return 0;
    const latestTs = rates[rates.length - 1].timestamp;
    const targetTs = latestTs - 86400;
    const closest = rates.reduce((prev, curr) =>
      Math.abs(curr.timestamp - targetTs) < Math.abs(prev.timestamp - targetTs)
        ? curr
        : prev
    );
    return rates[rates.length - 1].apy - closest.apy;
  }, [rates]);

  const latest =
    rates && rates.length > 0
      ? rates[rates.length - 1]
      : { apy: 0, block_number: 0, timestamp: 0 };
  const isCappedRaw = resolution === "RAW" && rates && rates.length >= 30000;
  return { rates, error, isLoading, stats, dailyChange, latest, isCappedRaw };
}
