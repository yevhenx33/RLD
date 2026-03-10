import { useState, useEffect, useMemo, useRef } from "react";
import useSWR from "swr";
import { SIM_API } from "../config/simulationConfig";

// Broker labels by deployment order (deployer always creates in this sequence)
const BROKER_LABELS = ["User A", "MM Daemon", "Chaos Trader"];

// ── GraphQL fetcher ──────────────────────────────────────────
const GQL_URL = `${SIM_API}/graphql`;

const gqlFetcher = ([url, query, variables]) => {
  const body = { query };
  if (variables) body.variables = variables;
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
    .then((r) => {
      if (!r.ok) throw new Error(`GraphQL HTTP ${r.status}`);
      return r.json();
    })
    .then((r) => {
      if (r.errors) console.warn("[GQL] errors:", r.errors);
      return r.data;
    });
};

// REST fetcher for chart data (resolution-specific, stays REST)
const restFetcher = (url) => fetch(url).then((r) => r.json());

// ── Single GraphQL query that replaces 7 REST calls ──────────
const SIM_QUERY = `
  query SimSnapshot {
    latest {
      blockNumber
      market {
        blockNumber
        blockTimestamp
        marketId
        normalizationFactor
        totalDebt
        lastUpdateTimestamp
        indexPrice
      }
      pool {
        poolId
        tick
        markPrice
        liquidity
        sqrtPriceX96
        token0Balance
        token1Balance
        feeGrowthGlobal0
        feeGrowthGlobal1
      }
      brokers {
        address
        collateral
        debt
        collateralValue
        debtValue
        healthFactor
      }
    }
    volume { volumeUsd swapCount }
    volumeHistory(hours: 168, bucketHours: 1) { timestamp volumeUsd swapCount }
    events(limit: 20, eventName: "Swap") {
      id blockNumber txHash eventName timestamp data
    }
    marketInfo {
      collateral { name symbol address }
      positionToken { name symbol address }
      brokerFactory
      infrastructure {
        brokerRouter brokerExecutor twammHook bondFactory basisTradeFactory
        poolFee tickSpacing poolManager v4Quoter
        v4PositionManager v4PositionDescriptor v4StateView
        universalRouter permit2
      }
      riskParams {
        minColRatio maintenanceMargin liqCloseFactor
        fundingPeriodSec debtCap
      }
    }
    status { totalBlockStates totalEvents lastIndexedBlock }
  }
`;

// Shallow-compare chart data to skip re-renders
const compareChartData = (a, b) => {
  if (a === b) return true;
  if (!a || !b) return false;
  const ad = a?.data,
    bd = b?.data;
  if (!ad || !bd || ad.length !== bd.length) return false;
  if (ad.length === 0) return true;
  return (
    ad[0].timestamp === bd[0].timestamp &&
    ad[ad.length - 1].timestamp === bd[bd.length - 1].timestamp &&
    ad.length === bd.length
  );
};

/**
 * Hook that connects to the simulation indexer API.
 * Uses a SINGLE GraphQL query for all core data (market, pool, brokers,
 * volume, events, marketInfo, status) and a separate REST call for
 * resolution-specific chart data.
 */
export function useSimulation({
  pollInterval = 2000,
  chartResolution = "1H",
  chartStartTime = null,
  chartEndTime = null,
} = {}) {
  const [connected, setConnected] = useState(false);
  const prevBlock = useRef(null);

  // ── Single GraphQL query for ALL core data ──────────────────
  const {
    data: gqlData,
    error: gqlError,
    isLoading: gqlLoading,
  } = useSWR([GQL_URL, SIM_QUERY, null], gqlFetcher, {
    refreshInterval: pollInterval,
    revalidateOnFocus: false,
    dedupingInterval: 1000,
    keepPreviousData: true,
    onSuccess: () => setConnected(true),
    onError: () => setConnected(false),
  });

  // ── Chart data (price history — still REST, resolution-specific) ──
  const chartUrl = useMemo(() => {
    let url = `${SIM_API}/api/chart/price?resolution=${chartResolution}&limit=1000`;
    if (chartStartTime) url += `&start_time=${chartStartTime}`;
    if (chartEndTime) url += `&end_time=${chartEndTime}`;
    return url;
  }, [chartResolution, chartStartTime, chartEndTime]);

  const { data: chartRaw, error: chartError } = useSWR(
    chartUrl,
    restFetcher,
    {
      refreshInterval: 30000,
      revalidateOnFocus: false,
      compare: compareChartData,
      keepPreviousData: false,
    },
  );

  // ── Extract data from GraphQL response ──────────────────────
  const latest = gqlData?.latest;
  const volumeRaw = gqlData?.volume;
  const volumeHistoryRaw = gqlData?.volumeHistory;
  const eventsRaw = gqlData?.events;
  const marketInfo = useMemo(() => {
    if (!gqlData?.marketInfo) return null;
    const mi = gqlData.marketInfo;
    return {
      collateral: mi.collateral,
      position_token: mi.positionToken,
      broker_factory: mi.brokerFactory,
      infrastructure: mi.infrastructure
        ? {
            broker_router: mi.infrastructure.brokerRouter,
            broker_executor: mi.infrastructure.brokerExecutor,
            twamm_hook: mi.infrastructure.twammHook,
            bond_factory: mi.infrastructure.bondFactory,
            basis_trade_factory: mi.infrastructure.basisTradeFactory,
            pool_fee: mi.infrastructure.poolFee,
            tick_spacing: mi.infrastructure.tickSpacing,
            pool_manager: mi.infrastructure.poolManager,
            v4_quoter: mi.infrastructure.v4Quoter,
            v4_position_manager: mi.infrastructure.v4PositionManager,
            v4_position_descriptor: mi.infrastructure.v4PositionDescriptor,
            v4_state_view: mi.infrastructure.v4StateView,
            universal_router: mi.infrastructure.universalRouter,
            permit2: mi.infrastructure.permit2,
          }
        : null,
      risk_params: mi.riskParams
        ? {
            min_col_ratio: mi.riskParams.minColRatio,
            maintenance_margin: mi.riskParams.maintenanceMargin,
            liq_close_factor: mi.riskParams.liqCloseFactor,
            funding_period_sec: mi.riskParams.fundingPeriodSec,
            debt_cap: mi.riskParams.debtCap,
          }
        : null,
    };
  }, [gqlData?.marketInfo]);

  const statusData = useMemo(() => {
    if (!gqlData?.status) return null;
    const s = gqlData.status;
    return {
      total_block_states: s.totalBlockStates,
      total_events: s.totalEvents,
      last_indexed_block: s.lastIndexedBlock,
    };
  }, [gqlData]);

  // ── Derived: market state ───────────────────────────────────
  const market = useMemo(() => {
    if (!latest?.market) return null;
    const ms = latest.market;
    return {
      marketId: ms.marketId,
      blockNumber: ms.blockNumber,
      blockTimestamp: ms.blockTimestamp,
      normalizationFactor: parseInt(ms.normalizationFactor) / 1e18,
      totalDebt: parseInt(ms.totalDebt) / 1e6,
      indexPrice: parseInt(ms.indexPrice) / 1e18,
      lastUpdateTimestamp: ms.lastUpdateTimestamp,
    };
  }, [latest]);

  // ── Derived: pool state ─────────────────────────────────────
  const pool = useMemo(() => {
    if (!latest?.pool) return null;
    const ps = latest.pool;
    return {
      poolId: ps.poolId,
      markPrice: ps.markPrice,
      tick: ps.tick,
      liquidity: ps.liquidity,
      sqrtPriceX96: ps.sqrtPriceX96,
      token0Balance: parseInt(ps.token0Balance || "0"),
      token1Balance: parseInt(ps.token1Balance || "0"),
      feeGrowthGlobal0: ps.feeGrowthGlobal0,
      feeGrowthGlobal1: ps.feeGrowthGlobal1,
    };
  }, [latest]);

  // ── Derived: pool TVL ───────────────────────────────────────
  const poolTVL = useMemo(() => {
    if (!pool) return 0;
    const t0 = pool.token0Balance / 1e6;
    const t1 = pool.token1Balance / 1e6;
    const price = pool.markPrice || 1;
    return t0 * price + t1;
  }, [pool]);

  // ── Derived: funding spread + annualized rate ────────────────
  // Contract formula: NF *= exp(-fundingRate × dt / fundingPeriod)
  //   fundingRate = (normalizedMark - index) / index
  //   normalizedMark = markPrice / NF
  //   fundingPeriod = configurable (default 30 days = 2_592_000s)
  const funding = useMemo(() => {
    if (!market || !pool) return null;

    const nf = market.normalizationFactor || 1;
    const normalizedMark = pool.markPrice / nf;
    const index = market.indexPrice;
    if (index <= 0) return null;

    const spread = normalizedMark - index;
    const spreadPct = (spread / index) * 100;

    // fundingRate as per contract (WAD-equivalent but in float)
    const fundingRate = spread / index;

    // Annualize: in the contract, rate is applied as exp(-rate * dt / period)
    // Over 1 year (365 days), the NF multiplier would be exp(-rate * 365d / period)
    // The annualized percentage change = (exp(-rate * 365*86400 / period) - 1) × 100
    const fundingPeriod = marketInfo?.risk_params?.funding_period_sec || 2_592_000;
    const yearSec = 365 * 86400;
    const annExponent = -fundingRate * yearSec / fundingPeriod;

    // Clamp exponent to avoid Infinity
    const clampedExp = Math.max(-20, Math.min(20, annExponent));
    const annPct = (Math.exp(clampedExp) - 1) * 100;

    return {
      spread,
      spreadPct,
      fundingRate,
      annualizedPct: annPct,      // contract-consistent annualized NF change %
      fundingPeriod,
      direction: spread >= 0 ? "LONGS_PAY" : "SHORTS_PAY",
    };
  }, [market, pool, marketInfo]);

  // ── Derived: broker positions ───────────────────────────────
  const brokers = useMemo(() => {
    if (!latest?.brokers?.length) return [];
    return latest.brokers.map((bp, i) => ({
      address: bp.address,
      label: BROKER_LABELS[i] || bp.address.slice(0, 8) + "...",
      collateral: parseInt(bp.collateral) / 1e6,
      debt: parseInt(bp.debt) / 1e6,
      collateralValue: parseInt(bp.collateralValue) / 1e6,
      debtValue: parseInt(bp.debtValue) / 1e6,
      healthFactor: bp.healthFactor,
    }));
  }, [latest]);

  // ── Derived: protocol-level stats ──────────────────────────
  const protocolStats = useMemo(() => {
    if (!brokers?.length || !market) return null;
    const totalCollateral = brokers.reduce((s, b) => s + b.collateralValue, 0);
    const totalDebtUnits = brokers.reduce((s, b) => s + b.debt, 0);
    const totalDebtUsd = totalDebtUnits * market.indexPrice;
    const overCollat =
      totalDebtUsd > 0 ? (totalCollateral / totalDebtUsd) * 100 : 0;
    return { totalCollateral, totalDebtUnits, totalDebtUsd, overCollat };
  }, [brokers, market]);

  // ── Derived: chart data ─────────────────────────────────────
  const chartData = useMemo(() => {
    if (!chartRaw?.data?.length) return [];

    const volBars = volumeHistoryRaw || [];
    const volMap = new Map();
    for (const bar of volBars) {
      volMap.set(bar.timestamp, bar.volumeUsd);
    }
    const bucketSec = 3600; // 1 hour default

    return chartRaw.data.map((d) => {
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

  // ── Derived: observed funding from NF change ────────────────
  // This measures the actual cumulative NF drift and annualizes it
  // using log-return math consistent with the exponential model
  const fundingFromNF = useMemo(() => {
    if (!market) return null;
    const nfNow = market.normalizationFactor;
    const blockTs = market.blockTimestamp;
    const lastUpdate = market.lastUpdateTimestamp;
    if (!blockTs || !lastUpdate || lastUpdate >= blockTs) return null;
    const elapsedSec = blockTs - lastUpdate;
    if (elapsedSec < 60) return null;

    // Use log-return: ln(NF) / elapsed, then annualize
    // This is consistent with the exponential model NF = NF0 * exp(r*t)
    const logReturn = Math.log(nfNow); // NF starts at 1.0, so log(NF/1) = log(NF)
    const annualLogReturn = (logReturn / elapsedSec) * (365 * 86400);
    const annualPct = (Math.exp(annualLogReturn) - 1) * 100;
    const dailyPct = annualPct / 365;

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
    return ((nowPrice - oldPrice) / oldPrice) * 100;
  }, [market, chartData]);

  // ── Derived: volume data ────────────────────────────────────
  const volumeData = useMemo(() => {
    if (!volumeRaw) return null;
    const vol = volumeRaw.volumeUsd;
    let formatted;
    if (vol >= 1e9) formatted = `$${(vol / 1e9).toFixed(2)}B`;
    else if (vol >= 1e6) formatted = `$${(vol / 1e6).toFixed(2)}M`;
    else if (vol >= 1e3) formatted = `$${(vol / 1e3).toFixed(0)}K`;
    else formatted = `$${vol.toLocaleString()}`;
    return {
      volume_usd: vol,
      swap_count: volumeRaw.swapCount,
      volume_formatted: formatted,
    };
  }, [volumeRaw]);

  // ── Derived: events ─────────────────────────────────────────
  const events = useMemo(() => {
    if (!eventsRaw?.length) return [];
    return eventsRaw
      .filter((e) => e.eventName === "Swap")
      .map((e) => ({
        id: e.id,
        blockNumber: e.blockNumber,
        txHash: e.txHash,
        eventName: e.eventName,
        timestamp: e.timestamp,
        data: e.data ? JSON.parse(e.data) : null,
      }));
  }, [eventsRaw]);

  // ── Block change detection ──────────────────────────────────
  const [blockChanged, setBlockChanged] = useState(false);
  useEffect(() => {
    if (!latest?.blockNumber) return;
    if (
      prevBlock.current !== null &&
      latest.blockNumber !== prevBlock.current
    ) {
      setBlockChanged(true); // eslint-disable-line react-hooks/set-state-in-effect
      const timer = setTimeout(() => setBlockChanged(false), 300);
      return () => clearTimeout(timer);
    }
    prevBlock.current = latest.blockNumber;
  }, [latest?.blockNumber]);

  return {
    // Connection
    connected,
    loading: gqlLoading && !gqlData,
    error: gqlError || chartError,

    // Raw (for backward compat with components that access latest directly)
    latest: latest
      ? {
          block_number: latest.blockNumber,
          market_states: latest.market
            ? [
                {
                  block_number: latest.market.blockNumber,
                  block_timestamp: latest.market.blockTimestamp,
                  market_id: latest.market.marketId,
                  normalization_factor: parseInt(
                    latest.market.normalizationFactor,
                  ),
                  total_debt: parseInt(latest.market.totalDebt),
                  index_price: parseInt(latest.market.indexPrice),
                  last_update_timestamp: latest.market.lastUpdateTimestamp,
                },
              ]
            : [],
          pool_states: latest.pool
            ? [
                {
                  pool_id: latest.pool.poolId,
                  tick: latest.pool.tick,
                  mark_price: latest.pool.markPrice,
                  liquidity: latest.pool.liquidity,
                  sqrt_price_x96: latest.pool.sqrtPriceX96,
                  token0_balance: latest.pool.token0Balance,
                  token1_balance: latest.pool.token1Balance,
                },
              ]
            : [],
          broker_positions: (latest.brokers || []).map((b) => ({
            broker_address: b.address,
            collateral: parseInt(b.collateral),
            debt: parseInt(b.debt),
            collateral_value: parseInt(b.collateralValue),
            debt_value: parseInt(b.debtValue),
            health_factor: b.healthFactor,
          })),
        }
      : null,
    statusData,

    // Derived
    market,
    pool,
    poolTVL,
    funding,
    fundingFromNF,
    oracleChange24h,
    volumeData,
    protocolStats,
    marketInfo,
    brokers,
    chartData,
    volumeHistory: (volumeHistoryRaw || []).map((b) => ({
      timestamp: b.timestamp,
      volume: b.volumeUsd,
      swapCount: b.swapCount,
    })),
    events,
    blockChanged,

    // Meta
    blockNumber: latest?.blockNumber || null,
    totalBlocks: statusData?.total_block_states || 0,
    totalEvents: statusData?.total_events || 0,
  };
}
