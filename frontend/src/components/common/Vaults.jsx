import React from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Vault,
  TrendingUp,
  Shield,
  Layers,
  Activity,
  Lock,
  Zap,
  BarChart3,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

/**
 * Vaults — Yield vault strategies page
 * Route: /vaults
 *
 * Design: Homepage terminal-panel aesthetic
 */

// ── MOCK VAULT DATA ───────────────────────────────────────────
const VAULTS = [
  {
    id: "001",
    name: "Fixed Yield",
    description:
      "Short RLP to lock a fixed borrow rate as yield. TWAMM unwinds the position over the chosen duration.",
    apy: 8.4,
    tvl: 12_500_000,
    asset: "USDC",
    protocol: "AAVE V3",
    risk: "LOW",
    status: "ACTIVE",
    accent: "cyan",
    icon: Lock,
  },
  {
    id: "002",
    name: "Delta Neutral",
    description:
      "Long RLP + short underlying. Captures funding rate spread while staying market-neutral on rate direction.",
    apy: 14.2,
    tvl: 8_200_000,
    asset: "USDC",
    protocol: "Morpho",
    risk: "MEDIUM",
    status: "ACTIVE",
    accent: "green",
    icon: Shield,
  },
  {
    id: "003",
    name: "Rate Arbitrage",
    description:
      "Cross-protocol rate arbitrage between Aave and Morpho. Captures the spread when rates diverge.",
    apy: 18.7,
    tvl: 4_800_000,
    asset: "USDC",
    protocol: "Multi",
    risk: "HIGH",
    status: "ACTIVE",
    accent: "pink",
    icon: Zap,
  },
  {
    id: "004",
    name: "Basis Trade",
    description:
      "Leveraged carry: sUSDe collateral, USDT debt. Long RLP hedges borrow cost spikes.",
    apy: 22.1,
    tvl: 3_100_000,
    asset: "sUSDe",
    protocol: "AAVE V3",
    risk: "HIGH",
    status: "ACTIVE",
    accent: "yellow",
    icon: TrendingUp,
  },
  {
    id: "005",
    name: "CDS Vault",
    description:
      "Parametric solvency insurance. Long RLP pays 6–10× on utilization cap events. Low premium, asymmetric upside.",
    apy: 6.8,
    tvl: 1_900_000,
    asset: "USDC",
    protocol: "Euler",
    risk: "MEDIUM",
    status: "ACTIVE",
    accent: "pink",
    icon: Shield,
  },
  {
    id: "006",
    name: "LP Vault",
    description:
      "Concentrated liquidity provision on the RLP–USDC pool. Mean-reverting rates minimize IL; earns swap fees.",
    apy: 11.5,
    tvl: 6_700_000,
    asset: "USDC",
    protocol: "Uniswap V4",
    risk: "LOW",
    status: "PAUSED",
    accent: "cyan",
    icon: Layers,
  },
];

const accentMap = {
  cyan: {
    text: "text-cyan-400",
    dot: "bg-cyan-400",
    glow: "shadow-[0_0_8px_rgba(34,211,238,0.4)]",
    border: "border-cyan-500/30",
    bg: "bg-cyan-500/5",
  },
  green: {
    text: "text-green-400",
    dot: "bg-green-500",
    glow: "shadow-[0_0_8px_#22c55e]",
    border: "border-green-500/30",
    bg: "bg-green-500/5",
  },
  pink: {
    text: "text-pink-400",
    dot: "bg-pink-500",
    glow: "shadow-[0_0_8px_#ec4899]",
    border: "border-pink-500/30",
    bg: "bg-pink-500/5",
  },
  yellow: {
    text: "text-yellow-400",
    dot: "bg-yellow-400",
    glow: "shadow-[0_0_8px_#eab308]",
    border: "border-yellow-500/30",
    bg: "bg-yellow-500/5",
  },
};

const riskColors = {
  LOW: "text-green-400",
  MEDIUM: "text-yellow-400",
  HIGH: "text-red-400",
};

function formatTVL(val) {
  if (val >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(0)}K`;
  return `$${val}`;
}

// ── HOW IT WORKS DIAGRAM ──────────────────────────────────────
const VaultDiagram = () => {
  const containerRef = React.useRef(null);
  const [visible, setVisible] = React.useState(false);

  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          obs.disconnect();
        }
      },
      { threshold: 0.3 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const step = (i) => ({
    opacity: visible ? 1 : 0,
    transform: visible ? "translateY(0)" : "translateY(18px)",
    transition: `opacity 0.5s cubic-bezier(0.16,1,0.3,1) ${i * 120}ms, transform 0.5s cubic-bezier(0.16,1,0.3,1) ${i * 120}ms`,
  });

  const hConnector = (label, i) => (
    <div
      className="flex flex-col items-center justify-center gap-1 shrink-0 px-1"
      style={step(i)}
    >
      {label && (
        <span className="text-[9px] text-gray-600 uppercase tracking-[0.2em]">
          {label}
        </span>
      )}
      <div className="flex items-center gap-0">
        <div className="w-1.5 h-1.5 border border-white/30 rotate-45" />
        <div
          className="w-10 h-px bg-gradient-to-r from-white/30 to-white/30"
          style={{
            backgroundImage:
              "repeating-linear-gradient(90deg, rgba(255,255,255,0.3) 0, rgba(255,255,255,0.3) 4px, transparent 4px, transparent 8px)",
          }}
        />
        <svg width="8" height="8" className="shrink-0">
          <polygon points="0,0 8,4 0,8" fill="white" fillOpacity="0.3" />
        </svg>
      </div>
    </div>
  );

  const panel = (title, subtitle, items, dotColor, stepIdx, id) => (
    <div
      className="border border-white/10 bg-[#080808] w-[180px] flex flex-col"
      style={step(stepIdx)}
    >
      <div className="px-4 py-2.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
        <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
          <div className={`w-1.5 h-1.5 ${dotColor}`} />
          {title}
        </span>
        <span className="text-[9px] text-gray-700 tracking-[0.15em]">{id}</span>
      </div>
      <div className="px-4 py-3 space-y-1.5 flex-1">
        {subtitle && (
          <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
            {subtitle}
          </div>
        )}
        {items.map((item) => (
          <div key={item} className="flex items-center gap-2">
            <div className={`w-1 h-1 ${dotColor} opacity-60`} />
            <span className="text-[10px] text-gray-500 uppercase tracking-widest">
              {item}
            </span>
          </div>
        ))}
      </div>
      <div className="px-4 py-2 border-t border-white/5 flex items-center justify-between">
        <span className="text-[9px] text-gray-700 uppercase tracking-[0.2em]">
          {title}
        </span>
        <div className={`w-1.5 h-1.5 ${dotColor} animate-pulse`} />
      </div>
    </div>
  );

  return (
    <div
      className="w-full flex items-center justify-center py-4"
      ref={containerRef}
    >
      <div className="flex items-stretch gap-0">
        {panel(
          "Deposit",
          "User Funds",
          ["USDC", "sUSDe", "DAI"],
          "bg-white",
          0,
          "::01",
        )}
        {hConnector("Allocate", 1)}
        {panel(
          "Vault",
          "Strategy Engine",
          ["Fixed Yield", "Delta Neutral", "Basis Trade"],
          "bg-green-500",
          2,
          "::02",
        )}
        {hConnector("Execute", 3)}
        {panel(
          "RLD Core",
          "CDP + Uniswap V4",
          ["Long/Short", "TWAMM", "Funding"],
          "bg-cyan-400",
          4,
          "::03",
        )}
        {hConnector("Yield", 5)}
        {panel(
          "Lending",
          "Yield Source",
          ["AAVE", "Morpho", "Euler"],
          "bg-green-500",
          6,
          "::04",
        )}
      </div>
    </div>
  );
};

// ── STRATEGY TABLE ────────────────────────────────────────────
const StrategyTable = () => {
  const [sortKey, setSortKey] = React.useState("apy");
  const [sortDir, setSortDir] = React.useState("desc");

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sorted = [...VAULTS].sort((a, b) => {
    const mul = sortDir === "asc" ? 1 : -1;
    if (sortKey === "name") return mul * a.name.localeCompare(b.name);
    if (sortKey === "apy") return mul * (a.apy - b.apy);
    if (sortKey === "tvl") return mul * (a.tvl - b.tvl);
    if (sortKey === "risk") {
      const order = { LOW: 0, MEDIUM: 1, HIGH: 2 };
      return mul * (order[a.risk] - order[b.risk]);
    }
    return 0;
  });

  const SortIcon = ({ col }) => {
    if (sortKey !== col)
      return <ChevronDown size={10} className="text-gray-700" />;
    return sortDir === "asc" ? (
      <ChevronUp size={10} className="text-white" />
    ) : (
      <ChevronDown size={10} className="text-white" />
    );
  };

  const headerCell = (label, key, align = "left") => (
    <th
      className={`py-3 px-4 text-[10px] font-bold uppercase tracking-widest text-gray-500 cursor-pointer hover:text-white transition-colors select-none ${align === "right" ? "text-right" : "text-left"}`}
      onClick={() => handleSort(key)}
    >
      <div
        className={`flex items-center gap-1 ${align === "right" ? "justify-end" : ""}`}
      >
        {label}
        <SortIcon col={key} />
      </div>
    </th>
  );

  return (
    <div className="border border-white/10 bg-[#080808] overflow-hidden">
      {/* Table Header */}
      <div className="px-5 py-3 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
        <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
          <BarChart3 size={12} className="text-gray-500" />
          Strategy_Comparison
        </span>
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]" />
          <span className="text-[10px] text-gray-600 uppercase tracking-[0.2em]">
            Live
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="border-b border-white/10">
            <tr>
              {headerCell("Strategy", "name")}
              {headerCell("APY", "apy", "right")}
              {headerCell("TVL", "tvl", "right")}
              {headerCell("Risk", "risk")}
              <th className="py-3 px-4 text-[10px] font-bold uppercase tracking-widest text-gray-500 text-left">
                Protocol
              </th>
              <th className="py-3 px-4 text-[10px] font-bold uppercase tracking-widest text-gray-500 text-center">
                Status
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {sorted.map((v) => {
              const colors = accentMap[v.accent];
              return (
                <tr
                  key={v.id}
                  className="hover:bg-white/[0.02] transition-colors cursor-pointer group"
                >
                  <td className="py-3.5 px-4">
                    <div className="flex items-center gap-2.5">
                      <div className={`w-1.5 h-1.5 ${colors.dot}`} />
                      <span className="text-[12px] text-white font-bold uppercase tracking-widest">
                        {v.name}
                      </span>
                    </div>
                  </td>
                  <td className="py-3.5 px-4 text-right">
                    <span
                      className={`text-[13px] font-mono font-bold ${colors.text}`}
                    >
                      {v.apy.toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-3.5 px-4 text-right">
                    <span className="text-[12px] font-mono text-gray-400">
                      {formatTVL(v.tvl)}
                    </span>
                  </td>
                  <td className="py-3.5 px-4">
                    <span
                      className={`text-[10px] font-bold uppercase tracking-widest ${riskColors[v.risk]}`}
                    >
                      {v.risk}
                    </span>
                  </td>
                  <td className="py-3.5 px-4">
                    <span className="text-[10px] text-gray-500 uppercase tracking-widest">
                      {v.protocol}
                    </span>
                  </td>
                  <td className="py-3.5 px-4 text-center">
                    <div className="flex items-center justify-center gap-1.5">
                      <div
                        className={`w-1.5 h-1.5 ${v.status === "ACTIVE" ? "bg-green-500 animate-pulse" : "bg-gray-600"}`}
                      />
                      <span
                        className={`text-[10px] uppercase tracking-widest ${v.status === "ACTIVE" ? "text-green-400" : "text-gray-600"}`}
                      >
                        {v.status}
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Table footer */}
      <div className="px-5 py-2.5 border-t border-white/10 bg-[#0a0a0a] flex items-center justify-between">
        <span className="text-[9px] text-gray-600 uppercase tracking-[0.2em]">
          {VAULTS.length} strategies
        </span>
        <span className="text-[9px] text-gray-600 uppercase tracking-[0.2em]">
          Sorted by {sortKey}
        </span>
      </div>
    </div>
  );
};

// ── MAIN COMPONENT ────────────────────────────────────────────
export default function Vaults() {
  const [heroVisible, setHeroVisible] = React.useState(false);
  React.useEffect(() => {
    const t = setTimeout(() => setHeroVisible(true), 100);
    return () => clearTimeout(t);
  }, []);

  const heroStep = (i) => ({
    opacity: heroVisible ? 1 : 0,
    transform: heroVisible ? "translateY(0)" : "translateY(18px)",
    transition: `opacity 0.6s cubic-bezier(0.16,1,0.3,1) ${i * 100}ms, transform 0.6s cubic-bezier(0.16,1,0.3,1) ${i * 100}ms`,
  });

  const totalTVL = VAULTS.reduce((sum, v) => sum + v.tvl, 0);
  const avgAPY = VAULTS.reduce((sum, v) => sum + v.apy, 0) / VAULTS.length;
  const activeCount = VAULTS.filter((v) => v.status === "ACTIVE").length;

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono">
      {/* ── HERO ── */}
      <section className="w-full px-6 md:px-24 pt-16 pb-12 border-b border-white/10 relative">
        <div className="absolute inset-0 pattern-grid opacity-10 pointer-events-none" />
        <div className="relative z-10 max-w-[1800px] mx-auto">
          {/* Title */}
          <div className="space-y-4 mb-10" style={heroStep(0)}>
            <div className="flex items-center gap-3 text-gray-600 text-[10px] font-bold tracking-[0.4em] uppercase">
              <div className="w-2 h-2 bg-green-500" />
              Vaults
            </div>
            <h1 className="text-3xl md:text-5xl font-bold tracking-tighter leading-[0.95] text-white uppercase">
              Automated Yield
              <br />
              Strategies
            </h1>
            <p className="text-sm text-gray-500 font-bold tracking-wide border-l-2 border-green-500/40 pl-4 max-w-lg">
              Deposit once. Let vault strategies compose RLD primitives — bonds,
              hedges, LP positions — to generate optimized yield.
            </p>
          </div>

          {/* Metrics */}
          <div
            className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-3xl"
            style={heroStep(1)}
          >
            {[
              {
                label: "Total TVL",
                value: formatTVL(totalTVL),
                icon: Layers,
                accent: "text-white",
              },
              {
                label: "Avg APY",
                value: `${avgAPY.toFixed(1)}%`,
                icon: TrendingUp,
                accent: "text-green-400",
              },
              {
                label: "Active Vaults",
                value: `${activeCount} / ${VAULTS.length}`,
                icon: Activity,
                accent: "text-cyan-400",
              },
            ].map((m) => (
              <div
                key={m.label}
                className="p-5 border border-white/10 bg-[#080808] flex flex-col justify-between"
              >
                <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-3 flex items-center justify-between">
                  {m.label}
                  <m.icon size={14} className="text-gray-600" />
                </div>
                <div
                  className={`text-2xl font-light tracking-tight ${m.accent}`}
                >
                  {m.value}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── VAULT CARDS GRID ── */}
      <section className="w-full px-6 md:px-24 py-16">
        <div className="max-w-[1800px] mx-auto">
          {/* Section header */}
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-3">
              <div className="w-2 h-2 bg-cyan-400" />
              <span className="text-[10px] font-bold tracking-[0.4em] uppercase text-cyan-400">
                All Vaults
              </span>
            </div>
            <span className="text-[9px] text-gray-700 uppercase tracking-[0.2em]">
              {VAULTS.length} strategies
            </span>
          </div>

          {/* Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {VAULTS.map((v) => {
              const colors = accentMap[v.accent];
              const Icon = v.icon;
              const isFixed = v.id === "001";
              const Wrapper = isFixed ? Link : "div";
              const wrapperProps = isFixed ? { to: "/vaults/fixed-yield" } : {};
              return (
                <Wrapper
                  key={v.id}
                  {...wrapperProps}
                  className="border border-white/10 bg-[#080808] hover:bg-white/[0.02] transition-all hover:border-white/20 group cursor-pointer block"
                >
                  {/* Card header */}
                  <div className="px-5 py-3 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
                    <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
                      <div className={`w-1.5 h-1.5 ${colors.dot}`} />
                      {v.name}
                    </span>
                    <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                      ::{v.id}
                    </span>
                  </div>

                  {/* Card body */}
                  <div className="px-5 py-4">
                    {/* Primary metrics row */}
                    <div className="flex items-baseline justify-between mb-4">
                      <div>
                        <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                          Strategy APY
                        </div>
                        <div
                          className={`text-2xl font-mono font-light tracking-tight ${colors.text}`}
                        >
                          {v.apy.toFixed(1)}%
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                          TVL
                        </div>
                        <div className="text-[13px] text-white font-mono">
                          {formatTVL(v.tvl)}
                        </div>
                      </div>
                    </div>

                    {/* Description */}
                    <p className="text-[11px] text-gray-500 leading-relaxed mb-4">
                      {v.description}
                    </p>

                    {/* Detail grid */}
                    <div className="grid grid-cols-3 gap-x-3 gap-y-2 pt-3 border-t border-white/5">
                      <div>
                        <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-0.5">
                          Asset
                        </div>
                        <div className="text-[11px] text-gray-400 uppercase tracking-widest">
                          {v.asset}
                        </div>
                      </div>
                      <div>
                        <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-0.5">
                          Risk
                        </div>
                        <div
                          className={`text-[11px] font-bold uppercase tracking-widest ${riskColors[v.risk]}`}
                        >
                          {v.risk}
                        </div>
                      </div>
                      <div>
                        <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-0.5">
                          Protocol
                        </div>
                        <div className="text-[11px] text-gray-400 uppercase tracking-widest">
                          {v.protocol}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Card footer */}
                  <div className="px-5 py-2.5 border-t border-white/5 flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div
                        className={`w-1.5 h-1.5 ${v.status === "ACTIVE" ? "bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]" : "bg-gray-600"}`}
                      />
                      <span
                        className={`text-[9px] uppercase tracking-widest ${v.status === "ACTIVE" ? "text-green-400" : "text-gray-600"}`}
                      >
                        {v.status}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 text-gray-600 group-hover:text-white transition-colors">
                      <span className="text-[9px] uppercase tracking-[0.2em]">
                        Details
                      </span>
                      <ArrowRight size={10} />
                    </div>
                  </div>
                </Wrapper>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── HOW IT WORKS ── */}
      <section className="w-full px-6 md:px-24 py-16 border-t border-white/10">
        <div className="max-w-[1800px] mx-auto">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-2 h-2 bg-white" />
            <span className="text-[10px] font-bold tracking-[0.4em] uppercase text-white">
              Architecture
            </span>
          </div>
          <h2 className="text-xl md:text-3xl font-bold tracking-tight text-white mb-2 uppercase">
            How Vaults Work
          </h2>
          <p className="text-sm text-gray-500 border-l-2 border-white/20 pl-4 mb-10 max-w-lg">
            Deposits flow through the vault strategy engine into RLD Core, which
            executes positions via Uniswap V4 and compounds yield from lending
            protocols.
          </p>

          {/* Diagram — desktop only */}
          <div className="hidden lg:block overflow-x-auto">
            <VaultDiagram />
          </div>

          {/* Mobile fallback */}
          <div className="lg:hidden space-y-3">
            {[
              {
                label: "01 · Deposit",
                desc: "User deposits USDC or sUSDe into the vault",
                dot: "bg-white",
              },
              {
                label: "02 · Vault Strategy",
                desc: "Allocates funds across Fixed Yield, Delta Neutral, or Basis Trade strategies",
                dot: "bg-green-500",
              },
              {
                label: "03 · RLD Core",
                desc: "Executes Long/Short positions via CDP Engine + Uniswap V4 TWAMM",
                dot: "bg-cyan-400",
              },
              {
                label: "04 · Lending",
                desc: "Yield sourced from AAVE, Morpho, and Euler protocols",
                dot: "bg-green-500",
              },
            ].map((s) => (
              <div
                key={s.label}
                className="border border-white/10 bg-[#080808] p-4 flex items-start gap-3"
              >
                <div className={`w-2 h-2 ${s.dot} mt-0.5 shrink-0`} />
                <div>
                  <div className="text-[11px] font-bold text-white uppercase tracking-widest mb-1">
                    {s.label}
                  </div>
                  <div className="text-[11px] text-gray-500 leading-relaxed">
                    {s.desc}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── STRATEGY TABLE ── */}
      <section className="w-full px-6 md:px-24 py-16 border-t border-white/10">
        <div className="max-w-[1800px] mx-auto">
          <div className="flex items-center gap-3 mb-8">
            <div className="w-2 h-2 bg-pink-500" />
            <span className="text-[10px] font-bold tracking-[0.4em] uppercase text-pink-400">
              Compare
            </span>
          </div>
          <StrategyTable />
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="w-full px-6 md:px-24 py-20 border-t border-white/10 relative">
        <div className="absolute inset-0 pattern-grid opacity-5 pointer-events-none" />
        <div className="relative z-10 max-w-[1800px] mx-auto text-center space-y-6">
          <div className="flex items-center justify-center gap-3 text-gray-600 text-[10px] font-bold tracking-[0.4em] uppercase">
            <div className="w-2 h-2 bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]" />
            Ready
          </div>
          <h2 className="text-2xl md:text-4xl font-bold tracking-tight text-white uppercase">
            Start Earning
          </h2>
          <p className="text-sm text-gray-500 max-w-md mx-auto">
            Connect your wallet, pick a vault, deposit. The strategy handles
            everything.
          </p>
          <div className="flex justify-center gap-4 pt-2">
            <a
              href="/bonds"
              className="border border-white/80 text-white px-6 py-3 text-[11px] uppercase tracking-[0.2em] font-bold hover:bg-white hover:text-black transition-all flex items-center gap-2"
            >
              Launch App <ArrowRight size={14} />
            </a>
            <a
              href="https://docs.rld.finance"
              target="_blank"
              rel="noopener noreferrer"
              className="border border-white/20 text-gray-400 px-6 py-3 text-[11px] uppercase tracking-[0.2em] font-bold hover:border-white/50 hover:text-white transition-all flex items-center gap-2"
            >
              Docs <ArrowRight size={14} />
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}
