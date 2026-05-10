import React, { useMemo, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import useSWR from "swr";
import { Activity, ArrowLeft, Loader2, ExternalLink, Shield, Link2, PieChart as PieChartIcon, Info } from "lucide-react";
import { MetricCell, StatItem } from "../../../components/pools/MetricsGrid";
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

const finiteNumber = (value, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
};

const formatCurrency = (value) => {
  const amount = finiteNumber(value);
  if (amount >= 1e9) return `$${(amount / 1e9).toFixed(2)}B`;
  if (amount >= 1e6) return `$${(amount / 1e6).toFixed(2)}M`;
  if (amount >= 1e3) return `$${(amount / 1e3).toFixed(0)}K`;
  return `$${amount.toFixed(0)}`;
};

const formatApy = (value) => {
  return `${(finiteNumber(value) * 100).toFixed(2)}%`;
};

const formatPercent = (value, digits = 2) => {
  return `${(finiteNumber(value) * 100).toFixed(digits)}%`;
};

const normalizeRatePoint = (point) => ({
  timestamp: finiteNumber(point?.timestamp),
  supplyApy: finiteNumber(point?.supplyApy),
  borrowApy: finiteNumber(point?.borrowApy),
  utilization: finiteNumber(point?.utilization),
  supplyUsd: finiteNumber(point?.supplyUsd),
  borrowUsd: finiteNumber(point?.borrowUsd),
});

const hasAnyFiniteValue = (point, keys) => {
  return keys.some((key) => Number.isFinite(Number(point?.[key])));
};

function ChartEmptyState({ label }) {
  return (
    <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
      {label}
    </div>
  );
}

export default function AaveMarketPage() {
  const { protocol: protocolSlug, marketId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const resolvedProtocolSlug = protocolSlug || location.pathname.split("/")[2] || "aave";
  const protocolKey = apiProtocolForSlug(resolvedProtocolSlug);
  const normalizedEntityId = useMemo(() => {
    return normalizeMarketIdForApi(resolvedProtocolSlug, marketId);
  }, [marketId, resolvedProtocolSlug]);

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
          allocationLimit: 0,
        },
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    }
  );

  const { market, tsData, flowData, genesisTs } = useMemo(() => {
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
        supplyApy: Math.max(0, finiteNumber(rawMarket.supplyApy)),
        borrowApy: Math.max(0, finiteNumber(rawMarket.borrowApy)),
        utilization: supplyUsd > 0 ? Math.min(1, borrowUsd / supplyUsd) : 0,
        lltv: rawMarket.lltv != null ? Number(rawMarket.lltv) : null,
        loanPriceUsd: rawMarket.loanPriceUsd != null ? Number(rawMarket.loanPriceUsd) : null,
        oracleSupport: rawMarket.oracleSupport || null,
      };
    }

    const chart = (page.rateChart || [])
      .map(normalizeRatePoint)
      .filter((p) => (
        p.timestamp > 0
        && hasAnyFiniteValue(p, ["supplyApy", "borrowApy", "supplyUsd", "borrowUsd", "utilization"])
      ))
      .sort((a, b) => a.timestamp - b.timestamp);
    const rawFlow = page.flowChart || [];
    const flowBase = rawFlow
      .map((p) => {
        const supplyOutflowAbs = Math.max(0, finiteNumber(p.supplyOutflowUsd));
        const borrowOutflowAbs = Math.max(0, finiteNumber(p.borrowOutflowUsd));
        return {
          timestamp: finiteNumber(p.timestamp),
          supplyInflowUsd: Math.max(0, finiteNumber(p.supplyInflowUsd)),
          // Plot outflows below baseline for intuitive directionality.
          supplyOutflowUsd: -supplyOutflowAbs,
          netSupplyFlowUsd: finiteNumber(p.netSupplyFlowUsd),
          borrowInflowUsd: Math.max(0, finiteNumber(p.borrowInflowUsd)),
          borrowOutflowUsd: -borrowOutflowAbs,
          netBorrowFlowUsd: finiteNumber(p.netBorrowFlowUsd),
          cumulativeSupplyNetInflowUsd: finiteNumber(p.cumulativeSupplyNetInflowUsd, NaN),
          cumulativeBorrowNetInflowUsd: finiteNumber(p.cumulativeBorrowNetInflowUsd, NaN),
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
    // Derive market "liquidity genesis" — first day cumulative supply inflow > 0.
    // All charts on the page start from this point to avoid long flat-line prefixes.
    const genesisPoint = flow.find((p) => p.cumulativeSupplyNetInflowUsd > 0);
    const genesisTs = genesisPoint ? genesisPoint.timestamp : 0;

    return {
      market: safeMarket,
      tsData: genesisTs > 0 ? chart.filter((p) => p.timestamp >= genesisTs) : chart,
      flowData: genesisTs > 0 ? flow.filter((p) => p.timestamp >= genesisTs) : flow,
      genesisTs,
    };
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
          <span className="font-mono text-[#333] text-[12px]">|—</span>
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

        {/* Stats Panel — 4-column MetricCell grid */}
        <div className="mb-8 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            <MetricCell
              label="OVERVIEW"
              Icon={PieChartIcon}
              hideLabelOnMobile={false}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem label="SUPPLIED" value={formatCurrency(market.supplyUsd)} />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem label="BORROWED" value={formatCurrency(market.borrowUsd)} />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="LIQUIDITY" value={formatCurrency(Math.max(0, market.supplyUsd - market.borrowUsd))} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="RATES"
              Icon={Activity}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem label="SUPPLY APR" value={formatApy(market.supplyApy)} />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem label="BORROW APR" value={formatApy(market.borrowApy)} />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="UTILIZATION" value={formatPercent(market.utilization)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="ASSET"
              Icon={Shield}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div>
                    <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-1">TOKEN</div>
                    <div className="flex items-center gap-2">
                      <img src={getTokenIcon(market.symbol)} alt={market.symbol} className="w-5 h-5 rounded-full" />
                      <span className="text-base md:text-xl font-light text-white font-mono tracking-tighter">{market.symbol}</span>
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    {market.lltv != null && market.lltv === 0 ? (
                      <div>
                        <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-0.5 md:mb-1">LLTV</div>
                        <div className="flex items-center gap-2 whitespace-nowrap">
                          <div className="text-base md:text-xl font-light text-white font-mono tracking-tighter">0.00%</div>
                          <span className="relative group cursor-help">
                            <Info size={12} className="text-gray-500 hover:text-gray-300 transition-colors" />
                            <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-[#1a1a1a] border border-white/10 text-[10px] text-gray-300 rounded whitespace-nowrap opacity-0 pointer-events-none group-hover:opacity-100 transition-opacity z-50">
                              Cannot be used as collateral for new borrows
                            </span>
                          </span>
                        </div>
                      </div>
                    ) : (
                      <StatItem label="LLTV" value={market.lltv != null ? formatPercent(market.lltv) : "—"} />
                    )}
                  </div>
                </div>
              }
            />
            <MetricCell
              label="ORACLE"
              Icon={Link2}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <StatItem
                    label="PRICE"
                    value={
                      market.loanPriceUsd != null
                        ? `$${Number(market.loanPriceUsd).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                        : "—"
                    }
                  />
                  <div className="border-t border-white/10 pt-3">
                    <StatItem
                      label="PROVIDER"
                      value={
                        market.oracleSupport
                          ? market.oracleSupport.replace(/_/g, " ").replace(/supported/i, "").trim().split(" ").map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(" ") || "Unknown"
                          : "—"
                      }
                    />
                  </div>
                </div>
              }
            />
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
            {pageLoading && tsData.length === 0 ? (
              <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
                <Loader2 size={14} className="animate-spin" />
                Loading Rate History...
              </div>
            ) : tsData.length === 0 ? (
              <ChartEmptyState label="No rate history available" />
            ) : (
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
            )}
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
            {pageLoading && tsData.length === 0 ? (
              <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
                <Loader2 size={14} className="animate-spin" />
                Loading Value History...
              </div>
            ) : tsData.length === 0 ? (
              <ChartEmptyState label="No value history available" />
            ) : (
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
            )}
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
                    { key: "netSupplyFlowUsd", color: "#22d3ee", name: "Net Supply Flow", format: "dollar", noFill: true }
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
                    { key: "netBorrowFlowUsd", color: "#facc15", name: "Net Borrow Flow", format: "dollar", noFill: true }
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
