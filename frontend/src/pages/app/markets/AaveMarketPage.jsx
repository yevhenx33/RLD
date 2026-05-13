import React, { useMemo, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { Activity, ArrowLeft, Loader2, ExternalLink, Shield, Link2, PieChart as PieChartIcon, Info, ChevronDown, ChevronUp } from "lucide-react";
import { MetricCell, StatItem } from "../../../components/pools/MetricsGrid";
import RLDPerformanceChart from "../../../charts/primitives/RLDPerformanceChart";
import { apiProtocolForSlug, normalizeMarketIdForApi } from "../../../lib/protocolConfig";
import { getTokenIcon } from "../../../utils/tokenIcons";
import { useMarketPageQuery } from "../../../hooks/queries/useMarketPageQuery";
import { ChartEmptyState } from "../../../components/data/MarketPageBlocks";
import {
  etherscanAddressUrl,
  finiteNumber,
  formatApy,
  formatCurrency,
  formatLltvRange,
  formatOptionalCurrency,
  formatPercent,
} from "../../../lib/analyticsFormatters";
import { buildStandardMarketPageModel } from "../../../lib/marketPageModel";

const CHART_RESOLUTION = "1D";
const TIMESERIES_LIMIT_DAYS = 500;
const FLOW_LIMIT_DAYS = 500;

export default function AaveMarketPage() {
  const { protocol: protocolSlug, marketId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [collateralSort, setCollateralSort] = useState({
    key: "collateralUsd",
    direction: "desc",
  });
  const resolvedProtocolSlug = protocolSlug || location.pathname.split("/")[2] || "aave";
  const protocolKey = apiProtocolForSlug(resolvedProtocolSlug);
  const normalizedEntityId = normalizeMarketIdForApi(resolvedProtocolSlug, marketId);

  const { data: pageGqlData, isLoading: pageLoading } = useMarketPageQuery({
    protocol: protocolKey,
    marketId: normalizedEntityId,
    timeseriesLimit: TIMESERIES_LIMIT_DAYS,
    flowLimit: FLOW_LIMIT_DAYS,
    allocationLimit: 0,
  });

  const { market, tsData, flowData, collateralBreakdown } = useMemo(
    () => buildStandardMarketPageModel(pageGqlData?.marketPage, {
      fallbackProtocol: "AAVE_MARKET",
      includeCollateralBreakdown: true,
    }),
    [pageGqlData],
  );

  const sortedCollateralBreakdown = useMemo(() => {
    const direction = collateralSort.direction === "asc" ? 1 : -1;
    return [...collateralBreakdown].sort((a, b) => {
      if (collateralSort.key === "symbol") {
        return direction * String(a.symbol || "").localeCompare(String(b.symbol || ""));
      }
      const av = a[collateralSort.key] == null ? -Infinity : finiteNumber(a[collateralSort.key], -Infinity);
      const bv = b[collateralSort.key] == null ? -Infinity : finiteNumber(b[collateralSort.key], -Infinity);
      if (av === bv) return String(a.symbol || "").localeCompare(String(b.symbol || ""));
      return direction * (av - bv);
    });
  }, [collateralBreakdown, collateralSort]);

  const handleCollateralSort = (key) => {
    setCollateralSort((current) => ({
      key,
      direction: current.key === key && current.direction === "desc" ? "asc" : "desc",
    }));
  };

  const collateralColumns = [
    { key: "symbol", label: "Asset", align: "text-left", span: "col-span-2" },
    { key: "collateralUsd", label: "Collateral Value" },
    { key: "capacity", label: "Capacity" },
    { key: "borrowCollateralFactor", label: "Borrow CF" },
    { key: "liquidateCollateralFactor", label: "Liquidation CF" },
    { key: "liquidationFactor", label: "Liquidation Factor" },
    { key: "oracle", label: "Oracle", sortable: false },
  ];

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

  const isCompoundV3 = market.protocol === "COMPOUND_V3_MARKET";
  const hasLltvRange = isCompoundV3 && finiteNumber(market.lltvMax) > 0;

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
                    {hasLltvRange ? (
                      <div>
                        <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-0.5 md:mb-1">LLTV Range</div>
                        <div className="flex items-center gap-2 whitespace-nowrap">
                          <div className="text-base md:text-xl font-light text-white font-mono tracking-tighter">{formatLltvRange(market)}</div>
                          <span className="relative group cursor-help">
                            <Info size={12} className="text-gray-500 hover:text-gray-300 transition-colors" />
                            <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-[#1a1a1a] border border-white/10 text-[10px] text-gray-300 rounded whitespace-nowrap opacity-0 pointer-events-none group-hover:opacity-100 transition-opacity z-50">
                              Borrow collateral factor range across active collateral assets
                            </span>
                          </span>
                        </div>
                      </div>
                    ) : market.lltv != null && market.lltv === 0 ? (
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

        {isCompoundV3 && collateralBreakdown.length > 0 && (
          <section className="mt-6 border border-white/10 bg-[#0a0a0a] rounded-sm">
            <div className="flex items-center justify-between border-b border-white/10 px-4 md:px-6 py-4">
              <div className="flex items-center gap-3">
                <Shield size={18} className="text-gray-500" />
                <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Collateral Assets</h2>
              </div>
              <span className="text-xs uppercase tracking-widest text-gray-500">
                {collateralBreakdown.length} assets
              </span>
            </div>
            <div className="w-full overflow-x-auto">
              <div className="min-w-[980px] flex flex-col">
                <div className="grid grid-cols-8 gap-4 px-4 md:px-6 py-3 text-[11px] md:text-[13px] text-gray-500 uppercase tracking-widest border-b border-white/10 bg-[#050505]">
                  {collateralColumns.map((column) => {
                    const active = collateralSort.key === column.key;
                    const sortable = column.sortable !== false;
                    const Icon = active && collateralSort.direction === "asc" ? ChevronUp : ChevronDown;
                    return (
                      <button
                        key={column.key}
                        type="button"
                        onClick={() => sortable && handleCollateralSort(column.key)}
                        className={`${column.span || ""} ${column.align || "text-center"} flex items-center gap-1.5 ${column.align === "text-left" ? "justify-start" : "justify-center"} transition-colors ${active ? "text-cyan-500" : "text-gray-500 hover:text-gray-300"} ${sortable ? "cursor-pointer" : "cursor-default"}`}
                      >
                        <span>{column.label}</span>
                        {sortable && (
                          <Icon size={13} className={active ? "text-cyan-500" : "text-gray-600"} />
                        )}
                      </button>
                    );
                  })}
                </div>
                <div className="flex flex-col divide-y divide-white/5">
                  {sortedCollateralBreakdown.map((row) => {
                    const oracleUrl = etherscanAddressUrl(row.priceFeed);
                    return (
                    <div
                      key={row.asset}
                      className={`grid grid-cols-8 gap-4 px-4 md:px-6 py-4 items-center transition-colors hover:bg-white/[0.02] ${row.borrowEnabled ? "" : "opacity-60"}`}
                    >
                      <div className="col-span-2 flex items-center gap-3 min-w-0">
                        <div className="w-8 h-8 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm">
                          <img
                            src={getTokenIcon(row.symbol)}
                            alt={row.symbol}
                            className="w-full h-full object-contain rounded-full"
                            loading="lazy"
                            onError={(e) => {
                              e.target.src = `https://ui-avatars.com/api/?name=${row.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                            }}
                          />
                        </div>
                        <div className="min-w-0">
                          <div className="text-[10px] md:text-[13px] text-white font-bold leading-none truncate">
                            {row.symbol}
                          </div>
                        </div>
                      </div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">
                        {formatOptionalCurrency(row.collateralUsd)}
                      </div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">
                        {row.capacity == null ? "-" : formatPercent(row.capacity)}
                      </div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">
                        {formatPercent(row.borrowCollateralFactor)}
                      </div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">
                        {formatPercent(row.liquidateCollateralFactor)}
                      </div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">
                        {formatPercent(row.liquidationFactor)}
                      </div>
                      <div className="flex justify-center">
                        {oracleUrl ? (
                          <a
                            href={oracleUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(event) => event.stopPropagation()}
                            className="inline-flex h-7 w-7 items-center justify-center rounded border border-white/10 text-gray-500 transition-colors hover:border-cyan-500/50 hover:text-cyan-500"
                            title={`${row.symbol} oracle`}
                          >
                            <ExternalLink size={14} />
                          </a>
                        ) : (
                          <span className="text-[10px] md:text-[13px] text-gray-600 tracking-widest">-</span>
                        )}
                      </div>
                    </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
