import React, { useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import useSWR from "swr";
import { Activity, ArrowLeft, Loader2, ExternalLink } from "lucide-react";
import RLDPerformanceChart from "../../components/charts/RLDChart";
import { ENVIO_GRAPHQL_URL } from "../../api/endpoints";
import { postGraphQL } from "../../api/graphqlClient";
import { getTokenIcon } from "../../utils/tokenIcons";

const PROTOCOL_MAP = {
  aave: "AAVE_MARKET",
  morpho: "MORPHO_MARKET",
  euler: "EULER_MARKET",
  fluid: "FLUID_MARKET",
};

const CHART_RESOLUTION = "1D";
const TIMESERIES_LIMIT_DAYS = 120;
const FLOW_LIMIT_DAYS = 120;

const LENDING_POOL_CORE_QUERY = `
  query LendingPoolCore($protocol: String!, $entityId: String!, $timeseriesLimit: Int!) {
    protocolMarkets(protocol: $protocol, entityId: $entityId) {
      entityId
      symbol
      protocol
      supplyUsd
      borrowUsd
      supplyApy
      borrowApy
      utilization
    }
    marketTimeseries(entityId: $entityId, resolution: "1D", limit: $timeseriesLimit) {
      timestamp
      supplyApy
      borrowApy
      utilization
      supplyUsd
      borrowUsd
    }
  }
`;

const LENDING_POOL_FLOW_QUERY = `
  query LendingPoolFlow($entityId: String!, $flowLimit: Int!) {
    marketFlowTimeseries(entityId: $entityId, resolution: "1D", limit: $flowLimit) {
      timestamp
      supplyInflowUsd
      supplyOutflowUsd
      borrowInflowUsd
      borrowOutflowUsd
      netSupplyFlowUsd
      netBorrowFlowUsd
    }
  }
`;

const formatCurrency = (value) => {
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
};

const formatApy = (value) => {
  return `${(value * 100).toFixed(2)}%`;
};

export default function LendingPoolPage() {
  const { protocol: protocolSlug, marketId } = useParams();
  const navigate = useNavigate();
  const protocolKey = PROTOCOL_MAP[protocolSlug?.toLowerCase()] || "AAVE_MARKET";
  const normalizedEntityId = useMemo(() => {
    const raw = String(marketId || "").trim().toLowerCase();
    if (!raw) return null;
    if (protocolKey === "AAVE_MARKET" && !raw.startsWith("0x")) {
      return `0x${raw}`;
    }
    return raw;
  }, [marketId, protocolKey]);

  const { data: coreGqlData, isLoading: coreLoading } = useSWR(
    normalizedEntityId ? [ENVIO_GRAPHQL_URL, `envio.lending-pool.core.${normalizedEntityId}.v1`] : null,
    ([url]) =>
      postGraphQL(url, {
        query: LENDING_POOL_CORE_QUERY,
        variables: {
          protocol: protocolKey,
          entityId: normalizedEntityId,
          timeseriesLimit: TIMESERIES_LIMIT_DAYS,
        },
      }),
    { refreshInterval: 30000, dedupingInterval: 5000, revalidateOnFocus: false }
  );

  const { data: flowGqlData, isLoading: flowLoading } = useSWR(
    normalizedEntityId ? [ENVIO_GRAPHQL_URL, `envio.lending-pool.flow.${normalizedEntityId}.v1`] : null,
    ([url]) =>
      postGraphQL(url, {
        query: LENDING_POOL_FLOW_QUERY,
        variables: { entityId: normalizedEntityId, flowLimit: FLOW_LIMIT_DAYS },
      }),
    {
      refreshInterval: 300000,
      dedupingInterval: 60000,
      revalidateOnFocus: false,
    }
  );

  const { market, tsData, flowData } = useMemo(() => {
    const rows = coreGqlData?.protocolMarkets || [];
    const rawMarket = rows[0] || null;

    let safeMarket = null;
    if (rawMarket) {
      const supplyUsd = Math.max(0, Number(rawMarket.supplyUsd) || 0);
      const borrowUsd = Math.max(0, Number(rawMarket.borrowUsd) || 0);
      safeMarket = {
        symbol: String(rawMarket.symbol || "UNKNOWN"),
        protocol: String(rawMarket.protocol || "AAVE_MARKET"),
        supplyUsd,
        borrowUsd,
        supplyApy: Math.max(0, Number(rawMarket.supplyApy) || 0),
        borrowApy: Math.max(0, Number(rawMarket.borrowApy) || 0),
        utilization: supplyUsd > 0 ? Math.min(1, borrowUsd / supplyUsd) : 0,
      };
    }

    const rawTs = coreGqlData?.marketTimeseries || [];
    const chart = rawTs
      .map((p) => ({
        timestamp: Number(p.timestamp) || 0,
        supplyApy: p.supplyApy ? p.supplyApy * 100 : 0,
        borrowApy: p.borrowApy ? p.borrowApy * 100 : 0,
        utilization: p.utilization ? p.utilization * 100 : 0,
        supplyUsd: Math.max(0, Number(p.supplyUsd) || 0),
        borrowUsd: Math.max(0, Number(p.borrowUsd) || 0),
      }))
      .filter((p) => p.timestamp > 0)
      .sort((a, b) => a.timestamp - b.timestamp);

    const rawFlow = flowGqlData?.marketFlowTimeseries || [];
    const flow = rawFlow
      .map((p) => {
        const supplyOutflowAbs = Math.max(0, Number(p.supplyOutflowUsd) || 0);
        const borrowOutflowAbs = Math.max(0, Number(p.borrowOutflowUsd) || 0);
        return {
          timestamp: Number(p.timestamp) || 0,
          supplyInflowUsd: Math.max(0, Number(p.supplyInflowUsd) || 0),
          // Plot outflows below baseline for intuitive directionality.
          supplyOutflowUsd: -supplyOutflowAbs,
          netSupplyFlowUsd: Number(p.netSupplyFlowUsd) || 0,
          borrowInflowUsd: Math.max(0, Number(p.borrowInflowUsd) || 0),
          borrowOutflowUsd: -borrowOutflowAbs,
          netBorrowFlowUsd: Number(p.netBorrowFlowUsd) || 0,
        };
      })
      .filter((p) => p.timestamp > 0)
      .sort((a, b) => a.timestamp - b.timestamp);

    return { market: safeMarket, tsData: chart, flowData: flow };
  }, [coreGqlData, flowGqlData]);

  if (coreLoading && !market) {
    return (
      <div className="min-h-screen bg-[#050505] flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-cyan-500 animate-spin" />
      </div>
    );
  }

  if (!market) {
    return (
      <div className="min-h-screen bg-[#050505] flex flex-col items-center justify-center gap-4 text-gray-400 font-mono">
        <span className="text-lg">Market not found or not indexed</span>
        <button onClick={() => navigate(-1)} className="text-cyan-500 hover:text-cyan-400 flex items-center gap-2 transition-colors">
          <ArrowLeft size={16} /> Return to Hub
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">
        {/* Navigation & Header */}
        <div className="py-6 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate(`/data`)}
              className="w-10 h-10 flex items-center justify-center rounded-full border border-white/10 hover:bg-white/[0.02] transition-colors"
            >
              <ArrowLeft size={18} className="text-gray-400" />
            </button>
            <div>
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm">
                  <img src={getTokenIcon(market.symbol)} alt={market.symbol} className="w-full h-full object-contain rounded-full" />
                </div>
                <h1 className="text-3xl font-bold tracking-tight text-white">{market.symbol}</h1>
                <a
                  href={normalizedEntityId?.startsWith("0x") ? `https://etherscan.io/address/${normalizedEntityId}` : "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`text-gray-500 transition-colors ml-2 ${normalizedEntityId?.startsWith("0x") ? "hover:text-cyan-400" : "pointer-events-none opacity-40"}`}
                >
                  <ExternalLink size={16} />
                </a>
              </div>
            </div>
          </div>
          <div className="text-right hidden md:block">
            <div className="text-xs uppercase tracking-widest text-gray-500 mb-1">Protocol</div>
            <div className="text-sm font-bold text-white tracking-widest">{market.protocol}</div>
          </div>
        </div>

        {/* Hero Metrics Banner */}
        <div className="border border-white/10 bg-[#0a0a0a] rounded-sm mb-6 flex flex-wrap">
          <div className="flex-1 min-w-[200px] border-r border-white/10 p-6 flex flex-col justify-center">
            <div className="text-xs uppercase tracking-widest text-gray-500 mb-2">Total Supply</div>
            <div className="text-2xl font-bold text-white">{formatCurrency(market.supplyUsd)}</div>
          </div>
          <div className="flex-1 min-w-[200px] border-r border-white/10 p-6 flex flex-col justify-center">
            <div className="text-xs uppercase tracking-widest text-gray-500 mb-2">Total Borrowed</div>
            <div className="text-2xl font-bold text-white">{formatCurrency(market.borrowUsd)}</div>
          </div>
          <div className="flex-1 min-w-[150px] border-r border-white/10 p-6 flex flex-col justify-center">
            <div className="text-xs uppercase tracking-widest text-emerald-500/70 mb-2">Supply APY</div>
            <div className="text-xl font-bold text-emerald-400">{formatApy(market.supplyApy)}</div>
          </div>
          <div className="flex-1 min-w-[150px] border-r border-white/10 p-6 flex flex-col justify-center">
            <div className="text-xs uppercase tracking-widest text-cyan-500/70 mb-2">Borrow APY</div>
            <div className="text-xl font-bold text-cyan-400">{formatApy(market.borrowApy)}</div>
          </div>
          <div className="flex-1 min-w-[150px] p-6 flex flex-col justify-center">
            <div className="text-xs uppercase tracking-widest text-purple-500/70 mb-2">Utilization</div>
            <div className="text-xl font-bold text-purple-400">{(market.utilization * 100).toFixed(2)}%</div>
          </div>
        </div>

        {/* Charts Grid */}
        <div className="grid grid-cols-1 gap-6">
          {/* APY / Utilization Chart */}
          <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
            <div className="flex items-center justify-between mb-8">
              <div className="flex items-center gap-3">
                <Activity size={18} className="text-gray-500" />
                <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Interest Rates</h2>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-emerald-400" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Supply APY</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-cyan-400" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Borrow APY</span>
                </div>
              </div>
            </div>
            <div className="h-[300px] w-full">
              <RLDPerformanceChart
                data={tsData}
                resolution={CHART_RESOLUTION}
                areas={[
                  { key: "borrowApy", color: "#22d3ee", name: "Borrow APY", format: "percent" },
                  { key: "supplyApy", color: "#34d399", name: "Supply APY", format: "percent" }
                ]}
              />
            </div>
          </div>

          {/* TVL Chart */}
          <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
            <div className="flex items-center justify-between mb-8">
              <div className="flex items-center gap-3">
                <Activity size={18} className="text-gray-500" />
                <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Value Locked</h2>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-[#818cf8]" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Supply TVL</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-[#fb7185]" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Borrow TVL</span>
                </div>
              </div>
            </div>
            <div className="h-[300px] w-full">
              <RLDPerformanceChart
                data={tsData}
                resolution={CHART_RESOLUTION}
                areas={[
                  { key: "supplyUsd", color: "#818cf8", name: "Supply TVL", format: "dollar" },
                  { key: "borrowUsd", color: "#fb7185", name: "Borrow TVL", format: "dollar" }
                ]}
              />
            </div>
          </div>

          {/* Supply Flow Chart */}
          <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
            <div className="flex items-center justify-between mb-8">
              <div className="flex items-center gap-3">
                <Activity size={18} className="text-gray-500" />
                <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Supply Inflow / Outflow (USD)</h2>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-emerald-500" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Inflow</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-rose-500" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Outflow</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-cyan-400" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Net</span>
                </div>
              </div>
            </div>
            {flowLoading && flowData.length === 0 ? (
              <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
                <Loader2 size={14} className="animate-spin" />
                Loading Flow History...
              </div>
            ) : (
              <div className="h-[300px] w-full">
                <RLDPerformanceChart
                  data={flowData}
                  resolution={CHART_RESOLUTION}
                  referenceLines={[{ y: 0, stroke: "#52525b" }]}
                  areas={[
                    { key: "supplyInflowUsd", color: "#22c55e", name: "Supply Inflow", format: "dollar" },
                    { key: "supplyOutflowUsd", color: "#f43f5e", name: "Supply Outflow", format: "dollar" },
                    { key: "netSupplyFlowUsd", color: "#22d3ee", name: "Net Supply Flow", format: "dollar" }
                  ]}
                />
              </div>
            )}
          </div>

          {/* Borrow Flow Chart */}
          <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
            <div className="flex items-center justify-between mb-8">
              <div className="flex items-center gap-3">
                <Activity size={18} className="text-gray-500" />
                <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Borrow Inflow / Outflow (USD)</h2>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-violet-500" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Inflow</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-orange-500" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Outflow</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-yellow-400" />
                  <span className="text-xs text-gray-500 uppercase tracking-widest">Net</span>
                </div>
              </div>
            </div>
            {flowLoading && flowData.length === 0 ? (
              <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
                <Loader2 size={14} className="animate-spin" />
                Loading Flow History...
              </div>
            ) : (
              <div className="h-[300px] w-full">
                <RLDPerformanceChart
                  data={flowData}
                  resolution={CHART_RESOLUTION}
                  referenceLines={[{ y: 0, stroke: "#52525b" }]}
                  areas={[
                    { key: "borrowInflowUsd", color: "#8b5cf6", name: "Borrow Inflow", format: "dollar" },
                    { key: "borrowOutflowUsd", color: "#f97316", name: "Borrow Outflow", format: "dollar" },
                    { key: "netBorrowFlowUsd", color: "#facc15", name: "Net Borrow Flow", format: "dollar" }
                  ]}
                />
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
