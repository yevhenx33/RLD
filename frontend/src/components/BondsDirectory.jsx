import React, { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronUp, ArrowUpDown, Loader2 } from "lucide-react";
import { useSimulation } from "../hooks/useSimulation";
import BondBrandingPanel from "./BondBrandingPanel";

// ── Token icon URLs ───────────────────────────────────────────
const TOKEN_ICONS = {
  waUSDC: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo.png",
  waDAI: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x6B175474E89094C44Da98b954EedeAC495271d0F/logo.png",
  waUSDT: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xdAC17F958D2ee523a2206206994597C13D831ec7/logo.png",
  default: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo.png",
};

// ── Formatters ────────────────────────────────────────────────
const formatUSD = (val) => {
  if (val == null || isNaN(val)) return "—";
  if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
  return `$${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};

const formatPrice = (val) => {
  if (val == null || isNaN(val)) return "—";
  if (val >= 1000) return `$${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (val >= 1) return `$${val.toFixed(4)}`;
  return `$${val.toFixed(6)}`;
};

// ── Sort Icon ─────────────────────────────────────────────────
function SortIcon({ col, sortKey, sortDir }) {
  if (sortKey !== col) return <ArrowUpDown size={10} className="opacity-30" />;
  return sortDir === "desc"
    ? <ChevronDown size={10} className="text-cyan-400" />
    : <ChevronUp size={10} className="text-cyan-400" />;
}

// ── Component ─────────────────────────────────────────────────
export default function BondsDirectory() {
  const navigate = useNavigate();
  const [sortKey, setSortKey] = useState("openInterest");
  const [sortDir, setSortDir] = useState("desc");

  // ── Live simulation data ─────────────────────────────────────
  const {
    connected,
    loading,
    market,
    pool,
    poolTVL,
    protocolStats,
    marketInfo,
    chartData,
  } = useSimulation({ pollInterval: 5000 });

  // ── Build bond markets from real data ────────────────────────
  const bondMarkets = useMemo(() => {
    if (!market || !marketInfo) return [];

    const colSymbol = marketInfo.collateral?.symbol || "waUSDC";
    const colName = marketInfo.collateral?.name || "Wrapped Aave USDC";
    const icon = TOKEN_ICONS[colSymbol] || TOKEN_ICONS.default;

    // Open Interest = total collateral + total debt in USD
    const oi = (protocolStats?.totalCollateral || 0) + (protocolStats?.totalDebtUsd || 0);

    // Index Price
    const indexPrice = market.indexPrice || 0;

    // 1Y Range from chart data min/max index prices
    let rangeMin = indexPrice;
    let rangeMax = indexPrice;
    if (chartData?.length > 0) {
      const prices = chartData.map((d) => d.indexPrice).filter((p) => p > 0);
      if (prices.length > 0) {
        rangeMin = Math.min(...prices);
        rangeMax = Math.max(...prices);
      }
    }

    return [
      {
        id: colSymbol,
        asset: colSymbol,
        name: colName,
        icon,
        protocol: "AAVE",
        indexPrice,
        openInterest: oi,
        liquidity: poolTVL || 0,
        rangeMin,
        rangeMax,
      },
    ];
  }, [market, pool, poolTVL, protocolStats, marketInfo, chartData]);

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sortedMarkets = useMemo(() => {
    const markets = [...bondMarkets];
    markets.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string") return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === "asc" ? av - bv : bv - av;
    });
    return markets;
  }, [bondMarkets, sortKey, sortDir]);

  // ── Aggregated header metrics ────────────────────────────────
  const totalOI = bondMarkets.reduce((s, m) => s + m.openInterest, 0);
  const totalLiq = bondMarkets.reduce((s, m) => s + m.liquidity, 0);

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">

        {/* 2-Column Layout: Branding + Table */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">

          {/* LEFT — Branding + Mechanism Card */}
          <div className="xl:col-span-3">
            <BondBrandingPanel accentSteps={["1", "7"]} />
          </div>

          {/* RIGHT — Table */}
          <div className="xl:col-span-9">
            <div className="border border-white/10 bg-[#080808]">
              {/* Table Header Bar */}
              <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="text-sm font-bold uppercase tracking-widest">
                    Bond Markets
                  </h3>
                  <span className="text-sm text-gray-600 font-mono">
                    {bondMarkets.length}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-sm text-gray-500 font-mono">
                  <span className="uppercase tracking-widest">
                    OI {loading ? <Loader2 size={12} className="inline animate-spin" /> : formatUSD(totalOI)}
                  </span>
                  <span className="uppercase tracking-widest">
                    TVL {loading ? <Loader2 size={12} className="inline animate-spin" /> : formatUSD(totalLiq)}
                  </span>
                </div>
              </div>

              {/* Column Headers */}
              <div className="hidden md:grid grid-cols-6 gap-4 px-6 py-3 text-sm text-gray-500 uppercase tracking-widest border-b border-white/5 bg-white/[0.02]">
                <button onClick={() => toggleSort("asset")} className="relative flex items-center gap-1.5 text-left hover:text-white transition-colors">
                  Asset <SortIcon col="asset" sortKey={sortKey} sortDir={sortDir} />
                </button>
                <button onClick={() => toggleSort("protocol")} className="relative text-center hover:text-white transition-colors">
                  Protocol <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="protocol" sortKey={sortKey} sortDir={sortDir} /></span>
                </button>
                <button onClick={() => toggleSort("indexPrice")} className="relative text-center hover:text-white transition-colors">
                  APY, % <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="indexPrice" sortKey={sortKey} sortDir={sortDir} /></span>
                </button>
                <button onClick={() => toggleSort("openInterest")} className="relative text-center hover:text-white transition-colors">
                  Open Interest <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="openInterest" sortKey={sortKey} sortDir={sortDir} /></span>
                </button>
                <button onClick={() => toggleSort("liquidity")} className="relative text-center hover:text-white transition-colors">
                  Liquidity <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="liquidity" sortKey={sortKey} sortDir={sortDir} /></span>
                </button>
                <button onClick={() => toggleSort("rangeMax")} className="relative text-center hover:text-white transition-colors">
                  1Y Range <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="rangeMax" sortKey={sortKey} sortDir={sortDir} /></span>
                </button>
              </div>

              {/* Loading state */}
              {loading && bondMarkets.length === 0 && (
                <div className="flex items-center justify-center py-20">
                  <Loader2 size={24} className="animate-spin text-gray-600" />
                </div>
              )}

              {/* Disconnected state */}
              {!loading && !connected && bondMarkets.length === 0 && (
                <div className="flex items-center justify-center py-20 text-gray-600 text-sm uppercase tracking-widest">
                  Simulation disconnected
                </div>
              )}

              {/* Table Rows */}
              {sortedMarkets.map((m) => (
                <div
                  key={m.id}
                  onClick={() => navigate(`/bonds/${m.id}`)}
                  className="grid grid-cols-1 md:grid-cols-6 gap-4 px-6 py-4 hover:bg-white/[0.02] transition-colors border-b border-white/5 last:border-b-0 cursor-pointer group items-center"
                >
                  {/* Asset */}
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-[#151515] border border-white/10 flex items-center justify-center p-1.5 group-hover:border-white/30 transition-colors">
                      <img
                        src={m.icon}
                        alt={m.asset}
                        className="w-full h-full object-contain rounded-full"
                      />
                    </div>
                    <div>
                      <div className="text-sm font-mono text-white group-hover:text-cyan-400 transition-colors">
                        {m.asset}
                      </div>
                      <div className="text-[10px] text-gray-600 uppercase tracking-widest">
                        {m.name}
                      </div>
                    </div>
                  </div>

                  {/* Protocol */}
                  <div className="text-sm font-mono text-gray-400 text-center">
                    {m.protocol}
                  </div>

                  {/* Index Price */}
                  <div className="text-sm font-mono text-cyan-400 text-center">
                    {formatPrice(m.indexPrice)}
                  </div>

                  {/* Open Interest */}
                  <div className="text-sm font-mono text-white text-center">
                    {formatUSD(m.openInterest)}
                  </div>

                  {/* Liquidity */}
                  <div className="text-sm font-mono text-white text-center">
                    {formatUSD(m.liquidity)}
                  </div>

                  {/* 1Y Range */}
                  <div className="text-sm font-mono text-center">
                    <span className="text-gray-500">{formatPrice(m.rangeMin)}</span>
                    <span className="text-gray-700 mx-1">–</span>
                    <span className="text-white">{formatPrice(m.rangeMax)}</span>
                  </div>
                </div>
              ))}

              {/* Footer */}
              <div className="px-6 py-3 border-t border-white/5 bg-[#0a0a0a] flex justify-between items-center text-[10px] uppercase tracking-widest text-gray-600">
                <span>Showing {bondMarkets.length} Market{bondMarkets.length !== 1 ? "s" : ""}</span>
                <span className="flex items-center gap-1">
                  Data provided by <span className="text-white ml-1">RLD Protocol</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
