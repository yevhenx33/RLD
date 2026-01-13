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

  // --- WebSocket Real-Time Updates ---
  const [realtimeLatest, setRealtimeLatest] = useState(null);

  useEffect(() => {
    // Determine WS Protocol
    const protocol = API_URL.startsWith("https") ? "wss" : "ws";
    const host = API_URL.replace(/^https?:\/\//, "");
    const wsUrl = `${protocol}://${host}/ws/rates`;

    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "UPDATE" && msg.data) {
          // Assume this hook tracks USDC (Default)
          setRealtimeLatest({
            apy: msg.data.USDC,
            timestamp: msg.data.timestamp,
            eth_price: msg.data.ETH,
          });
        }
      } catch (e) {
        console.error("WS Parse Error", e);
      }
    };

    return () => {
      if (ws.readyState === 1) ws.close();
    };
  }, []);

  // Merge SWR data with Real-Time WS data
  const swrLatest =
    rates && rates.length > 0
      ? rates[rates.length - 1]
      : { apy: 0, block_number: 0, timestamp: 0 };

  const latest = realtimeLatest || swrLatest;

  const dailyChange = useMemo(() => {
    if (!rates || rates.length < 2) return 0;
    
    // Find ~24h ago from historical data
    const latestTs = latest.timestamp || rates[rates.length - 1].timestamp;
    const targetTs = latestTs - 86400;
    
    const closest = rates.reduce((prev, curr) =>
      Math.abs(curr.timestamp - targetTs) < Math.abs(prev.timestamp - targetTs)
        ? curr
        : prev
    );
    
    // Compare Live Latest vs Historical
    return latest.apy - closest.apy;
  }, [rates, latest]);

  const isCappedRaw = resolution === "RAW" && rates && rates.length >= 30000;
  return { rates, error, isLoading, stats, dailyChange, latest, isCappedRaw };
}
