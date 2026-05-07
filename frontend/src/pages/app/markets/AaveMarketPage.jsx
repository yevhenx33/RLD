import React, { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import useSWR from "swr";
import { Activity, ArrowLeft, Loader2, ExternalLink } from "lucide-react";
import RLDPerformanceChart from "../../../charts/primitives/RLDPerformanceChart";
import { API_GRAPHQL_URL } from "../../../api/endpoints";
import { apiGraphQL } from "../../../api/apiClient";
import { MARKET_PAGE_QUERY } from "../../../api/apiQueries";
import { queryKeys } from "../../../api/queryKeys";
import { apiProtocolForSlug, normalizeMarketIdForApi } from "../../../lib/protocolConfig";
import { getTokenIcon } from "../../../utils/tokenIcons";
import { REFRESH_INTERVALS } from "../../../config/refreshIntervals";

const CHART_RESOLUTION = "1D";
const TIMESERIES_LIMIT_DAYS = 500;
const FLOW_LIMIT_DAYS = 500;

const formatCurrency = (value) => {
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
};

const formatApy = (value) => {
  return `${(value * 100).toFixed(2)}%`;
};

export default function AaveMarketPage() {
  const { protocol: protocolSlug, marketId } = useParams();
  const navigate = useNavigate();
  const protocolKey = apiProtocolForSlug(protocolSlug);
  const normalizedEntityId = useMemo(() => {
    return normalizeMarketIdForApi(protocolSlug, marketId);
  }, [marketId, protocolSlug]);

  const { data: pageGqlData, isLoading: pageLoading } = useSWR(
    queryKeys.apiMarketPage(API_GRAPHQL_URL, protocolKey, normalizedEntityId),
    ([, , variables]) =>
      apiGraphQL("MarketPage", {
        query: MARKET_PAGE_QUERY,
        variables: {
          protocol: variables.protocol,
          marketId: variables.marketId,
          timeseriesLimit: TIMESERIES_LIMIT_DAYS,
          flowLimit: FLOW_LIMIT_DAYS,
        },
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    }
  );

  const { market, tsData, flowData } = useMemo(() => {
    const page = pageGqlData?.marketPage || {};
    const rawMarket = page.market || null;

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

    const chart = page.rateChart || [];
    const rawFlow = page.flowChart || [];
    const flowBase = rawFlow
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
          cumulativeSupplyNetInflowUsd: Number(p.cumulativeSupplyNetInflowUsd),
          cumulativeBorrowNetInflowUsd: Number(p.cumulativeBorrowNetInflowUsd),
        };
      })
      .filter((p) => p.timestamp > 0)
      .sort((a, b) => a.timestamp - b.timestamp);
    const flow = flowBase.reduce(
      (acc, point) => {
        const hasSupplyFromApi = Number.isFinite(point.cumulativeSupplyNetInflowUsd);
        const hasBorrowFromApi = Number.isFinite(point.cumulativeBorrowNetInflowUsd);
        const cumulativeSupplyNetInflowUsd = hasSupplyFromApi
          ? point.cumulativeSupplyNetInflowUsd
          : acc.cumulativeSupply + point.netSupplyFlowUsd;
        const cumulativeBorrowNetInflowUsd = hasBorrowFromApi
          ? point.cumulativeBorrowNetInflowUsd
          : acc.cumulativeBorrow + point.netBorrowFlowUsd;
        return {
          cumulativeSupply: cumulativeSupplyNetInflowUsd,
          cumulativeBorrow: cumulativeBorrowNetInflowUsd,
          rows: [
            ...acc.rows,
            {
              ...point,
              cumulativeSupplyNetInflowUsd,
              cumulativeBorrowNetInflowUsd,
            },
          ],
        };
      },
      { cumulativeSupply: 0, cumulativeBorrow: 0, rows: [] }
    ).rows;

    return { market: safeMarket, tsData: chart, flowData: flow };
  }, [pageGqlData]);

  if (pageLoading && !market) {
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

        {/* Nav Route / Breadcrumbs */}
        <div className="flex items-center gap-3 my-6 transition-all duration-500">
          <span className="font-mono text-[#333] text-[12px]">|вЂ”</span>
          <div className="flex items-center gap-2 font-mono text-[11px] md:text-[13px] tracking-[0.28em] uppercase text-[#999]">
            <button onClick={() => navigate("/data")} className="hover:text-white transition-colors uppercase">data</button>
            <span className="text-[#999]">/</span>
            <span className="text-[#999] hover:text-white">{market.protocol.replace('_MARKET', '')}</span>
            <span className="text-[#999]">/</span>
            <span className="text-[#999] flex items-center gap-2 hover:text-white">
              <img src={getTokenIcon(market.symbol)} alt={market.symbol} className="w-4 h-4 rounded-full grayscale opacity-80" />
              {market.symbol}
              <a
                href={normalizedEntityId?.startsWith("0x") ? `https://etherscan.io/address/${normalizedEntityId}` : "#"}
                target="_blank"
                rel="noopener noreferrer"
                className={`hover:text-[#888] transition-colors ml-1 ${!normalizedEntityId?.startsWith("0x") && "pointer-events-none opacity-40"}`}
              >
                <ExternalLink size={12} />
              </a>
            </span>
          </div>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        {/* Horizontal Stats Panel */}
        <div className="flex flex-wrap lg:flex-nowrap border border-white/10 bg-[#080808] mb-8 divide-y lg:divide-y-0 lg:divide-x divide-white/10 rounded-sm">
          <div className="flex flex-col p-4 md:p-6 flex-1">
            <div className="text-[10px] md:text-xs uppercase tracking-widest text-emerald-500/70 mb-1">Supply APR</div>
            <div className="text-lg md:text-2xl font-bold text-emerald-400">{formatApy(market.supplyApy)}</div>
          </div>
          <div className="flex flex-col p-4 md:p-6 flex-1">
            <div className="text-[10px] md:text-xs uppercase tracking-widest text-cyan-500/70 mb-1">Borrow APR</div>
            <div className="text-lg md:text-2xl font-bold text-cyan-400">{formatApy(market.borrowApy)}</div>
          </div>
          <div className="flex flex-col p-4 md:p-6 flex-1">
            <div className="text-[10px] md:text-xs uppercase tracking-widest text-gray-500 mb-1">Supplied</div>
            <div className="text-lg md:text-2xl font-bold text-white">{formatCurrency(market.supplyUsd)}</div>
          </div>
          <div className="flex flex-col p-4 md:p-6 flex-1">
            <div className="text-[10px] md:text-xs uppercase tracking-widest text-gray-500 mb-1">Borrowed</div>
            <div className="text-lg md:text-2xl font-bold text-white">{formatCurrency(market.borrowUsd)}</div>
          </div>
          <div className="flex flex-col p-4 md:p-6 flex-1">
            <div className="text-[10px] md:text-xs uppercase tracking-widest text-purple-500/70 mb-1">Utilization</div>
            <div className="text-lg md:text-2xl font-bold text-purple-400">{(market.utilization * 100).toFixed(2)}%</div>
          </div>
        </div>

        {/* 2x2 Chart Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
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
            {pageLoading && flowData.length === 0 ? (
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
            {pageLoading && flowData.length === 0 ? (
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

        {/* Cumulative Net Flow Chart (Full Width) */}
        <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-3">
              <Activity size={18} className="text-gray-500" />
              <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Cumulative Net Inflow (USD)</h2>
            </div>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-blue-400" />
                <span className="text-xs text-gray-500 uppercase tracking-widest">Supply</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-lime-300" />
                <span className="text-xs text-gray-500 uppercase tracking-widest">Borrow</span>
              </div>
            </div>
          </div>
          {pageLoading && flowData.length === 0 ? (
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
                  {
                    key: "cumulativeSupplyNetInflowUsd",
                    color: "#60a5fa",
                    name: "Cumulative Net Supply Inflow",
                    format: "dollar",
                  },
                  {
                    key: "cumulativeBorrowNetInflowUsd",
                    color: "#bef264",
                    name: "Cumulative Net Borrow Inflow",
                    format: "dollar",
                  }
                ]}
              />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
