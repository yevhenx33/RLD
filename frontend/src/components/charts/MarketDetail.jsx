import React, { useState, useEffect, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Brush, Legend,
} from "recharts";
import {
  Loader2, ArrowLeft, ExternalLink,
} from "lucide-react";
import { ENVIO_GQL_URL } from "../../utils/helpers";
import { getTokenIcon, getTokenName, getProtocolDisplayName } from "../../utils/tokenIcons";

const PROTOCOL_MAP = {
  aave: "AAVE_MARKET",
  morpho: "MORPHO_MARKET",
  euler: "EULER_MARKET",
  fluid: "FLUID_MARKET",
};

const formatCurrency = (v) => {
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
};

const formatPct = (v) => `${(v * 100).toFixed(2)}%`;

const RESOLUTIONS = [
  { key: "1H", label: "1H" },
  { key: "4H", label: "4H" },
  { key: "1D", label: "1D" },
  { key: "1W", label: "1W" },
];

export default function MarketDetail() {
  const { protocol: protocolSlug, marketId } = useParams();
  const navigate = useNavigate();
  const protocolKey = PROTOCOL_MAP[protocolSlug] || "AAVE_MARKET";
  const protocolName = getProtocolDisplayName(protocolKey);
  const isMorpho = protocolKey.startsWith("MORPHO");

  const [market, setMarket] = useState(null);
  const [timeseries, setTimeseries] = useState([]);
  const [allocations, setAllocations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tsLoading, setTsLoading] = useState(true);
  const [resolution, setResolution] = useState("1H");

  // Fetch market metadata
  useEffect(() => {
    const fetch_ = async () => {
      setLoading(true);
      try {
        const res = await fetch(ENVIO_GQL_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: `{ protocolMarkets(protocol: "${protocolKey}") { entityId symbol protocol supplyUsd borrowUsd supplyApy borrowApy utilization collateralSymbol lltv } }`,
          }),
        });
        const json = await res.json();
        const rows = json?.data?.protocolMarkets || [];
        const found = rows.find((r) => r.entityId.toLowerCase().includes(marketId.toLowerCase()));
        if (found) {
          setMarket({
            ...found,
            loanIcon: getTokenIcon(found.symbol),
            loanName: getTokenName(found.symbol),
            collateralIcon: found.collateralSymbol ? getTokenIcon(found.collateralSymbol) : null,
          });
        }
      } catch (err) {
        console.error("MarketDetail fetch error:", err);
      }
      setLoading(false);
    };
    fetch_();
  }, [protocolKey, marketId]);

  // Fetch timeseries and allocations
  useEffect(() => {
    if (!market?.entityId) return;
    const fetch_ = async () => {
      setTsLoading(true);
      try {
        const entityId = market.entityId;
        const queryStr = `{
          marketTimeseries(entityId: "${entityId}", resolution: "${resolution}", limit: 500) { timestamp supplyApy borrowApy utilization supplyUsd borrowUsd }
          ${isMorpho ? `marketVaultAllocations(entityId: "${entityId}", limit: 90) { timestamp allocations { name vaultAddress shares } }` : ''}
        }`;
        const res = await fetch(ENVIO_GQL_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: queryStr }),
        });
        const json = await res.json();
        setTimeseries(json?.data?.marketTimeseries || []);
        if (isMorpho) {
          setAllocations(json?.data?.marketVaultAllocations || []);
        }
      } catch (err) {
        console.error("Timeseries fetch error:", err);
      }
      setTsLoading(false);
    };
    fetch_();
  }, [market?.entityId, resolution, isMorpho]);

  // Chart data
  const chartData = useMemo(() => {
    return timeseries.map((p) => ({
      timestamp: p.timestamp,
      supplyApy: p.supplyApy ? p.supplyApy * 100 : null,
      borrowApy: p.borrowApy ? p.borrowApy * 100 : null,
      utilization: p.utilization ? p.utilization * 100 : null,
      supplyUsd: p.supplyUsd,
      borrowUsd: p.borrowUsd,
    }));
  }, [timeseries]);

  const { allocationChartData, vaultColors, topVaults } = useMemo(() => {
    if (!allocations.length) return { allocationChartData: [], vaultColors: {}, topVaults: [] };
    
    const colors = ["#22d3ee", "#818cf8", "#f472b6", "#34d399", "#fbbf24", "#a78bfa", "#f87171", "#6ee7b7", "#93c5fd"];
    const colorMap = {};
    let colorIdx = 0;

    const data = allocations.map(pt => {
      const row = { timestamp: pt.timestamp };
      
      // Deduplicate within the same timestamp if the DB returns multiple identical block rows
      const uniqAllocs = new Map();
      pt.allocations.forEach(a => {
        if (!uniqAllocs.has(a.vaultAddress)) {
          uniqAllocs.set(a.vaultAddress, a);
        }
      });

      Array.from(uniqAllocs.values()).forEach(a => {
        const val = parseFloat(a.shares || 0);
        if (val > 0) {
          row[a.name] = val;
          if (!colorMap[a.name]) {
            colorMap[a.name] = colors[colorIdx % colors.length];
            colorIdx++;
          }
        }
      });
      return row;
    });

    // Extract current top vaults from latest datapoint for the table
    let latestTop = [];
    if (allocations.length > 0) {
      const maxTsPoint = [...allocations].sort((a,b) => b.timestamp - a.timestamp)[0];
      
      const uniqAllocs = new Map();
      maxTsPoint.allocations.forEach(a => {
        if (!uniqAllocs.has(a.vaultAddress)) {
          uniqAllocs.set(a.vaultAddress, a);
        }
      });
      const uniqueList = Array.from(uniqAllocs.values());

      let totalShares = 0;
      uniqueList.forEach(v => totalShares += parseFloat(v.shares || 0));

      latestTop = uniqueList.map(a => {
        const s = parseFloat(a.shares || 0);
        return {
          ...a,
          sharePct: totalShares > 0 ? (s / totalShares) * 100 : 0
        };
      }).sort((a,b) => b.sharePct - a.sharePct);
    }

    return { allocationChartData: data, vaultColors: colorMap, topVaults: latestTop };
  }, [allocations]);

  const formatTick = (ts) => {
    const d = new Date(ts * 1000);
    if (resolution === "1H" || resolution === "4H") {
      return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    }
    return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
  };

  const ChartTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    const d = new Date(label * 1000);
    return (
      <div className="bg-[#0a0a0a] border border-zinc-800 p-3 rounded shadow-2xl font-mono text-xs z-50">
        <div className="text-zinc-500 mb-2 border-b border-zinc-800 pb-1">
          {d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" })}
        </div>
        {payload.map((entry) => (
          <div key={entry.name} className="flex justify-between gap-4">
            <div className="flex items-center gap-1.5">
              {entry.color && <div className="w-2 h-2" style={{ backgroundColor: entry.color }} />}
              <span className="text-zinc-300">{entry.name}:</span>
            </div>
            <span className="text-white font-bold">
              {entry.name.includes("APY") || entry.name === "Utilization" 
                 ? `${entry.value.toFixed(2)}%` 
                 : entry.name.includes("TVL") 
                    ? formatCurrency(entry.value) 
                    : `${entry.value.toExponential(2)} shares`} 
            </span>
          </div>
        ))}
      </div>
    );
  };
  
  const AllocationTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    const d = new Date(label * 1000);
    
    // For 100% stacked bar chart, calculate percentages
    let total = 0;
    payload.forEach(p => total += p.value);
    
    return (
      <div className="bg-[#0a0a0a] border border-zinc-800 p-3 rounded shadow-2xl font-mono text-xs z-50 min-w-[200px]">
        <div className="text-zinc-500 mb-2 border-b border-zinc-800 pb-1">
          {d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric" })}
        </div>
        {[...payload].sort((a,b) => b.value - a.value).map((entry) => {
          const pct = total > 0 ? (entry.value / total) * 100 : 0;
          return (
            <div key={entry.name} className="flex justify-between gap-4 py-0.5">
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2" style={{ backgroundColor: entry.color }} />
                <span className="text-zinc-300 truncate max-w-[120px]" title={entry.name}>{entry.name}</span>
              </div>
              <span className="text-white font-bold">{pct.toFixed(2)}%</span>
            </div>
          );
        })}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-cyan-500 animate-spin" />
      </div>
    );
  }

  if (!market) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex flex-col items-center justify-center gap-4">
        <span className="text-lg text-gray-500">Market not found</span>
        <button onClick={() => navigate(-1)} className="text-cyan-400 hover:text-cyan-300 flex items-center gap-2">
          <ArrowLeft size={16} /> Back
        </button>
      </div>
    );
  }

  const pairLabel = isMorpho && market.collateralSymbol
    ? `${market.collateralSymbol} / ${market.symbol}`
    : market.symbol;

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono selection:bg-pink-500/30">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">
        {/* Back nav */}
        <div className="py-4 flex items-center gap-3">
          <button
            onClick={() => navigate(`/explore/${protocolSlug}`)}
            className="flex items-center gap-2 text-gray-500 hover:text-white transition-colors text-sm uppercase tracking-widest"
          >
            <ArrowLeft size={16} />
            {protocolName}
          </button>
          <span className="text-gray-700">/</span>
          <span className="text-white text-sm uppercase tracking-widest font-bold">{pairLabel}</span>
        </div>

        {/* Market Header */}
        <div className="border border-white/10 bg-[#0a0a0a] p-6 mb-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              {/* Token icons */}
              <div className="flex items-center -space-x-3">
                {market.collateralIcon && (
                  <div className="w-12 h-12 rounded-full bg-[#151515] border-2 border-[#0a0a0a] flex items-center justify-center p-1.5 z-10">
                    <img src={market.collateralIcon} alt={market.collateralSymbol} className="w-full h-full object-contain rounded-full" />
                  </div>
                )}
                <div className={`w-12 h-12 rounded-full bg-[#151515] border-2 border-[#0a0a0a] flex items-center justify-center p-1.5 ${market.collateralIcon ? 'z-0' : 'z-10'}`}>
                  <img src={market.loanIcon} alt={market.symbol} className="w-full h-full object-contain rounded-full" />
                </div>
              </div>
              <div>
                <h1 className="text-2xl font-bold text-white tracking-tight">{pairLabel}</h1>
                <div className="flex items-center gap-3 mt-1">
                  <span className="text-xs text-gray-600 uppercase tracking-widest font-bold">{protocolName}</span>
                  {market.lltv > 0 && (
                    <span className="text-xs text-gray-500 font-mono bg-white/5 px-2 py-0.5">
                      LLTV {(market.lltv * 100).toFixed(0)}%
                    </span>
                  )}
                  <a
                    href={`https://etherscan.io/address/0x${marketId}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-gray-600 hover:text-cyan-400 transition-colors"
                  >
                    <ExternalLink size={12} />
                  </a>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Stats Banner */}
        <div className="flex flex-wrap lg:flex-nowrap items-stretch border border-white/10 bg-[#0a0a0a] mb-6">
          {[
            { label: "Supply TVL", value: formatCurrency(market.supplyUsd), color: "text-white" },
            { label: "Borrow TVL", value: formatCurrency(market.borrowUsd), color: "text-white" },
            { label: "Supply APY", value: formatPct(market.supplyApy), color: "text-emerald-400" },
            { label: "Borrow APY", value: formatPct(market.borrowApy), color: "text-cyan-400" },
            { label: "Utilization", value: formatPct(market.utilization), color: "text-purple-400" },
          ].map((stat, i) => (
            <div key={stat.label} className={`flex-1 flex flex-col items-center justify-center py-5 ${i > 0 ? 'border-l border-white/10' : ''}`}>
              <span className="text-gray-500 text-xs uppercase tracking-widest mb-1">{stat.label}</span>
              <span className={`text-lg font-mono font-bold ${stat.color}`}>{stat.value}</span>
            </div>
          ))}
        </div>

        {/* Resolution selector */}
        <div className="flex items-center gap-2 mb-4">
          <span className="text-xs text-gray-600 uppercase tracking-widest mr-2">Resolution</span>
          {RESOLUTIONS.map((r) => (
            <button
              key={r.key}
              onClick={() => setResolution(r.key)}
              className={`px-3 py-1.5 text-xs font-mono uppercase tracking-widest transition-colors ${
                resolution === r.key
                  ? "bg-white/10 text-white"
                  : "text-gray-500 hover:bg-white/5 hover:text-gray-300"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>

        {/* APY & Utilization Chart */}
        <div className="border border-white/10 bg-[#0a0a0a] p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm uppercase tracking-widest text-gray-500 font-bold">Interest Rates & Utilization</h3>
            <div className="flex items-center gap-6">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-emerald-400" />
                <span className="text-sm uppercase tracking-widest text-gray-400">Supply APY</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-cyan-400" />
                <span className="text-sm uppercase tracking-widest text-gray-400">Borrow APY</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-purple-400" />
                <span className="text-sm uppercase tracking-widest text-gray-400">Utilization</span>
              </div>
            </div>
          </div>
          <div className="h-[350px] w-full">
            {tsLoading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                  <XAxis
                    dataKey="timestamp"
                    scale="time"
                    tickFormatter={formatTick}
                    stroke="#71717a"
                    fontSize={12}
                    tickMargin={12}
                  />
                  <YAxis
                    yAxisId="left"
                    stroke="#71717a"
                    fontSize={12}
                    tickFormatter={(v) => `${v.toFixed(1)}%`}
                    width={55}
                  />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    domain={[0, 100]}
                    ticks={[0, 25, 50, 75, 100]}
                    stroke="#a78bfa"
                    fontSize={12}
                    tickFormatter={(v) => `${v}%`}
                    width={45}
                  />
                  <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#52525b", strokeDasharray: "4 4" }} />
                  <Line yAxisId="left" type="monotone" dataKey="supplyApy" name="Supply APY" stroke="#34d399" strokeWidth={2} dot={false} />
                  <Line yAxisId="left" type="monotone" dataKey="borrowApy" name="Borrow APY" stroke="#22d3ee" strokeWidth={2} dot={false} />
                  <Line yAxisId="right" type="monotone" dataKey="utilization" name="Utilization" stroke="#a78bfa" strokeWidth={2} dot={false} strokeDasharray="5 5" />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* TVL Chart */}
        <div className="border border-white/10 bg-[#0a0a0a] p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm uppercase tracking-widest text-gray-500 font-bold">Total Value Locked</h3>
            <div className="flex items-center gap-6">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-indigo-400" />
                <span className="text-sm uppercase tracking-widest text-gray-400">Supply</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-rose-400" />
                <span className="text-sm uppercase tracking-widest text-gray-400">Borrow</span>
              </div>
            </div>
          </div>
          <div className="h-[250px] w-full">
            {tsLoading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                  <XAxis
                    dataKey="timestamp"
                    scale="time"
                    tickFormatter={formatTick}
                    stroke="#71717a"
                    fontSize={12}
                    tickMargin={12}
                  />
                  <YAxis
                    stroke="#71717a"
                    fontSize={12}
                    tickFormatter={(v) => {
                      if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
                      if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
                      return `$${(v / 1e3).toFixed(0)}K`;
                    }}
                    width={60}
                  />
                  <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#52525b", strokeDasharray: "4 4" }} />
                  <Area type="monotone" dataKey="supplyUsd" name="Supply TVL" stroke="#818cf8" fill="#818cf8" fillOpacity={0.1} strokeWidth={2} dot={false} />
                  <Area type="monotone" dataKey="borrowUsd" name="Borrow TVL" stroke="#fb7185" fill="#fb7185" fillOpacity={0.1} strokeWidth={2} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* Vault Allocations Chart (Morpho Only) */}
        {isMorpho && allocationChartData.length > 0 && (
          <div className="border border-white/10 bg-[#0a0a0a] mb-6">
            <div className="p-6 border-b border-white/10">
              <h3 className="text-sm uppercase tracking-widest text-gray-500 font-bold mb-4">Historical Vault Allocations</h3>
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={allocationChartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                    <XAxis
                      dataKey="timestamp"
                      scale="time"
                      tickFormatter={formatTick}
                      stroke="#71717a"
                      fontSize={12}
                      tickMargin={12}
                    />
                    <YAxis
                      stroke="#71717a"
                      fontSize={12}
                      tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                      width={45}
                    />
                    <Tooltip content={<AllocationTooltip />} cursor={{ fill: 'rgba(255, 255, 255, 0.05)' }} />
                    {Object.entries(vaultColors).map(([vaultName, color]) => (
                      <Bar 
                        key={vaultName} 
                        dataKey={vaultName} 
                        stackId="a" 
                        fill={color} 
                        stackOffset="expand" 
                      />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            
            {/* Curators Table */}
            <div className="p-5">
              <h3 className="text-sm uppercase tracking-widest text-gray-500 font-bold mb-4">Current Curators Breakdown</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-white/10 text-gray-500 text-xs uppercase tracking-widest">
                      <th className="pb-3 pr-4 font-bold">Curator / Vault</th>
                      <th className="pb-3 px-4 font-bold text-center">Color Code</th>
                      <th className="pb-3 pl-4 font-bold text-right">Share Pct</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {topVaults.map(v => (
                      <tr key={v.vaultAddress} className="hover:bg-white/[0.02]">
                        <td className="py-3 pr-4">
                          <div className="text-sm font-bold text-white">
                            {v.name}
                          </div>
                          <div className="text-xs text-gray-600 font-mono">
                            {v.vaultAddress.slice(0, 6)}...{v.vaultAddress.slice(-4)}
                          </div>
                        </td>
                        <td className="py-3 px-4 text-center">
                          <div className="w-4 h-4 mx-auto rounded-sm" style={{ backgroundColor: vaultColors[v.name] || '#333' }} />
                        </td>
                        <td className="py-3 pl-4 text-right">
                          <span className="text-sm font-mono font-bold text-white">
                            {v.sharePct.toFixed(2)}%
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

      </main>
    </div>
  );
}
