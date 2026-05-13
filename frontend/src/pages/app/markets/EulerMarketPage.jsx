import React, { useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Activity, ArrowLeft, Loader2, ExternalLink, Shield, Link2, PieChart as PieChartIcon } from "lucide-react";
import { MetricCell, StatItem } from "../../../components/pools/MetricsGrid";
import RLDPerformanceChart from "../../../charts/primitives/RLDPerformanceChart";
import { apiProtocolForSlug, normalizeMarketIdForApi } from "../../../lib/protocolConfig";
import { getTokenIcon } from "../../../utils/tokenIcons";
import { useMarketPageQuery } from "../../../hooks/queries/useMarketPageQuery";
import { ChartEmptyState, FlowChartCard } from "../../../components/data/MarketPageBlocks";
import { formatApy, formatCurrency, formatPercent } from "../../../lib/analyticsFormatters";
import { buildStandardMarketPageModel } from "../../../lib/marketPageModel";

const CHART_RESOLUTION = "1D";
const TIMESERIES_LIMIT_DAYS = 500;
const FLOW_LIMIT_DAYS = 500;

export default function EulerMarketPage() {
  const { marketId } = useParams();
  const navigate = useNavigate();
  const protocolSlug = "euler";
  const protocolKey = apiProtocolForSlug(protocolSlug);
  const normalizedEntityId = normalizeMarketIdForApi(protocolSlug, marketId);

  const { data: pageGqlData, isLoading: pageLoading } = useMarketPageQuery({
    protocol: protocolKey,
    marketId: normalizedEntityId,
    timeseriesLimit: TIMESERIES_LIMIT_DAYS,
    flowLimit: FLOW_LIMIT_DAYS,
    allocationLimit: 0,
  });

  const { market, tsData, flowData } = useMemo(
    () => buildStandardMarketPageModel(pageGqlData?.marketPage, {
      fallbackProtocol: "EULER_MARKET",
    }),
    [pageGqlData],
  );

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
