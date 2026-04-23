import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ExternalLink, Loader2 } from "lucide-react";
import { ENVIO_GRAPHQL_URL } from "../../api/endpoints";
import { postGraphQL } from "../../api/graphqlClient";
import {
  getProtocolDisplayName,
  getTokenIcon,
  getTokenName,
} from "../../utils/tokenIcons";
import { parseMarketSnapshots, aggregateProtocolStats, calculateTotals } from "../../utils/lendingDataPokaYoke";

const PROTOCOL_META = {
  AAVE: { slug: "aave", color: "#6366f1", label: "Aave V3" },
  MORPHO: { slug: "morpho", color: "#06b6d4", label: "Morpho" },
  EULER: { slug: "euler", color: "#f59e0b", label: "Euler" },
  FLUID: { slug: "fluid", color: "#8b5cf6", label: "Fluid" },
};

const PROTOCOL_ORDER = ["AAVE", "MORPHO", "EULER", "FLUID"];

const MARKET_SNAPSHOTS_QUERY = `
  query MarketSnapshots {
    marketSnapshots {
      symbol
      protocol
      supplyUsd
      borrowUsd
      supplyApy
      borrowApy
      utilization
    }
  }
`;

const PROTOCOL_TVL_HISTORY_QUERY = `
  query ProtocolTvlHistory {
    protocolTvlHistory {
      date
      aave
      morpho
      euler
      fluid
    }
  }
`;

function formatCurrency(value) {
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

function formatPercent(value) {
  return `${(value * 100).toFixed(2)}%`;
}

function ProtocolButton({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-xs uppercase tracking-widest border transition-colors ${
        active
          ? "bg-white/10 text-white border-white/30"
          : "text-gray-500 border-white/10 hover:border-white/20 hover:text-white"
      }`}
    >
      {label}
    </button>
  );
}

export default function LendingDataPage() {
  const navigate = useNavigate();
  const [activeProtocol, setActiveProtocol] = useState("ALL");

  const {
    data: snapshotData,
    error: snapshotError,
    isLoading: snapshotsLoading,
  } = useSWR(
    [ENVIO_GRAPHQL_URL, "envio.lending-market-snapshots.v1", null],
    ([url]) => postGraphQL(url, { query: MARKET_SNAPSHOTS_QUERY }),
    {
      refreshInterval: 30000,
      dedupingInterval: 5000,
      revalidateOnFocus: false,
    },
  );

  const {
    data: tvlHistoryData,
    error: tvlHistoryError,
    isLoading: historyLoading,
  } = useSWR(
    [ENVIO_GRAPHQL_URL, "envio.lending-protocol-tvl-history.v1", null],
    ([url]) => postGraphQL(url, { query: PROTOCOL_TVL_HISTORY_QUERY }),
    {
      refreshInterval: 60000,
      dedupingInterval: 10000,
      revalidateOnFocus: false,
    },
  );

  useEffect(() => {
    if (snapshotError) {
      console.error("LendingDataPage snapshots error:", snapshotError);
    }
  }, [snapshotError]);

  useEffect(() => {
    if (tvlHistoryError) {
      console.error("LendingDataPage tvl history error:", tvlHistoryError);
    }
  }, [tvlHistoryError]);

  const markets = useMemo(() => {
    const parsed = parseMarketSnapshots(snapshotData?.marketSnapshots);
    return parsed.map((market) => ({
      ...market,
      protocolName: getProtocolDisplayName(market.protocol),
    }));
  }, [snapshotData]);

  const protocolStats = useMemo(() => {
    return aggregateProtocolStats(markets, PROTOCOL_META, PROTOCOL_ORDER);
  }, [markets]);

  const visibleProtocols = useMemo(() => {
    if (activeProtocol === "ALL") {
      return protocolStats;
    }
    return protocolStats.filter((row) => row.key === activeProtocol);
  }, [protocolStats, activeProtocol]);

  const visibleMarkets = useMemo(() => {
    const filtered =
      activeProtocol === "ALL"
        ? markets
        : markets.filter((market) => market.protocolKey === activeProtocol);
    return filtered.sort((a, b) => b.borrowUsd - a.borrowUsd).slice(0, 35);
  }, [markets, activeProtocol]);

  const totals = useMemo(() => {
    return calculateTotals(protocolStats);
  }, [protocolStats]);

  const tvlHistory = useMemo(() => {
    const rows = tvlHistoryData?.protocolTvlHistory || [];
    return rows.map((row) => ({
      date: row.date,
      AAVE: Number(row.aave || 0),
      MORPHO: Number(row.morpho || 0),
      EULER: Number(row.euler || 0),
      FLUID: Number(row.fluid || 0),
    }));
  }, [tvlHistoryData]);

  const chartProtocols = useMemo(() => {
    if (activeProtocol !== "ALL") {
      return [activeProtocol];
    }
    return PROTOCOL_ORDER.filter((key) =>
      tvlHistory.some((row) => Number(row[key] || 0) > 0),
    );
  }, [activeProtocol, tvlHistory]);

  if (snapshotError || tvlHistoryError) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center p-6">
        <div className="border border-red-500/30 bg-red-500/5 p-8 max-w-xl w-full text-center">
          <div className="text-red-400 font-bold mb-4 uppercase tracking-widest text-xl">Data Pipeline Offline</div>
          <p className="text-gray-400 text-sm mb-6 leading-relaxed">
            The frontend is unable to fetch deterministic market states from the pipeline. 
            To prevent invalid execution assumptions, data rendering has been halted.
          </p>
          <div className="text-xs text-red-400/60 uppercase break-all font-semibold p-4 bg-red-900/10 border border-red-900/30">
            {snapshotError?.message || tvlHistoryError?.message || "Unknown Connection Error"}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">
        <section className="pt-8 pb-6 border-b border-white/10 mb-6">
          <h1 className="text-2xl text-white font-semibold tracking-tight">
            Lending Data Hub
          </h1>
          <p className="text-sm text-gray-500 uppercase tracking-widest mt-2">
            Live Aave and multi-lending market monitor
          </p>
        </section>

        <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-4 mb-6">
          <div className="border border-white/10 bg-[#0a0a0a] p-4">
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">
              Total Supply
            </div>
            <div className="text-xl text-white font-semibold">
              {formatCurrency(totals.totalSupplyUsd)}
            </div>
          </div>
          <div className="border border-white/10 bg-[#0a0a0a] p-4">
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">
              Total Borrow
            </div>
            <div className="text-xl text-white font-semibold">
              {formatCurrency(totals.totalBorrowUsd)}
            </div>
          </div>
          <div className="border border-white/10 bg-[#0a0a0a] p-4">
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">
              Avg Supply APY
            </div>
            <div className="text-xl text-emerald-400 font-semibold">
              {formatPercent(totals.averageSupplyApy)}
            </div>
          </div>
          <div className="border border-white/10 bg-[#0a0a0a] p-4">
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">
              Avg Borrow APY
            </div>
            <div className="text-xl text-cyan-400 font-semibold">
              {formatPercent(totals.averageBorrowApy)}
            </div>
          </div>
          <div className="border border-white/10 bg-[#0a0a0a] p-4">
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">
              Active Markets
            </div>
            <div className="text-xl text-white font-semibold">{totals.marketCount}</div>
          </div>
        </section>

        <section className="border border-white/10 bg-[#0a0a0a] mb-6">
          <div className="p-4 border-b border-white/10 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
            <h2 className="text-sm text-gray-400 uppercase tracking-widest font-bold">
              Protocol Overview
            </h2>
            <div className="flex items-center flex-wrap gap-2">
              <ProtocolButton
                label="ALL"
                active={activeProtocol === "ALL"}
                onClick={() => setActiveProtocol("ALL")}
              />
              {protocolStats.map((protocol) => (
                <ProtocolButton
                  key={protocol.key}
                  label={protocol.label}
                  active={activeProtocol === protocol.key}
                  onClick={() => setActiveProtocol(protocol.key)}
                />
              ))}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-white/[0.02]">
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold">
                    Protocol
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Supply
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Borrow
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Supply APY
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Borrow APY
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Utilization
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Markets
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Open
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {visibleProtocols.map((protocol) => (
                  <tr
                    key={protocol.key}
                    className="hover:bg-white/[0.03] transition-colors"
                  >
                    <td className="p-4">
                      <div className="flex items-center gap-3">
                        <div
                          className="w-2.5 h-2.5"
                          style={{ backgroundColor: protocol.color }}
                        />
                        <span className="text-sm text-white font-semibold">
                          {protocol.label}
                        </span>
                      </div>
                    </td>
                    <td className="p-4 text-right text-sm text-white font-semibold">
                      {formatCurrency(protocol.supplyUsd)}
                    </td>
                    <td className="p-4 text-right text-sm text-white font-semibold">
                      {formatCurrency(protocol.borrowUsd)}
                    </td>
                    <td className="p-4 text-right text-sm text-emerald-400 font-semibold">
                      {formatPercent(protocol.avgSupplyApy)}
                    </td>
                    <td className="p-4 text-right text-sm text-cyan-400 font-semibold">
                      {formatPercent(protocol.avgBorrowApy)}
                    </td>
                    <td className="p-4 text-right text-sm text-purple-400 font-semibold">
                      {formatPercent(protocol.utilization)}
                    </td>
                    <td className="p-4 text-right text-sm text-gray-300">
                      {protocol.markets}
                    </td>
                    <td className="p-4 text-right">
                      <button
                        onClick={() => navigate(`/data/${protocol.slug}`)}
                        className="inline-flex items-center gap-1 text-cyan-400 hover:text-cyan-300 text-xs uppercase tracking-widest"
                      >
                        Protocol
                        <ExternalLink size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
                {!snapshotsLoading && visibleProtocols.length === 0 && (
                  <tr>
                    <td
                      colSpan={8}
                      className="p-8 text-center text-sm uppercase tracking-widest text-gray-500"
                    >
                      No protocol data available.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="border border-white/10 bg-[#0a0a0a] p-5 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm text-gray-400 uppercase tracking-widest font-bold">
              Supply TVL Trend
            </h2>
            <span className="text-xs text-gray-600 uppercase tracking-widest">
              Weekly protocol history
            </span>
          </div>
          <div className="h-[340px] w-full">
            {historyLoading ? (
              <div className="h-full flex items-center justify-center">
                <Loader2 className="w-6 h-6 animate-spin text-cyan-500" />
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={tvlHistory}
                  margin={{ top: 5, right: 10, left: 5, bottom: 5 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                  <XAxis
                    dataKey="date"
                    stroke="#71717a"
                    fontSize={12}
                    tickFormatter={(value) => {
                      const date = new Date(value);
                      return date.toLocaleDateString("en-US", {
                        month: "short",
                        year: "2-digit",
                      });
                    }}
                  />
                  <YAxis
                    stroke="#71717a"
                    fontSize={12}
                    tickFormatter={(value) => {
                      if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
                      if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
                      return `$${value.toFixed(0)}`;
                    }}
                    width={60}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#0a0a0a",
                      borderColor: "#27272a",
                      borderRadius: 0,
                    }}
                    formatter={(value) => formatCurrency(Number(value))}
                  />
                  {chartProtocols.map((protocolKey) => {
                    const meta = PROTOCOL_META[protocolKey];
                    return (
                      <Line
                        key={protocolKey}
                        type="monotone"
                        dataKey={protocolKey}
                        name={meta?.label || protocolKey}
                        stroke={meta?.color || "#94a3b8"}
                        strokeWidth={2}
                        dot={false}
                      />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </section>

        <section className="border border-white/10 bg-[#0a0a0a]">
          <div className="p-4 border-b border-white/10">
            <h2 className="text-sm text-gray-400 uppercase tracking-widest font-bold">
              Top Markets by Borrow
            </h2>
            <p className="text-xs text-gray-600 uppercase tracking-widest mt-2">
              Filtered for {activeProtocol === "ALL" ? "all protocols" : activeProtocol}
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-white/[0.02]">
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold">
                    Asset
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Protocol
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Supply
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Borrow
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Supply APY
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Borrow APY
                  </th>
                  <th className="p-4 text-xs uppercase tracking-widest text-gray-500 font-bold text-right">
                    Utilization
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {visibleMarkets.map((market) => (
                  <tr
                    key={`${market.protocol}-${market.symbol}`}
                    className="hover:bg-white/[0.03] transition-colors"
                  >
                    <td className="p-4">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-[#151515] border border-white/10 flex items-center justify-center p-1.5">
                          <img
                            src={getTokenIcon(market.symbol)}
                            alt={market.symbol}
                            className="w-full h-full object-contain rounded-full"
                            loading="lazy"
                            onError={(event) => {
                              event.target.src = `https://ui-avatars.com/api/?name=${market.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                            }}
                          />
                        </div>
                        <div>
                          <div className="text-sm text-white font-semibold">
                            {market.symbol}
                          </div>
                          <div className="text-xs text-gray-600 uppercase tracking-widest">
                            {getTokenName(market.symbol)}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="p-4 text-right text-xs text-gray-300 uppercase tracking-widest">
                      {market.protocolName}
                    </td>
                    <td className="p-4 text-right text-sm text-white font-semibold">
                      {formatCurrency(market.supplyUsd)}
                    </td>
                    <td className="p-4 text-right text-sm text-white font-semibold">
                      {formatCurrency(market.borrowUsd)}
                    </td>
                    <td className="p-4 text-right text-sm text-emerald-400 font-semibold">
                      {formatPercent(market.supplyApy)}
                    </td>
                    <td className="p-4 text-right text-sm text-cyan-400 font-semibold">
                      {formatPercent(market.borrowApy)}
                    </td>
                    <td className="p-4 text-right text-sm text-purple-400 font-semibold">
                      {formatPercent(market.utilization)}
                    </td>
                  </tr>
                ))}
                {!snapshotsLoading && visibleMarkets.length === 0 && (
                  <tr>
                    <td
                      colSpan={7}
                      className="p-8 text-center text-sm uppercase tracking-widest text-gray-500"
                    >
                      No markets available for this filter.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}
