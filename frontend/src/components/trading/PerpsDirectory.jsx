import React, { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { TrendingUp, ChevronDown, ChevronUp, ArrowUpDown, Loader2 } from "lucide-react";
import useSWR from "swr";
import { SIM_GRAPHQL_URL } from "../../api/endpoints";
import { postGraphQL } from "../../api/graphqlClient";

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

const MARKETS_QUERY = `
  query PerpsRepositoryMarkets {
    perpInfo: marketInfo(market: "perp")
    perpSnapshot: snapshot(market: "perp")
    cdsInfo: marketInfo(market: "cds")
    cdsSnapshot: snapshot(market: "cds")
  }
`;

const fetchMarkets = ([url]) => postGraphQL(url, { query: MARKETS_QUERY });

function buildRow({ type, info, snapshot }) {
  if (!info || !snapshot?.market || !snapshot?.pool) return null;

  const positionSymbol = type === "CDS" ? "wCDS" : "wRLP";
  const collateralSymbol = info.collateral?.symbol || info.wausdcSymbol || "USDC";
  const derived = snapshot.derived || {};

  return {
    type,
    address: info.marketId || info.market_id,
    route: type === "CDS" ? "/markets/perps/cds" : `/markets/perps/${info.marketId || info.market_id}`,
    pair: `${positionSymbol} / USD`,
    base: collateralSymbol,
    price: snapshot.market.indexPrice || 0,
    markPrice: snapshot.pool.markPrice || 0,
    change24h: derived.index24hChangePct || 0,
    openInterest: (derived.totalCollateralUsd || 0) + (derived.totalDebtUsd || 0),
    volume24h: derived.volume24hUsd || 0,
    liquidity: derived.poolTvlUsd || snapshot.pool.tvlUsd || 0,
    protocol: "Aave V3",
  };
}

export default function PerpsDirectory() {
  const navigate = useNavigate();
  const [sortKey, setSortKey] = useState("volume24h");
  const [sortDir, setSortDir] = useState("desc");

  const { data, error, isLoading } = useSWR(
    [SIM_GRAPHQL_URL, "perps.repository.markets.v1"],
    fetchMarkets,
    { refreshInterval: 2000, revalidateOnFocus: false, keepPreviousData: true },
  );

  // ── Build markets array from real data ───────────────────────
  const markets = useMemo(() => {
    return [
      buildRow({ type: "RLP", info: data?.perpInfo, snapshot: data?.perpSnapshot }),
      buildRow({ type: "CDS", info: data?.cdsInfo, snapshot: data?.cdsSnapshot }),
    ].filter(Boolean);
  }, [data]);

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const getSortIcon = (col) => {
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
  const loading = isLoading && !data;
  const connected = !error;

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
          {/* Desktop Table Header (lg+) */}
          <div className="hidden lg:grid grid-cols-8 gap-4 px-6 py-3 text-sm text-gray-500 uppercase tracking-widest border-b border-white/5 bg-[#0a0a0a]">
            <button onClick={() => toggleSort("pair")} className="relative flex items-center gap-1.5 text-left hover:text-white transition-colors">
              Market {getSortIcon("pair")}
            </button>
            <button onClick={() => toggleSort("price")} className="relative text-center hover:text-white transition-colors">
              Price <span className="absolute ml-1 top-1/2 -translate-y-1/2">{getSortIcon("price")}</span>
            </button>
            <button onClick={() => toggleSort("change24h")} className="relative text-center hover:text-white transition-colors">
              24H <span className="absolute ml-1 top-1/2 -translate-y-1/2">{getSortIcon("change24h")}</span>
            </button>
            <button onClick={() => toggleSort("base")} className="relative text-center hover:text-white transition-colors">
              Base <span className="absolute ml-1 top-1/2 -translate-y-1/2">{getSortIcon("base")}</span>
            </button>
            <button onClick={() => toggleSort("protocol")} className="relative text-center hover:text-white transition-colors">
              Protocol <span className="absolute ml-1 top-1/2 -translate-y-1/2">{getSortIcon("protocol")}</span>
            </button>
            <button onClick={() => toggleSort("openInterest")} className="relative text-center hover:text-white transition-colors">
              Open Interest <span className="absolute ml-1 top-1/2 -translate-y-1/2">{getSortIcon("openInterest")}</span>
            </button>
            <button onClick={() => toggleSort("volume24h")} className="relative text-center hover:text-white transition-colors">
              Volume 24H <span className="absolute ml-1 top-1/2 -translate-y-1/2">{getSortIcon("volume24h")}</span>
            </button>
            <button onClick={() => toggleSort("liquidity")} className="relative text-center hover:text-white transition-colors">
              Pool Liquidity <span className="absolute ml-1 top-1/2 -translate-y-1/2">{getSortIcon("liquidity")}</span>
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
              onClick={() => navigate(market.route)}
              className="border-b border-white/5 last:border-b-0 cursor-pointer group hover:bg-white/[0.02] transition-colors"
            >
              {/* ── Desktop Row (lg+) ── */}
              <div className="hidden lg:grid grid-cols-8 gap-4 px-6 py-4 items-center">
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

              {/* ── Mobile Card (<lg) ── */}
              <div className="lg:hidden flex flex-col">
                {/* Card Header: cyan accent bar */}
                <div className="flex items-center justify-between px-4 py-3 bg-cyan-500/[0.06] border-b border-cyan-500/10">
                  <div className="text-base font-mono text-cyan-400 font-bold tracking-tight">
                    {market.base}
                    <span className="text-cyan-600 ml-1.5 font-normal text-sm">[{market.type}]</span>
                  </div>
                  <div className="text-[10px] font-mono text-cyan-600 uppercase tracking-widest">
                    {market.pair}
                  </div>
                </div>
                {/* Card Metrics */}
                <div className="grid grid-cols-2 gap-x-6 gap-y-3 px-4 py-4">
                  <div className="flex flex-col">
                    <span className="text-[10px] text-gray-600 uppercase tracking-widest mb-1">Price</span>
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-base font-mono text-white">{formatPrice(market.price)}</span>
                      <span className={`text-xs font-mono ${market.change24h >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {market.change24h >= 0 ? "+" : ""}{market.change24h.toFixed(2)}%
                      </span>
                    </div>
                  </div>
                  <div className="flex flex-col items-end">
                    <span className="text-[10px] text-gray-600 uppercase tracking-widest mb-1">OI</span>
                    <span className="text-base font-mono text-white">{formatUSD(market.openInterest)}</span>
                  </div>
                  <div className="flex flex-col">
                    <span className="text-[10px] text-gray-600 uppercase tracking-widest mb-1">Volume</span>
                    <span className="text-base font-mono text-white">{formatUSD(market.volume24h)}</span>
                  </div>
                  <div className="flex flex-col items-end">
                    <span className="text-[10px] text-gray-600 uppercase tracking-widest mb-1">Liquidity</span>
                    <span className="text-base font-mono text-white">{formatUSD(market.liquidity)}</span>
                  </div>
                </div>
              </div>
            </div>
          ))}

          {/* Footer */}
          <div className="px-4 md:px-6 py-3 border-t border-white/5 bg-[#0a0a0a] flex justify-between items-center text-[10px] uppercase tracking-widest text-gray-600">
            <span>Showing {markets.length} Market{markets.length !== 1 ? "s" : ""}</span>
            <span className="flex items-center gap-1">
              Data provided by <span className="text-white ml-1">RLD Protocol</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
