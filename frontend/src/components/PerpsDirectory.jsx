import React, { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { TrendingUp, ChevronDown, ChevronUp, ArrowUpDown, Loader2 } from "lucide-react";
import { useSimulation } from "../hooks/useSimulation";

const formatUSD = (val) => {
  if (val == null || isNaN(val)) return "—";
  if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(0)}K`;
  return `$${val.toLocaleString()}`;
};

const formatPrice = (val) => {
  if (val == null || isNaN(val)) return "—";
  if (val >= 1000) return `$${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (val >= 1) return `$${val.toFixed(2)}`;
  return `$${val.toFixed(4)}`;
};

export default function PerpsDirectory() {
  const navigate = useNavigate();
  const [sortKey, setSortKey] = useState("volume24h");
  const [sortDir, setSortDir] = useState("desc");

  // ── Live simulation data ─────────────────────────────────────
  const {
    connected,
    loading,
    market,
    pool,
    volumeData,
    protocolStats,
    marketInfo,
    oracleChange24h,
  } = useSimulation({ pollInterval: 5000 });

  // ── Build markets array from real data ───────────────────────
  const markets = useMemo(() => {
    if (!market || !pool || !marketInfo) return [];

    const posSymbol = marketInfo.position_token?.symbol || "wRLP";
    const colSymbol = marketInfo.collateral?.symbol || "USDC";

    // Liquidity in USD: pool.liquidity is raw Uni V3 liquidity units
    // Approximate via protocolStats or use pool liquidity as-is
    const liquidityUsd = pool.liquidity
      ? (pool.liquidity / 1e12) * (pool.markPrice || market.indexPrice)
      : 0;

    return [
      {
        address: marketInfo.infrastructure?.twamm_hook || "0x0",
        pair: `${posSymbol} / USD`,
        base: colSymbol,
        price: market.indexPrice || 0,
        markPrice: pool.markPrice || 0,
        change24h: oracleChange24h ?? 0,
        openInterest: protocolStats?.totalDebtUsd || 0,
        volume24h: volumeData?.volume_24h_usd || 0,
        liquidity: liquidityUsd,
        protocol: "RLD",
      },
    ];
  }, [market, pool, marketInfo, volumeData, protocolStats, oracleChange24h]);

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const SortIcon = ({ col }) => {
    if (sortKey !== col) return <ArrowUpDown size={10} className="opacity-30" />;
    return sortDir === "desc"
      ? <ChevronDown size={10} className="text-cyan-400" />
      : <ChevronUp size={10} className="text-cyan-400" />;
  };

  const sortedMarkets = useMemo(() => {
    const copy = [...markets];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string") return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === "asc" ? av - bv : bv - av;
    });
    return copy;
  }, [markets, sortKey, sortDir]);

  // ── Aggregated header metrics ────────────────────────────────
  const totalOI = markets.reduce((s, m) => s + m.openInterest, 0);
  const totalVolume = markets.reduce((s, m) => s + m.volume24h, 0);
  const totalLiquidity = markets.reduce((s, m) => s + m.liquidity, 0);

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">

        {/* Header Metrics */}
        <div className="border border-white/10 grid grid-cols-1 lg:grid-cols-12">
          {/* Branding */}
          <div className="lg:col-span-5 flex flex-col justify-center p-6 border-b lg:border-b-0 lg:border-r border-white/10 min-h-[140px]">
            <div className="flex items-center gap-3 mb-2">
              <TrendingUp size={18} className="text-cyan-400" />
              <h1 className="text-2xl font-medium tracking-tight">
                Perpetual Markets
              </h1>
            </div>
            <p className="text-sm text-gray-500 tracking-widest uppercase">
              {markets.length} active market{markets.length !== 1 ? "s" : ""} · RLD Protocol
            </p>
          </div>

          {/* Metrics */}
          <div className="lg:col-span-7 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
            {/* Total OI */}
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Open Interest
              </div>
              <div className="text-2xl font-light tracking-tight text-cyan-400">
                {loading ? <Loader2 size={20} className="animate-spin" /> : formatUSD(totalOI)}
              </div>
            </div>

            {/* Volume 24H */}
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Volume 24H
              </div>
              <div className="text-2xl font-light tracking-tight text-white">
                {loading ? <Loader2 size={20} className="animate-spin" /> : formatUSD(totalVolume)}
              </div>
            </div>

            {/* Total Liquidity */}
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Pool Liquidity
              </div>
              <div className="text-2xl font-light tracking-tight text-white">
                {loading ? <Loader2 size={20} className="animate-spin" /> : formatUSD(totalLiquidity)}
              </div>
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="border border-white/10">
          {/* Table Header */}
          <div className="hidden md:grid grid-cols-8 gap-4 px-6 py-3 text-sm text-gray-500 uppercase tracking-widest border-b border-white/5 bg-[#0a0a0a]">
            <button onClick={() => toggleSort("pair")} className="relative flex items-center gap-1.5 text-left hover:text-white transition-colors">
              Market <SortIcon col="pair" />
            </button>
            <button onClick={() => toggleSort("price")} className="relative text-center hover:text-white transition-colors">
              Price <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="price" /></span>
            </button>
            <button onClick={() => toggleSort("change24h")} className="relative text-center hover:text-white transition-colors">
              24H <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="change24h" /></span>
            </button>
            <button onClick={() => toggleSort("base")} className="relative text-center hover:text-white transition-colors">
              Base <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="base" /></span>
            </button>
            <button onClick={() => toggleSort("protocol")} className="relative text-center hover:text-white transition-colors">
              Protocol <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="protocol" /></span>
            </button>
            <button onClick={() => toggleSort("openInterest")} className="relative text-center hover:text-white transition-colors">
              Open Interest <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="openInterest" /></span>
            </button>
            <button onClick={() => toggleSort("volume24h")} className="relative text-center hover:text-white transition-colors">
              Volume 24H <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="volume24h" /></span>
            </button>
            <button onClick={() => toggleSort("liquidity")} className="relative text-center hover:text-white transition-colors">
              Pool Liquidity <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="liquidity" /></span>
            </button>
          </div>

          {/* Loading state */}
          {loading && markets.length === 0 && (
            <div className="flex items-center justify-center py-20">
              <Loader2 size={24} className="animate-spin text-gray-600" />
            </div>
          )}

          {/* Disconnected state */}
          {!loading && !connected && markets.length === 0 && (
            <div className="flex items-center justify-center py-20 text-gray-600 text-sm uppercase tracking-widest">
              Simulation disconnected
            </div>
          )}

          {/* Table Rows */}
          {sortedMarkets.map((market) => (
            <div
              key={market.address}
              onClick={() => navigate(`/markets/perps/${market.address}`)}
              className="grid grid-cols-1 md:grid-cols-8 gap-4 px-6 py-4 hover:bg-white/[0.02] transition-colors border-b border-white/5 last:border-b-0 cursor-pointer group items-center"
            >
              {/* Market */}
              <div className="flex items-center gap-3">
                <div className="w-2 h-2 bg-cyan-500 shadow-[0_0_6px_rgba(6,182,212,0.4)]" />
                <div className="text-sm font-mono text-white group-hover:text-cyan-400 transition-colors">
                  {market.pair}
                </div>
              </div>

              {/* Price */}
              <div className="text-sm font-mono text-white text-center">
                {formatPrice(market.price)}
              </div>

              {/* 24H Change */}
              <div className={`text-sm font-mono text-center ${market.change24h >= 0 ? "text-green-400" : "text-red-400"}`}>
                {market.change24h >= 0 ? "+" : ""}{market.change24h.toFixed(2)}%
              </div>

              {/* Base */}
              <div className="text-sm font-mono text-gray-400 text-center">
                {market.base}
              </div>

              {/* Protocol */}
              <div className="text-sm font-mono text-gray-400 text-center">
                {market.protocol}
              </div>

              {/* Open Interest */}
              <div className="text-sm font-mono text-white text-center">
                {formatUSD(market.openInterest)}
              </div>

              {/* Volume 24H */}
              <div className="text-sm font-mono text-white text-center">
                {formatUSD(market.volume24h)}
              </div>

              {/* Pool Liquidity */}
              <div className="text-sm font-mono text-white text-center">
                {formatUSD(market.liquidity)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
