import React, { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronUp, ArrowUpDown } from "lucide-react";
import BondBrandingPanel from "./BondBrandingPanel";



// ── Mock bond markets data ────────────────────────────────────
const BOND_MARKETS = [
  {
    id: "waUSDC",
    asset: "waUSDC",
    name: "Wrapped Aave USDC",
    icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo.png",
    protocol: "AAVE",
    apy: 8.40,
    openInterest: 12_400_000,
    liquidity: 45_600_000,
    rangeMin: 4.20,
    rangeMax: 11.85,
  },
  {
    id: "waDAI",
    asset: "waDAI",
    name: "Wrapped Aave DAI",
    icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x6B175474E89094C44Da98b954EedeAC495271d0F/logo.png",
    protocol: "AAVE",
    apy: 7.85,
    openInterest: 8_200_000,
    liquidity: 28_900_000,
    rangeMin: 3.60,
    rangeMax: 10.42,
  },
  {
    id: "waUSDT",
    asset: "waUSDT",
    name: "Wrapped Aave USDT",
    icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xdAC17F958D2ee523a2206206994597C13D831ec7/logo.png",
    protocol: "AAVE",
    apy: 6.90,
    openInterest: 5_800_000,
    liquidity: 18_200_000,
    rangeMin: 2.80,
    rangeMax: 9.15,
  },
  {
    id: "wstETH",
    asset: "wstETH",
    name: "Wrapped stETH",
    icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84/logo.png",
    protocol: "Lido",
    apy: 3.42,
    openInterest: 24_100_000,
    liquidity: 89_500_000,
    rangeMin: 2.80,
    rangeMax: 5.20,
  },
  {
    id: "sDAI",
    asset: "sDAI",
    name: "Savings DAI",
    icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x6B175474E89094C44Da98b954EedeAC495271d0F/logo.png",
    protocol: "Maker",
    apy: 5.00,
    openInterest: 6_900_000,
    liquidity: 22_400_000,
    rangeMin: 3.10,
    rangeMax: 8.00,
  },
  {
    id: "maUSDC",
    asset: "maUSDC",
    name: "Morpho Aave USDC",
    icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo.png",
    protocol: "Morpho",
    apy: 9.12,
    openInterest: 3_400_000,
    liquidity: 14_800_000,
    rangeMin: 5.40,
    rangeMax: 12.60,
  },
];

// ── Formatters ────────────────────────────────────────────────
const formatUSD = (val) => {
  if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(0)}K`;
  return `$${val.toLocaleString()}`;
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

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sortedMarkets = useMemo(() => {
    const markets = [...BOND_MARKETS];
    markets.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string") return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === "asc" ? av - bv : bv - av;
    });
    return markets;
  }, [sortKey, sortDir]);

  const totalLiq = BOND_MARKETS.reduce((s, m) => s + m.liquidity, 0);

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
                    {BOND_MARKETS.length}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-sm text-gray-500 font-mono">
                  <span className="text-sm text-gray-500 uppercase tracking-widest">
                    TVL {formatUSD(totalLiq)}
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
                <button onClick={() => toggleSort("apy")} className="relative text-center hover:text-white transition-colors">
                  APY <span className="absolute ml-1 top-1/2 -translate-y-1/2"><SortIcon col="apy" sortKey={sortKey} sortDir={sortDir} /></span>
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

                  {/* APY */}
                  <div className="text-sm font-mono text-cyan-400 text-center">
                    {m.apy.toFixed(2)}%
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
                    <span className="text-gray-500">{m.rangeMin.toFixed(1)}%</span>
                    <span className="text-gray-700 mx-1">–</span>
                    <span className="text-white">{m.rangeMax.toFixed(1)}%</span>
                  </div>
                </div>
              ))}

              {/* Footer */}
              <div className="px-6 py-3 border-t border-white/5 bg-[#0a0a0a] flex justify-between items-center text-[10px] uppercase tracking-widest text-gray-600">
                <span>Showing {BOND_MARKETS.length} Markets</span>
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
