import React, { useMemo } from "react";
import useSWR from "swr";
import { MetricCell, StatItem } from "../../components/pools/MetricsGrid";
import { Activity, PieChart, Layers, Users, Check, Loader2 } from "lucide-react";
import RLDPerformanceChart from "../../components/charts/RLDChart";
import { ENVIO_GRAPHQL_URL } from "../../api/endpoints";
import { postGraphQL } from "../../api/graphqlClient";
import { parseMarketSnapshots, aggregateProtocolStats, calculateTotals } from "../../utils/lendingDataPokaYoke";

const LENDING_DATA_QUERY = `
  query LendingDataHub {
    marketSnapshots {
      symbol
      protocol
      supplyUsd
      borrowUsd
      supplyApy
      borrowApy
      utilization
    }
    protocolTvlHistory {
      date
      aave
      morpho
      euler
      fluid
    }
  }
`;

const CHART_AREAS = [
  { key: "tvl", color: "#22d3ee", name: "Protocol TVL", format: "dollar" }
];

const formatCurrency = (value) => {
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
};

const formatApy = (value) => {
  return `${(value * 100).toFixed(2)}%`;
};

const CustomCheckbox = ({ label, checked = false, disabled = false }) => (
  <div className={`flex items-center gap-3 select-none ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer hover:opacity-80 transition-opacity'}`}>
    <div className={`w-4 h-4 rounded-sm border flex items-center justify-center transition-colors ${checked ? 'bg-cyan-500 border-cyan-500' : 'bg-[#080808] border-white/20'
      }`}>
      {checked && <Check size={12} strokeWidth={3} className="text-black" />}
    </div>
    <span className="text-xs tracking-wide">{label}</span>
  </div>
);

export default function LendingDataPage() {
  const { data: gqlData, error, isLoading: loading } = useSWR(
    [ENVIO_GRAPHQL_URL, "envio.lending-data-hub.v1"],
    ([url]) => postGraphQL(url, { query: LENDING_DATA_QUERY }),
    { refreshInterval: 30000, dedupingInterval: 5000 }
  );

  const { stats, chartData, marketsData } = useMemo(() => {
    // 1. Safe parsing & Top-level stats (AAVE ONLY)
    const rawMarkets = parseMarketSnapshots(gqlData?.marketSnapshots || []);
    const parsedMarkets = rawMarkets.filter(m => m.protocolKey === "AAVE");
    const protocolStats = aggregateProtocolStats(parsedMarkets);
    const totals = calculateTotals(protocolStats);

    // 2. Chart Transformation (Enforce UNIX Epoch & Aave TVL)
    const rawHistory = gqlData?.protocolTvlHistory || [];
    const chart = rawHistory.map((row) => ({
      timestamp: Math.floor(new Date(row.date).getTime() / 1000),
      tvl: row.aave || 0,
    }));

    // 3. Markets Table Transformation
    const tableData = [...parsedMarkets]
      .sort((a, b) => b.borrowUsd - a.borrowUsd)
      .map(m => ({
        ...m,
        netWorth: Math.max(0, m.supplyUsd - m.borrowUsd)
      }));

    return { stats: totals, chartData: chart, marketsData: tableData };
  }, [gqlData]);

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">


        <div className="mb-6 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            <MetricCell
              label="OVERVIEW"
              Icon={PieChart}
              hideLabelOnMobile={false}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="TOTAL NET WORTH" value={formatCurrency(Math.max(0, stats.totalSupplyUsd - stats.totalBorrowUsd))} />
                  </div>
                  <div className="flex flex-col justify-center gap-2 border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="TOTAL SUPPLY" value={formatCurrency(stats.totalSupplyUsd)} />
                    <StatItem label="TOTAL BORROW" value={formatCurrency(stats.totalBorrowUsd)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="RATES"
              Icon={Activity}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="AVG SUPPLY" value={formatApy(stats.averageSupplyApy)} />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="AVG BORROW" value={formatApy(stats.averageBorrowApy)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="TVL_BY_TYPE"
              Icon={Layers}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="POOLED" value="$22.3B" change="+1.5%" />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="ISOLATED" value="(soon)" />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="STATS"
              Icon={Users}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="MARKETS" value={stats.marketCount} />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="USERS" value="N/A" />
                  </div>
                </div>
              }
            />
          </div>
        </div>

        <section className="mt-8 grid grid-cols-1 lg:grid-cols-4 gap-0 lg:gap-0 border border-white/10 bg-[#080808] divide-y lg:divide-y-0 lg:divide-x divide-white/10">

          {/* Settings Panel (Left - 1 Col) */}
          <div className="col-span-1 p-4 md:p-6 flex flex-col">

            <div className="flex flex-col gap-8">

              <div className="flex flex-col gap-3">
                <div className="text-[12px] text-gray-500 uppercase tracking-widest border-b border-white/10 pb-2 mb-1">Protocols</div>
                <CustomCheckbox label="ALL MARKETS" checked={true} />
                <CustomCheckbox label="AAVE" checked={true} />
                <CustomCheckbox label="Morpho (soon)" disabled={true} />
                <CustomCheckbox label="Fluid (soon)" disabled={true} />
                <CustomCheckbox label="Euler (soon)" disabled={true} />
              </div>

              <div className="flex flex-col gap-3">
                <div className="text-[12px] text-gray-500 uppercase tracking-widest border-b border-white/10 pb-2 mb-1">Metrics</div>
                <CustomCheckbox label="Supply APY" checked={false} />
                <CustomCheckbox label="Borrow APY" checked={false} />
              </div>

              <div className="flex flex-col gap-3">
                <div className="text-[12px] text-gray-500 uppercase tracking-widest border-b border-white/10 pb-2 mb-1">Display In</div>
                <CustomCheckbox label="USD" checked={true} />
                <CustomCheckbox label="BTC" checked={false} />
                <CustomCheckbox label="ETH" checked={false} />
              </div>

            </div>
          </div>

          {/* Chart Panel (Right - 3 Cols) */}
          <div className="lg:col-span-3 flex flex-col p-4 md:p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                PROTOCOL TVL
              </h2>
              <div className="text-[9px] md:text-xs text-gray-500 uppercase tracking-widest border border-white/10 px-2 py-1 bg-[#050505]">
                3Y / 1W STEP
              </div>
            </div>
            <div className="h-[280px] md:h-[394px] w-full relative mt-auto">
              {loading && chartData.length === 0 ? (
                <div className="absolute inset-0 bg-[#0a0a0a]/50 backdrop-blur-sm z-10 flex flex-col items-center justify-center border border-white/10">
                  <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  <span className="text-[10px] uppercase tracking-widest text-gray-400">Syncing Chart...</span>
                </div>
              ) : (
                <RLDPerformanceChart
                  data={chartData}
                  areas={CHART_AREAS}
                  resolution="1W"
                />
              )}
            </div>
          </div>

        </section>

        {/* Markets Table Section */}
        <section className="mt-8 border border-white/10 bg-[#080808]">
          <div className="p-4 md:p-6 border-b border-white/10">
            <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
              MARKETS
            </h2>
          </div>

          <div className="w-full overflow-x-auto">
            <div className="min-w-[1000px] flex flex-col">
              {/* Table Header */}
              <div className="grid grid-cols-9 gap-4 px-4 md:px-6 py-3 text-[11px] md:text-[13px] text-gray-500 uppercase tracking-widest border-b border-white/10 bg-[#050505]">
                <div className="col-span-2">Asset</div>
                <div className="text-center">Net Worth</div>
                <div className="text-center">Total Supply</div>
                <div className="text-center">Total Borrow</div>
                <div className="text-center">Supply APY</div>
                <div className="text-center">Borrow APY</div>
                <div className="text-center">Utilization</div>
                <div className="text-center">Protocol</div>

              </div>

              {/* Table Body */}
              <div className="flex flex-col divide-y divide-white/5 relative min-h-[200px]">
                {loading && marketsData.length === 0 ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center mt-12">
                    <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  </div>
                ) : (
                  marketsData.map((pool, idx) => (
                    <div key={`${pool.symbol}-${idx}`} className="grid grid-cols-9 gap-4 px-4 md:px-6 py-4 items-center hover:bg-white/[0.02] transition-colors cursor-pointer">
                      <div className="col-span-2 flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-zinc-800 border border-white/10 flex items-center justify-center text-[10px] font-bold text-white">
                          {pool.symbol.substring(0, 1)}
                        </div>
                        <span className="text-sm text-white font-medium">{pool.symbol}</span>
                      </div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.netWorth)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.supplyUsd)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.borrowUsd)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-green-500 tracking-widest">{formatApy(pool.supplyApy)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-red-500 tracking-widest">{formatApy(pool.borrowApy)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">{(pool.utilization * 100).toFixed(1)}%</div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-400 tracking-widest">{pool.protocol === "AAVE_MARKET" || pool.protocol === "AAVE" ? "AAVE_V3" : pool.protocol}</div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </section>

      </main>
    </div>
  );
}
