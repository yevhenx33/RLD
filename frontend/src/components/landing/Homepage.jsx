import React from "react";
import { ArrowRight, TrendingUp, Shield } from "lucide-react";

/**
 * Homepage — Pitch Deck (concise, aligned to RLD Whitepaper)
 * Route: /
 */

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
    <div className="min-h-screen overflow-y-auto lg:h-screen lg:overflow-hidden bg-[#050505] text-[#e0e0e0] font-mono">
      {/* HERO */}
      <section className="h-[calc(100vh-48px)] flex flex-col relative noise-overlay">
        <div className="absolute inset-0 pattern-grid opacity-10 pointer-events-none" />
        {/* Ambient glow orbs */}
        <div className="absolute top-1/4 left-1/4 w-[250px] h-[250px] md:w-[350px] md:h-[350px] lg:w-[500px] lg:h-[500px] bg-cyan-500/[0.04] rounded-full blur-[80px] md:blur-[100px] lg:blur-[120px] pointer-events-none" />
        <div className="absolute top-1/3 right-1/4 w-[200px] h-[200px] md:w-[300px] md:h-[300px] lg:w-[400px] lg:h-[400px] bg-pink-500/[0.03] rounded-full blur-[80px] md:blur-[100px] lg:blur-[120px] pointer-events-none" />
        <div className="absolute bottom-1/4 right-1/3 w-[180px] h-[180px] md:w-[250px] md:h-[250px] lg:w-[350px] lg:h-[350px] bg-green-500/[0.03] rounded-full blur-[60px] md:blur-[80px] lg:blur-[100px] pointer-events-none" />
        {/* Spotlight behind headline */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] h-[200px] md:w-[600px] md:h-[300px] lg:w-[800px] lg:h-[400px] bg-gradient-radial from-white/[0.03] to-transparent rounded-full blur-[40px] md:blur-[50px] lg:blur-[60px] pointer-events-none" style={{ background: 'radial-gradient(ellipse at center, rgba(255,255,255,0.04) 0%, transparent 70%)' }} />
        <div className="relative z-10 max-w-[1800px] mx-auto w-full px-4 md:px-6 lg:px-12 flex flex-col items-center my-auto py-10 lg:py-0 lg:pb-[96px]" style={{ zIndex: 2 }}>
          {/* ── Row 1: Centered Text Block ── */}
          <div className="text-center max-w-3xl space-y-3 lg:space-y-4 mb-6 lg:mb-10">
            <div className="flex items-center justify-center gap-3 text-gray-600 text-[10px] font-bold tracking-[0.4em] uppercase">
              <div className="w-2 h-2 bg-white" />
              RLD Protocol
            </div>
            <h1 className="text-3xl sm:text-4xl md:text-5xl lg:text-7xl font-bold tracking-tighter leading-[0.95] uppercase bg-gradient-to-b from-white via-white to-gray-500 bg-clip-text text-transparent">
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
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 lg:gap-4 w-full max-w-[1100px]">
            {/* Card 1 — Synthetic Bond */}
            <a
              href="/bonds"
              className="group border border-white/[0.08] bg-white/[0.02] backdrop-blur-sm hover:border-cyan-500/40 hover:shadow-[0_0_30px_rgba(34,211,238,0.08)] transition-all duration-500 lg:scale-[1.02] lg:hover:scale-[1.04]"
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
          <div className="mt-6 lg:mt-10 flex flex-col items-center gap-3 lg:gap-5" style={heroStep(3)}>
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
                    className="flex items-center gap-2 lg:gap-2.5 text-gray-500 hover:text-white transition-colors duration-300 group px-3 lg:px-5 py-1"
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
                    <div className="hidden sm:block w-px h-4 bg-white/10" />
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
