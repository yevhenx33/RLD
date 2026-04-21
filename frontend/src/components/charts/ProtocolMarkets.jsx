import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Loader2,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
  Zap,
} from "lucide-react";
import { ENVIO_GQL_URL } from "../../utils/helpers";
import { getTokenIcon, getTokenName, getProtocolDisplayName } from "../../utils/tokenIcons";

const PAGE_SIZE = 25;

const PROTOCOL_MAP = {
  aave: "AAVE_MARKET",
  morpho: "MORPHO_MARKET",
  euler: "EULER_MARKET",
  fluid: "FLUID_MARKET",
};

const formatCurrency = (value) => {
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
};

const formatApy = (value) => {
  const pct = value * 100;
  if (pct >= 1000) return `${pct.toFixed(0)}%`;
  return `${pct.toFixed(2)}%`;
};

export default function ProtocolMarkets() {
  const { protocol: protocolSlug } = useParams();
  const navigate = useNavigate();
  const protocolKey = PROTOCOL_MAP[protocolSlug] || "AAVE_MARKET";
  const protocolName = getProtocolDisplayName(protocolKey);
  const isMorpho = protocolKey.startsWith("MORPHO");

  const [markets, setMarkets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [sortKey, setSortKey] = useState("supplyUsd");
  const [sortDir, setSortDir] = useState("desc");

  // --- Fetch individual markets ---
  useEffect(() => {
    const fetchMarkets = async () => {
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
        setMarkets(
          rows
            .map((r) => ({
              entityId: r.entityId,
              collateralIcon: r.collateralSymbol ? getTokenIcon(r.collateralSymbol) : null,
              collateralSymbol: r.collateralSymbol || null,
              loanIcon: getTokenIcon(r.symbol),
              loanName: getTokenName(r.symbol),
              symbol: r.symbol,
              protocol: r.protocol,
              supplyUsd: r.supplyUsd || 0,
              borrowUsd: r.borrowUsd || 0,
              supplyApy: r.supplyApy || 0,
              borrowApy: r.borrowApy || 0,
              utilization: r.utilization || 0,
              lltv: r.lltv || 0,
            }))
            .filter((m) => m.utilization < 0.99)
        );
      } catch (err) {
        console.error("ProtocolMarkets fetch error:", err);
      }
      setLoading(false);
    };
    fetchMarkets();
  }, [protocolKey]);

  // --- Sort handler ---
  const handleSort = useCallback((key) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === "desc" ? "asc" : "desc"));
        return key;
      }
      setSortDir("desc");
      return key;
    });
    setCurrentPage(1);
  }, []);

  // --- Memoized sorted + paginated ---
  const sortedData = useMemo(() => {
    const mul = sortDir === "desc" ? -1 : 1;
    return [...markets].sort((a, b) => mul * (a[sortKey] - b[sortKey]));
  }, [markets, sortKey, sortDir]);

  const totalPages = Math.ceil(sortedData.length / PAGE_SIZE);
  const pagedData = useMemo(() => {
    const safe = Math.min(currentPage, totalPages || 1);
    return sortedData.slice((safe - 1) * PAGE_SIZE, safe * PAGE_SIZE);
  }, [sortedData, currentPage, totalPages]);

  // --- Stats (exclude trapped markets from APY averages) ---
  const stats = useMemo(() => {
    const totalSupply = markets.reduce((s, m) => s + m.supplyUsd, 0);
    const totalBorrow = markets.reduce((s, m) => s + m.borrowUsd, 0);
    const avgUtil = totalSupply > 0 ? totalBorrow / totalSupply : 0;
    // Filter out trapped markets (util >= 99.5% or APY > 100%) for avg stats
    const healthy = markets.filter((m) => m.utilization < 0.995 && m.supplyApy < 1.0);
    const healthySupply = healthy.reduce((s, m) => s + m.supplyUsd, 0);
    const healthyBorrow = healthy.reduce((s, m) => s + m.borrowUsd, 0);
    const avgSupplyApy = healthySupply > 0
      ? healthy.reduce((s, m) => s + m.supplyApy * m.supplyUsd, 0) / healthySupply
      : 0;
    const avgBorrowApy = healthyBorrow > 0
      ? healthy.reduce((s, m) => s + m.borrowApy * m.borrowUsd, 0) / healthyBorrow
      : 0;
    return { totalSupply, totalBorrow, avgUtil, avgSupplyApy, avgBorrowApy, count: markets.length };
  }, [markets]);

  // --- Dynamic column config depending on protocol ---
  const COLUMNS = isMorpho
    ? [
        { key: "lltv", label: "LLTV" },
        { key: "supplyUsd", label: "Total Market Size" },
        { key: "borrowUsd", label: "Total Borrow" },
        { key: "supplyApy", label: "Supply APY" },
        { key: "borrowApy", label: "Borrow APY" },
        { key: "utilization", label: "Utilization" },
      ]
    : [
        { key: "supplyUsd", label: "Supply USD" },
        { key: "borrowUsd", label: "Borrow USD" },
        { key: "supplyApy", label: "Supply APY" },
        { key: "borrowApy", label: "Borrow APY" },
        { key: "utilization", label: "Utilization" },
      ];

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono selection:bg-pink-500/30">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">

        {/* Back nav + Title */}
        <div className="flex items-center gap-4 mb-6">
          <button
            onClick={() => navigate("/explore")}
            className="p-2 border border-white/10 hover:bg-white/5 transition-colors"
          >
            <ArrowLeft size={16} className="text-gray-400" />
          </button>
          <div>
            <h1 className="text-2xl font-medium tracking-tight text-white">
              {protocolName} Markets
            </h1>
            <p className="text-sm text-gray-500 uppercase tracking-widest">
              {stats.count} individual {isMorpho ? "markets" : "reserves"} · Ethereum Mainnet
            </p>
          </div>
        </div>

        {/* Metrics Banner */}
        <div className="flex flex-wrap lg:flex-nowrap items-center justify-between border border-white/10 p-4 bg-[#0a0a0a] mb-6 gap-4">
          <div className="flex flex-row items-center justify-between flex-1 pr-2">
            <span className="text-gray-500 text-xs md:text-sm uppercase tracking-widest">Total Supply</span>
            <span className="text-white text-base md:text-lg font-mono">
              {loading ? "..." : formatCurrency(stats.totalSupply)}
            </span>
          </div>
          <div className="flex flex-row items-center justify-between flex-1 border-l border-white/10 pl-4 pr-2">
            <span className="text-gray-500 text-xs md:text-sm uppercase tracking-widest">Total Borrow</span>
            <span className="text-white text-base md:text-lg font-mono">
              {loading ? "..." : formatCurrency(stats.totalBorrow)}
            </span>
          </div>
          <div className="flex flex-row items-center justify-between flex-1 border-l border-white/10 pl-4 pr-2">
            <span className="text-gray-500 text-xs md:text-sm uppercase tracking-widest">Avg Supply APY</span>
            <span className="text-emerald-400 text-base md:text-lg font-mono">
              {loading ? "..." : `${(stats.avgSupplyApy * 100).toFixed(2)}%`}
            </span>
          </div>
          <div className="flex flex-row items-center justify-between flex-1 border-l border-white/10 pl-4 pr-2">
            <span className="text-gray-500 text-xs md:text-sm uppercase tracking-widest">Avg Borrow APY</span>
            <span className="text-cyan-400 text-base md:text-lg font-mono">
              {loading ? "..." : `${(stats.avgBorrowApy * 100).toFixed(2)}%`}
            </span>
          </div>
          <div className="flex flex-row items-center justify-between flex-1 border-l border-white/10 pl-4 pr-2">
            <span className="text-gray-500 text-xs md:text-sm uppercase tracking-widest">Avg Utilization</span>
            <span className="text-purple-400 text-base md:text-lg font-mono">
              {loading ? "..." : `${(stats.avgUtil * 100).toFixed(2)}%`}
            </span>
          </div>
        </div>

        {/* Table */}
        <div className="border border-white/10 bg-[#0a0a0a] relative">
          {loading && (
            <div className="absolute inset-0 bg-black/50 backdrop-blur-sm z-10 flex flex-col items-center justify-center">
              <Loader2 className="w-8 h-8 text-cyan-500 animate-spin mb-2" />
              <span className="text-sm uppercase tracking-widest text-white">Loading Markets...</span>
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-white/[0.02]">
                  <th className="p-5 text-sm uppercase tracking-widest text-gray-500 font-bold text-left min-w-[240px]">
                    {isMorpho ? "Collateral / Loan" : "Asset"}
                  </th>
                  {COLUMNS.map((col) => (
                    <th
                      key={col.key}
                      onClick={() => handleSort(col.key)}
                      className="p-5 text-sm uppercase tracking-widest text-gray-500 font-bold text-center cursor-pointer select-none hover:text-gray-300 transition-colors"
                    >
                      <div className="flex items-center justify-center gap-1.5">
                        {col.label}
                        {sortKey === col.key ? (
                          sortDir === "desc"
                            ? <ChevronDown size={14} className="text-cyan-400" />
                            : <ChevronUp size={14} className="text-cyan-400" />
                        ) : (
                          <ChevronDown size={14} className="opacity-30" />
                        )}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {pagedData.map((m) => {
                  const isTrapped = m.utilization >= 0.995 && m.supplyApy > 1.0;
                  return (
                    <tr
                      key={m.entityId}
                      className={`hover:bg-white/[0.03] transition-all duration-300 group cursor-pointer ${isTrapped ? "opacity-50" : ""}`}
                      onClick={() => navigate(`/explore/${protocolSlug}/${m.entityId.replace('0x', '')}`)}
                    >
                      <td className="p-5">
                        {isMorpho ? (
                          /* ── Morpho: Collateral / Loan pair ── */
                          <div className="flex items-center gap-3">
                            <div className="relative flex items-center">
                              {/* Collateral icon */}
                              <div className="w-9 h-9 rounded-full bg-[#151515] border border-white/10 flex items-center justify-center p-1.5 z-10 group-hover:border-white/30 transition-colors">
                                <img
                                  src={m.collateralIcon || `https://ui-avatars.com/api/?name=${m.collateralSymbol || "?"}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`}
                                  alt={m.collateralSymbol || "?"}
                                  className="w-full h-full object-contain rounded-full"
                                  loading="lazy"
                                  onError={(e) => {
                                    e.target.src = `https://ui-avatars.com/api/?name=${m.collateralSymbol || "?"}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                                  }}
                                />
                              </div>
                              {/* Loan icon (overlapping) */}
                              <div className="w-9 h-9 rounded-full bg-[#151515] border border-white/10 flex items-center justify-center p-1.5 -ml-3 group-hover:border-white/30 transition-colors">
                                <img
                                  src={m.loanIcon}
                                  alt={m.symbol}
                                  className="w-full h-full object-contain rounded-full"
                                  loading="lazy"
                                  onError={(e) => {
                                    e.target.src = `https://ui-avatars.com/api/?name=${m.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                                  }}
                                />
                              </div>
                            </div>
                            <div>
                              <div className="text-base font-bold text-white tracking-tight">
                                {m.collateralSymbol || "Unknown"} <span className="text-gray-600 font-normal">/</span> {m.symbol}
                              </div>
                              <div className="text-xs text-gray-600 uppercase tracking-widest font-bold">
                                {m.loanName}
                                {isTrapped && <span className="text-red-500/70 ml-2">● TRAPPED</span>}
                              </div>
                            </div>
                          </div>
                        ) : (
                          /* ── Aave: Single asset ── */
                          <div className="flex items-center gap-4">
                            <div className="relative">
                              <div className="w-10 h-10 rounded-full bg-[#151515] border border-white/10 flex items-center justify-center p-2 group-hover:border-white/30 transition-colors">
                                <img
                                  src={m.loanIcon}
                                  alt={m.symbol}
                                  className="w-full h-full object-contain rounded-full"
                                  loading="lazy"
                                  onError={(e) => {
                                    e.target.src = `https://ui-avatars.com/api/?name=${m.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                                  }}
                                />
                              </div>
                              <div className="absolute -bottom-1 -right-1 w-4 h-4 bg-[#0a0a0a] rounded-full flex items-center justify-center border border-white/10">
                                <Zap size={8} className="text-yellow-500" fill="currentColor" />
                              </div>
                            </div>
                            <div>
                              <div className="text-base font-bold text-white tracking-tight">{m.symbol}</div>
                              <div className="text-sm text-gray-600 uppercase tracking-widest font-bold">{m.loanName}</div>
                            </div>
                          </div>
                        )}
                      </td>
                      {isMorpho && (
                        <td className="p-5 text-center">
                          <div className="text-sm font-mono font-bold tracking-widest text-gray-400">
                            {(m.lltv * 100).toFixed(0)}%
                          </div>
                        </td>
                      )}
                      <td className="p-5 text-center">
                        <div className="text-sm font-mono font-bold tracking-widest text-white">
                          {formatCurrency(m.supplyUsd)}
                        </div>
                      </td>
                      <td className="p-5 text-center">
                        <div className="text-sm font-mono font-bold tracking-widest text-white">
                          {formatCurrency(m.borrowUsd)}
                        </div>
                      </td>
                      <td className="p-5 text-center">
                        <div className={`text-sm font-mono font-bold tracking-widest ${isTrapped ? "text-red-400" : "text-emerald-400"}`}>
                          {formatApy(m.supplyApy)}
                        </div>
                      </td>
                      <td className="p-5 text-center">
                        <div className={`text-sm font-mono font-bold tracking-widest ${isTrapped ? "text-red-400" : "text-cyan-400"}`}>
                          {formatApy(m.borrowApy)}
                        </div>
                      </td>
                      <td className="p-5 text-center">
                        <div className={`text-sm font-mono font-bold tracking-widest ${m.utilization >= 0.995 ? "text-red-400" : "text-purple-400"}`}>
                          {(m.utilization * 100).toFixed(2)}%
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Pagination */}
            {!loading && markets.length > 0 && (
              <div className="p-4 border-t border-white/5 bg-[#0d0d0d] flex justify-between items-center text-sm uppercase tracking-widest text-gray-600">
                <span>
                  Showing {Math.min((currentPage - 1) * PAGE_SIZE + 1, sortedData.length)}–
                  {Math.min(currentPage * PAGE_SIZE, sortedData.length)} of {sortedData.length} Markets
                </span>
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage === 1}
                    className="p-1.5 rounded border border-white/10 hover:bg-white/5 disabled:opacity-20 disabled:cursor-not-allowed transition-all"
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <span className="text-white font-mono font-bold">{currentPage}</span>
                  <span className="text-gray-600">/</span>
                  <span className="font-mono">{totalPages}</span>
                  <button
                    onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                    disabled={currentPage === totalPages}
                    className="p-1.5 rounded border border-white/10 hover:bg-white/5 disabled:opacity-20 disabled:cursor-not-allowed transition-all"
                  >
                    <ChevronRight size={14} />
                  </button>
                </div>
                <span className="flex items-center gap-1">
                  Data provided by <span className="text-white ml-1">RLD Protocol</span>
                </span>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
