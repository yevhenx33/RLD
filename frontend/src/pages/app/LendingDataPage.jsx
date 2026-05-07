import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import { MetricCell, StatItem } from "../../components/pools/MetricsGrid";
import { Activity, PieChart as PieChartIcon, Layers, Users, Check, Loader2 } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import RLDPerformanceChart from "../../charts/primitives/RLDPerformanceChart";
import { ENVIO_GRAPHQL_URL } from "../../api/endpoints";
import { postGraphQL } from "../../api/graphqlClient";
import { getTokenIcon } from "../../utils/tokenIcons";
import { REFRESH_INTERVALS } from "../../config/refreshIntervals";

const LENDING_DATA_QUERY = `
  query LendingDataHub($displayIn: String!) {
    lendingDataPage(displayIn: $displayIn) {
      freshness { ready status generatedAt }
      stats {
        totalSupplyUsd
        totalBorrowUsd
        averageSupplyApy
        averageBorrowApy
        marketCount
      }
      chartData {
        timestamp
        tvl
        averageSupplyApy
        averageBorrowApy
      }
      markets {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
        netWorth
      }
    }
  }
`;

const SUPPLY_APY_AREA = {
  key: "averageSupplyApy",
  color: "#34d399",
  name: "Avg Supply APY",
  format: "percent",
  yAxisId: "right",
};
const BORROW_APY_AREA = {
  key: "averageBorrowApy",
  color: "#06b6d4",
  name: "Avg Borrow APY",
  format: "percent",
  yAxisId: "right",
};

const formatCurrency = (value) => {
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
};

const formatApy = (value) => {
  return `${(value * 100).toFixed(2)}%`;
};

const CustomCheckbox = ({ label, checked = false, disabled = false, onClick }) => (
  <button
    type="button"
    onClick={disabled ? undefined : onClick}
    className={`w-full text-left flex items-center gap-3 select-none ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer hover:opacity-80 transition-opacity"
      }`}
  >
    <div className={`w-4 h-4 rounded-sm border flex items-center justify-center transition-colors ${checked ? 'bg-cyan-500 border-cyan-500' : 'bg-[#080808] border-white/20'
      }`}>
      {checked && <Check size={12} strokeWidth={3} className="text-black" />}
    </div>
    <span className="text-xs tracking-wide">{label}</span>
  </button>
);

export default function LendingDataPage() {
  const navigate = useNavigate();
  const [displayUnit, setDisplayUnit] = useState("USD");
  const [showSupplyApyHistory, setShowSupplyApyHistory] = useState(false);
  const [showBorrowApyHistory, setShowBorrowApyHistory] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [protocolFilter, setProtocolFilter] = useState('ALL');
  const { data: gqlData, error: _error, isLoading: loading } = useSWR(
    [ENVIO_GRAPHQL_URL, `envio.lending-data-hub.${displayUnit}.v6`],
    ([url]) =>
      postGraphQL(url, {
        query: LENDING_DATA_QUERY,
        variables: { displayIn: displayUnit },
      }),
    { refreshInterval: REFRESH_INTERVALS.ANALYTICS_PAGE_MS, dedupingInterval: REFRESH_INTERVALS.ANALYTICS_DEDUPE_MS }
  );

  const { stats, chartData, marketsData } = useMemo(() => {
    const page = gqlData?.lendingDataPage || {};
    return {
      stats: page.stats || {
        totalSupplyUsd: 0,
        totalBorrowUsd: 0,
        averageSupplyApy: 0,
        averageBorrowApy: 0,
        marketCount: 0,
      },
      chartData: page.chartData || [],
      marketsData: page.markets || [],
    };
  }, [gqlData]);

  const handleProtocolFilter = (filter) => {
    setProtocolFilter(filter);
    setCurrentPage(1);
  };

  const filteredMarkets = useMemo(() => {
    return marketsData.filter(pool => {
      if (protocolFilter === 'ALL') return true;
      const protocol = (pool.protocol === "AAVE_MARKET" || pool.protocol === "AAVE") ? "AAVE" : pool.protocol?.toUpperCase();
      if (protocolFilter === 'LENDING') {
        return ['AAVE', 'MORPHO', 'FLUID', 'EULER'].includes(protocol);
      }
      return protocol === protocolFilter;
    });
  }, [marketsData, protocolFilter]);

  const ITEMS_PER_PAGE = 10;
  const maxPage = Math.ceil(filteredMarkets.length / ITEMS_PER_PAGE) || 1;
  const safeCurrentPage = Math.min(currentPage, maxPage);

  const paginatedMarkets = useMemo(() => {
    const startIndex = (safeCurrentPage - 1) * ITEMS_PER_PAGE;
    return filteredMarkets.slice(startIndex, startIndex + ITEMS_PER_PAGE);
  }, [filteredMarkets, safeCurrentPage]);

  const totalPages = maxPage;

  const tvlArea = useMemo(() => {
    if (displayUnit === "USD") {
      return {
        key: "tvl",
        color: "#22d3ee",
        name: "Protocol TVL",
        format: "dollar",
      };
    }
    return {
      key: "tvl",
      color: "#22d3ee",
      name: `Protocol TVL (${displayUnit})`,
      format: "asset",
      unit: displayUnit,
    };
  }, [displayUnit]);

  const chartAreas = useMemo(() => {
    const areas = [tvlArea];
    if (showSupplyApyHistory) areas.push(SUPPLY_APY_AREA);
    if (showBorrowApyHistory) areas.push(BORROW_APY_AREA);
    return areas;
  }, [showBorrowApyHistory, showSupplyApyHistory, tvlArea]);

  const mockRatioData = useMemo(() => {
    const data = [];
    const now = Math.floor(Date.now() / 1000);
    let currentRatio = 45;
    for (let i = 0; i < 90; i++) {
      currentRatio = currentRatio + (Math.random() - 0.5) * 2;
      data.push({
        timestamp: now - (90 - i) * 86400,
        ratio: Math.max(20, Math.min(80, currentRatio)),
      });
    }
    return data;
  }, []);

  const mockMarketShareData = useMemo(() => {
    const data = [];
    const now = Math.floor(Date.now() / 1000);
    let aave = 70;
    let morpho = 20;
    let fluid = 10;
    for (let i = 0; i < 30; i++) {
      aave += (Math.random() - 0.5) * 2;
      morpho += (Math.random() - 0.5) * 1.5;
      fluid += (Math.random() - 0.5) * 1;

      const sum = Math.max(0.1, aave) + Math.max(0.1, morpho) + Math.max(0.1, fluid);
      const date = new Date((now - (30 - i) * 86400) * 1000);

      data.push({
        timestamp: now - (30 - i) * 86400,
        dateStr: `${date.getMonth() + 1}/${date.getDate()}`,
        aave: (Math.max(0, aave) / sum) * 100,
        morpho: (Math.max(0, morpho) / sum) * 100,
        fluid: (Math.max(0, fluid) / sum) * 100,
      });
    }
    return data;
  }, []);

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">


        <div className="mb-6 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            <MetricCell
              label="OVERVIEW"
              Icon={PieChartIcon}
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
                    <StatItem label="POOLED" value={formatCurrency(stats.totalSupplyUsd)} />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="ISOLATED" value="N/A" />
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

        <section className="mt-8">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

            {/* Top-Left: Historical Interest Rates */}
            <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                  HISTORICAL INTEREST RATES
                </h2>
                <div className="flex gap-4">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2" style={{ backgroundColor: SUPPLY_APY_AREA.color }} />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">Supply</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2" style={{ backgroundColor: BORROW_APY_AREA.color }} />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">Borrow</span>
                  </div>
                </div>
              </div>
              <div className="h-[280px] w-full relative mt-auto">
                {loading && chartData.length === 0 ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  </div>
                ) : (
                  <RLDPerformanceChart
                    data={chartData}
                    areas={[SUPPLY_APY_AREA, BORROW_APY_AREA]}
                    resolution="1D"
                  />
                )}
              </div>
            </div>

            {/* Top-Right: Historical Protocol TVL */}
            <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                  {`PROTOCOL TVL (${displayUnit})`}
                </h2>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2" style={{ backgroundColor: tvlArea.color }} />
                  <span className="text-[9px] text-gray-500 uppercase tracking-widest">TVL</span>
                </div>
              </div>
              <div className="h-[280px] w-full relative mt-auto">
                {loading && chartData.length === 0 ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  </div>
                ) : (
                  <RLDPerformanceChart
                    data={chartData}
                    areas={[tvlArea]}
                    resolution="1D"
                  />
                )}
              </div>
            </div>

            {/* Bottom-Left: Historical Debt/Collateral Ratio */}
            <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                  DEBT / COLLATERAL RATIO
                </h2>
                <div className="text-[9px] text-gray-500 uppercase tracking-widest border border-white/10 px-2 py-1 bg-[#050505]">
                  MOCK DATA
                </div>
              </div>
              <div className="h-[280px] w-full relative mt-auto">
                <RLDPerformanceChart
                  data={mockRatioData}
                  areas={[
                    { key: "ratio", color: "#F43F5E", name: "D/C Ratio", format: "percent" }
                  ]}
                  resolution="1D"
                  yAxisDomain={[0, 100]}
                />
              </div>
            </div>

            {/* Bottom-Right: Market Share Stacked Bar Chart */}
            <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                  MARKET SHARE
                </h2>
                <div className="flex items-center gap-4">
                  <div className="text-[9px] text-gray-500 uppercase tracking-widest border border-white/10 px-2 py-1 bg-[#050505] mr-2">
                    MOCK DATA
                  </div>
                  <div className="flex gap-3">
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 bg-[#818cf8]" />
                      <span className="text-[9px] text-gray-500 uppercase tracking-widest">Aave</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 bg-[#34d399]" />
                      <span className="text-[9px] text-gray-500 uppercase tracking-widest">Morpho</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 bg-[#22d3ee]" />
                      <span className="text-[9px] text-gray-500 uppercase tracking-widest">Fluid</span>
                    </div>
                  </div>
                </div>
              </div>
              <div className="h-[280px] w-full relative mt-auto pt-4">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={mockMarketShareData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#222" vertical={false} />
                    <XAxis
                      dataKey="dateStr"
                      stroke="#555"
                      tick={{ fill: '#888', fontSize: 10 }}
                      tickLine={false}
                      axisLine={false}
                      minTickGap={20}
                    />
                    <YAxis
                      stroke="#555"
                      tick={{ fill: '#888', fontSize: 10 }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(val) => `${val}%`}
                      domain={[0, 100]}
                    />
                    <Tooltip
                      cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                      contentStyle={{ backgroundColor: '#111', borderColor: '#333', color: '#fff', fontSize: '12px' }}
                      itemStyle={{ color: '#fff' }}
                      formatter={(value, name) => [`${value.toFixed(1)}%`, name.charAt(0).toUpperCase() + name.slice(1)]}
                      labelStyle={{ color: '#888', marginBottom: '4px' }}
                    />
                    <Bar dataKey="aave" stackId="a" fill="#818cf8" maxBarSize={40} isAnimationActive={false} />
                    <Bar dataKey="morpho" stackId="a" fill="#34d399" maxBarSize={40} isAnimationActive={false} />
                    <Bar dataKey="fluid" stackId="a" fill="#22d3ee" maxBarSize={40} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

          </div>
        </section>

        {/* Markets Table Section */}
        <section className="mt-8 border border-white/10 bg-[#080808]">
          <div className="flex flex-col md:flex-row md:items-center justify-between p-4 md:p-4 md:px-6 border-b border-white/10 gap-4">
            <div className="flex items-center gap-2 md:gap-4 overflow-x-auto no-scrollbar pb-1 md:pb-0">
              <button
                onClick={() => handleProtocolFilter('ALL')}
                className={`text-xs md:text-sm tracking-widest uppercase transition-colors px-3 py-1.5 rounded-sm border whitespace-nowrap ${protocolFilter === 'ALL' ? 'text-cyan-400 border-cyan-400/30 bg-cyan-400/5' : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-white/5'}`}
              >
                ALL
              </button>

              <div className="h-4 w-px bg-white/10 shrink-0 mx-1" />

              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => handleProtocolFilter('LENDING')}
                  className={`text-xs md:text-sm tracking-widest uppercase transition-colors px-3 py-1.5 rounded-sm border ${protocolFilter === 'LENDING' ? 'text-cyan-400 border-cyan-400/30 bg-cyan-400/5' : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-white/5'}`}
                >
                  LENDING
                </button>
                <div className="flex items-center gap-1">
                  {['AAVE', 'MORPHO', 'FLUID'].map(p => (
                    <button
                      key={p}
                      onClick={() => handleProtocolFilter(p)}
                      className={`text-xs md:text-sm tracking-widest uppercase transition-colors px-3 py-1.5 rounded-sm border ${protocolFilter === p ? 'text-cyan-400 border-cyan-400/30 bg-cyan-400/5' : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-white/5'}`}
                    >
                      {p}
                    </button>
                  ))}
                  <button disabled className="text-xs md:text-sm tracking-widest uppercase text-gray-700 px-3 py-1.5 pt-2 cursor-not-allowed border border-transparent">
                    EULER <span className="text-[9px] opacity-50">(SOON)</span>
                  </button>
                </div>
              </div>

              <div className="h-4 w-px bg-white/10 shrink-0 mx-1" />

              <button
                onClick={() => handleProtocolFilter('PENDLE')}
                className={`text-xs md:text-sm tracking-widest uppercase transition-colors px-3 py-1.5 rounded-sm border shrink-0 ${protocolFilter === 'PENDLE' ? 'text-cyan-400 border-cyan-400/30 bg-cyan-400/5' : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-white/5'}`}
              >
                PENDLE
              </button>
            </div>
          </div>

          <div className="w-full overflow-x-auto">
            <div className="min-w-[1000px] flex flex-col">
              {/* Table Header */}
              <div className="grid grid-cols-9 gap-4 px-4 md:px-6 py-3 text-[11px] md:text-[13px] text-gray-500 uppercase tracking-widest border-b border-white/10 bg-[#050505]">
                <div className="col-span-2">Asset</div>
                <div className="text-center">Liquidity</div>
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
                  paginatedMarkets.map((pool, idx) => (
                    <div
                      key={`${pool.symbol}-${idx}`}
                      onClick={() => pool.entityId && navigate(`/data/aave/${pool.entityId}`)}
                      className={`grid grid-cols-9 gap-4 px-4 md:px-6 py-4 items-center transition-colors ${pool.entityId ? 'hover:bg-white/[0.02] cursor-pointer' : 'opacity-50 cursor-not-allowed'
                        }`}
                    >
                      <div className="col-span-2 flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm">
                          <img src={getTokenIcon(pool.symbol)} alt={pool.symbol} className="w-full h-full object-contain rounded-full" />
                        </div>
                        <span className="text-sm text-white font-medium">{pool.symbol}</span>
                      </div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.netWorth)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.supplyUsd)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.borrowUsd)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-green-500 tracking-widest">{formatApy(pool.supplyApy)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-cyan-500 tracking-widest">{formatApy(pool.borrowApy)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">{(pool.utilization * 100).toFixed(1)}%</div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-400 tracking-widest">{pool.protocol === "AAVE_MARKET" || pool.protocol === "AAVE" ? "AAVE_V3" : pool.protocol}</div>
                    </div>
                  ))
                )}
              </div>

              {/* Pagination Controls */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-6 px-4 md:px-6 py-4 border-t border-white/10 bg-[#080808]">
                  <span className="text-xs text-gray-500 uppercase tracking-widest">
                    Page {safeCurrentPage} of {totalPages}
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setCurrentPage(safeCurrentPage - 1)}
                      disabled={safeCurrentPage === 1}
                      className="px-3 py-1 bg-[#111] border border-white/10 text-xs text-gray-300 uppercase tracking-widest hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      Prev
                    </button>
                    <button
                      onClick={() => setCurrentPage(safeCurrentPage + 1)}
                      disabled={safeCurrentPage === totalPages}
                      className="px-3 py-1 bg-[#111] border border-white/10 text-xs text-gray-300 uppercase tracking-widest hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>

      </main>
    </div>
  );
}
