import React, { useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import useSWR from "swr";
import { Activity, ArrowLeft, Loader2, ExternalLink, Shield, Link2, PieChart as PieChartIcon } from "lucide-react";
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

const formatApy = (value) => `${(finiteNumber(value) * 100).toFixed(2)}%`;
const formatPercent = (value, digits = 2) => `${(finiteNumber(value) * 100).toFixed(digits)}%`;

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

function FlowChartCard({ title, loading, data, areas }) {
  return (
    <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Activity size={18} className="text-gray-500" />
          <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">{title}</h2>
        </div>
        <div className="flex items-center gap-4">
          {areas.map((area) => (
            <div key={area.key} className="flex items-center gap-2">
              <div className="w-2 h-2" style={{ backgroundColor: area.color }} />
              <span className="text-xs text-gray-500 uppercase tracking-widest">{area.legend}</span>
            </div>
          ))}
        </div>
      </div>
      {loading && data.length === 0 ? (
        <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
          <Loader2 size={14} className="animate-spin" />
          Loading Flow History...
        </div>
      ) : data.length === 0 ? (
        <ChartEmptyState label="No flow history available" />
      ) : (
        <div className="h-[300px] w-full">
          <RLDPerformanceChart
            data={data}
            resolution={CHART_RESOLUTION}
            referenceLines={[{ y: 0, stroke: "#52525b" }]}
            areas={areas.map(({ legend, ...area }) => ({ ...area, name: legend }))}
          />
        </div>
      )}
    </div>
  );
}

export default function EulerMarketPage() {
  const { marketId } = useParams();
  const navigate = useNavigate();
  const protocolSlug = "euler";
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
          allocationLimit: 0,
        },
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
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
        protocol: String(rawMarket.protocol || "EULER_MARKET"),
        supplyUsd,
        borrowUsd,
        supplyApy: Math.max(0, finiteNumber(rawMarket.supplyApy)),
        borrowApy: Math.max(0, finiteNumber(rawMarket.borrowApy)),
        utilization: supplyUsd > 0 ? Math.min(1, borrowUsd / supplyUsd) : 0,
        loanPriceUsd: rawMarket.loanPriceUsd != null ? Number(rawMarket.loanPriceUsd) : null,
        oracleSupport: rawMarket.oracleSupport || null,
      };
    }

    const chart = (page.rateChart || [])
      .map(normalizeRatePoint)
      .filter((point) => (
        point.timestamp > 0
        && hasAnyFiniteValue(point, ["supplyApy", "borrowApy", "supplyUsd", "borrowUsd", "utilization"])
      ))
      .sort((a, b) => a.timestamp - b.timestamp);

    const flowBase = (page.flowChart || [])
      .map((point) => {
        const supplyOutflowAbs = Math.max(0, finiteNumber(point.supplyOutflowUsd));
        const borrowOutflowAbs = Math.max(0, finiteNumber(point.borrowOutflowUsd));
        return {
          timestamp: finiteNumber(point.timestamp),
          supplyInflowUsd: Math.max(0, finiteNumber(point.supplyInflowUsd)),
          supplyOutflowUsd: -supplyOutflowAbs,
          netSupplyFlowUsd: finiteNumber(point.netSupplyFlowUsd),
          borrowInflowUsd: Math.max(0, finiteNumber(point.borrowInflowUsd)),
          borrowOutflowUsd: -borrowOutflowAbs,
          netBorrowFlowUsd: finiteNumber(point.netBorrowFlowUsd),
          cumulativeSupplyNetInflowUsd: finiteNumber(point.cumulativeSupplyNetInflowUsd, NaN),
          cumulativeBorrowNetInflowUsd: finiteNumber(point.cumulativeBorrowNetInflowUsd, NaN),
        };
      })
      .filter((point) => point.timestamp > 0)
      .sort((a, b) => a.timestamp - b.timestamp);

    const flow = flowBase.reduce(
      (acc, point) => {
        const cumulativeSupplyNetInflowUsd = Number.isFinite(point.cumulativeSupplyNetInflowUsd)
          ? point.cumulativeSupplyNetInflowUsd
          : acc.cumulativeSupply + point.netSupplyFlowUsd;
        const cumulativeBorrowNetInflowUsd = Number.isFinite(point.cumulativeBorrowNetInflowUsd)
          ? point.cumulativeBorrowNetInflowUsd
          : acc.cumulativeBorrow + point.netBorrowFlowUsd;
        return {
          cumulativeSupply: cumulativeSupplyNetInflowUsd,
          cumulativeBorrow: cumulativeBorrowNetInflowUsd,
          rows: [...acc.rows, { ...point, cumulativeSupplyNetInflowUsd, cumulativeBorrowNetInflowUsd }],
        };
      },
      { cumulativeSupply: 0, cumulativeBorrow: 0, rows: [] },
    ).rows;

    const genesisPoint = flow.find((point) => point.cumulativeSupplyNetInflowUsd > 0);
    const genesisTs = genesisPoint ? genesisPoint.timestamp : 0;

    return {
      market: safeMarket,
      tsData: genesisTs > 0 ? chart.filter((point) => point.timestamp >= genesisTs) : chart,
      flowData: genesisTs > 0 ? flow.filter((point) => point.timestamp >= genesisTs) : flow,
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
        <div className="flex items-center gap-3 my-6 transition-all duration-500">
          <span className="font-mono text-[#333] text-[12px]">|-</span>
          <div className="flex items-center gap-2 font-mono text-[11px] md:text-[13px] tracking-[0.28em] uppercase text-[#999]">
            <button onClick={() => navigate("/data")} className="hover:text-white transition-colors uppercase">data</button>
            <span className="text-[#999]">/</span>
            <span className="text-[#999] hover:text-white">EULER</span>
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
                    <StatItem label="VAULT" value={normalizedEntityId ? `${normalizedEntityId.slice(0, 6)}...${normalizedEntityId.slice(-4)}` : "-"} />
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
                        ? `$${Number(market.loanPriceUsd).toLocaleString(undefined, { maximumFractionDigits: 4 })}`
                        : "-"
                    }
                  />
                  <div className="border-t border-white/10 pt-3">
                    <StatItem
                      label="PROVIDER"
                      value={
                        market.oracleSupport
                          ? market.oracleSupport.replace(/_/g, " ").replace(/supported/i, "").trim().split(" ").map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()).join(" ") || "Unknown"
                          : "-"
                      }
                    />
                  </div>
                </div>
              }
            />
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
            <div className="flex items-center justify-between mb-8">
              <div className="flex items-center gap-3">
                <Activity size={18} className="text-gray-500" />
                <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Interest Rates</h2>
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
                    { key: "supplyApy", color: "#34d399", name: "Supply APY", format: "percent" },
                  ]}
                />
              </div>
            )}
          </div>

          <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
            <div className="flex items-center justify-between mb-8">
              <div className="flex items-center gap-3">
                <Activity size={18} className="text-gray-500" />
                <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Value Locked</h2>
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
                    { key: "borrowUsd", color: "#fb7185", name: "Borrow TVL", format: "dollar" },
                  ]}
                />
              </div>
            )}
          </div>

          <FlowChartCard
            title="Supply Inflow / Outflow (USD)"
            loading={pageLoading}
            data={flowData}
            areas={[
              { key: "supplyInflowUsd", color: "#22c55e", legend: "Inflow", format: "dollar" },
              { key: "supplyOutflowUsd", color: "#f43f5e", legend: "Outflow", format: "dollar" },
              { key: "netSupplyFlowUsd", color: "#22d3ee", legend: "Net", format: "dollar", noFill: true },
            ]}
          />
          <FlowChartCard
            title="Borrow Inflow / Outflow (USD)"
            loading={pageLoading}
            data={flowData}
            areas={[
              { key: "borrowInflowUsd", color: "#8b5cf6", legend: "Inflow", format: "dollar" },
              { key: "borrowOutflowUsd", color: "#f97316", legend: "Outflow", format: "dollar" },
              { key: "netBorrowFlowUsd", color: "#facc15", legend: "Net", format: "dollar", noFill: true },
            ]}
          />
        </div>

        <FlowChartCard
          title="Cumulative Net Inflow (USD)"
          loading={pageLoading}
          data={flowData}
          areas={[
            { key: "cumulativeSupplyNetInflowUsd", color: "#60a5fa", legend: "Supply", format: "dollar" },
            { key: "cumulativeBorrowNetInflowUsd", color: "#bef264", legend: "Borrow", format: "dollar" },
          ]}
        />
      </main>
    </div>
  );
}
