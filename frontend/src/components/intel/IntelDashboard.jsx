import React, { useState, useEffect } from 'react';
import useInView from '../../hooks/useInView';
import Header from '../layout/Header';

// ── Shared grain overlay ──────────────────────────────────────────
const GRAIN_SVG = `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.08'/%3E%3C/svg%3E")`;
const Grain = () => (
  <div
    className="pointer-events-none absolute inset-0 opacity-[0.15]"
    style={{ backgroundImage: GRAIN_SVG, backgroundSize: '192px 192px' }}
  />
);

function IntelCard({ index, title, value, subtext, delay }) {
  const [ref, inView] = useInView(0.05);
  return (
    <div
      ref={ref}
      className="relative border border-[#222] bg-gradient-to-br from-[#0c0c0c] to-[#050505] p-7 flex flex-col gap-4 transition-all duration-[800ms] hover:border-[#444] hover:-translate-y-1 hover:shadow-2xl hover:shadow-[#ffffff05] group"
      style={{
        transitionDelay: `${delay}ms`,
        opacity: inView ? 1 : 0,
        transform: inView ? 'translateY(0)' : 'translateY(16px)'
      }}
    >
      <div className="absolute inset-0 bg-gradient-to-br from-[#ffffff03] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none" />
      <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#444]" />
      <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#444]" />
      
      <div className="flex items-start justify-between mb-2">
        <span className="font-jbm text-[9px] tracking-[0.3em] text-[#444]">0{index}</span>
        <span className="font-jbm text-[8px] tracking-[0.25em] uppercase text-[#777] border border-[#222] px-2 py-[2px]">
          Global Macro
        </span>
      </div>
      <p className="font-jbm text-[10px] tracking-[0.2em] uppercase text-[#666]">{title}</p>
      <div className="font-['Space_Grotesk'] font-light text-[#dedede] leading-none text-2xl lg:text-3xl tracking-tight">
        {value}
      </div>
      <div className="h-px w-8 bg-[#333] group-hover:w-16 transition-all duration-700" />
      <p className="font-jbm text-[9px] text-[#555] mt-1 tracking-wider uppercase">{subtext}</p>
    </div>
  );
}

export default function IntelDashboard() {
  const [vis, setVis] = useState(false);
  useEffect(() => { const t = setTimeout(() => setVis(true), 80); return () => clearTimeout(t); }, []);

  return (
    <div className="relative min-h-screen bg-[#020202] text-[#eee] overflow-hidden pb-40" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>
      <Grain />
      <div className="absolute top-[-20%] left-[-10%] w-[50%] h-[50%] bg-[#cfad7a] opacity-[0.02] blur-[150px] rounded-full pointer-events-none" />
      
      <div className="relative z-20">
        <Header isCapped={false} ratesLoaded={true} transparent />
      </div>
      
      {/* Header Area */}
      <div className={`relative z-10 max-w-[1300px] mx-auto px-8 md:px-14 pt-32 pb-16 transition-all duration-[1000ms] ${vis ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'}`}>
        <div className="flex items-center gap-5 mb-8 select-none relative">
          <div className="absolute left-[-20px] top-[8px] w-2 h-2 bg-[#d4af37] animate-pulse rounded-full" />
          <span className="font-jbm text-[10px] text-[#444] tracking-[0.35em]">LIVE INSTITUTIONAL INDEXING</span>
          <span className="font-jbm text-[9px] text-[#d4af37] tracking-[0.15em] border border-[#d4af37]/30 bg-[#d4af37]/5 px-3 py-1">EST. YIELD $8.42B / YR</span>
        </div>
        <h1 className="leading-[1.05] tracking-tight mb-8">
          <span className="block text-[#fff] font-['Space_Grotesk'] font-extralight text-5xl md:text-6xl lg:text-[72px]">
            Intelligence
          </span>
          <span className="block text-[#444] font-['Space_Grotesk'] font-extralight text-4xl md:text-5xl lg:text-[72px] mt-2">
            DeFi Absolute Yield
          </span>
        </h1>
        <p className="text-[11px] text-[#666] tracking-[0.08em] max-w-[540px] leading-[2.2] uppercase">
          Mapping the fundamental origins of on-chain yield. Tracking recursive leverage vectors, Real-World Asset inflows, and structural risk premiums dynamically across 42 networks.
        </p>
      </div>

      {/* Overview Cards */}
      <div className="relative z-10 max-w-[1300px] mx-auto px-8 md:px-14 mb-24 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
        <IntelCard index={1} delay={0} title="Total DeFi TVL" value="$94.2 B" subtext="Mapped Institutional Depth" />
        <IntelCard index={2} delay={100} title="Absolute Annual Yield" value="$8.42 B" subtext="Blended Benchmark Rate: 8.94%" />
        <IntelCard index={3} delay={200} title="Staking & MEV Share" value="42.1%" subtext="Network Inflation & Priority Fees" />
        <IntelCard index={4} delay={300} title="RWA Treasury Share" value="18.5%" subtext="Tokenized T-Bill Aggregation" />
      </div>

      {/* Main Graph & Treemap Area */}
      <div className="relative z-10 max-w-[1300px] mx-auto px-8 md:px-14 flex flex-col gap-10">
        
        {/* Absolute Annual Yield Treemap (Mock) */}
        <div className="border border-[#1a1a1a] bg-[#050505] flex flex-col h-[500px] shadow-2xl shadow-[#000]">
          <div className="p-5 border-b border-[#1a1a1a] flex justify-between items-center bg-[#080808]">
            <span className="font-jbm text-[10px] tracking-[0.25em] uppercase text-[#666]">Yield Market Share Composition</span>
            <span className="font-jbm text-[9px] text-[#444] border border-[#222] px-3 py-1 tracking-widest">WEIGHTED: TVL × APY</span>
          </div>
          <div className="flex-1 w-full p-3 relative bg-[#020202]">
            <div className="w-full h-full flex flex-col gap-2">
              {/* Row 1: Giants */}
              <div className="flex-[4] w-full flex gap-2">
                <div className="flex-[3] relative bg-[#0a0c0a] border border-[#20251f] p-5 flex flex-col justify-end hover:border-[#384236] transition-colors cursor-pointer group">
                  <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-[#d4af37]/40 to-transparent" />
                  <span className="text-[11px] text-[#9eb58c] font-jbm uppercase tracking-widest mb-1">Sky USDS & Spark</span>
                  <span className="text-[#dedede] font-['Space_Grotesk'] text-2xl font-light">$1.24 B</span>
                </div>
                <div className="flex-[2] relative bg-[#0a0c12] border border-[#171c26] p-5 flex flex-col justify-end hover:border-[#252f40] transition-colors cursor-pointer group">
                  <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-[#8ba1c4]/30 to-transparent" />
                  <span className="text-[11px] text-[#7185a6] font-jbm uppercase tracking-widest mb-1">Ethena sUSDe</span>
                  <span className="text-[#dedede] font-['Space_Grotesk'] text-2xl font-light">$840 M</span>
                </div>
                <div className="flex-[1.5] relative bg-[#140b12] border border-[#2e1929] p-5 flex flex-col justify-end hover:border-[#42253a] transition-colors cursor-pointer group">
                  <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-[#b5769c]/30 to-transparent" />
                  <span className="text-[10px] text-[#a66a8e] font-jbm uppercase tracking-widest mb-1">Uniswap (LP Fees)</span>
                  <span className="text-[#dedede] font-['Space_Grotesk'] text-xl font-light">$620 M</span>
                </div>
              </div>
              
              {/* Row 2: Lending & Rates */}
              <div className="flex-[2] w-full flex gap-2">
                <div className="flex-[1.8] relative bg-[#0a1214] border border-[#15272a] p-4 flex flex-col justify-end hover:border-[#223d42] transition-colors cursor-pointer text-right">
                  <span className="text-[10px] text-[#699da3] font-jbm uppercase tracking-widest mb-1">Fluid</span>
                  <span className="text-[#dedede] font-['Space_Grotesk'] text-lg font-light">$460 M</span>
                </div>
                <div className="flex-[1.5] relative bg-[#0d0a14] border border-[#211a33] p-4 flex flex-col justify-end hover:border-[#332a4d] transition-colors cursor-pointer">
                  <span className="text-[10px] text-[#8369a3] font-jbm uppercase tracking-widest mb-1">Morpho Blue</span>
                  <span className="text-[#dedede] font-['Space_Grotesk'] text-lg font-light">$410 M</span>
                </div>
                <div className="flex-[1.2] relative bg-[#12140a] border border-[#2a2e16] p-4 flex flex-col justify-end hover:border-[#3e4521] transition-colors cursor-pointer">
                  <span className="text-[10px] text-[#9b9e69] font-jbm uppercase tracking-widest mb-1">Pendle (Fixed)</span>
                  <span className="text-[#dedede] font-['Space_Grotesk'] text-lg font-light">$380 M</span>
                </div>
              </div>

              {/* Row 3: Trailing */}
              <div className="flex-[1] w-full flex gap-2">
                <div className="flex-[2] relative bg-[#14100a] border border-[#33261a] p-4 flex flex-col justify-end hover:border-[#4d3a27] transition-colors cursor-pointer">
                  <span className="text-[9px] text-[#a37d5c] font-jbm truncate uppercase tracking-widest mb-1">Aave V3</span>
                  <span className="text-[#dedede] font-['Space_Grotesk'] font-light">$290 M</span>
                </div>
                <div className="flex-[1.5] relative bg-[#140a0a] border border-[#331a1a] p-4 flex flex-col justify-end hover:border-[#4d2828] transition-colors cursor-pointer text-center">
                  <span className="text-[9px] text-[#a35c5c] font-jbm truncate uppercase tracking-widest mb-1">Euler v2</span>
                  <span className="text-[#dedede] font-['Space_Grotesk'] font-light">$150 M</span>
                </div>
                <div className="flex-[1] relative bg-[#080808] border border-[#1a1a1a] p-4 flex flex-col justify-end">
                  <span className="text-[9px] text-[#444] font-jbm uppercase tracking-widest">Other Markets</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Yield Pipeline - Alluvial Diagram (Mock) */}
        <div className="border border-[#1a1a1a] bg-[#050505] flex flex-col h-[700px] overflow-hidden shadow-2xl shadow-[#000]">
          <div className="p-5 border-b border-[#1a1a1a] bg-[#080808] flex justify-between items-center relative z-10">
            <span className="font-jbm text-[10px] tracking-[0.25em] uppercase text-[#666]">Macro Yield Pipeline</span>
            <span className="font-jbm text-[9px] text-[#444] border border-[#222] px-3 py-1 tracking-widest">SOURCE INTERMEDIARY SINK</span>
          </div>
          <div className="flex-1 w-full relative bg-[#020202]">
            <svg viewBox="0 0 1000 500" className="w-full h-full" preserveAspectRatio="none">
              
              <defs>
                {/* Institutional Premium Gradients */}
                <linearGradient id="goldToSilver" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#d4af37" stopOpacity="0.2" />
                  <stop offset="100%" stopColor="#e5e7eb" stopOpacity="0.2" />
                </linearGradient>
                <linearGradient id="silverToSlate" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#e5e7eb" stopOpacity="0.15" />
                  <stop offset="100%" stopColor="#64748b" stopOpacity="0.3" />
                </linearGradient>
                <linearGradient id="slateToBronze" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#64748b" stopOpacity="0.2" />
                  <stop offset="100%" stopColor="#a37d5c" stopOpacity="0.25" />
                </linearGradient>
                <linearGradient id="bronzeToObsidian" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#a37d5c" stopOpacity="0.25" />
                  <stop offset="100%" stopColor="#444444" stopOpacity="0.2" />
                </linearGradient>
              </defs>

              {/* Background structural tracking lines */}
              <g stroke="#ffffff05" strokeWidth="1" strokeDasharray="2 6">
                {[100, 150, 200, 250, 300, 350, 400].map(y => (
                  <line key={y} x1="0" y1={y} x2="1000" y2={y} />
                ))}
              </g>

              {/* Cinematic Alluvial Flow Paths */}
              {/* T-Bills to Sky to USDS */}
              <path d="M 150,120 C 350,120 350,180 500,180" fill="none" stroke="url(#goldToSilver)" strokeWidth="45" strokeLinecap="square" />
              <path d="M 500,180 C 650,180 650,120 850,120" fill="none" stroke="url(#goldToSilver)" strokeWidth="65" strokeLinecap="square" />
              
              {/* Staking to Ethena to Pendle/Loopers */}
              <path d="M 150,280 C 350,280 350,280 500,280" fill="none" stroke="url(#silverToSlate)" strokeWidth="55" strokeLinecap="square" />
              <path d="M 500,280 C 650,280 650,420 850,420" fill="none" stroke="url(#silverToSlate)" strokeWidth="30" strokeLinecap="square" />
              <path d="M 500,270 C 650,270 650,240 850,240" fill="none" stroke="url(#goldToSilver)" strokeWidth="15" strokeLinecap="square" />
              
              {/* Retail IL to Uniswap to JIT */}
              <path d="M 150,420 C 350,420 350,380 500,380" fill="none" stroke="url(#bronzeToObsidian)" strokeWidth="25" strokeLinecap="square" />
              <path d="M 500,380 C 650,380 650,340 850,340" fill="none" stroke="url(#bronzeToObsidian)" strokeWidth="25" strokeLinecap="square" />

              {/* Defi Native Leverage (Morpho/Fluid) */}
              <path d="M 150,200 C 350,200 350,100 850,100" fill="none" stroke="url(#slateToBronze)" strokeWidth="18" strokeLinecap="square" />
              <path d="M 150,340 C 350,340 350,220 500,220" fill="none" stroke="url(#slateToBronze)" strokeWidth="35" strokeLinecap="square" />
              <path d="M 500,220 C 650,220 650,300 850,300" fill="none" stroke="url(#slateToBronze)" strokeWidth="35" strokeLinecap="square" />

              {/* Node Column Demarcation Lines */}
              <line x1="150" y1="40" x2="150" y2="460" stroke="#333" strokeWidth="1" strokeDasharray="3 3" />
              <line x1="500" y1="40" x2="500" y2="460" stroke="#333" strokeWidth="1" strokeDasharray="3 3" />
              <line x1="850" y1="40" x2="850" y2="460" stroke="#333" strokeWidth="1" strokeDasharray="3 3" />

              {/* Labels - Left (Sources) */}
              <text x="135" y="123" fill="#888" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="end" className="uppercase">INSTITUTIONAL RWA</text>
              <text x="135" y="203" fill="#888" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="end" className="uppercase">VALIDATOR YIELD</text>
              <text x="135" y="283" fill="#888" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="end" className="uppercase">PERP BASIS DEMAND</text>
              <text x="135" y="343" fill="#888" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="end" className="uppercase">SMART CONTRACT DEBT</text>
              <text x="135" y="423" fill="#888" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="end" className="uppercase">RETAIL VOLATILITY</text>

              {/* Labels - Center (Protocols) */}
              <g transform="translate(450, 110)">
                <rect width="100" height="26" fill="#0a0a0a" stroke="#222" rx="1" />
                <text x="50" y="17" fill="#ccc" fontSize="9" fontFamily="monospace" letterSpacing="0.1em" textAnchor="middle" className="uppercase">SPARKLEND</text>
              </g>

              <g transform="translate(450, 168)">
                <rect width="100" height="26" fill="#0a0a0a" stroke="#384236" rx="1" />
                <text x="50" y="17" fill="#9eb58c" fontSize="9" fontFamily="monospace" letterSpacing="0.1em" textAnchor="middle" className="uppercase">SKY ECOSYSTEM</text>
              </g>

              <g transform="translate(450, 208)">
                <rect width="100" height="26" fill="#0a0a0a" stroke="#4d3a27" rx="1" />
                <text x="50" y="17" fill="#a37d5c" fontSize="9" fontFamily="monospace" letterSpacing="0.1em" textAnchor="middle" className="uppercase">FLUID / AAVE</text>
              </g>

              <g transform="translate(450, 268)">
                <rect width="100" height="26" fill="#0a0a0a" stroke="#252f40" rx="1" />
                <text x="50" y="17" fill="#7185a6" fontSize="9" fontFamily="monospace" letterSpacing="0.1em" textAnchor="middle" className="uppercase">ETHENA LABS</text>
              </g>
              
              <g transform="translate(450, 368)">
                <rect width="100" height="26" fill="#0a0a0a" stroke="#42253a" rx="1" />
                <text x="50" y="17" fill="#a66a8e" fontSize="9" fontFamily="monospace" letterSpacing="0.1em" textAnchor="middle" className="uppercase">UNISWAP V4</text>
              </g>

              {/* Labels - Right (Sinks) */}
              <text x="865" y="103" fill="#888" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="start" className="uppercase">LEVERAGED LONGS</text>
              <text x="865" y="123" fill="#9eb58c" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="start" className="uppercase">USDS SAVINGS RATE</text>
              <text x="865" y="243" fill="#888" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="start" className="uppercase">RECURSIVE LOOPING</text>
              <text x="865" y="303" fill="#a37d5c" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="start" className="uppercase">RISK LIQUIDATORS</text>
              <text x="865" y="343" fill="#a66a8e" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="start" className="uppercase">JIT / MEV ARBITRAGE</text>
              <text x="865" y="423" fill="#7185a6" fontSize="10" fontFamily="monospace" letterSpacing="0.1em" textAnchor="start" className="uppercase">PENDLE FIXED PT</text>

            </svg>
          </div>
        </div>

      </div>
    </div>
  );
}
