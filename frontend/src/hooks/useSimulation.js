import { useState, useEffect, useMemo, useRef } from "react";
import useSWR from "swr";
import { SIM_API } from "../config/simulationConfig";

// Broker labels by deployment order (deployer always creates in this sequence)
const BROKER_LABELS = ["User A", "MM Daemon", "Chaos Trader"];

const fetcher = (url) => fetch(url).then((r) => r.json());

// Shallow-compare JSON arrays to skip SWR re-renders when data is identical
const compareChartData = (a, b) => {
  if (a === b) return true;
  if (!a || !b) return false;
  const ad = a?.data, bd = b?.data;
  if (!ad || !bd || ad.length !== bd.length) return false;
  // Compare first and last timestamps — if unchanged, data is the same
  if (ad.length === 0) return true;
  return ad[0].timestamp === bd[0].timestamp &&
    ad[ad.length - 1].timestamp === bd[bd.length - 1].timestamp &&
    ad.length === bd.length;
};

/**
 * Hook that connects to the simulation indexer API.
 * Provides live market state, pool state, broker positions,
 * chart data and recent events.
 */
export function useSimulation({
  pollInterval = 2000,
  chartResolution = "1H",
  chartStartTime = null,
  chartEndTime = null,
} = {}) {
  const [connected, setConnected] = useState(false);
  const prevBlock = useRef(null);

  // ── Live snapshot (polled) ──────────────────────────────────
  const {
    data: latest,
    error: latestError,
    isLoading: latestLoading,
  } = useSWR(`${SIM_API}/api/latest`, fetcher, {
    refreshInterval: pollInterval,
    revalidateOnFocus: false,
    dedupingInterval: 1000,
    onSuccess: () => setConnected(true),
    onError: () => setConnected(false),
  });

  // ── Chart data (price history — resolution-bucketed) ────────
  const chartUrl = useMemo(() => {
    let url = `${SIM_API}/api/chart/price?resolution=${chartResolution}&limit=1000`;
    if (chartStartTime) url += `&start_time=${chartStartTime}`;
    if (chartEndTime) url += `&end_time=${chartEndTime}`;
    return url;
  }, [chartResolution, chartStartTime, chartEndTime]);

  const { data: chartRaw, error: chartError } = useSWR(
    chartUrl,
    fetcher,
    {
      refreshInterval: 30000, // 30s — chart data changes slowly
      revalidateOnFocus: false,
      compare: compareChartData,
      keepPreviousData: false, // allow GC of old data
    },
  );

  // ── Recent events ───────────────────────────────────────────
  const { data: eventsRaw } = useSWR(
    `${SIM_API}/api/events?limit=20`,
    fetcher,
    { refreshInterval: pollInterval * 3, revalidateOnFocus: false },
  );

  // ── 24h trade volume (server-aggregated) ────────────────────
  const { data: volumeData } = useSWR(`${SIM_API}/api/volume`, fetcher, {
    refreshInterval: pollInterval * 10,
    revalidateOnFocus: false,
  });

  // ── Volume history (bar chart data) ─────────────────────────
  const { data: volumeHistoryRaw } = useSWR(
    `${SIM_API}/api/volume-history?hours=168&bucket=1`,
    fetcher,
    { refreshInterval: pollInterval * 10, revalidateOnFocus: false },
  );

  // ── On-chain market info (token names, risk params) ────────
  const { data: marketInfo } = useSWR(`${SIM_API}/api/market-info`, fetcher, {
    refreshInterval: 0, // static config, fetch once
    revalidateOnFocus: false,
  });

  // ── Indexer status ──────────────────────────────────────────
  const { data: statusData } = useSWR(`${SIM_API}/api/status`, fetcher, {
    refreshInterval: pollInterval * 5,
    revalidateOnFocus: false,
  });

  // ── Derived: market state ───────────────────────────────────
  const market = useMemo(() => {
    if (!latest?.market_states?.length) return null;
    const ms = latest.market_states[0];
    return {
      marketId: ms.market_id,
      blockNumber: ms.block_number,
      blockTimestamp: ms.block_timestamp,
      normalizationFactor: ms.normalization_factor / 1e18,
      totalDebt: ms.total_debt / 1e6,
      indexPrice: ms.index_price / 1e18,
      lastUpdateTimestamp: ms.last_update_timestamp,
    };
  }, [latest]);

  // ── Derived: pool state ─────────────────────────────────────
  const pool = useMemo(() => {
    if (!latest?.pool_states?.length) return null;
    const ps = latest.pool_states[0];
    return {
      poolId: ps.pool_id,
      markPrice: ps.mark_price,
      tick: ps.tick,
      liquidity: ps.liquidity,
      sqrtPriceX96: ps.sqrt_price_x96,
    };
  }, [latest]);

  // ── Derived: funding spread ─────────────────────────────────
  const funding = useMemo(() => {
    if (!market || !pool) return null;
    const spread = pool.markPrice - market.indexPrice;
    const spreadPct =
      market.indexPrice > 0 ? (spread / market.indexPrice) * 100 : 0;
    return {
      spread,
      spreadPct,
      direction: spread >= 0 ? "LONGS_PAY" : "SHORTS_PAY",
    };
  }, [market, pool]);

  // ── Derived: broker positions ───────────────────────────────
  const brokers = useMemo(() => {
    if (!latest?.broker_positions?.length) return [];
    return latest.broker_positions.map((bp, i) => ({
      address: bp.broker_address,
      label: BROKER_LABELS[i] || bp.broker_address.slice(0, 8) + "...",
      collateral: bp.collateral / 1e6,
      debt: bp.debt / 1e6,
      collateralValue: bp.collateral_value / 1e6,
      debtValue: bp.debt_value / 1e6,
      healthFactor: bp.health_factor,
    }));
  }, [latest]);

  // ── Derived: protocol-level stats ──────────────────────────
  const protocolStats = useMemo(() => {
    if (!brokers?.length || !market) return null;
    const totalCollateral = brokers.reduce((s, b) => s + b.collateralValue, 0);
    // debt is in wRLP units; convert to USD with indexPrice
    const totalDebtUnits = brokers.reduce((s, b) => s + b.debt, 0);
    const totalDebtUsd = totalDebtUnits * market.indexPrice;
    const overCollat =
      totalDebtUsd > 0 ? (totalCollateral / totalDebtUsd) * 100 : 0;

    return { totalCollateral, totalDebtUnits, totalDebtUsd, overCollat };
  }, [brokers, market]);

  // ── Derived: chart data ─────────────────────────────────────
  const chartData = useMemo(() => {
    if (!chartRaw?.data?.length) return [];

    // Build a volume lookup from real volume history bars
    const volBars = volumeHistoryRaw?.bars || [];
    const volMap = new Map();
    for (const bar of volBars) {
      volMap.set(bar.timestamp, bar.volume_usd);
    }

    // Bucket size in seconds (default 1 hour)
    const bucketSec = (volumeHistoryRaw?.bucket_hours || 1) * 3600;

    return chartRaw.data.map((d) => {
      // Find volume for this data point's time bucket
      const bucketTs = Math.floor((d.timestamp || 0) / bucketSec) * bucketSec;
      const vol = volMap.get(bucketTs) || 0;

      return {
        timestamp: d.timestamp,
        blockNumber: d.block_number,
        indexPrice: d.index_price,
        markPrice: d.mark_price || null,
        normalizationFactor: d.normalization_factor,
        totalDebt: d.total_debt,
        tick: d.tick,
        liquidity: d.liquidity,
        volume: vol,
      };
    });
  }, [chartRaw, volumeHistoryRaw]);

  // ── Derived: funding from NF change ─────────────────────────
  // Per the whitepaper: NF(t+Δt) = NF(t) · (1 - F·Δt)
  // NF starts at 1.0 and drifts based on cumulative funding.
  // Daily rate = (NF_now - 1) / elapsed_days
  const fundingFromNF = useMemo(() => {
    if (!market) return null;

    const nfNow = market.normalizationFactor;
    const blockTs = market.blockTimestamp;
    const lastUpdate = market.lastUpdateTimestamp;

    if (!blockTs || !lastUpdate || lastUpdate >= blockTs) return null;

    const elapsedSec = blockTs - lastUpdate;
    if (elapsedSec < 60) return null;

    // NF change from genesis (1.0)
    const totalChange = nfNow - 1.0;
    const dailyPct = (totalChange / (elapsedSec / 86400)) * 100;
    const annualPct = dailyPct * 365;

    return { dailyPct, annualPct };
  }, [market]);

  // ── Derived: oracle (index) price 24h change ───────────────
  const oracleChange24h = useMemo(() => {
    if (!market || !chartData?.length) return null;

    const nowPrice = market.indexPrice;
    const earliest = chartData[chartData.length - 1];
    if (!earliest || !earliest.indexPrice) return null;

    const oldPrice = earliest.indexPrice;
    if (oldPrice === 0) return null;

    const changePct = ((nowPrice - oldPrice) / oldPrice) * 100;
    return changePct;
  }, [market, chartData]);

  // ── Derived: events ─────────────────────────────────────────
  const events = useMemo(() => {
    if (!eventsRaw?.length) return [];
    return eventsRaw
      .filter((e) => e.event_name === "Swap")
      .map((e) => ({
        id: e.id,
        blockNumber: e.block_number,
        txHash: e.tx_hash,
        eventName: e.event_name,
        timestamp: e.block_timestamp,
        data: e.event_data,
      }));
  }, [eventsRaw]);

  // ── Block change detection ──────────────────────────────────
  const [blockChanged, setBlockChanged] = useState(false);
  useEffect(() => {
    if (!latest?.block_number) return;
    if (
      prevBlock.current !== null &&
      latest.block_number !== prevBlock.current
    ) {
      setBlockChanged(true); // eslint-disable-line react-hooks/set-state-in-effect
      const timer = setTimeout(() => setBlockChanged(false), 300);
      return () => clearTimeout(timer);
    }
    prevBlock.current = latest.block_number;
  }, [latest?.block_number]);

  return {
    // Connection
    connected,
    loading: latestLoading,
    error: latestError || chartError,

    // Raw
    latest,
    statusData,

    // Derived
    market,
    pool,
    funding,
    fundingFromNF,
    oracleChange24h,
    volumeData,
    protocolStats,
    marketInfo,
    brokers,
    chartData,
    volumeHistory: (volumeHistoryRaw?.bars || []).map((b) => ({
      timestamp: b.timestamp,
      volume: b.volume_usd,
      swapCount: b.swap_count,
    })),
    events,
    blockChanged,

    // Meta
    blockNumber: latest?.block_number || null,
    totalBlocks: statusData?.total_block_states || 0,
    totalEvents: statusData?.total_events || 0,
  };
}
