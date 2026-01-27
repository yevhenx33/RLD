import React from "react";
import { Link } from "react-router-dom";
import {
  Terminal,
  Shield,
  TrendingUp,
  ArrowRight,
  ExternalLink,
  Check,
} from "lucide-react";
import {
  AreaChart,
  Area,
  ResponsiveContainer,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceArea,
} from "recharts";
import BondCard from "./components/BondCard";
import Header from "./components/Header";
import ratesCsv from "./assets/aave_usdc_rates_full_history_2026-01-27.csv?raw";

const mockBond = {
  id: "mock-1",
  status: "ACTIVE", // Set to MATURED to show the green 'claim' state or ACTIVE for blue. Visual preference. User asked for "bonds card", let's show a nice one. Let's start with ACTIVE as it's more standard for a landing page example.
  maturityDate: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString(),
  currency: "USDC",
  tokenId: "00033",
  rate: 12.0,
  principal: 50000,
};

/**
 * RLD Protocol Landing Page
 * Aesthetic: Industrial DeFi Terminal (Unit410 inspired)
 * Core System: Grid layouts, functional typography, brutalist structure.
 */
const LandingPage = () => {
  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white/30 selection:text-white flex flex-col items-center">
      {/* Global Borders Container */}
      <div className="w-full max-w-[1400px] border-x border-white/10 min-h-screen relative flex flex-col">
        {/* Standard App Header */}
        <Header latest={{}} isCapped={false} ratesLoaded={true} />

        {/* HERO WRAPPER (Hero + Stats = Full Screen minus Header) */}
        <div className="min-h-[calc(100vh-80px)] flex flex-col border-t border-white/10">
          {/* HERO SECTION */}
          <section className="border-b border-white/10 p-6 md:p-24 flex-1 flex flex-col justify-center relative overflow-hidden">
            {/* Background Grid Decoration */}
            <div className="absolute inset-0 pattern-grid opacity-20 pointer-events-none" />

            <div className="space-y-4 md:space-y-6 relative z-10">
              <div className="flex items-center gap-3 text-gray-500 text-xs font-bold tracking-[0.3em] uppercase">
                <Terminal size={14} />
                Deployment: Testnet
              </div>

              <h1 className="text-3xl md:text-5xl font-bold tracking-tighter leading-[0.9] text-white uppercase max-w-4xl">
                Rate-Level Derivatives
              </h1>

              <p className="max-w-xl text-md md:text-md text-gray-600 font-bold leading-relaxed tracking-wide border-l-2 border-gray-600 pl-4">
                Trade Rates. Fix Yields. Insure Solvency.
              </p>

              <div className="pt-4 flex gap-4">
                <Link
                  to="/app"
                  className="w-full md:w-64 h-12 md:h-14 bg-white font-bold hover:bg-gray-200 text-black flex items-center justify-between px-4 md:px-6 text-xs md:text-sm tracking-[0.2em] uppercase group transition-all"
                >
                  Launch App
                  <ArrowRight
                    size={16}
                    className="group-hover:translate-x-1 transition-transform"
                  />
                </Link>
                <a
                  href="/whitepaper"
                  className="w-full md:w-64 h-12 md:h-14 border border-white/20 hover:border-white text-gray-400 hover:text-white flex items-center justify-between px-4 md:px-6 text-xs md:text-sm font-bold tracking-[0.2em] uppercase transition-all"
                >
                  Documentation
                  <ExternalLink size={16} />
                </a>
              </div>
            </div>
          </section>

          {/* MARKET STATS TICKER */}
          <div className="grid grid-cols-2 md:grid-cols-4 border-b border-white/10 divide-x divide-white/10">
            <StatBox
              label="Total_Value_Locked"
              value="$42,392,109"
              change="+2.4%"
            />
            <StatBox label="Trade_Volume" value="$12,842,000" change="24h" />
            <StatBox
              label="Avg_Market_Rate"
              value="8.42%" //TODO: Update to real value
              change="Debt-weighted"
              className="hidden md:flex"
            />
            <StatBox
              label="LIVE_MARKETS"
              value="3"
              change="AAVE, Morpho, Euler"
              color="text-green-500"
              className="hidden md:flex"
            />
          </div>
        </div>

        {/* PRIMITIVES SECTION - UNIT410 STYLE LISTS */}
        <section className="grid grid-cols-1 md:grid-cols-12 divide-x divide-white/10">
          {/* LEFT: HEADER */}
          <div className="col-span-12 md:col-span-4 p-6 md:p-12 border-b md:border-b-0 border-white/10 relative md:sticky md:top-16 h-fit bg-[#050505]">
            <h2 className="text-2xl font-light text-white tracking-tight mb-4">
              CORE_PRIMITIVES
            </h2>
            <p className="text-sm text-gray-500 tracking-wide leading-relaxed">
              The RLD Protocol consists of three financial primitives designed
              to decompose and restructure yield risk.
            </p>
            <div className="mt-12 space-y-2">
              <div className="text-sm text-gray-600 uppercase tracking-widest">
                Navigation
              </div>
              <div className="h-[1px] w-full bg-white/10 mb-4" />
              <NavJump href="#bonds" label="01. Synthetic Bonds" />
              <NavJump href="#rates" label="02. Rate-Level Perps" />
              <NavJump href="#insurance" label="03. Credit Default Swaps" />
            </div>
          </div>

          {/* RIGHT: CONTENT STACK */}
          <div className="col-span-12 md:col-span-8 divide-y divide-white/10">
            <LandingSectionFeature
              id="bonds"
              index="01"
              title="Synthetic Bonds"
              icon={
                <div className="w-5 h-5 border border-cyan-400 rounded-none flex items-center justify-center text-cyan-400 text-[10px] font-bold">
                  %
                </div>
              }
              accentColor="cyan"
              description="Fix your yield by converting floating underlying rate into a fixed-income product."
              features={[
                "Custom maturity from 1 block to 1 year",
                "Zero lockouts",
                "No liquidity fragmentation across expirations",
              ]}
              visual={
                <div className="flex justify-center lg:justify-end">
                  <div className="w-full max-w-sm">
                    <BondCard nft={mockBond} />
                  </div>
                </div>
              }
            />

            <LandingSectionFeature
              id="rates"
              index="02"
              title="Rate-Level Perps"
              icon={<TrendingUp className="text-pink-500" size={20} />}
              accentColor="pink"
              description="Go long/short on USDC borrowing cost to capitalize on:"
              features={[
                "Natural interest rate assymetry",
                "USDC rate and ETH price cointegration",
                "Cross-rates arbitrage",
              ]}
              visual={
                <div className="flex justify-center lg:justify-end items-center">
                  <div className="w-full max-w-lg">
                    <LandingChart />
                  </div>
                </div>
              }
            />

            <LandingSectionFeature
              id="insurance"
              index="03"
              title="Credit Default Swaps"
              icon={<Shield className="text-green-500" size={20} />}
              accentColor="green"
              description="Protect against protocol bankruptcy (Stream-like case) with an automatic default system triggered by:"
              features={[
                "Utilization > 90%",
                "Collateral Depeg > 90%",
                "DEX liquidity drop > 90%",
              ]}
              visual={
                <div className="flex justify-center lg:justify-end items-center">
                  <div className="w-full max-w-lg">
                    <CDSKinkCurve />
                  </div>
                </div>
              }
            />
          </div>
        </section>

        {/* FOOTER */}
        {/* FOOTER */}
        <footer className="border-t border-white/10 relative bg-[#050505] overflow-hidden">
          {/* Background Grid Decoration */}
          <div className="absolute inset-0 pattern-grid opacity-10 pointer-events-none" />

          <div className="p-6 md:p-12 grid grid-cols-1 md:grid-cols-4 gap-8 md:gap-10 relative z-10">
            <div className="space-y-4">
              <div className="text-[13px] font-bold tracking-[0.2em] flex items-center gap-2 text-white">
                <div className="w-2 h-2 bg-gray-500 rounded-none" />
                RLD
              </div>
              <p className="text-[11px] text-gray-600 leading-relaxed max-w-xs">
                Interest Rate Derivatives Layer.
                <br />
                Powered by Ethereum.
              </p>
            </div>

            <FooterList title="Protocol" items={["Whitepaper", "Research "]} />
            <FooterList
              title="Interface"
              items={["Terminal", "Analytics", "Documentation", "Status"]}
            />
            <FooterList
              title="Community"
              items={["Twitter", "Telegram", "Github"]}
            />
          </div>

          {/* Bottom Bar */}
          <div className="border-t border-white/10 p-6 md:px-12 py-6 flex flex-col md:flex-row justify-between items-center text-xs md:text-[10px] text-gray-600 uppercase tracking-widest relative z-10">
            <div>© 2025 RLD Protocol. All rights reserved.</div>
            <div className="flex gap-6 mt-4 md:mt-0">
              <a href="#" className="hover:text-white transition-colors">
                Privacy Policy
              </a>
              <a href="#" className="hover:text-white transition-colors">
                Terms of Service
              </a>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
};

// Sub-components for cleaner code
const StatBox = ({
  label,
  value,
  change,
  color = "text-white",
  className = "",
}) => (
  <div
    className={`p-4 md:p-8 flex flex-col justify-between h-24 md:h-32 hover:bg-white/[0.02] transition-colors ${className}`}
  >
    <div className="text-[10px] text-gray-500 uppercase tracking-widest">
      {label}
    </div>
    <div>
      <div className={`text-xl md:text-2xl font-light tracking-tight ${color}`}>
        {value}
      </div>
      <div className="text-[9px] md:text-[10px] text-gray-600 mt-1 uppercase tracking-widest">
        {change}
      </div>
    </div>
  </div>
);

const NavJump = ({ href, label }) => (
  <a
    href={href}
    className="block py-2 text-[12px] text-gray-500 hover:text-cyan-500 uppercase tracking-widest transition-colors flex justify-between group"
  >
    <span>{label}</span>
    <span className="opacity-0 group-hover:opacity-100 transition-opacity">
      {"->"}
    </span>
  </a>
);

// Reusable Section Component
const LandingSectionFeature = ({
  id,
  index,
  title,
  icon,
  accentColor,
  description,
  features = [],
  visual,
}) => {
  // Dynamic color classes map
  const accentColorMap = {
    cyan: "text-cyan-400 border-cyan-500/50",
    pink: "text-pink-500 border-pink-500/50",
    green: "text-green-500 border-green-500/50",
  };

  // Fallback if needed, though we strictly use cyan/pink/green
  const accentClass =
    accentColorMap[accentColor] || "text-white border-white/20";
  const textAccentClass = accentClass.split(" ")[0]; // just the text color

  return (
    <div
      id={id}
      className="group min-h-[50vh] flex flex-col justify-center px-6 pb-6 pt-12 md:p-12 hover:bg-white/[0.02] transition-colors relative"
    >
      <div className="absolute top-4 right-4 text-[10px] font-bold text-gray-700 tracking-widest z-10">
        [{index}]
      </div>

      <div className="flex flex-col-reverse lg:grid lg:grid-cols-2 gap-12">
        {/* LEFT: Content */}
        <div className="flex flex-col">
          <div className="mb-6 flex items-center gap-3">
            {icon}
            <h3 className="text-xl font-bold text-white tracking-widest uppercase">
              {title}
            </h3>
          </div>
          <p className="text-sm text-gray-400 mb-8 leading-relaxed">
            {description}
          </p>

          {features.length > 0 && (
            <ul className="space-y-4">
              {features.map((feat, i) => (
                <li key={i} className="flex gap-3">
                  <div
                    className={`w-4 h-4 mt-0.5 border flex items-center justify-center ${accentClass.split(" ")[1]}`}
                  >
                    <Check size={10} className={textAccentClass} />
                  </div>
                  <span className="text-xs text-gray-400 uppercase tracking-wide">
                    {feat}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-8 flex-1 flex items-end">
            <Link to="/app">
              <button className="border border-white/20 hover:border-white px-6 py-2 text-[10px] font-bold tracking-widest uppercase hover:bg-white hover:text-black transition-all flex items-center gap-2 group">
                Launch App
                <ArrowRight size={14} className="" />
              </button>
            </Link>
          </div>
        </div>

        {/* RIGHT: Visual */}
        {visual}
      </div>
    </div>
  );
};

// CDS Kink Curve Component
const CDSKinkCurve = () => {
  const data = [
    { util: 0, rate: 2 },
    { util: 20, rate: 2.5 },
    { util: 40, rate: 3 },
    { util: 60, rate: 3.5 },
    { util: 80, rate: 4 }, // Base Kink
    { util: 85, rate: 6 },
    { util: 90, rate: 10 }, // Crisis Trigger
    { util: 95, rate: 50 },
    { util: 100, rate: 100 }, // Max Cap
  ];

  return (
    <div className="group relative bg-[#0a0a0a] border border-white/10 hover:border-white/20 transition-colors flex flex-col w-full h-full">
      {/* Header */}
      <div className="bg-white/5 px-5 py-3 border-b border-white/5 flex justify-between items-center">
        <div className="text-[10px] text-gray-500 uppercase tracking-widest font-medium">
          Payoff Curve
        </div>
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
          <span className="text-[10px] text-red-500 uppercase tracking-widest font-bold">
            Auto-Default Trigger
          </span>
        </div>
      </div>

      {/* Body: Chart */}
      <div className="h-[250px] relative w-full bg-black/40">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={data}
            margin={{ top: 10, right: 0, bottom: 0, left: 0 }}
          >
            <defs>
              <linearGradient id="colorRateGreen" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
              </linearGradient>
            </defs>
            <Tooltip
              cursor={{ stroke: "#333", strokeWidth: 1 }}
              contentStyle={{ backgroundColor: "#000", borderColor: "#333" }}
              itemStyle={{
                color: "#22c55e",
                fontSize: "10px",
                textTransform: "uppercase",
              }}
              labelStyle={{ display: "none" }}
              formatter={(value) => [`${value}%`, "RATE"]}
            />
            <Area
              type="monotone"
              dataKey="rate"
              stroke="#22c55e"
              fill="url(#colorRateGreen)"
              strokeWidth={2}
              isAnimationActive={true}
              animationDuration={2000}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Footer */}
      <div className="px-5 py-4 bg-[#050505] border-t border-white/5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
          <span className="text-[10px] uppercase tracking-widest font-medium text-green-500">
            Active
          </span>
        </div>
        <div className="text-[10px] text-gray-600 uppercase tracking-widest font-bold">
          Solvency Protection
        </div>
      </div>
    </div>
  );
};

const LandingChart = () => {
  // Parse Real CSV Data
  const data = React.useMemo(() => {
    if (!ratesCsv) return [];

    const lines = ratesCsv.trim().split("\n");
    // Header is line 0

    // Config: Exact Range Jan 1, 2025 - Jan 1, 2026
    const START_TIMESTAMP = 1735689600; // Jan 1, 2025 00:00:00 UTC
    const END_TIMESTAMP = 1767225600; // Jan 1, 2026 00:00:00 UTC

    const parsed = [];

    for (let i = 1; i < lines.length; i++) {
      const line = lines[i];
      if (!line) continue;

      // CSV: Timestamp,Date,APY,TWAR,Price
      const parts = line.split(",");
      const ts = parseInt(parts[0], 10);

      if (ts < START_TIMESTAMP || ts > END_TIMESTAMP) continue;

      // Downsample: ~ Daily (Every 24th hourly point)
      if (i % 24 !== 0) continue;

      parsed.push({
        timestamp: ts,
        rate: parseFloat(parts[2]),
        price: parseFloat(parts[4]),
      });
    }

    return parsed;
  }, []);

  return (
    <div className="group relative bg-[#0a0a0a] border border-white/10 hover:border-white/20 transition-colors flex flex-col w-full h-full">
      {/* Header */}
      <div className="bg-white/5 px-5 py-3 border-b border-white/5 flex justify-between items-center">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-pink-500"></div>
          <span className="font-mono text-xs text-gray-200 font-medium">
            USDC Rate
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-cyan-400"></div>
          <span className="font-mono text-xs text-gray-200 font-medium">
            ETH Price
          </span>
        </div>
      </div>

      {/* Body: Chart */}
      <div className="h-[250px] relative w-full bg-black/40">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={data}
            margin={{ top: 10, right: 0, bottom: 0, left: 0 }}
          >
            <defs>
              <linearGradient id="splitColorRate" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#ec4899" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#ec4899" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="splitColorPrice" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="rate"
              stroke="#ec4899"
              fill="url(#splitColorRate)"
              strokeWidth={2}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="price"
              stroke="#22d3ee"
              fill="url(#splitColorPrice)"
              strokeWidth={2}
              yAxisId="right"
              isAnimationActive={false}
            />
            <YAxis hide domain={["auto", "auto"]} />
            <YAxis yAxisId="right" hide domain={["auto", "auto"]} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Footer */}
      <div className="px-5 py-4 bg-[#050505] border-t border-white/5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
          <span className="text-[10px] uppercase tracking-widest font-medium text-green-500">
            Live Feed
          </span>
        </div>
        <div className="text-[10px] text-gray-600 uppercase tracking-widest font-bold">
          Jan 1, 25 - Jan 1, 26 Data
        </div>
      </div>
    </div>
  );
};

const FooterList = ({ title, items }) => (
  <div className="space-y-4">
    <div className="text-[11px] text-white font-bold uppercase tracking-widest border-b border-white/10 pb-2 w-fit">
      {title}
    </div>
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item}>
          <a
            href="#"
            className="text-[11px] text-gray-500 hover:text-white uppercase tracking-widest transition-colors hover:pl-1 block"
          >
            [{item}]
          </a>
        </li>
      ))}
    </ul>
  </div>
);

export default LandingPage;
