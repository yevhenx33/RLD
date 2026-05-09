import React, { useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import useSWR from "swr";
import { Activity, ArrowLeft, Loader2, ExternalLink, Shield, PieChart as PieChartIcon, Link2 } from "lucide-react";
import { MetricCell, StatItem } from "../../../components/pools/MetricsGrid";
import RLDPerformanceChart from "../../../charts/primitives/RLDPerformanceChart";
import { API_GRAPHQL_URL } from "../../../api/endpoints";
import { apiGraphQL } from "../../../api/apiClient";
import { FLUID_VAULT_PAGE_QUERY } from "../../../api/apiQueries";
import { getTokenIcon } from "../../../utils/tokenIcons";
import { REFRESH_INTERVALS } from "../../../config/refreshIntervals";

const CHART_RESOLUTION = "1D";
const TIMESERIES_LIMIT = 700;
const FLOW_LIMIT = 700;

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

const normalizeFlowPoint = (point) => ({
  timestamp: finiteNumber(point?.timestamp),
  supplyInflowUsd: finiteNumber(point?.supplyInflowUsd),
  supplyOutflowUsd: finiteNumber(point?.supplyOutflowUsd),
  borrowInflowUsd: finiteNumber(point?.borrowInflowUsd),
  borrowOutflowUsd: finiteNumber(point?.borrowOutflowUsd),
  netSupplyFlowUsd: Number(point?.netSupplyFlowUsd) || 0,
  netBorrowFlowUsd: Number(point?.netBorrowFlowUsd) || 0,
});

const hasAnyFiniteValue = (point, keys) =>
  keys.some((key) => Number.isFinite(Number(point?.[key])));

function ChartEmptyState({ label }) {
  return (
    <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
      {label}
    </div>
  );
}

export default function FluidVaultPage() {
  const { vaultId } = useParams();
  const navigate = useNavigate();

  const { data: pageGqlData, isLoading: pageLoading } = useSWR(
    vaultId ? ["fluidVaultPage", vaultId] : null,
    () =>
      apiGraphQL("FluidVaultPage", {
        query: FLUID_VAULT_PAGE_QUERY,
        variables: {
          vaultId,
          timeseriesLimit: TIMESERIES_LIMIT,
          flowLimit: FLOW_LIMIT,
        },
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    }
  );

  const { market, tsData, flowData, cumulativeFlowData } = useMemo(() => {
    const page = pageGqlData?.fluidVaultPage || {};
    const rawMarket = page.market || null;

    let safeMarket = null;
    if (rawMarket) {
      const supplyUsd = Math.max(0, Number(rawMarket.supplyUsd) || 0);
      const borrowUsd = Math.max(0, Number(rawMarket.borrowUsd) || 0);
      safeMarket = {
        symbol: String(rawMarket.symbol || "UNKNOWN"),
        protocol: "FLUID_VAULT",
        supplyUsd,
        borrowUsd,
        supplyApy: Math.max(0, finiteNumber(rawMarket.supplyApy)),
        borrowApy: Math.max(0, finiteNumber(rawMarket.borrowApy)),
        utilization: supplyUsd > 0 ? Math.min(1, borrowUsd / supplyUsd) : 0,
        collateralSymbol: rawMarket.collateralSymbol || "",
        loanAsset: rawMarket.loanAsset || "",
        collateralPriceUsd: rawMarket.collateralPriceUsd != null ? Number(rawMarket.collateralPriceUsd) : null,
        oracleSupport: rawMarket.oracleSupport || null,
        lltvMin: rawMarket.lltvMin != null ? Number(rawMarket.lltvMin) : null,
        lltvMax: rawMarket.lltvMax != null ? Number(rawMarket.lltvMax) : null,
      };
    }

    const chart = (page.rateChart || [])
      .map(normalizeRatePoint)
      .filter((p) =>
        p.timestamp > 0
        && hasAnyFiniteValue(p, ["supplyApy", "borrowApy", "supplyUsd", "borrowUsd", "utilization"])
      )
      .sort((a, b) => a.timestamp - b.timestamp);

    const rawFlow = (page.flowChart || [])
      .map(normalizeFlowPoint)
      .filter((p) => p.timestamp > 0)
      .sort((a, b) => a.timestamp - b.timestamp);

    // Filter to period starting from first positive net inflow
    const firstPositiveIdx = rawFlow.findIndex(
      (p) => p.netSupplyFlowUsd > 0 || p.netBorrowFlowUsd > 0
    );
    const filteredFlow = firstPositiveIdx >= 0 ? rawFlow.slice(firstPositiveIdx) : rawFlow;

    // Build cumulative flow data
    let cumSupply = 0;
    let cumBorrow = 0;
    const cumFlow = filteredFlow.map((p) => {
      cumSupply += p.netSupplyFlowUsd;
      cumBorrow += p.netBorrowFlowUsd;
      return {
        ...p,
        cumulativeSupplyNetInflowUsd: cumSupply,
        cumulativeBorrowNetInflowUsd: cumBorrow,
      };
    });

    return {
      market: safeMarket,
      tsData: chart,
      flowData: filteredFlow,
      cumulativeFlowData: cumFlow,
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
        <span className="text-lg">Vault not found or not indexed</span>
        <button onClick={() => navigate(-1)} className="text-cyan-500 hover:text-cyan-400 flex items-center gap-2 transition-colors">
          <ArrowLeft size={16} /> Return
        </button>
      </div>
    );
  }

  const symbolParts = market.symbol.split("/");
  const collateralSymbol = symbolParts[0] || market.symbol;
  const debtSymbol = symbolParts[1] || market.symbol;

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">

        {/* Breadcrumbs */}
        <div className="flex items-center gap-3 my-6 transition-all duration-500">
          <span className="font-mono text-[#333] text-[12px]">|—</span>
          <div className="flex items-center gap-2 font-mono text-[11px] md:text-[13px] tracking-[0.28em] uppercase text-[#999]">
            <button onClick={() => navigate("/data")} className="hover:text-white transition-colors uppercase">data</button>
            <span className="text-[#999]">/</span>
            <button onClick={() => navigate(-1)} className="hover:text-white transition-colors uppercase">FLUID</button>
            <span className="text-[#999]">/</span>
            <span className="text-[#999] flex items-center gap-2 hover:text-white">
              <img src={getTokenIcon(collateralSymbol)} alt={collateralSymbol} className="w-4 h-4 rounded-full grayscale opacity-80" />
              {market.symbol}
              <a
                href={vaultId?.startsWith("0x") ? `https://etherscan.io/address/${vaultId}` : "#"}
                target="_blank"
                rel="noopener noreferrer"
                className={`hover:text-[#888] transition-colors ml-1 ${!vaultId?.startsWith("0x") && "pointer-events-none opacity-40"}`}
              >
                <ExternalLink size={12} />
              </a>
            </span>
          </div>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        {/* Stats Panel */}
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
              label="MARKET_PARAMS"
              Icon={Shield}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-1">COLLATERAL</div>
                      <div className="flex items-center gap-2">
                        <img src={getTokenIcon(collateralSymbol)} alt={collateralSymbol} className="w-5 h-5 rounded-full" />
                        <span className="text-base md:text-xl font-light text-white font-mono tracking-tighter">{collateralSymbol}</span>
                      </div>
                    </div>
                    <div className="border-l border-white/10 pl-4">
                      <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-1">DEBT</div>
                      <div className="flex items-center gap-2">
                        <img src={getTokenIcon(debtSymbol)} alt={debtSymbol} className="w-5 h-5 rounded-full" />
                        <span className="text-base md:text-xl font-light text-white font-mono tracking-tighter">{debtSymbol}</span>
                      </div>
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem
                      label="LLTV"
                      value={
                        market.lltvMin != null
                          ? market.lltvMin === market.lltvMax
                            ? formatPercent(market.lltvMin)
                            : `${formatPercent(market.lltvMin)}–${formatPercent(market.lltvMax)}`
                          : "—"
                      }
                    />
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
                      market.collateralPriceUsd != null
                        ? `$${Number(market.collateralPriceUsd).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                        : "—"
                    }
                  />
                  <div className="border-t border-white/10 pt-3">
                    <StatItem
                      label="PROVIDER"
                      value={market.oracleSupport || "—"}
                    />
                  </div>
                </div>
              }
            />
          </div>
        </div>

        {/* 2x2 Chart Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          {/* Interest Rates Chart */}
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
                Loading...
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
                Loading...
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
                Loading...
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
                Loading...
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
          {pageLoading && cumulativeFlowData.length === 0 ? (
            <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
              <Loader2 size={14} className="animate-spin" />
              Loading...
            </div>
          ) : (
            <div className="h-[300px] w-full">
              <RLDPerformanceChart
                data={cumulativeFlowData}
                resolution={CHART_RESOLUTION}
                referenceLines={[{ y: 0, stroke: "#52525b" }]}
                areas={[
                  { key: "cumulativeSupplyNetInflowUsd", color: "#60a5fa", name: "Cumulative Net Supply Inflow", format: "dollar" },
                  { key: "cumulativeBorrowNetInflowUsd", color: "#bef264", name: "Cumulative Net Borrow Inflow", format: "dollar" }
                ]}
              />
            </div>
          )}
        </div>

      </main>
    </div>
  );
}
