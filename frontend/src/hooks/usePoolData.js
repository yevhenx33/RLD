import { useState, useEffect, useMemo, useCallback } from "react";
import { useSim } from "../context/SimulationContext";
import { useSimulation } from "./useSimulation";
import { useWallet } from "../context/WalletContext";
import { useBrokerAccount } from "./useBrokerAccount";
import { usePoolLiquidity } from "./usePoolLiquidity";
import { useChartControls } from "./useChartControls";
import { SIM_GRAPHQL_URL } from "../api/endpoints";
import { postGraphQL } from "../api/graphqlClient";

/**
 * usePoolData — Coordinator hook for the Pool LP page.
 *
 * Wires all 6 data sources in parallel and exposes a single `ready` flag.
 * Components render <Loader> until `ready` flips, then everything appears at once.
 *
 * Also provides a unified `refreshAll()` function for onRefreshComplete callbacks.
 */
export function usePoolData() {
  // ── 1. Shared simulation state (global context, 2s poll) ────────
  const sim = useSim();
  const {
    connected,
    loading,
    error,
    market,
    pool,
    poolTVL,
    funding,
    fundingFromNF,
    volumeData,
    marketInfo,
  } = sim;

  const selectedMarketKey =
    marketInfo?.marketId ||
    marketInfo?.market_id ||
    market?.marketId ||
    null;

  // ── 2. Wallet (instant) ──────────────────────────────────────────
  const { account } = useWallet();

  // ── 3. Chart controls + chart-specific simulation ────────────────
  const chartControls = useChartControls({
    defaultRange: "1W",
    defaultDays: 7,
    defaultResolution: "1H",
  });
  const { appliedStart, appliedEnd, resolution } = chartControls;
  const simBlockTs = market?.blockTimestamp || sim?.latest?.market?.blockTimestamp || null;
  const chartReferenceTime = useMemo(() => {
    if (!appliedEnd) return null;
    return Math.floor(new Date(`${appliedEnd}T23:59:59Z`).getTime() / 1000);
  }, [appliedEnd]);

  const chartStartTime = useMemo(() => {
    if (!simBlockTs || !appliedStart || !chartReferenceTime) return null;
    const wallStart = Math.floor(new Date(appliedStart).getTime() / 1000);
    return simBlockTs - (chartReferenceTime - wallStart);
  }, [appliedStart, chartReferenceTime, simBlockTs]);

  const chartEndTime = useMemo(() => {
    if (!simBlockTs || !appliedEnd || !chartReferenceTime) return null;
    const wallEnd = Math.floor(new Date(appliedEnd + "T23:59:59Z").getTime() / 1000);
    return simBlockTs - (chartReferenceTime - wallEnd);
  }, [appliedEnd, chartReferenceTime, simBlockTs]);

  const simChart = useSimulation({
    pollInterval: 2000,
    chartResolution: resolution,
    chartStartTime,
    chartEndTime,
    marketKey: selectedMarketKey,
  });
  const { chartData, volumeHistory } = simChart;

  // ── 4. Broker account (depends on wallet + marketInfo) ──────────
  const { hasBroker, brokerAddress, checkBroker, fetchBrokerBalance } = useBrokerAccount(
    account,
    marketInfo?.broker_factory,
    marketInfo?.collateral?.address,
  );

  const refreshAll = useCallback(async () => {
    await Promise.all([
      fetchBrokerBalance?.(),
      checkBroker?.(),
    ].filter(Boolean));
  }, [fetchBrokerBalance, checkBroker]);
  const refreshCallbacks = useMemo(() => [refreshAll], [refreshAll]);

  // ── 5. Pool liquidity (depends on broker + marketInfo) ──────────
  const {
    executeAddLiquidity,
    executeCollectFees,
    executeRemoveLiquidity,
    trackLpPosition,
    untrackLpPosition,
    activePosition,
    allPositions,
    positionsLoaded,
    refreshPosition,
    executing: lpExecuting,
    executionStep: lpStep,
    executionError: lpError,
    clearError: clearLpError,
  } = usePoolLiquidity(brokerAddress, marketInfo, {
    onRefreshComplete: refreshCallbacks,
  });

  // ── 6. Liquidity distribution bins (depends on pool data) ───────
  const [liquidityBins, setLiquidityBins] = useState([]);
  const [liqDistPrice, setLiqDistPrice] = useState(null);

  const buildLocalBins = useCallback((positions, price) => {
    if (!positions?.length || !price) return [];
    const NUM_BINS = 60;
    const minP = 0, maxP = 100;
    const binW = (maxP - minP) / NUM_BINS;
    return Array.from({ length: NUM_BINS }, (_, i) => {
      const priceFrom = minP + i * binW;
      const priceTo = minP + (i + 1) * binW;
      let liq = 0;
      for (const p of positions) {
        const tl = Math.min(p.tickLower ?? 0, p.tickUpper ?? 0);
        const tu = Math.max(p.tickLower ?? 0, p.tickUpper ?? 0);
        const pL = Math.pow(1.0001, tl);
        const pH = Math.pow(1.0001, tu);
        if (pH > priceFrom && pL < priceTo) liq += Number(p.liquidity || 0);
      }
      const sa = Math.sqrt(priceFrom), sb = Math.sqrt(priceTo);
      const sp = Math.max(sa, Math.min(Math.sqrt(price), sb));
      const a0 = sp < sb ? liq * (1 / sp - 1 / sb) / 1e6 : 0;
      const a1 = sp > sa ? liq * (sp - sa) / 1e6 : 0;
      return { price: ((priceFrom + priceTo) / 2).toFixed(3), priceFrom, priceTo, liquidity: liq, amount0: Math.max(0, a0), amount1: Math.max(0, a1) };
    });
  }, []);

  useEffect(() => {
    if (!selectedMarketKey) return;
    let cancelled = false;
    const LIQ_QUERY = `
      query PoolLiquidityDistribution($market: String) {
        liquidityDistribution(market: $market)
      }
    `;

    async function fetchDistribution() {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const json = await postGraphQL(SIM_GRAPHQL_URL, {
            query: LIQ_QUERY,
            variables: { market: selectedMarketKey },
          });
          const rawBins = json?.liquidityDistribution;
          if (!cancelled && rawBins?.length) {
            const curPrice = pool?.markPrice || 1;
            const bins = rawBins
              .filter((b) => b.priceHigh >= 2 && b.priceLow <= 20)
              .map((b) => {
                const priceFrom = b.priceLow;
                const priceTo = b.priceHigh;
                const midPrice = (priceFrom + priceTo) / 2;
                const liq = b.liquidity || 0;
                const sa = Math.sqrt(priceFrom), sb = Math.sqrt(priceTo);
                const sp = Math.max(sa, Math.min(Math.sqrt(curPrice), sb));
                const a0 = sp < sb ? liq * (1 / sp - 1 / sb) / 1e6 : 0;
                const a1 = sp > sa ? liq * (sp - sa) / 1e6 : 0;
                return {
                  price: midPrice.toFixed(3),
                  priceFrom, priceTo, liquidity: liq,
                  amount0: Math.max(0, a0), amount1: Math.max(0, a1),
                };
              });
            setLiquidityBins(bins);
            const priceFromMid = bins[Math.floor(bins.length / 2)]?.price;
            if (priceFromMid) setLiqDistPrice(parseFloat(priceFromMid));
            return;
          }
        } catch (err) {
          if (attempt < 2) {
            await new Promise(r => setTimeout(r, 2000));
            continue;
          }
          console.warn("[LP] GQL liquidityDistribution unavailable after retries:", err.message);
        }
      }
      // Fallback: build from local positions
      if (!cancelled && allPositions?.length) {
        const price = pool?.markPrice || 1;
        setLiquidityBins(buildLocalBins(allPositions, price));
      } else if (!cancelled) {
        setLiquidityBins([]);
        setLiqDistPrice(null);
      }
    }
    fetchDistribution();
    return () => { cancelled = true; };
  }, [pool?.markPrice, allPositions, buildLocalBins, selectedMarketKey]);

  // ── Readiness gate ────────────────────────────────────────────────
  // Render once the core pool snapshot is available. Chart candles and
  // liquidity bins are derived views and may legitimately be empty after an
  // indexer reset or before the first LP event replay completes.
  const ready = useMemo(() => {
    return (
      connected &&
      market !== null &&
      pool !== null &&
      marketInfo !== null &&
      positionsLoaded
    );
  }, [connected, market, pool, marketInfo, positionsLoaded]);

  return {
    // Gate
    ready,

    // Simulation state
    connected,
    loading,
    error,
    market,
    pool,
    poolTVL,
    funding,
    fundingFromNF,
    volumeData,
    marketInfo,
    simShared: sim,

    // Wallet
    account,

    // Broker
    hasBroker,
    brokerAddress,
    checkBroker,
    fetchBrokerBalance,

    // Chart
    chartControls,
    chartData,
    volumeHistory,
    simChart,

    // LP positions
    executeAddLiquidity,
    executeCollectFees,
    executeRemoveLiquidity,
    trackLpPosition,
    untrackLpPosition,
    activePosition,
    allPositions,
    refreshPosition,
    lpExecuting,
    lpStep,
    lpError,
    clearLpError,

    // Liquidity distribution
    liquidityBins,
    liqDistPrice,
    buildLocalBins,

    // Unified refresh
    refreshAll,
  };
}
