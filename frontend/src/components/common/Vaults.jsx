import React from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Lock,
  Shield,
  TrendingUp,
  Zap,
  Layers,
} from "lucide-react";

/**
 * Vaults — Yield vault strategies directory
 * Route: /strategies
 *
 * Design: App-page style with consumer-oriented product cards
 */

// ── STRATEGY DATA ─────────────────────────────────────────────
const STRATEGIES = [
  {
    id: "001",
    slug: "fixed-yield",
    route: "/bonds",
    name: "Fixed Yield",
    headline: "Lock in a guaranteed rate",
    description: "Synthetic bonds: Earn a predictable, fixed return on your USDC.",
    apy: 8.4,
    tvl: 12_500_000,
    asset: "USDC",
    protocol: "AAVE V3",
    risk: "LOW",
    riskLabel: "Conservative",
    status: "ACTIVE",

    icon: Lock,
    linked: true,
    features: ["Fixed rate", "No liquidation risk", "Auto-compounding"],
  },
  {
    id: "002",
    slug: "delta-neutral",
    name: "Delta Neutral",
    headline: "Capitalize on market volatility",
    description: "Cointegration: wstETH + short interest rate to capture funding rate spreads.",
    apy: 14.2,
    tvl: 8_200_000,
    asset: "USDC",
    protocol: "Morpho",
    risk: "MEDIUM",
    riskLabel: "Balanced",
    status: "SOON",

    icon: Shield,
    linked: false,
    features: ["Market neutral", "Funding arbitrage", "Auto-rebalancing"],
  },
    {
    id: "003",
    slug: "basis-trade",
    name: "Basis Trade",
    headline: "Leveraged carry trade",
    description: "High-yield carry strategy using sUSDe collateral with built-in rate hedging.",
    apy: 22.1,
    tvl: 3_100_000,
    asset: "sUSDe",
    protocol: "AAVE V3",
    risk: "HIGH",
    riskLabel: "Aggressive",
    status: "ACTIVE",

    icon: TrendingUp,
    linked: true,
    features: ["High yield", "Rate hedged", "sUSDe native"],
  },
  {
    id: "004",
    slug: "rate-arbitrage",
    name: "Rate Arbitrage",
    headline: "Earn delta-neutral yield from rate arbitrage.",
    description: "Automatically captures yield spreads between lending protocols when rates diverge.",
    apy: 18.7,
    tvl: 4_800_000,
    asset: "USDC",
    protocol: "Multi",
    risk: "HIGH",
    riskLabel: "Aggressive",
    status: "SOON",

    icon: Zap,
    linked: false,
    features: ["Cross-protocol", "Automated execution", "Spread capture"],
  },
  {
    id: "005",
    slug: "cds-vault",
    name: "CDS Vault",
    headline: "Insure against pool failures",
    description: "Earn premiums by providing solvency insurance.",
    apy: 6.8,
    tvl: 1_900_000,
    asset: "USDC",
    protocol: "Euler",
    risk: "MEDIUM",
    riskLabel: "Balanced",
    status: "SOON",

    icon: Shield,
    linked: false,
    features: ["Asymmetric upside", "Low premium", "Parametric payout"],
  },
];

// ── ASSET LOGOS ───────────────────────────────────────────────
const ASSET_LOGOS = {
  USDC: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo.png",
  sUSDe: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x9D39A5DE30e57443BfF2A8307A4256c8797A3497/logo.png",
  DAI: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x6B175474E89094C44Da98b954EedeAC495271d0F/logo.png",
  USDT: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xdAC17F958D2ee523a2206206994597C13D831ec7/logo.png",
};

// ── RISK-BASED COLOR SYSTEM ───────────────────────────────────
// Conservative (LOW) = green, Balanced (MEDIUM) = cyan, Aggressive (HIGH) = pink
const riskAccent = {
  LOW: {
    text: "text-green-400", dot: "bg-green-500",
    bg: "bg-green-500/[0.06]", border: "border-green-500/20",
    tag: "text-green-400 bg-green-500/10 border-green-500/20",
  },
  MEDIUM: {
    text: "text-cyan-400", dot: "bg-cyan-400",
    bg: "bg-cyan-500/[0.06]", border: "border-cyan-500/20",
    tag: "text-cyan-400 bg-cyan-500/10 border-cyan-500/20",
  },
  HIGH: {
    text: "text-pink-400", dot: "bg-pink-500",
    bg: "bg-pink-500/[0.06]", border: "border-pink-500/20",
    tag: "text-pink-400 bg-pink-500/10 border-pink-500/20",
  },
};

const grayAccent = {
  text: "text-gray-500", dot: "bg-gray-600",
  bg: "bg-white/[0.02]", border: "border-white/10",
  tag: "text-gray-500 bg-white/5 border-white/10",
};

function formatTVL(val) {
  if (val >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(0)}K`;
  return `$${val}`;
}

// ── MAIN COMPONENT ────────────────────────────────────────────
export default function Vaults() {
  const totalTVL = STRATEGIES.reduce((sum, v) => sum + v.tvl, 0);
  const avgAPY = STRATEGIES.reduce((sum, v) => sum + v.apy, 0) / STRATEGIES.length;
  const activeCount = STRATEGIES.filter((v) => v.status === "ACTIVE").length;

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">

        {/* ── Header Metrics ── */}
        <div className="border border-white/10 grid grid-cols-1 lg:grid-cols-12">
          {/* Branding */}
          <div className="lg:col-span-4 flex flex-col justify-center p-6 border-b lg:border-b-0 lg:border-r border-white/10 min-h-[140px]">
            <div className="flex items-center gap-3 mb-2">
              <Layers size={18} className="text-cyan-400" />
              <h1 className="text-2xl font-medium tracking-tight">
                Strategies
              </h1>
            </div>
            <p className="text-sm text-gray-500 tracking-widest uppercase">
              {STRATEGIES.length} vault strategies · RLD Protocol
            </p>
          </div>

          {/* Metrics */}
          <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Total TVL
              </div>
              <div className="text-2xl font-light tracking-tight text-white">
                {formatTVL(totalTVL)}
              </div>
            </div>
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Avg APY
              </div>
              <div className="text-2xl font-light tracking-tight text-cyan-400">
                {avgAPY.toFixed(1)}%
              </div>
            </div>
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Active Vaults
              </div>
              <div className="text-2xl font-light tracking-tight text-white">
                {activeCount} / {STRATEGIES.length}
              </div>
            </div>
          </div>
        </div>

        {/* ── Strategy Columns by Risk Level ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {[
            { risk: "LOW", label: "Conservative", sublabel: "Lower risk, stable returns" },
            { risk: "MEDIUM", label: "Balanced", sublabel: "Moderate risk, higher yield" },
            { risk: "HIGH", label: "Aggressive", sublabel: "Higher risk, maximum yield" },
          ].map((col) => {
            const c = riskAccent[col.risk];
            const items = STRATEGIES.filter((s) => s.risk === col.risk);

            return (
              <div key={col.risk} className="flex flex-col gap-4">
                {/* Column Header */}
                <div className={`px-4 py-3 border ${c.border} ${c.bg} flex items-center justify-between`}>
                  <div className="flex items-center gap-2.5">
                    <div className={`w-2 h-2 ${c.dot}`} />
                    <div>
                      <span className={`text-[11px] font-bold uppercase tracking-widest ${c.text}`}>
                        {col.label}
                      </span>
                      <p className="text-[9px] text-gray-600 uppercase tracking-widest mt-0.5">
                        {col.sublabel}
                      </p>
                    </div>
                  </div>
                  <span className="text-[9px] text-gray-600 uppercase tracking-widest">
                    {items.length} {items.length === 1 ? "vault" : "vaults"}
                  </span>
                </div>

                {/* Cards */}
                {items.map((s) => {
                  const Icon = s.icon;
                  const isActive = s.status === "ACTIVE";
                  const sc = isActive ? c : grayAccent;
                  const Wrapper = s.linked ? Link : "div";
                  const wrapperProps = s.linked ? { to: s.route || `/strategies/${s.slug}` } : {};

                  return (
                    <Wrapper
                      key={s.id}
                      {...wrapperProps}
                      className={`relative border border-white/10 bg-[#080808] transition-all group block flex flex-col ${isActive ? "hover:bg-[#0a0a0a] hover:border-white/20 cursor-pointer" : "cursor-not-allowed"}`}
                    >
                      {/* Coming Soon tooltip (Homepage-style) */}
                      {!isActive && (
                        <div className="absolute -top-10 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none z-20">
                          <div className={`px-3 py-1.5 bg-[#0a0a0a] border ${sc.border} text-[9px] font-mono font-bold uppercase tracking-[0.25em] ${sc.text} whitespace-nowrap`}>
                            Coming Soon
                          </div>
                        </div>
                      )}
                      {/* ── APY Hero ── */}
                      <div className={`px-5 pt-5 pb-4 border-b border-white/5 ${sc.bg}`}>
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex items-center gap-2.5">
                            <div className={`w-9 h-9 border ${sc.border} flex items-center justify-center bg-[#080808]`}>
                              <Icon size={18} className={sc.text} />
                            </div>
                            <div>
                              <h3 className="text-sm font-bold text-white uppercase tracking-widest">
                                {s.name}
                              </h3>
                              <span className="text-[10px] text-gray-500 uppercase tracking-widest">
                                RLD · {s.protocol}
                              </span>
                            </div>
                          </div>
                          <img
                            src={ASSET_LOGOS[s.asset] || ASSET_LOGOS.USDC}
                            alt={s.asset}
                            className="w-9 h-9 rounded-full object-contain opacity-80 group-hover:opacity-100 transition-opacity"
                          />
                        </div>
                        <div className="flex items-end justify-between">
                          <div>
                            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-0.5">
                              Projected APY
                            </div>
                            <div className={`text-3xl font-mono font-light tracking-tight ${sc.text}`}>
                              {isActive ? `${s.apy.toFixed(1)}%` : "—"}
                            </div>
                          </div>
                          <div className="text-right">
                            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-0.5">
                              TVL
                            </div>
                            <div className="text-lg font-mono text-white tracking-tight">
                              {isActive ? formatTVL(s.tvl) : "—"}
                            </div>
                          </div>
                        </div>
                      </div>

                      {/* ── Content ── */}
                      <div className="px-5 py-4 flex-1 flex flex-col">
                        <p className="text-sm text-white font-bold mb-1.5">
                          {s.headline}
                        </p>
                        <p className="text-[11px] text-gray-500 leading-relaxed mb-4">
                          {s.description}
                        </p>
                        <div className="flex flex-wrap gap-1.5 mb-4">
                          {s.features.map((f) => (
                            <span
                              key={f}
                              className="text-[9px] text-gray-400 uppercase tracking-widest border border-white/10 px-2 py-0.5 bg-white/[0.02]"
                            >
                              {f}
                            </span>
                          ))}
                        </div>
                        <div className="mt-auto flex items-center justify-between">
                          <span className={`text-[10px] font-bold uppercase tracking-widest ${sc.text} ${sc.bg} border ${sc.border} px-2 py-0.5`}>
                            {s.riskLabel}
                          </span>
                          <div className="flex items-center gap-1.5">
                            <div className={`w-1.5 h-1.5 ${isActive ? "bg-green-500 animate-pulse shadow-[0_0_6px_rgba(34,197,94,0.5)]" : "bg-gray-600"}`} />
                            <span className={`text-[10px] uppercase tracking-widest ${isActive ? "text-green-400" : "text-gray-600"}`}>
                              {isActive ? "Live" : "Coming Soon"}
                            </span>
                          </div>
                        </div>
                      </div>

                      {/* ── CTA Footer ── */}
                      <div className="px-5 py-3 border-t border-white/5 flex items-center justify-between bg-[#070707]">
                        <span className="text-[10px] text-gray-600 uppercase tracking-widest">
                          {s.asset} Deposit
                        </span>
                        {isActive ? (
                          <div className="flex items-center gap-1.5 text-gray-500 group-hover:text-white transition-colors">
                            <span className="text-[10px] font-bold uppercase tracking-[0.2em]">
                              View Strategy
                            </span>
                            <ArrowRight size={12} className="group-hover:translate-x-0.5 transition-transform" />
                          </div>
                        ) : (
                          <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-gray-700">
                            Coming Soon
                          </span>
                        )}
                      </div>
                    </Wrapper>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
