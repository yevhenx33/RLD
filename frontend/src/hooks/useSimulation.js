import { useState, useEffect, useMemo, useRef } from "react";
import useSWR from "swr";
import { SIM_GRAPHQL_URL } from "../api/endpoints";
import { postGraphQL } from "../api/graphqlClient";
import { queryKeys } from "../api/queryKeys";

// Broker labels by deployment order (deployer always creates in this sequence)
const BROKER_LABELS = ["User A", "MM Daemon", "Chaos Trader"];

// ── GraphQL fetchers ────────────────────────────────────────
const fetchSimulationSnapshot = ([url, , variables]) =>
  postGraphQL(url, { query: SIM_QUERY, variables });

const fetchSimulationAccount = ([url, , variables]) =>
  postGraphQL(url, { query: ACCOUNT_QUERY, variables });

const fetchSimulationCandles = ([url, , variables]) =>
  postGraphQL(url, { query: CHART_QUERY, variables });

// ── Single GraphQL query — indexer returns JSON scalars ───────
const SIM_QUERY = `
  query SimSnapshot($market: String) {
    snapshot(market: $market)
    events(limit: 20) { blockNumber eventName data }
    marketInfo(market: $market)
    indexerStatus { lastIndexedBlock lastIndexedAt totalEvents }
  }
`;

// ── Helper: remap flat marketInfo JSON to nested format components expect ──
function _remapMarketInfo(mi) {
  if (!mi) return null;
  return {
    marketId: mi.marketId || mi.market_id || null,
    poolId: mi.poolId || mi.pool_id || null,
    pool_id: mi.poolId || mi.pool_id || null,
    collateral: mi.collateral || { name: mi.wausdc ? "waUSDC" : "Unknown", symbol: "waUSDC", address: mi.wausdc || "" },
    position_token: mi.positionToken || mi.position_token || { name: "wRLP", symbol: "wRLP", address: mi.wrlp || "" },
    broker_factory: mi.brokerFactory || mi.broker_factory || "",
    infrastructure: {
      broker_router: mi.infrastructure?.brokerRouter || mi.infrastructure?.broker_router || mi.brokerRouter || mi.swapRouter || "",
      broker_executor: mi.infrastructure?.brokerExecutor || mi.infrastructure?.broker_executor || mi.brokerExecutor || "",
      twamm_hook: mi.infrastructure?.twammHook || mi.infrastructure?.twamm_hook || mi.twammHook || "",
      twapEngine: mi.infrastructure?.twapEngine || mi.infrastructure?.twap_engine || mi.twapEngine || mi.twap_engine || "",
      twap_engine: mi.infrastructure?.twapEngine || mi.infrastructure?.twap_engine || mi.twapEngine || mi.twap_engine || "",
      poolId: mi.infrastructure?.poolId || mi.infrastructure?.pool_id || mi.poolId || mi.pool_id || "",
      pool_id: mi.infrastructure?.poolId || mi.infrastructure?.pool_id || mi.poolId || mi.pool_id || "",
      bond_factory: mi.infrastructure?.bondFactory || mi.infrastructure?.bond_factory || mi.bondFactory || "",
      basis_trade_factory: mi.infrastructure?.basisTradeFactory || mi.infrastructure?.basis_trade_factory || mi.basisTradeFactory || "",
      pool_fee: mi.infrastructure?.poolFee || mi.poolFee || 0,
      tick_spacing: mi.infrastructure?.tickSpacing || mi.tickSpacing || 0,
      pool_manager: mi.infrastructure?.poolManager || mi.poolManager || "",
      v4_quoter: mi.infrastructure?.v4Quoter || mi.v4Quoter || "",
      v4_position_manager: mi.infrastructure?.v4PositionManager || mi.v4PositionManager || "",
      v4_position_descriptor: mi.infrastructure?.v4PositionDescriptor || mi.v4PositionDescriptor || "",
      v4_state_view: mi.infrastructure?.v4StateView || mi.v4StateView || "",
      universal_router: mi.infrastructure?.universalRouter || mi.universalRouter || "",
      permit2: mi.infrastructure?.permit2 || mi.permit2 || "",
      cds_coverage_factory: mi.infrastructure?.cdsCoverageFactory || mi.infrastructure?.cds_coverage_factory || mi.cdsCoverageFactory || mi.cds_coverage_factory || "",
      cdsCoverageFactory: mi.infrastructure?.cdsCoverageFactory || mi.infrastructure?.cds_coverage_factory || mi.cdsCoverageFactory || mi.cds_coverage_factory || "",
    },
    risk_params: mi.riskParams || {
      min_col_ratio: mi.minColRatio || 0,
      min_col_ratio_pct: mi.minColRatio ? `${(parseFloat(mi.minColRatio) / 1e16).toFixed(0)}%` : "—",
      maintenance_margin: mi.maintenanceMargin || 0,
      maintenance_margin_pct: mi.maintenanceMargin ? `${(parseFloat(mi.maintenanceMargin) / 1e16).toFixed(0)}%` : "—",
      liq_close_factor: mi.liqCloseFactor || 0,
      funding_period_sec: mi.fundingPeriodSec || 0,
      debt_cap: mi.debtCap || 0,
    },
    external_contracts: mi.externalContracts || null,
  };
}

// ── Account-specific GraphQL query (bonds, balances) ──────────
const ACCOUNT_QUERY = `
  query AccountSnapshot($owner: String!, $status: String) {
    bonds(owner: $owner, status: $status) {
      bondId brokerAddress notionalUsd debtUsd freeCollateral
      maturityDays elapsedDays remainingDays maturityDate
      frozen isMatured hasActiveOrder orderId bondFactory createdTx status
    }
    balances(owner: $owner) {
      token { name symbol address }
      balance
    }
  }
`;

// ── Chart-specific GraphQL query (candles) ────────────────────
const CHART_QUERY = `
  query ChartCandles($marketId: String!, $resolution: String!, $limit: Int, $fromBucket: Int, $toBucket: Int) {
    candles(marketId: $marketId, resolution: $resolution, limit: $limit, fromBucket: $fromBucket, toBucket: $toBucket) {
      bucket
      indexOpen indexClose indexHigh indexLow
      markOpen markClose markHigh markLow
      volumeUsd swapCount
    }
  }
`;

const RESOLUTION_SECONDS = {
  "1m": 60,
  "5m": 5 * 60,
  "15m": 15 * 60,
  "1h": 60 * 60,
  "4h": 4 * 60 * 60,
  "1d": 24 * 60 * 60,
  "1w": 7 * 24 * 60 * 60,
};

function buildFlatChartData({ snapshot, chartStartTime, chartEndTime, chartResolution }) {
  const markPrice = Number(snapshot?.pool?.markPrice || 0);
  const indexPrice = Number(snapshot?.market?.indexPrice || 0);
  if (markPrice <= 0 && indexPrice <= 0) return [];

  const resolutionSeconds = RESOLUTION_SECONDS[chartResolution.toLowerCase()] || RESOLUTION_SECONDS["1h"];
  const end = chartEndTime || snapshot?.blockTimestamp || snapshot?.market?.blockTimestamp || Math.floor(Date.now() / 1000);
  const start = chartStartTime || Math.max(0, end - resolutionSeconds * 24);
  const safeEnd = end > start ? end : start + resolutionSeconds;

  return [
    {
      timestamp: start,
      indexPrice,
      markPrice,
      indexOpen: indexPrice,
      indexHigh: indexPrice,
      indexLow: indexPrice,
      markOpen: markPrice,
      markHigh: markPrice,
      markLow: markPrice,
      normalizationFactor: 0,
      totalDebt: 0,
      tick: 0,
      liquidity: 0,
      volume: 0,
      swapCount: 0,
    },
    {
      timestamp: safeEnd,
      indexPrice,
      markPrice,
      indexOpen: indexPrice,
      indexHigh: indexPrice,
      indexLow: indexPrice,
      markOpen: markPrice,
      markHigh: markPrice,
      markLow: markPrice,
      normalizationFactor: 0,
      totalDebt: 0,
      tick: 0,
      liquidity: 0,
      volume: 0,
      swapCount: 0,
    },
  ];
}

function buildSnapshotChartPoint(snapshot, timestamp) {
  const markPrice = Number(snapshot?.pool?.markPrice || 0);
  const indexPrice = Number(snapshot?.market?.indexPrice || 0);
  if (markPrice <= 0 && indexPrice <= 0) return null;

  return {
    timestamp,
    indexPrice,
    markPrice,
    indexOpen: indexPrice,
    indexHigh: indexPrice,
    indexLow: indexPrice,
    markOpen: markPrice,
    markHigh: markPrice,
    markLow: markPrice,
    normalizationFactor: 0,
    totalDebt: 0,
    tick: 0,
    liquidity: 0,
    volume: 0,
    swapCount: 0,
  };
}

/**
 * useSimulation — Connects to the simulation indexer API.
 *
 * Tier 1 (GLOBAL_QUERY): market, pool, volume, events, marketInfo, rates — 2s poll
 * Tier 2 (ACCOUNT_QUERY): bonds, balances — 5s poll, keyed on account
 * Tier 3 (CHART_QUERY): OHLC candles — 30s poll, resolution-specific
 */
export function useSimulation({
  pollInterval = 2000,
  chartResolution = "1H",
  chartStartTime = null,
  chartEndTime = null,
  account = null,        // wallet address for Tier 2 user query
  marketKey = null,
} = {}) {
  const [connected, setConnected] = useState(false);
  const prevBlock = useRef(null);

  // ── Tier 1: Global SWR (no wallet needed) ──────────────────────
  const {
    data: gqlData,
    error: gqlError,
    isLoading: gqlLoading,
  } = useSWR(
    queryKeys.simulationSnapshot(SIM_GRAPHQL_URL, marketKey),
    fetchSimulationSnapshot,
    {
    refreshInterval: pollInterval,
    revalidateOnFocus: false,
    dedupingInterval: 1000,
    keepPreviousData: true,
    onSuccess: () => setConnected(true),
    onError: () => setConnected(false),
    },
  );

  // ── Tier 2: Account SWR (wallet-keyed, 5s poll) ────────────────
  const { data: accountData } = useSWR(
    account ? queryKeys.simulationAccount(SIM_GRAPHQL_URL, account) : null,
    fetchSimulationAccount,
    {
      refreshInterval: 15000,   // balances trigger RPC calls — don't hammer
      revalidateOnFocus: false,
      dedupingInterval: 2000,
      keepPreviousData: true,
    },
  );



  // ── Tier 3: Chart SWR (GQL, resolution-specific, 30s poll) ──────
  // marketId from snapshot (needed for candles resolver)
  const snapshotMarketId = gqlData?.snapshot?.market?.marketId || gqlData?.marketInfo?.marketId || null;
  const chartVars = useMemo(() => ({
    marketId: snapshotMarketId,
    resolution: chartResolution.toLowerCase(),
    limit: 1000,
    fromBucket: chartStartTime || null,
    toBucket: chartEndTime || null,
  }), [chartResolution, chartStartTime, chartEndTime, snapshotMarketId]);

  const { data: chartGqlData, error: chartError } = useSWR(
    snapshotMarketId
      ? queryKeys.simulationCandles(SIM_GRAPHQL_URL, chartVars)
      : null,
    fetchSimulationCandles,
    {
      refreshInterval: 30000,
      revalidateOnFocus: false,
      keepPreviousData: false,
    },
  );

  // ── Extract data from indexer's JSON scalar response ────────
  const snapshot = gqlData?.snapshot;
  const eventsRaw = gqlData?.events;
  const volumeRaw = useMemo(() => snapshot?.derived ? { volumeUsd: snapshot.derived.volume24hUsd || 0, swapCount: snapshot.derived.swapCount24h || 0 } : null, [snapshot]);
  const marketInfo = useMemo(() => _remapMarketInfo(gqlData?.marketInfo), [gqlData?.marketInfo]);

  const statusData = useMemo(() => {
    const s = gqlData?.indexerStatus;
    if (!s) return null;
    return {
      total_block_states: 0,
      total_events: s.totalEvents,
      last_indexed_block: s.lastIndexedBlock,
    };
  }, [gqlData]);

  // ── Derived: market state (indexer returns pre-computed floats) ──
  const market = useMemo(() => {
    if (!snapshot?.market) return null;
    const ms = snapshot.market;
    return {
      marketId: ms.marketId,
      blockNumber: ms.blockNumber,
      blockTimestamp: ms.blockTimestamp,
      normalizationFactor: ms.normalizationFactor,
      totalDebt: ms.totalDebt,
      indexPrice: ms.indexPrice,
      lastUpdateTimestamp: ms.lastUpdateTimestamp,
    };
  }, [snapshot]);

  // ── Derived: pool state (indexer returns pre-computed floats) ──
  const pool = useMemo(() => {
    if (!snapshot?.pool) return null;
    const ps = snapshot.pool;
    return {
      poolId: ps.poolId,
      markPrice: ps.markPrice,
      tick: ps.tick,
      liquidity: ps.liquidity,
      sqrtPriceX96: ps.sqrtPriceX96,
      token0Balance: ps.token0Balance,
      token1Balance: ps.token1Balance,
      feeGrowthGlobal0: ps.feeGrowthGlobal0,
      feeGrowthGlobal1: ps.feeGrowthGlobal1,
    };
  }, [snapshot]);

  // ── Derived: pool TVL (indexer provides tvlUsd directly) ────
  const poolTVL = useMemo(() => {
    if (snapshot?.pool?.tvlUsd) return snapshot.pool.tvlUsd;
    if (!pool) return 0;
    const price = pool.markPrice || 1;
    return pool.token0Balance * price + pool.token1Balance;
  }, [pool, snapshot]);

  // ── Derived: funding spread + annualized rate ────────────────
  // Contract formula: NF *= exp(-fundingRate × dt / fundingPeriod)
  //   fundingRate = (normalizedMark - index) / index
  //   normalizedMark = markPrice / NF
  //   fundingPeriod = configurable (default 30 days = 2_592_000s)
  const funding = useMemo(() => {
    if (!market || !pool) return null;

    const mark = pool.markPrice;
    const index = market.indexPrice;
    if (index <= 0) return null;

    const spread = mark - index;
    const spreadPct = (spread / index) * 100;

    // fundingRate based on raw mark vs index (no NF normalization for display)
    const fundingRate = spread / index;

    // Simple linear annualization (industry standard for display):
    // annualizedPct = fundingRate × (periodsPerYear) × 100
    const fundingPeriod = marketInfo?.risk_params?.funding_period_sec || 2_592_000;
    const yearSec = 365 * 86400;
    const annPct = fundingRate * (yearSec / fundingPeriod) * 100;

    return {
      spread,
      spreadPct,
      fundingRate,
      annualizedPct: annPct,      // contract-consistent annualized NF change %
      fundingPeriod,
      direction: spread >= 0 ? "LONGS_PAY" : "SHORTS_PAY",
    };
  }, [market, pool, marketInfo]);

  // ── Derived: broker positions (indexer returns pre-computed values) ──
  const brokers = useMemo(() => {
    if (!snapshot?.brokers?.length) return [];
    return snapshot.brokers.map((bp, i) => ({
      address: bp.address,
      owner: bp.owner,
      label: BROKER_LABELS[i] || bp.address.slice(0, 8) + "...",
      collateral: bp.collateral,
      debt: bp.debt,
      collateralValue: bp.collateralValue,
      debtValue: bp.debtValue,
      wrlpBalance: bp.wrlpBalance || 0,
      healthFactor: bp.healthFactor,
    }));
  }, [snapshot]);

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

  // ── Derived: chart data (from GQL candles) ────────────────────
  const chartData = useMemo(() => {
    const candles = chartGqlData?.candles;
    if (!candles?.length) {
      return buildFlatChartData({ snapshot, chartStartTime, chartEndTime, chartResolution });
    }

    const mapped = candles.map((c) => ({
      timestamp: c.bucket,
      indexPrice: c.indexClose,
      markPrice: c.markClose,
      indexOpen: c.indexOpen, indexHigh: c.indexHigh, indexLow: c.indexLow,
      markOpen: c.markOpen,  markHigh: c.markHigh,  markLow: c.markLow,
      normalizationFactor: 0,
      totalDebt: 0,
      tick: 0,
      liquidity: 0,
      volume: c.volumeUsd || 0,
      swapCount: c.swapCount,
    }));

    if (mapped.length === 1) {
      const flatTail = buildFlatChartData({
        snapshot,
        chartStartTime: mapped[0].timestamp,
        chartEndTime,
        chartResolution,
      });
      return flatTail.length ? [{ ...mapped[0] }, { ...flatTail[1] }] : mapped;
    }

    const snapshotTs = snapshot?.blockTimestamp || snapshot?.market?.blockTimestamp;
    const resolutionSeconds = RESOLUTION_SECONDS[chartResolution.toLowerCase()] || RESOLUTION_SECONDS["1h"];
    const selectedWindowIncludesSnapshot = !chartEndTime || chartEndTime >= snapshotTs - resolutionSeconds;
    const lastPoint = mapped[mapped.length - 1];
    if (snapshotTs && selectedWindowIncludesSnapshot && lastPoint?.timestamp < snapshotTs) {
      const currentPoint = buildSnapshotChartPoint(snapshot, snapshotTs);
      if (currentPoint) return [...mapped, currentPoint];
    }

    return mapped;
  }, [chartGqlData, chartEndTime, chartResolution, chartStartTime, snapshot]);

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
    const earliest = chartData[0];
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
    if (!snapshot?.blockNumber) return;
    if (
      prevBlock.current !== null &&
      snapshot.blockNumber !== prevBlock.current
    ) {
      setBlockChanged(true); // eslint-disable-line react-hooks/set-state-in-effect
      const timer = setTimeout(() => setBlockChanged(false), 300);
      return () => clearTimeout(timer);
    }
    prevBlock.current = snapshot.blockNumber;
  }, [snapshot?.blockNumber]);

  // ── Derived: USDC borrow rate (rates fetched separately) ──
  const latestRate = useMemo(() => {
    // ratesGqlData is null — rates handled by useMarketData.js
    return null;
  }, []);

  // ── Derived: account bonds (from Tier 2 ACCOUNT_QUERY) ──────────
  const bonds = useMemo(() => {
    return (accountData?.bonds || []).map((b) => ({
      id: b.bondId,
      brokerAddress: b.brokerAddress,
      principal: b.notionalUsd,
      debtTokens: b.debtUsd,
      freeCollateral: b.freeCollateral,
      fixedRate: latestRate?.apy || 0,
      maturityDays: b.maturityDays,
      elapsed: b.elapsedDays,
      remaining: b.remainingDays,
      maturityDate: b.maturityDate,
      frozen: b.frozen,
      isMatured: b.isMatured,
      hasActiveOrder: b.hasActiveOrder,
      orderId: b.orderId,
      bondFactory: b.bondFactory,
      txHash: b.createdTx,
      status: b.status,
      accrued: b.notionalUsd * ((latestRate?.apy || 0) / 100) * (b.elapsedDays / 365),
    }));
  }, [accountData, latestRate]);

  // ── Derived: account token balances (from Tier 2 ACCOUNT_QUERY) ──
  const balances = useMemo(() => accountData?.balances ?? null, [accountData]);

  return {
    // Connection
    connected,
    loading: gqlLoading && !gqlData,
    error: gqlError || chartError,

    // Raw (for backward compat with components that access snapshot directly)
    latest: snapshot
      ? {
          block_number: snapshot.blockNumber,
          market: snapshot.market,
          pool: snapshot.pool,
          market_states: snapshot.market
            ? [{
                block_number: snapshot.market.blockNumber,
                block_timestamp: snapshot.market.blockTimestamp,
                market_id: snapshot.market.marketId,
                normalization_factor: snapshot.market.normalizationFactor,
                total_debt: snapshot.market.totalDebt,
                index_price: snapshot.market.indexPrice,
                last_update_timestamp: snapshot.market.lastUpdateTimestamp,
              }]
            : [],
          pool_states: snapshot.pool
            ? [{
                pool_id: snapshot.pool.poolId,
                tick: snapshot.pool.tick,
                mark_price: snapshot.pool.markPrice,
                liquidity: snapshot.pool.liquidity,
                sqrt_price_x96: snapshot.pool.sqrtPriceX96,
                token0_balance: snapshot.pool.token0Balance,
                token1_balance: snapshot.pool.token1Balance,
              }]
            : [],
          broker_positions: (snapshot.brokers || []).map((b) => ({
            broker_address: b.address,
            collateral: b.collateral,
            debt: b.debt,
            collateral_value: b.collateralValue,
            debt_value: b.debtValue,
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
    volumeHistory: chartData.map((c) => ({
      timestamp: c.timestamp,
      volume: c.volume,
      swapCount: c.swapCount,
    })),
    events,
    blockChanged,

    // Tier 2: per-account
    bonds,
    balances,
    latestRate,

    // Meta
    blockNumber: snapshot?.blockNumber || null,
    totalBlocks: statusData?.total_block_states || 0,
    totalEvents: statusData?.total_events || 0,
  };
}
