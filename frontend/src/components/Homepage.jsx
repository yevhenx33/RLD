import React from "react";
import {
  ArrowRight,
  TrendingUp,
  Shield,
  Zap,
  Target,
  BarChart3,
  Briefcase,
  GitBranch,
} from "lucide-react";
import RLDPerformanceChart from "./RLDChart";
import ratesCsv from "../assets/aave_usdc_rates_full_history_2026-01-27.csv?raw";

/**
 * Homepage — Pitch Deck (concise, aligned to RLD Whitepaper)
 * Route: /
 */

const TWAR_WINDOW = 3600; // 1 hour smoothing

/** Pearson correlation */
function calculateCorrelation(x, y) {
  if (x.length !== y.length || x.length === 0) return 0;
  const n = x.length;
  const sumX = x.reduce((a, b) => a + b, 0);
  const sumY = y.reduce((a, b) => a + b, 0);
  const sumXY = x.reduce((sum, xi, i) => sum + xi * y[i], 0);
  const sumX2 = x.reduce((sum, xi) => sum + xi * xi, 0);
  const sumY2 = y.reduce((sum, yi) => sum + yi * yi, 0);
  const num = n * sumXY - sumX * sumY;
  const den = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
  return den === 0 ? 0 : num / den;
}

const slides = [
  {
    index: "01",
    label: "PROBLEM",
    title: "Rates are volatile, untradeable, unhedgeable.",
    body: "$50B+ in DeFi lending. Very limited protection with small fragmented liquidity and high slippage.",
    bullets: [
      "LPs want fixed predictable yield",
      "Carry traders need fixed borrowing cost to lock margins",
      "General demand for solvency protection (CDS)",
    ],
    accent: "red",
    visual: "chart",
    cta: { label: "Launch App", href: "/bonds" },
  },
  {
    index: "02",
    label: "MECHANISM",
    title: "A perpetual that tracks the cost of money.",
    body: "CDP-based perpetual futures:",
    bullets: [
      "Oracle: USDC borrowing rates from lending protocols",
      "Price: 100 × Rate",
      "5% -> 10% -> 2x on notional",
      "No expirations and liquidity fragmentation",
    ],
    accent: "cyan",
    visual: "diagram",
    cta: { label: "Launch App", href: "/bonds" },
  },
  {
    index: "03",
    label: "FIXED_YIELD",
    title: "Fixed yield. Any duration. One pool.",
    body: "Deposit + short RLP to create synthetic bonds.",
    bullets: [
      "Demand: fixed-yield generation",
      "Problem: Rate Volatility",
      "Solution: Short RLP to fix yield + receive funding",
      "Result: 1 pool, any duration, no fragmentation",
    ],
    accent: "yellow",
    visual: "bonds",
    cta: { label: "Launch App", href: "/bonds" },
  },
  {
    index: "04",
    label: "FIXED_BORROWING",
    title: "Lock your cost of capital.",
    body: "Buy Long RLP to pre-pay interest at today's rate. Rate spikes offset by hedge profit.",
    bullets: [
      "Leveraged basis-trade: Collateral: sUSDe, Debt: USDT",
      "Problem: USDC borrowing cost goes up → strategy unprofitable",
      "Solution: buy long RLP to fix interest rate costs",
    ],
    accent: "green",
    visual: "basis",
    cta: { label: "Launch App", href: "/bonds" },
  },
  {
    index: "05",
    label: "RATE_PERPS",
    title: "Rate-Level Perps",
    body: "Go long/short on USDC borrowing cost to capitalize on:",
    bullets: [
      "Natural interest rate asymmetry",
      "USDC rate and ETH price cointegration",
      "Cross-rates arbitrage",
    ],
    accent: "cyan",
    visual: "perps",
    icon: <TrendingUp size={20} />,
    cta: { label: "Launch App", href: "/bonds" },
  },
  {
    index: "06",
    label: "CDS",
    title: "Parametric insurance. Trustless.",
    body: "Default = 100% utilization → rate cap → Long RLP pays out 6–10× instantly. No claims, no disputes.",
    accent: "pink",
    visual: "stream",
    cta: { label: "Launch App", href: "/bonds" },
  },
  {
    index: "07",
    label: "LP_STRUCTURE",
    title: "Mean-reverting rates. LP paradise.",
    body: "Rates oscillate 4–15%. Concentrated ranges earn consistent fees, no long-term IL. One pool serves every maturity.",
    icon: <GitBranch size={20} />,
    accent: "cyan",
    visual: "rates",
    cta: { label: "Launch App", href: "/bonds" },
  },
];

const accentMap = {
  red: { text: "text-red-400", border: "border-red-500/40", dot: "bg-red-500" },
  cyan: {
    text: "text-cyan-400",
    border: "border-cyan-500/40",
    dot: "bg-cyan-400",
  },
  yellow: {
    text: "text-yellow-400",
    border: "border-yellow-500/40",
    dot: "bg-yellow-400",
  },
  green: {
    text: "text-green-400",
    border: "border-green-500/40",
    dot: "bg-green-400",
  },
  purple: {
    text: "text-purple-400",
    border: "border-purple-500/40",
    dot: "bg-purple-400",
  },
  pink: {
    text: "text-pink-400",
    border: "border-pink-500/40",
    dot: "bg-pink-400",
  },
};

/** Pre-process CSV into the same format App.jsx uses for RLDPerformanceChart */
function buildChartData() {
  if (!ratesCsv) return { chartData: [], correlation: 0 };
  const lines = ratesCsv.trim().split("\n");

  // Parse all hourly rows
  const hourly = [];
  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(",");
    if (!parts[0]) continue;
    hourly.push({
      timestamp: parseInt(parts[0], 10),
      apy: parseFloat(parts[2]),
      eth_price: parseFloat(parts[4]),
    });
  }

  // Compute TWAR with 3600s sliding window, then downsample to daily
  const result = [];
  const historyQueue = [];
  let runningArea = 0;
  let runningTime = 0;

  for (let i = 0; i < hourly.length; i++) {
    const cur = hourly[i];
    const prevTs = i > 0 ? hourly[i - 1].timestamp : cur.timestamp;
    let dt = cur.timestamp - prevTs;
    if (dt < 0) dt = 0;
    const stepArea = cur.apy * dt;
    historyQueue.push({ dt, area: stepArea, timestamp: cur.timestamp });
    runningArea += stepArea;
    runningTime += dt;

    while (
      historyQueue.length > 0 &&
      cur.timestamp - historyQueue[0].timestamp > TWAR_WINDOW
    ) {
      const removed = historyQueue.shift();
      runningArea -= removed.area;
      runningTime -= removed.dt;
    }
    const twar =
      runningTime > 0 ? Math.max(0, runningArea / runningTime) : cur.apy;

    // Downsample: keep every 24th point (daily)
    if (i % 24 === 0) {
      result.push({
        timestamp: cur.timestamp,
        apy: cur.apy,
        twar,
        ethPrice: cur.eth_price || null,
      });
    }
  }

  // Correlation
  const apys = result.map((d) => d.apy);
  const prices = result.map((d) => d.ethPrice || 0);
  const corr = calculateCorrelation(apys, prices);

  return { chartData: result, correlation: corr };
}

const { chartData: STATIC_CHART_DATA } = buildChartData();

// Jan 1, 2025 – Jan 27, 2026 filtered subset
const START_2025 = 1735689600; // Jan 1, 2025 00:00:00 UTC
const STATIC_CHART_DATA_2025 = STATIC_CHART_DATA.filter(
  (d) => d.timestamp >= START_2025,
);

const CHART_AREAS = [
  { key: "apy", name: "Spot", color: "#22d3ee" },
  { key: "ethPrice", name: "ETH Price", color: "#a1a1aa", yAxisId: "right" },
  { key: "twar", name: "TWAR", color: "#ec4899" },
];

// 95th percentile band for rate oscillation chart (slide 06)
const RATE_AREAS = [{ key: "apy", name: "Borrow Rate", color: "#22d3ee" }];
const STATIC_CHART_DATA_WEEKLY = STATIC_CHART_DATA.filter(
  (_, i) => i % 7 === 0,
);
const sortedApys = STATIC_CHART_DATA.map((d) => d.apy)
  .filter((v) => v != null)
  .sort((a, b) => a - b);
const p2_5 = sortedApys[Math.floor(sortedApys.length * 0.025)];
const p97_5 = sortedApys[Math.floor(sortedApys.length * 0.975)];
const RATE_REF_LINES = [
  { y: p2_5, stroke: "#a1a1aa", label: `P2.5 ${p2_5.toFixed(1)}%` },
  { y: p97_5, stroke: "#ef4444", label: `P97.5 ${p97_5.toFixed(1)}%` },
];

const RateChartPanel = () => (
  <div className="flex flex-col w-full h-full">
    <div className="flex justify-between items-end mb-2 px-1">
      <div className="flex gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-cyan-400" />
          <span className="text-[10px] uppercase tracking-widest">
            Borrow_Rate
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-0 border-t border-dashed border-zinc-400" />
          <span className="text-[10px] uppercase tracking-widest text-zinc-400">
            P2.5
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-0 border-t border-dashed border-red-500" />
          <span className="text-[10px] uppercase tracking-widest text-red-400">
            P97.5
          </span>
        </div>
      </div>
      <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest">
        AAVE V3 · 3Y · 95th Pctl
      </span>
    </div>
    <div className="flex-1 min-h-0 w-full border border-white/10 p-3 bg-[#080808]">
      <RLDPerformanceChart
        data={STATIC_CHART_DATA_WEEKLY}
        resolution="1W"
        areas={RATE_AREAS}
        referenceLines={RATE_REF_LINES}
      />
    </div>
  </div>
);

const PERPS_CHART_AREAS = [
  { key: "apy", name: "USDC Rate", color: "#ec4899" },
  { key: "ethPrice", name: "ETH Price", color: "#22d3ee", yAxisId: "right" },
];

const PerpsChartPanel = () => (
  <div className="flex flex-col w-full h-full">
    <div className="flex justify-between items-end mb-2 px-1">
      <div className="flex gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-pink-500" />
          <span className="text-[10px] uppercase tracking-widest">
            USDC Rate
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-cyan-400" />
          <span className="text-[10px] uppercase tracking-widest">
            ETH Price
          </span>
        </div>
      </div>
    </div>
    <div className="flex-1 min-h-0 w-full border border-white/10 p-3 bg-[#080808]">
      <RLDPerformanceChart
        data={STATIC_CHART_DATA_2025}
        resolution="1D"
        areas={PERPS_CHART_AREAS}
      />
    </div>
    <div className="flex justify-between items-center mt-2 px-1">
      <div className="flex items-center gap-2">
        <div className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" />
        <span className="text-[10px] uppercase tracking-widest text-green-500 font-bold">
          Live Feed
        </span>
      </div>
      <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest">
        Jan 1, 25 – Jan 1, 26 Data
      </span>
    </div>
  </div>
);

/** Reusable chart panel */
const ChartPanel = ({ data, label }) => (
  <div className="flex flex-col w-full h-full">
    <div className="flex justify-between items-end mb-2 px-1">
      <div className="flex gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-cyan-400" />
          <span className="text-[10px] uppercase tracking-widest">
            Spot_Rate
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-pink-500" />
          <span className="text-[10px] uppercase tracking-widest">
            RATE_TWAR_1H
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-zinc-400" />
          <span className="text-[10px] uppercase tracking-widest">
            ETH_Price
          </span>
        </div>
      </div>
      <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest">
        {label}
      </span>
    </div>
    <div className="flex-1 min-h-0 w-full border border-white/10 p-3 bg-[#080808]">
      <RLDPerformanceChart data={data} resolution="1D" areas={CHART_AREAS} />
    </div>
  </div>
);

/** Dashed-box helper */
const DBox = ({ children, className = "" }) => (
  <div
    className={`border border-dashed border-white/30 px-5 py-2.5 text-[11px] uppercase tracking-widest text-white text-center whitespace-nowrap ${className}`}
  >
    {children}
  </div>
);

/** Protocol architecture diagram — hero-style terminal panel cards */
const MechanismDiagram = () => {
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

  /* shared transition style per step index */
  const step = (i) => ({
    opacity: visible ? 1 : 0,
    transform: visible ? "translateY(0)" : "translateY(18px)",
    transition: `opacity 0.5s cubic-bezier(0.16,1,0.3,1) ${i * 120}ms, transform 0.5s cubic-bezier(0.16,1,0.3,1) ${i * 120}ms`,
  });

  /* Horizontal connector arrow */
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

  return (
    <div
      className="w-full h-full flex items-center justify-center"
      ref={containerRef}
    >
      <div className="flex items-stretch gap-0">
        {/* ── ORACLE PANEL ── */}
        <div
          className="border border-white/10 bg-[#080808] w-[190px] flex flex-col"
          style={step(0)}
        >
          <div className="px-4 py-2.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
            <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-green-500" />
              Oracle
            </span>
            <span className="text-[9px] text-gray-700 tracking-[0.15em]">
              ::01
            </span>
          </div>
          <div className="px-4 py-3 space-y-1.5 flex-1">
            {["AAVE", "Morpho", "Euler", "Fluid"].map((p) => (
              <div key={p} className="flex items-center gap-2">
                <div className="w-1 h-1 bg-green-500/60" />
                <span className="text-[10px] text-gray-500 uppercase tracking-widest">
                  {p}
                </span>
              </div>
            ))}
          </div>
          <div className="px-4 py-2 border-t border-white/5 flex items-center justify-between">
            <span className="text-[9px] text-gray-700 uppercase tracking-[0.2em]">
              Rate_Feeds
            </span>
            <div className="w-1.5 h-1.5 bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]" />
          </div>
        </div>

        {hConnector("Rates", 1)}

        {/* ── CDP ENGINE PANEL ── */}
        <div className="relative" style={step(2)}>
          {/* Short label — positioned above */}
          <div className="absolute bottom-full left-1/2 -translate-x-1/2 flex flex-col items-center mb-2">
            <div className="border border-pink-500/30 bg-pink-500/5 px-4 py-1.5 flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-pink-500" />
              <span className="text-[10px] text-pink-400 font-bold uppercase tracking-[0.2em]">
                Short
              </span>
            </div>
            <div className="h-4 w-px bg-pink-500/30" />
            <svg width="8" height="6">
              <polygon points="0,0 8,0 4,6" fill="#ec4899" fillOpacity="0.5" />
            </svg>
          </div>

          <div className="border border-white/10 bg-[#080808] w-[190px] flex flex-col">
            <div className="px-4 py-2.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
                <div className="w-1.5 h-1.5 bg-white" />
                CDP_Engine
              </span>
              <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                ::02
              </span>
            </div>
            <div className="px-4 py-3 flex-1">
              <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                Index Price
              </div>
              <div className="text-lg text-white font-mono font-light tracking-tight">
                100 × Rate
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-3 pt-2.5 border-t border-white/5">
                {["Funding", "Margin", "Settle", "Liq."].map((t) => (
                  <div key={t} className="flex items-center gap-1.5">
                    <div className="w-1 h-1 bg-white/40" />
                    <span className="text-[10px] text-gray-500 uppercase tracking-widest">
                      {t}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div className="px-4 py-2 border-t border-white/5 flex items-center justify-between">
              <span className="text-[9px] text-gray-700 uppercase tracking-[0.2em]">
                Perpetual
              </span>
              <div className="w-1.5 h-1.5 bg-cyan-400 animate-pulse" />
            </div>
          </div>
        </div>

        {hConnector("Trade", 3)}

        {/* ── UNISWAP V4 POOL PANEL ── */}
        <div className="relative" style={step(4)}>
          <div className="border border-white/10 bg-[#080808] w-[190px] flex flex-col">
            <div className="px-4 py-2.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-cyan-400 flex items-center gap-2">
                <div className="w-1.5 h-1.5 bg-cyan-400" />
                Uniswap_V4
              </span>
              <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                ::03
              </span>
            </div>
            <div className="px-4 py-3 flex-1">
              <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                Pool
              </div>
              <div className="text-lg text-white font-mono font-light tracking-tight">
                RLP — USDC
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-3 pt-2.5 border-t border-white/5">
                {["Market", "Limit", "TWAP", "LP"].map((t) => (
                  <div key={t} className="flex items-center gap-1.5">
                    <div className="w-1 h-1 bg-cyan-400/60" />
                    <span className="text-[10px] text-gray-500 uppercase tracking-widest">
                      {t}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div className="px-4 py-2 border-t border-white/5 flex items-center justify-between">
              <span className="text-[9px] text-gray-700 uppercase tracking-[0.2em]">
                Concentrated_LP
              </span>
              <div className="w-1.5 h-1.5 bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]" />
            </div>
          </div>

          {/* Long label — positioned below */}
          <div className="absolute top-full left-1/2 -translate-x-1/2 flex flex-col items-center mt-2">
            <svg width="8" height="6">
              <polygon points="0,6 8,6 4,0" fill="#22d3ee" fillOpacity="0.5" />
            </svg>
            <div className="h-4 w-px bg-cyan-400/30" />
            <div className="border border-cyan-400/30 bg-cyan-400/5 px-4 py-1.5 flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-cyan-400" />
              <span className="text-[10px] text-cyan-400 font-bold uppercase tracking-[0.2em]">
                Long
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

/** TWAMM bond duration diagram */
const BOND_DURATIONS = [
  { label: "30D", fill: 0.15 },
  { label: "90D", fill: 0.35 },
  { label: "1Y", fill: 0.65 },
  { label: "5Y", fill: 1.0 },
];

const BondsDiagram = () => {
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

  return (
    <div
      className="w-full h-full flex items-center justify-center"
      ref={containerRef}
    >
      <div
        className="flex flex-col items-center gap-0"
        style={{ transform: "scale(1.25)", transformOrigin: "center" }}
      >
        {/* ── TOP: POOL PANEL ── */}
        <div
          className="border border-white/10 bg-[#080808] w-full"
          style={step(0)}
        >
          <div className="px-4 py-2 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
            <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-yellow-400 flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-yellow-400" />
              Single_Pool
            </span>
            <span className="text-[9px] text-gray-700 tracking-[0.15em]">
              RLP — USDC
            </span>
          </div>
          <div className="px-4 py-2 flex items-center justify-between">
            <span className="text-[9px] text-gray-700 uppercase tracking-[0.2em]">
              Any_Duration
            </span>
            <div className="w-1.5 h-1.5 bg-yellow-400 animate-pulse shadow-[0_0_8px_#eab308]" />
          </div>
        </div>

        {/* ── VERTICAL CONNECTORS ── */}
        <div className="flex items-start gap-3">
          {BOND_DURATIONS.map((d, i) => (
            <div
              key={i}
              className="flex flex-col items-center"
              style={step(1 + i)}
            >
              {/* Connector line */}
              <div className="h-5 w-px bg-yellow-500/30" />
              <svg width="8" height="6">
                <polygon
                  points="0,0 8,0 4,6"
                  fill="#eab308"
                  fillOpacity="0.5"
                />
              </svg>

              {/* ── DURATION CARD ── */}
              <div className="border border-white/10 bg-[#080808] w-[120px] mt-0.5">
                <div className="px-3 py-1.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
                  <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
                    <div className="w-1.5 h-1.5 bg-yellow-400" />
                    {d.label}
                  </span>
                  <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                    ::0{i + 1}
                  </span>
                </div>
                <div className="px-3 py-2">
                  <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                    Unwind
                  </div>
                  <div className="h-1.5 w-full bg-white/5 border border-white/10">
                    <div
                      className="h-full bg-yellow-500/50"
                      style={{ width: `${d.fill * 100}%` }}
                    />
                  </div>
                </div>
                <div className="px-3 py-1.5 border-t border-white/5 flex items-center justify-between">
                  <span className="text-[9px] text-gray-700 uppercase tracking-[0.2em]">
                    TWAMM
                  </span>
                  <div className="w-1 h-1 bg-green-500/60" />
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* ── TIMELINE LABEL ── */}
        <div
          className="mt-3 w-full flex items-center justify-between text-[9px] text-gray-600 uppercase tracking-[0.2em]"
          style={step(5)}
        >
          <span>← 1 block</span>
          <div className="flex-1 mx-2 border-t border-dashed border-white/10" />
          <span>5 years →</span>
        </div>
      </div>
    </div>
  );
};
/** Leveraged basis trade diagram */
const BasisTradeDiagram = () => {
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

  /* Horizontal connector */
  const hConnector = (labelTop, labelBottom) => (
    <div className="flex flex-col items-center gap-1 shrink-0 px-1">
      <div className="flex items-center gap-1">
        <span className="text-[9px] text-gray-600 uppercase tracking-[0.2em]">
          {labelTop}
        </span>
        <div className="flex items-center gap-0">
          <div className="w-1.5 h-1.5 border border-white/30 rotate-45" />
          <div
            className="w-8 h-px"
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
      <div className="flex items-center gap-1">
        <div className="flex items-center gap-0">
          <svg width="8" height="8" className="shrink-0 rotate-180">
            <polygon points="0,0 8,4 0,8" fill="white" fillOpacity="0.3" />
          </svg>
          <div
            className="w-8 h-px"
            style={{
              backgroundImage:
                "repeating-linear-gradient(90deg, rgba(255,255,255,0.3) 0, rgba(255,255,255,0.3) 4px, transparent 4px, transparent 8px)",
            }}
          />
          <div className="w-1.5 h-1.5 border border-white/30 rotate-45" />
        </div>
        <span className="text-[9px] text-gray-600 uppercase tracking-[0.2em]">
          {labelBottom}
        </span>
      </div>
    </div>
  );

  return (
    <div
      className="w-full h-full flex items-center justify-center"
      ref={containerRef}
    >
      <div
        className="flex flex-col items-center"
        style={{ transform: "scale(1.25)", transformOrigin: "center" }}
      >
        {/* Top row: Trader ←→ Lending */}
        <div className="flex items-center gap-0" style={step(0)}>
          {/* Trader Panel */}
          <div className="border border-white/10 bg-[#080808] w-[120px]">
            <div className="px-3 py-1.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
                <div className="w-1.5 h-1.5 bg-white" />
                Trader
              </span>
              <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                ::01
              </span>
            </div>
            <div className="px-3 py-2">
              <div className="text-[9px] text-gray-500 uppercase tracking-widest">
                Basis_Trade
              </div>
            </div>
          </div>

          {hConnector("Deposit_sUSDe", "Borrow_USDT")}

          {/* Lending Panel */}
          <div className="border border-white/10 bg-[#080808] w-[140px]">
            <div className="px-3 py-1.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-green-400 flex items-center gap-2">
                <div className="w-1.5 h-1.5 bg-green-500" />
                Lending
              </span>
              <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                ::02
              </span>
            </div>
            <div className="px-3 py-2">
              <div className="text-[9px] text-gray-500 uppercase tracking-widest">
                AAVE / Morpho
              </div>
            </div>
          </div>
        </div>

        {/* Vertical: Rate risk */}
        <div className="flex flex-col items-center" style={step(1)}>
          <div className="h-3 w-px bg-white/20" />
          <div className="border border-red-500/20 bg-red-500/5 px-4 py-1.5 pb-2.5">
            <span className="text-[9px] text-red-400 uppercase tracking-[0.15em]">
              ⚠ Rate_spike → margin_squeezed
            </span>
          </div>
          <div className="h-3 w-px bg-white/20" />
          <svg width="8" height="6">
            <polygon points="0,0 8,0 4,6" fill="white" fillOpacity="0.35" />
          </svg>
        </div>

        {/* Hedge: Long RLP */}
        <div
          className="border border-white/10 bg-[#080808] w-[160px] mt-0.5"
          style={step(2)}
        >
          <div className="px-3 py-1.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
            <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-green-400 flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-green-500" />
              Long_RLP
            </span>
            <span className="text-[9px] text-gray-700 tracking-[0.15em]">
              ::03
            </span>
          </div>
          <div className="px-3 py-1.5 flex items-center justify-between">
            <span className="text-[9px] text-gray-700 uppercase tracking-[0.2em]">
              Rate_Hedge
            </span>
          </div>
        </div>

        {/* Connector down */}
        <div className="flex flex-col items-center" style={step(3)}>
          <div className="h-4 w-px bg-green-500/30" />
          <svg width="8" height="6">
            <polygon points="0,0 8,0 4,6" fill="#22c55e" fillOpacity="0.5" />
          </svg>
        </div>

        {/* Result */}
        <div
          className="border border-green-500/20 bg-[#080808] w-[220px] mt-0.5"
          style={step(4)}
        >
          <div className="px-3 py-1.5 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
            <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-green-400 flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-green-500" />
              Result
            </span>
            <div className="w-1.5 h-1.5 bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]" />
          </div>
          <div className="px-3 py-2 text-center">
            <div className="text-[9px] text-green-400 uppercase tracking-widest mb-1">
              Rate Up → RLP Profit Offsets Cost
            </div>
            <div className="text-[10px] text-white font-bold uppercase tracking-[0.15em]">
              = Fixed_Borrowing_Cost
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

/** Stream Finance crisis data (daily, from euler_stream_case.csv, values in $M) */
const STREAM_DATA = [
  {
    timestamp: 1758668400,
    borrowApy: 5.0,
    supplyApy: 0.0,
    totalBorrows: 0.0,
    totalDeposits: 0.0,
  },
  {
    timestamp: 1758754800,
    borrowApy: 5.0,
    supplyApy: 0.0,
    totalBorrows: 0.0,
    totalDeposits: 0.0,
  },
  {
    timestamp: 1758841200,
    borrowApy: 19.92,
    supplyApy: 16.19,
    totalBorrows: 19.44,
    totalDeposits: 19.81,
  },
  {
    timestamp: 1758927600,
    borrowApy: 10.22,
    supplyApy: 7.54,
    totalBorrows: 30.57,
    totalDeposits: 36.27,
  },
  {
    timestamp: 1759014000,
    borrowApy: 10.75,
    supplyApy: 8.71,
    totalBorrows: 42.09,
    totalDeposits: 45.44,
  },
  {
    timestamp: 1759100400,
    borrowApy: 10.29,
    supplyApy: 7.69,
    totalBorrows: 42.72,
    totalDeposits: 50.0,
  },
  {
    timestamp: 1759186800,
    borrowApy: 11.38,
    supplyApy: 9.48,
    totalBorrows: 47.58,
    totalDeposits: 50.0,
  },
  {
    timestamp: 1759273200,
    borrowApy: 10.87,
    supplyApy: 9.0,
    totalBorrows: 51.07,
    totalDeposits: 54.01,
  },
  {
    timestamp: 1759359600,
    borrowApy: 10.89,
    supplyApy: 9.04,
    totalBorrows: 51.03,
    totalDeposits: 53.79,
  },
  {
    timestamp: 1759446000,
    borrowApy: 10.79,
    supplyApy: 8.8,
    totalBorrows: 66.49,
    totalDeposits: 71.29,
  },
  {
    timestamp: 1759532400,
    borrowApy: 10.71,
    supplyApy: 8.62,
    totalBorrows: 92.02,
    totalDeposits: 100.0,
  },
  {
    timestamp: 1759618800,
    borrowApy: 10.72,
    supplyApy: 8.64,
    totalBorrows: 92.13,
    totalDeposits: 100.02,
  },
  {
    timestamp: 1759705200,
    borrowApy: 10.72,
    supplyApy: 8.64,
    totalBorrows: 92.16,
    totalDeposits: 100.04,
  },
  {
    timestamp: 1759791600,
    borrowApy: 10.71,
    supplyApy: 8.63,
    totalBorrows: 92.11,
    totalDeposits: 100.06,
  },
  {
    timestamp: 1759878000,
    borrowApy: 10.21,
    supplyApy: 7.74,
    totalBorrows: 96.86,
    totalDeposits: 115.0,
  },
  {
    timestamp: 1759964400,
    borrowApy: 10.22,
    supplyApy: 7.53,
    totalBorrows: 96.89,
    totalDeposits: 115.0,
  },
  {
    timestamp: 1760050800,
    borrowApy: 10.19,
    supplyApy: 7.47,
    totalBorrows: 96.35,
    totalDeposits: 115.01,
  },
  {
    timestamp: 1760137200,
    borrowApy: 27.98,
    supplyApy: 24.48,
    totalBorrows: 96.39,
    totalDeposits: 96.39,
  },
  {
    timestamp: 1760223600,
    borrowApy: 17.05,
    supplyApy: 14.46,
    totalBorrows: 96.89,
    totalDeposits: 100.01,
  },
  {
    timestamp: 1760310000,
    borrowApy: 10.6,
    supplyApy: 8.38,
    totalBorrows: 96.92,
    totalDeposits: 107.32,
  },
  {
    timestamp: 1760396400,
    borrowApy: 10.61,
    supplyApy: 8.41,
    totalBorrows: 97.0,
    totalDeposits: 107.17,
  },
  {
    timestamp: 1760482800,
    borrowApy: 10.67,
    supplyApy: 8.54,
    totalBorrows: 97.16,
    totalDeposits: 106.26,
  },
  {
    timestamp: 1760569200,
    borrowApy: 10.65,
    supplyApy: 8.49,
    totalBorrows: 97.22,
    totalDeposits: 106.74,
  },
  {
    timestamp: 1760655600,
    borrowApy: 10.8,
    supplyApy: 8.84,
    totalBorrows: 97.25,
    totalDeposits: 104.0,
  },
  {
    timestamp: 1760742000,
    borrowApy: 10.81,
    supplyApy: 8.84,
    totalBorrows: 97.28,
    totalDeposits: 104.0,
  },
  {
    timestamp: 1760828400,
    borrowApy: 10.81,
    supplyApy: 8.85,
    totalBorrows: 97.3,
    totalDeposits: 104.02,
  },
  {
    timestamp: 1760914800,
    borrowApy: 10.81,
    supplyApy: 8.85,
    totalBorrows: 97.33,
    totalDeposits: 104.0,
  },
  {
    timestamp: 1761001200,
    borrowApy: 10.81,
    supplyApy: 8.85,
    totalBorrows: 97.36,
    totalDeposits: 104.01,
  },
  {
    timestamp: 1761087600,
    borrowApy: 10.81,
    supplyApy: 8.86,
    totalBorrows: 97.39,
    totalDeposits: 104.0,
  },
  {
    timestamp: 1761174000,
    borrowApy: 10.81,
    supplyApy: 8.86,
    totalBorrows: 97.41,
    totalDeposits: 104.0,
  },
  {
    timestamp: 1761260400,
    borrowApy: 14.07,
    supplyApy: 11.82,
    totalBorrows: 97.42,
    totalDeposits: 101.5,
  },
  {
    timestamp: 1761346800,
    borrowApy: 11.32,
    supplyApy: 9.42,
    totalBorrows: 96.57,
    totalDeposits: 101.51,
  },
  {
    timestamp: 1761433200,
    borrowApy: 11.57,
    supplyApy: 9.64,
    totalBorrows: 96.51,
    totalDeposits: 101.36,
  },
  {
    timestamp: 1761519600,
    borrowApy: 11.48,
    supplyApy: 9.56,
    totalBorrows: 96.46,
    totalDeposits: 101.34,
  },
  {
    timestamp: 1761606000,
    borrowApy: 10.9,
    supplyApy: 9.06,
    totalBorrows: 96.0,
    totalDeposits: 101.06,
  },
  {
    timestamp: 1761692400,
    borrowApy: 21.48,
    supplyApy: 18.45,
    totalBorrows: 95.07,
    totalDeposits: 96.84,
  },
  {
    timestamp: 1761778800,
    borrowApy: 15.42,
    supplyApy: 13.01,
    totalBorrows: 94.39,
    totalDeposits: 97.92,
  },
  {
    timestamp: 1761865200,
    borrowApy: 21.14,
    supplyApy: 18.14,
    totalBorrows: 94.57,
    totalDeposits: 96.43,
  },
  {
    timestamp: 1761951600,
    borrowApy: 18.11,
    supplyApy: 15.41,
    totalBorrows: 93.28,
    totalDeposits: 95.97,
  },
  {
    timestamp: 1762038000,
    borrowApy: 13.19,
    supplyApy: 11.05,
    totalBorrows: 93.01,
    totalDeposits: 97.17,
  },
  {
    timestamp: 1762124400,
    borrowApy: 17.04,
    supplyApy: 14.44,
    totalBorrows: 93.05,
    totalDeposits: 96.05,
  },
  {
    timestamp: 1762210800,
    borrowApy: 75.0,
    supplyApy: 65.62,
    totalBorrows: 90.37,
    totalDeposits: 90.37,
  },
  {
    timestamp: 1762297200,
    borrowApy: 75.0,
    supplyApy: 65.62,
    totalBorrows: 90.51,
    totalDeposits: 90.51,
  },
  {
    timestamp: 1762383600,
    borrowApy: 75.0,
    supplyApy: 65.62,
    totalBorrows: 90.65,
    totalDeposits: 90.65,
  },
  {
    timestamp: 1762470000,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.7,
    totalDeposits: 90.7,
  },
  {
    timestamp: 1762556400,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.7,
    totalDeposits: 90.7,
  },
  {
    timestamp: 1762642800,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.7,
    totalDeposits: 90.7,
  },
  {
    timestamp: 1762729200,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.7,
    totalDeposits: 90.7,
  },
  {
    timestamp: 1762815600,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.7,
    totalDeposits: 90.7,
  },
  {
    timestamp: 1762902000,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.7,
    totalDeposits: 90.7,
  },
  {
    timestamp: 1762988400,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.7,
    totalDeposits: 90.7,
  },
  {
    timestamp: 1763074800,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.7,
    totalDeposits: 90.7,
  },
  {
    timestamp: 1763161200,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763247600,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763334000,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763420400,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763506800,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763593200,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763679600,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763766000,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763852400,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
  {
    timestamp: 1763938800,
    borrowApy: 0.2,
    supplyApy: 0.17,
    totalBorrows: 90.71,
    totalDeposits: 90.71,
  },
];

const STREAM_CHART_AREAS = [
  { key: "borrowApy", name: "Borrow APY", color: "#ef4444" },
  { key: "supplyApy", name: "Supply APY", color: "#a1a1aa" },
  {
    key: "totalBorrows",
    name: "Borrows ($M)",
    color: "#ec4899",
    yAxisId: "right",
  },
  {
    key: "totalDeposits",
    name: "Deposits ($M)",
    color: "#22d3ee",
    yAxisId: "right",
  },
];

const StreamChartPanel = () => (
  <div className="flex flex-col w-full h-full">
    <div className="flex justify-between items-end mb-2 px-1">
      <div className="flex gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-red-500" />
          <span className="text-[10px] uppercase tracking-widest">
            Borrow_APY
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-zinc-400" />
          <span className="text-[10px] uppercase tracking-widest">
            Supply_APY
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-pink-500" />
          <span className="text-[10px] uppercase tracking-widest">Borrows</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-cyan-400" />
          <span className="text-[10px] uppercase tracking-widest">
            Deposits
          </span>
        </div>
      </div>
      <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest">
        Euler · Stream Default · Sep – Nov 2025
      </span>
    </div>
    <div className="flex-1 min-h-0 w-full border border-white/10 p-3 bg-[#080808]">
      <RLDPerformanceChart
        data={STREAM_DATA}
        resolution="1D"
        areas={STREAM_CHART_AREAS}
      />
    </div>
  </div>
);

export default function Homepage() {
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
  return (
    <div className="h-screen overflow-hidden bg-[#050505] text-[#e0e0e0] font-mono">
      {/* HERO */}
      <section className="h-[calc(100vh-48px)] flex flex-col relative overflow-hidden noise-overlay">
        <div className="absolute inset-0 pattern-grid opacity-10 pointer-events-none" />
        {/* Ambient glow orbs */}
        <div className="absolute top-1/4 left-1/4 w-[500px] h-[500px] bg-cyan-500/[0.04] rounded-full blur-[120px] pointer-events-none" />
        <div className="absolute top-1/3 right-1/4 w-[400px] h-[400px] bg-pink-500/[0.03] rounded-full blur-[120px] pointer-events-none" />
        <div className="absolute bottom-1/4 right-1/3 w-[350px] h-[350px] bg-green-500/[0.03] rounded-full blur-[100px] pointer-events-none" />
        {/* Spotlight behind headline */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[400px] bg-gradient-radial from-white/[0.03] to-transparent rounded-full blur-[60px] pointer-events-none" style={{ background: 'radial-gradient(ellipse at center, rgba(255,255,255,0.04) 0%, transparent 70%)' }} />
        <div className="relative z-10 max-w-[1800px] mx-auto w-full px-6 md:px-12 flex flex-col items-center my-auto pb-[96px]" style={{ zIndex: 2 }}>
          {/* ── Row 1: Centered Text Block ── */}
          <div className="text-center max-w-3xl space-y-4 mb-10">
            <div className="flex items-center justify-center gap-3 text-gray-600 text-[10px] font-bold tracking-[0.4em] uppercase">
              <div className="w-2 h-2 bg-white" />
              RLD Protocol
            </div>
            <h1 className="text-4xl md:text-6xl lg:text-7xl font-bold tracking-tighter leading-[0.95] uppercase bg-gradient-to-b from-white via-white to-gray-500 bg-clip-text text-transparent">
              The Interest Rate
              <br />
              Derivatives Layer
            </h1>
            <p className="text-sm md:text-base text-gray-500 font-bold tracking-wide max-w-lg mx-auto">
              Fix Yields. Trade Rates. Insure Solvency.
            </p>
            <div className="flex gap-4 pt-2 justify-center">
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

          {/* ── Row 2: Product Cards ── */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 w-full max-w-[1100px]">
            {/* Card 1 — Synthetic Bond */}
            <a
              href="/bonds"
              className="group border border-white/[0.08] bg-white/[0.02] backdrop-blur-sm hover:border-cyan-500/40 hover:shadow-[0_0_30px_rgba(34,211,238,0.08)] transition-all duration-500 md:scale-[1.02] md:hover:scale-[1.04]"
              style={heroStep(0)}
            >
              <div className="p-5">
                <div className="flex justify-between items-center mb-4">
                  <span className="text-[11px] font-bold uppercase tracking-widest text-cyan-400 bg-cyan-400/10 px-2 py-1">
                    Synthetic Bond
                  </span>
                  <Shield size={18} className="text-cyan-400" />
                </div>
                <h3 className="text-base font-mono text-white mb-1.5 tracking-tight">
                  FIXED_YIELD
                </h3>
                <p className="text-[11px] text-gray-500 font-mono mb-4 leading-relaxed">
                  Fix your yield for any custom duration to protect against
                  market volatility.
                </p>
                <div className="border border-white/10 bg-[#0a0a0a]">
                  <div className="px-4 py-2 border-b border-white/10 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-1.5 bg-cyan-400" />
                      <span className="text-[10px] font-bold uppercase tracking-widest text-white">
                        Bond
                      </span>
                    </div>
                    <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                      #0042
                    </span>
                  </div>
                  <div className="px-4 py-2.5 flex justify-between items-end">
                    <div>
                      <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                        Fixed APY
                      </div>
                      <div className="text-lg font-mono font-light text-cyan-400 tracking-tight">
                        8.40%
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                        Principal
                      </div>
                      <div className="text-lg font-mono text-white">
                        25,000 USDC
                      </div>
                    </div>
                  </div>
                  <div className="px-4 py-1.5 border-t border-white/5 flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div className="w-1.5 h-1.5 bg-cyan-400" />
                      <span className="text-[9px] text-cyan-500 uppercase tracking-widest">
                        Active
                      </span>
                    </div>
                    <span className="text-[9px] text-gray-600 uppercase tracking-widest">
                      453 Days
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-4 text-cyan-400 text-[11px] font-bold uppercase tracking-[0.2em] group-hover:gap-3 transition-all">
                  Explore <ArrowRight size={14} />
                </div>
              </div>
            </a>

            {/* Card 2 — Rate Trading */}
            <a
              href="/markets/perps"
              className="group border border-white/[0.08] bg-white/[0.02] backdrop-blur-sm hover:border-green-500/40 hover:shadow-[0_0_30px_rgba(34,197,94,0.08)] transition-all duration-500"
              style={heroStep(1)}
            >
              <div className="p-5">
                <div className="flex justify-between items-center mb-4">
                  <span className="text-[11px] font-bold uppercase tracking-widest text-green-400 bg-green-400/10 px-2 py-1">
                    Rate Trading
                  </span>
                  <TrendingUp size={18} className="text-green-400" />
                </div>
                <h3 className="text-base font-mono text-white mb-1.5 tracking-tight">
                  PERPETUAL_MARKET
                </h3>
                <p className="text-[11px] text-gray-500 font-mono mb-4 leading-relaxed">
                  Trade interest rates as a volatility instrument. Capitalize on rates & crypto cointegration.
                </p>
                <div className="border border-white/10 bg-[#0a0a0a]">
                  {/* Pair + Order types */}
                  <div className="px-4 py-2 border-b border-white/10 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-1.5 bg-green-500" />
                      <span className="text-[10px] font-bold uppercase tracking-widest text-white">
                        Perp
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      {["Market", "Limit", "TWAP"].map((t, i) => (
                        <span key={t} className="flex items-center gap-2">
                          <span className="text-[9px] font-bold uppercase tracking-widest text-green-400">
                            {t}
                          </span>
                          {i < 2 && <span className="text-green-500/30 text-[8px]">·</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                  {/* Cross-margin collateral types */}
                  <div className="px-4 py-2.5 flex items-start justify-between">
                    <div>
                      <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                        Collateral
                      </div>
                      <div className="text-lg font-mono font-light text-white tracking-tight">
                        Cross-Margin
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-0.5">
                      <span className="text-[9px] font-mono text-gray-500 uppercase tracking-widest">Assets</span>
                      <span className="text-[9px] font-mono text-gray-500 uppercase tracking-widest">Orders</span>
                      <span className="text-[9px] font-mono text-gray-500 uppercase tracking-widest">LP Positions</span>
                    </div>
                  </div>
                  {/* Status bar */}
                  <div className="px-4 py-1.5 border-t border-white/5 flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div className="w-1.5 h-1.5 bg-green-500" />
                      <span className="text-[9px] text-green-400 uppercase tracking-widest">
                        Live
                      </span>
                    </div>
                    <span className="text-[9px] text-gray-600 uppercase tracking-widest">
                      Uniswap V4
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-4 text-green-400 text-[11px] font-bold uppercase tracking-[0.2em] group-hover:gap-3 transition-all">
                  Explore <ArrowRight size={14} />
                </div>
              </div>
            </a>

            {/* Card 3 — Solvency Insurance (Coming Soon) */}
            <div
              className="relative group border border-white/[0.08] bg-white/[0.02] backdrop-blur-sm hover:border-pink-500/40 hover:shadow-[0_0_30px_rgba(236,72,153,0.08)] transition-all duration-500 cursor-not-allowed"
              style={heroStep(2)}
            >
              {/* Tooltip */}
              <div className="absolute -top-10 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none z-20">
                <div className="px-3 py-1.5 bg-[#0a0a0a] border border-pink-500/30 text-[9px] font-mono font-bold uppercase tracking-[0.25em] text-pink-400 whitespace-nowrap">
                  Coming Soon
                </div>
              </div>
              <div className="p-5">
                <div className="flex justify-between items-center mb-4">
                  <span className="text-[11px] font-bold uppercase tracking-widest text-pink-400 bg-pink-400/10 px-2 py-1">
                    Solvency Insurance
                  </span>
                  <Shield size={18} className="text-pink-400" />
                </div>
                <h3 className="text-base font-mono text-white mb-1.5 tracking-tight">
                  CREDIT_DEFAULT_SWAP (SOON)
                </h3>
                <p className="text-[11px] text-gray-500 font-mono mb-4 leading-relaxed">
                  100% payout on pool bankruptcy. Prediction market with parametric,
                  trustless, instant settlement.
                </p>
                <div className="border border-white/10 bg-[#0a0a0a]">
                  <div className="px-4 py-2 border-b border-white/10 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-1.5 bg-pink-500" />
                      <span className="text-[10px] font-bold uppercase tracking-widest text-white">
                        CDS
                      </span>
                    </div>
                    <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                      #0108
                    </span>
                  </div>
                  <div className="px-4 py-2.5 flex justify-between items-end">
                    <div>
                      <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                        Pool
                      </div>
                      <div className="text-lg font-mono font-light text-pink-400 tracking-tight">
                        AAVE V3 USDT
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                        Insurance
                      </div>
                      <div className="text-lg font-mono text-white">
                        100,000 USDC
                      </div>
                    </div>
                  </div>
                  <div className="px-4 py-1.5 border-t border-white/5 flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div className="w-1.5 h-1.5 bg-pink-500" />
                      <span className="text-[9px] text-pink-400 uppercase tracking-widest">
                        Protected
                      </span>
                    </div>
                    <span className="text-[9px] text-gray-600 uppercase tracking-widest">
                      Collateral: ETH
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-4 text-gray-600 text-[11px] font-bold uppercase tracking-[0.2em]">
                  Coming Soon
                </div>
              </div>
            </div>
          </div>

          {/* ── Row 3: Powered By ── */}
          <div className="mt-10 flex flex-col items-center gap-5" style={heroStep(3)}>
            <span className="text-[10px] text-gray-600 uppercase tracking-[0.3em]">
              Powered by
            </span>
            <div className="flex items-center gap-0 flex-wrap justify-center">
              {[
                { name: "Ethereum", logo: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/info/logo.png" },
                { name: "Uniswap", logo: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984/logo.png" },
                { name: "AAVE", logo: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9/logo.png" },
                { name: "Morpho", logo: "https://cdn.morpho.org/assets/logos/morpho.svg" },
                { name: "Fluid", logo: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x6f40d4A6237C257fff2dB00FA0510DeEECd303eb/logo.png" },
                { name: "Euler", logo: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xd9Fcd98c322942075A5C3860693e9f4f03AAE07b/logo.png" },
              ].map((p, i, arr) => (
                <React.Fragment key={p.name}>
                  <div
                    className="flex items-center gap-2.5 text-gray-500 hover:text-white transition-colors duration-300 group px-5 py-1"
                  >
                    <img
                      src={p.logo}
                      alt={p.name}
                      className="w-5 h-5 object-contain opacity-40 group-hover:opacity-90 transition-all duration-300 grayscale group-hover:grayscale-0"
                    />
                    <span className="text-[11px] font-bold uppercase tracking-[0.2em]">
                      {p.name}
                    </span>
                  </div>
                  {i < arr.length - 1 && (
                    <div className="w-px h-4 bg-white/10" />
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
