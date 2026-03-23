import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import useInView from '../../hooks/useInView'
import Header from '../layout/Header'

// ── Shared grain overlay ──────────────────────────────────────────
const GRAIN_SVG = `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.08'/%3E%3C/svg%3E")`
const Grain = () => (
  <div
    className="pointer-events-none absolute inset-0 opacity-25"
    style={{ backgroundImage: GRAIN_SVG, backgroundSize: '192px 192px' }}
  />
)

/* ════════════════════════════════════════════════════
   HERO
════════════════════════════════════════════════════ */

function LiveTicker() {
  const rand = () => Math.floor(Math.random() * 0xffffff).toString(16).toUpperCase().padStart(6, '0')
  const [vals, setVals] = useState(() => Array.from({ length: 3 }, rand))
  useEffect(() => {
    const id = setInterval(() => setVals(p => {
      const n = [...p]; n[Math.floor(Math.random() * 3)] = rand(); return n
    }), 200)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="flex items-center gap-4 mb-5 select-none" aria-hidden="true">
      {vals.map((v, i) => (
        <span key={i} className="font-jbm text-[10px] text-[#2a2a2a] tracking-widest">{v}</span>
      ))}
      <span className="font-jbm text-[10px] text-[#222] tracking-widest">— LIVE</span>
    </div>
  )
}

function HeroSection() {
  const [vis, setVis] = useState(false)
  useEffect(() => { const t = setTimeout(() => setVis(true), 80); return () => clearTimeout(t) }, [])

  return (
    <section className="relative bg-[#050505] overflow-hidden min-h-screen flex flex-col">
      <Grain />

      {/* Header lives inside the hero for the homepage */}
      <div className="relative z-20">
        <Header isCapped={false} ratesLoaded={true} transparent />
      </div>

      {/* Hero body — flex-1 so it fills the remaining space and centers content */}
      <div className="relative z-10 flex-1 flex items-center justify-center py-20 px-8 md:px-14 lg:ml-[120px] mb-[50px]" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>

        <div className={`w-full max-w-[800px] transition-all duration-700 ${vis ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-5'}`}>

          <LiveTicker />

          {/* HEADLINE */}
          <h1 className="mb-5 leading-[1.08] lg:tracking-[-0.025em]">
            <span className="block text-white font-['Space_Grotesk'] font-light"
              style={{ fontSize: 'clamp(35px, 5.5vw, 62px)' }}>
              Interest Rate Derivatives
            </span>
            <span className="block text-[#666] font-['Space_Grotesk'] font-light"
              style={{ fontSize: 'clamp(35px, 5.5vw, 62px)' }}>
              for On-Chain Finance
            </span>
          </h1>

          {/* Tagline */}
          <p className="text-[14px] text-[#999] tracking-[0.05em] mb-8">
            Fix yields.&nbsp; Trade rates.&nbsp; Insure solvency.
          </p>

          {/* Body blurbs */}
          <div className="space-y-6 mb-10 max-w-[620px]">
            <p className="text-[12px] leading-[1.9] text-[#888]">
              <span className="text-white tracking-[0.12em] uppercase text-[11px] mr-2">Synthetic Bonds</span>
              Lock in today&apos;s rates — fix your yield or borrowing cost for leveraged
              basis trading. One pool, any maturity, no liquidity fragmentation and rolls.
            </p>
            <p className="text-[12px] leading-[1.9] text-[#888]">
              <span className="text-white tracking-[0.12em] uppercase text-[11px] mr-2">CDS</span>
              Insure underlying pool solvency with 100% payout on bankruptcy.
              Parametric, trustless, and instant settlement.
            </p>
            <p className="text-[12px] leading-[1.9] text-[#888]">
              <span className="text-white tracking-[0.12em] uppercase text-[11px] mr-2">Perps</span>
              Trade interest rates as a volatility instrument. Capitalize on rates &amp; crypto cointegration.
            </p>
          </div>

          {/* CTAs */}
          <div className="flex flex-wrap items-center gap-10 font-bold">
            <Link
              to="/bonds"
              id="cta-launch-app-hero"
              className="flex items-center gap-2 px-12 py-[12px] border border-[#555]
                         text-[11px] tracking-[0.22em] uppercase text-white font-jbm
                         hover:border-white hover:bg-white hover:text-black
                         transition-all duration-200"
            >
              Launch App ↗
            </Link>
            <a
              href="https://docs.rld.fi/introduction/rate-level-derivatives.html"
              id="cta-docs"
              className="text-[11px] tracking-[0.22em] uppercase text-[#666] font-jbm
                         hover:text-[#ccc] transition-colors duration-200 border-b border-transparent
                         hover:border-[#555] pb-[1px]"
            >
              Docs ↗
            </a>
          </div>
        </div>
      </div>

      {/* Status strip */}
      <div className="absolute bottom-0 left-0 right-0 z-10 flex items-center justify-between px-8 md:px-14 py-3 border-t border-[#111]" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>
        <span className="font-jbm text-[11px] tracking-[0.2em] uppercase text-[#666]">Testnet Live</span>
        <span className="font-jbm text-[11px] tracking-[0.2em] uppercase text-[#666]">V.01 / Experimental Beta</span>
      </div>
    </section>
  )
}

/* ════════════════════════════════════════════════════
   USE CASES — Synthetic Bonds  (v2 design)
════════════════════════════════════════════════════ */

function UseCasesSection() {
  const [labelRef, labelInView] = useInView(0.05)
  const [bodyRef, bodyInView] = useInView(0.05)
  const [cardsRef, cardsInView] = useInView(0.05)

  return (
    <section className="relative bg-[#050505]/95 min-h-screen flex flex-col justify-center px-8 md:px-14 py-20 border-t border-[#111]" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>
      <Grain />
      <div className="relative z-10 max-w-[1100px] mx-auto w-full">

        {/* Section label */}
        <div
          ref={labelRef}
          className="flex items-center gap-3 mb-14 transition-all duration-500"
          style={{ opacity: labelInView ? 1 : 0, transform: labelInView ? 'translateY(0)' : 'translateY(10px)' }}
        >
          <span className="font-jbm text-[#333] text-[11px]">|—</span>
          <span className="font-jbm text-[12px] tracking-[0.28em] uppercase text-[#333]">Synthetic Bonds</span>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        {/* Two-column: copy + metrics */}
        <div
          ref={bodyRef}
          className="grid grid-cols-1 lg:grid-cols-2 gap-16 mb-16 transition-all duration-[600ms]"
          style={{ opacity: bodyInView ? 1 : 0, transform: bodyInView ? 'translateY(0)' : 'translateY(16px)' }}
        >
          {/* Left — headline + body */}
          <div>
            <h2
              className="font-['Space_Grotesk'] font-light text-white leading-[1.1] tracking-[-0.02em] mb-6"
              style={{ fontSize: 'clamp(28px, 3.5vw, 46px)' }}
            >
              Fix Your Yield.<br />
              <span className="text-[#555]">Any Maturity. One Pool.</span>
            </h2>
            <p className="text-[12px] leading-[1.9] text-[#666] max-w-[460px] mb-6">
              Deposit into an RLD pool and take the fixed-rate side of an interest rate swap.
              Your yield is locked at entry — regardless of how floating rates move.
              No liquidity fragmentation, no roll risk, permissionless exit.
            </p>
            <Link
              to="/bonds"
              className="inline-flex items-center gap-2 px-6 py-[11px] border border-white
                         font-jbm text-[10px] tracking-[0.22em] uppercase text-white
                         hover:bg-white hover:text-black transition-all duration-200"
            >
              Explore Bonds ↗
            </Link>
          </div>

          {/* Right — metrics */}
          <div className="flex flex-col justify-center">
            <div className="border border-[#141414] divide-y divide-[#141414]">
              {[
                ['Underlying Yield', 'Lending protocols & T-bills'],
                ['Maturity', 'Any — from 1 hour to 1 year'],
                ['Deposit Token', 'Stablecoins / Tokenized Treasuries'],
                ['Settlement', 'Instant, on-chain'],
                ['Exit', 'Permissionless, no lockup'],
                ['Roll Risk', 'None — single pool design'],
              ].map(([label, value]) => (
                <div key={label} className="flex items-baseline justify-between px-6 py-4">
                  <span className="font-jbm text-[10px] tracking-[0.18em] uppercase text-[#444]">{label}</span>
                  <span className="font-jbm text-[12px] text-[#888]">{value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Use-case cards */}
        <div ref={cardsRef} className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Fixed Yield */}
          <div
            className="relative border border-[#141414] bg-[#111] p-8 transition-all duration-700"
            style={{ opacity: cardsInView ? 1 : 0, transform: cardsInView ? 'translateY(0)' : 'translateY(20px)' }}
          >
            <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#222]" />
            <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#222]" />
            <div className="flex items-center gap-3 mb-5">
              <span className="font-jbm text-[9px] tracking-[0.28em] text-[#333]">01</span>
              <span className="font-jbm text-[9px] tracking-[0.22em] uppercase text-[#666] border border-[#1e1e1e] px-2 py-[2px]">Yield</span>
            </div>
            <h3
              className="font-['Space_Grotesk'] font-light text-white leading-[1.15] tracking-[-0.01em] mb-3"
              style={{ fontSize: 'clamp(18px, 2vw, 24px)' }}
            >
              Fixed Yield
            </h3>
            <p className="text-[11px] leading-[1.85] text-[#666]">
              Lock in a predictable return on stablecoins. Deposit USDC, receive a fixed rate
              for your chosen maturity. No lockups, no rebalancing, no governance risk.
            </p>
          </div>

          {/* Fixed-Rate Leverage */}
          <div
            className="relative border border-[#141414] bg-[#111] p-8 transition-all duration-700"
            style={{ transitionDelay: '80ms', opacity: cardsInView ? 1 : 0, transform: cardsInView ? 'translateY(0)' : 'translateY(20px)' }}
          >
            <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#222]" />
            <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#222]" />
            <div className="flex items-center gap-3 mb-5">
              <span className="font-jbm text-[9px] tracking-[0.28em] text-[#333]">02</span>
              <span className="font-jbm text-[9px] tracking-[0.22em] uppercase text-[#666] border border-[#1e1e1e] px-2 py-[2px]">Leverage</span>
            </div>
            <h3
              className="font-['Space_Grotesk'] font-light text-white leading-[1.15] tracking-[-0.01em] mb-3"
              style={{ fontSize: 'clamp(18px, 2vw, 24px)' }}
            >
              Fixed-Rate Leverage
            </h3>
            <p className="text-[11px] leading-[1.85] text-[#666]">
              Running a delta-neutral basis trade? Fix your borrow cost so you can
              receive bull-market funding while paying a predictable rate.
              Eliminates rate-spike risk that compresses P&L.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}

/* ════════════════════════════════════════════════════
   SOLVENCY INSURANCE
════════════════════════════════════════════════════ */

function Stat({ value, label }) {
  return (
    <div className="border-l border-[#1e1e1e] pl-5">
      <div
        className="font-['Space_Grotesk'] font-light text-white leading-none mb-1"
        style={{ fontSize: 'clamp(24px, 2.8vw, 36px)' }}
      >
        {value}
      </div>
      <div className="font-jbm text-[10px] tracking-[0.18em] uppercase text-[#444]">{label}</div>
    </div>
  )
}

function RiskCard({ index, title, description, inView, delay }) {
  return (
    <div
      className="relative border border-[#141414] bg-[#0d0d0d] p-7 flex flex-col gap-5 transition-all duration-[600ms]"
      style={{
        transitionDelay: `${delay}ms`,
        opacity: inView ? 1 : 0,
        transform: inView ? 'translateY(0)' : 'translateY(18px)',
      }}
    >
      <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#1e1e1e]" />
      <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#1e1e1e]" />

      <div className="flex items-start justify-between">
        <span className="font-jbm text-[9px] tracking-[0.3em] text-[#252525]">0{index}</span>
        <span className="font-jbm text-[8px] tracking-[0.22em] uppercase text-[#1e1e1e] border border-[#181818] px-2 py-[2px]">
          Covered
        </span>
      </div>

      <h3
        className="font-['Space_Grotesk'] font-light text-white leading-[1.15] tracking-[-0.01em]"
        style={{ fontSize: 'clamp(18px, 2vw, 24px)' }}
      >
        {title}
      </h3>

      <p className="font-jbm text-[11px] leading-[1.85] text-[#666] flex-1">{description}</p>
      <div className="h-px w-8 bg-[#222]" />
    </div>
  )
}

function SolvencyInsuranceSection() {
  const [labelRef, labelInView] = useInView(0.05)
  const [bodyRef, bodyInView] = useInView(0.05)
  const [cardsRef, cardsInView] = useInView(0.05)

  return (
    <section className="relative bg-[#050505]/95 min-h-screen flex flex-col justify-center px-8 md:px-14 py-20 border-t border-[#111]" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>
      <Grain />
      <div className="relative z-10 max-w-[1100px] mx-auto w-full">

        <div
          ref={labelRef}
          className="flex items-center gap-3 mb-14 transition-all duration-500"
          style={{ opacity: labelInView ? 1 : 0, transform: labelInView ? 'translateY(0)' : 'translateY(10px)' }}
        >
          <span className="font-jbm text-[#333] text-[11px]">|—</span>
          <span className="font-jbm text-[12px] tracking-[0.28em] uppercase text-[#333]">Solvency Insurance</span>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        <div
          ref={bodyRef}
          className="grid grid-cols-1 lg:grid-cols-2 gap-12 mb-16 transition-all duration-[600ms]"
          style={{ opacity: bodyInView ? 1 : 0, transform: bodyInView ? 'translateY(0)' : 'translateY(16px)' }}
        >
          <div>
            <h2
              className="font-['Space_Grotesk'] font-light text-white leading-[1.1] tracking-[-0.02em] mb-6"
              style={{ fontSize: 'clamp(28px, 3.5vw, 46px)' }}
            >
              Insure Protocol<br />
              <span className="text-[#666]">Solvency On-Chain</span>
            </h2>
            <p className="text-[12px] leading-[1.9] text-[#666] max-w-[460px] mb-8">
              Protocol insolvency is DeFi&apos;s largest unpriced tail risk.
              RLD Credit Default Swaps let you hedge it — parametric trigger,
              trustless execution, and 100% notional payout.
            </p>
            <div className="inline-flex items-center gap-3 px-6 py-[11px] border border-[#1e1e1e] cursor-not-allowed">
              <span className="font-jbm text-[10px] tracking-[0.22em] uppercase text-[#333]">Explore CDS</span>
              <span className="font-jbm text-[9px] tracking-[0.2em] uppercase text-cyan-900 border border-cyan-900/40 px-1.5 py-px">Soon</span>
            </div>
          </div>

          <div className="flex flex-col justify-center">
            <div className="grid grid-cols-2 gap-y-8 gap-x-6">
              <Stat value="100%" label="Payout on trigger" />
              <Stat value="Instant" label="Settlement" />
              <Stat value="Parametric" label="Trigger mechanism" />
              <Stat value="Trustless" label="No manual claim" />
            </div>
          </div>
        </div>

        <div ref={cardsRef} className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          <RiskCard index={1} inView={cardsInView} delay={0}
            title="Depeg Event"
            description="Stablecoin or LST depegs from its target, collapsing the pool's collateral value faster than liquidations can fire."
          />
          <RiskCard index={2} inView={cardsInView} delay={80}
            title="Oracle Failure"
            description="A manipulated or stale price feed triggers mass liquidations at incorrect prices, leaving the protocol insolvent. Parametric trigger activates on confirmed health breach."
          />
          <RiskCard index={3} inView={cardsInView} delay={160}
            title="Security Exploit"
            description="Funds drained via a reentrancy attack, logic bug, or upgrade vulnerability. If the exploit collapses the health factor below threshold, CDS settlement fires automatically."
          />
          <RiskCard index={4} inView={cardsInView} delay={240}
            title="Bad Debt"
            description="Underwater positions accumulate past the protocol's reserves — common in high-leverage or illiquid markets. RLD tracks the health factor in real time and settles instantly."
          />
        </div>
      </div>
    </section>
  )
}

/* ════════════════════════════════════════════════════
   RATE PERPETUALS
════════════════════════════════════════════════════ */

function RateChart({ inView }) {
  return (
    <div
      className="relative border border-[#141414] bg-[#0b0b0b] p-6 transition-all duration-700"
      style={{ opacity: inView ? 1 : 0, transform: inView ? 'translateY(0)' : 'translateY(20px)' }}
    >
      <div className="flex items-center justify-between mb-5">
        <span className="font-jbm text-[9px] tracking-[0.28em] uppercase text-[#2a2a2a]">Borrow Rate / Time</span>
        <span className="font-jbm text-[9px] tracking-[0.2em] uppercase text-[#666]">Illustrative</span>
      </div>
      <svg viewBox="0 0 320 140" className="w-full" preserveAspectRatio="none">
        {[0, 35, 70, 105, 140].map((y) => (
          <line key={y} x1="0" y1={y} x2="320" y2={y} stroke="#161616" strokeWidth="1" />
        ))}
        <line x1="0" y1="115" x2="320" y2="115" stroke="#222" strokeWidth="1" strokeDasharray="4 4" />
        <text x="3" y="125" fill="#444444" fontSize="7" fontFamily="monospace">FED FLOOR</text>
        <polyline
          points="0,110 40,108 70,105 90,95 100,60 110,30 120,15 135,22 150,45 170,80 190,90 220,100 250,108 280,110 320,110"
          fill="none" stroke="#333" strokeWidth="1.5" strokeLinejoin="round"
        />
        <polyline
          points="90,95 100,60 110,30 120,15 135,22 150,45"
          fill="none" stroke="#888" strokeWidth="1.5" strokeLinejoin="round"
        />
        <circle cx="120" cy="15" r="2.5" fill="#aaa" />
        <text x="124" y="13" fill="#666" fontSize="7" fontFamily="monospace">5–10×</text>
        <circle cx="0" cy="110" r="2" fill="#333" />
      </svg>
      <div className="flex items-center gap-6 mt-4">
        <div className="flex items-center gap-2">
          <div className="w-6 h-px bg-[#888]" />
          <span className="font-jbm text-[9px] text-[#444]">Demand spike range</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-6 h-px bg-[#333]" />
          <span className="font-jbm text-[9px] text-[#333]">Baseline range</span>
        </div>
      </div>
    </div>
  )
}

function Feature({ index, label, title, body, inView, delay }) {
  return (
    <div
      className="relative border border-[#141414] bg-[#111] p-8 transition-all duration-[600ms]"
      style={{
        transitionDelay: `${delay}ms`,
        opacity: inView ? 1 : 0,
        transform: inView ? 'translateY(0)' : 'translateY(14px)',
      }}
    >
      <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#222]" />
      <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#222]" />
      <div className="flex items-center gap-3 mb-5">
        <span className="font-jbm text-[9px] tracking-[0.28em] text-[#333]">0{index}</span>
        <span className="font-jbm text-[9px] tracking-[0.22em] uppercase text-[#666] border border-[#1e1e1e] px-2 py-[2px]">
          {label}
        </span>
      </div>
      <h3
        className="font-['Space_Grotesk'] font-light text-white leading-[1.15] tracking-[-0.015em] mb-3"
        style={{ fontSize: 'clamp(18px, 2vw, 24px)' }}
      >
        {title}
      </h3>
      <p className="text-[11px] leading-[1.85] text-[#666]">{body}</p>
    </div>
  )
}

function RatePerpsSection() {
  const [labelRef, labelInView] = useInView(0.05)
  const [topRef, topInView] = useInView(0.05)
  const [featRef, featInView] = useInView(0.05)

  return (
    <section className="relative bg-[#050505]/95 min-h-screen flex flex-col justify-center px-8 md:px-14 py-20 border-t border-[#111]" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>
      <Grain />
      <div className="relative z-10 max-w-[1100px] mx-auto w-full">

        <div
          ref={labelRef}
          className="flex items-center gap-3 mb-14 transition-all duration-500"
          style={{ opacity: labelInView ? 1 : 0, transform: labelInView ? 'translateY(0)' : 'translateY(10px)' }}
        >
          <span className="font-jbm text-[#555] text-[11px]">|—</span>
          <span className="font-jbm text-[12px] tracking-[0.28em] uppercase text-[#333]">Rate Perpetuals</span>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        <div ref={topRef} className="grid grid-cols-1 lg:grid-cols-2 gap-12 mb-16">
          <div
            className="transition-all duration-[600ms]"
            style={{ opacity: topInView ? 1 : 0, transform: topInView ? 'translateY(0)' : 'translateY(16px)' }}
          >
            <h2
              className="font-['Space_Grotesk'] font-light text-white leading-[1.1] tracking-[-0.02em] mb-6"
              style={{ fontSize: 'clamp(28px, 3.5vw, 46px)' }}
            >
              Trade Interest Rates
              <br />
              <span className="text-[#555]">as a Volatility Asset</span>
            </h2>
            <p className="text-[12px] leading-[1.9] text-[#666] max-w-[440px] mb-8">
              DeFi borrow rates are not symmetric. They have a hard floor at the
              risk-free rate but can surge 5–10× during periods of high leverage
              demand — creating a structurally asymmetric payoff profile that
              perpetual traders can exploit.
            </p>
            <Link
              to="/markets/perps"
              className="inline-flex items-center gap-2 px-6 py-[11px] border border-white
                         font-jbm text-[10px] tracking-[0.22em] uppercase text-white
                         hover:bg-white hover:text-black transition-all duration-200"
            >
              Trade Rates <span className="text-[#555]">↗</span>
            </Link>
          </div>

          <RateChart inView={topInView} />
        </div>

        <div ref={featRef} className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <Feature index={1} inView={featInView} delay={0}
            label="Asymmetry"
            title="Floored by policy, spiked by demand"
            body="The FED rate sets a structural floor — rates almost never go below it. But during increased demand, borrow rates can spike 5–10× overnight. This asymmetry creates attractive asymmetric payoff."
          />
          <Feature index={2} inView={featInView} delay={100}
            label="Cross-Margin"
            title="Unified margin across your entire account"
            body="Margin with ERC20 assets, open limit & TWAP orders, and Uniswap V4 LP positions — all inside a single PrimeBroker. One account, full cross-margin efficiency."
          />
          <Feature index={3} inView={featInView} delay={200}
            label="Volatility"
            title="Rates co-move with market sentiment"
            body="High correlation between interest rates and market sentiment. Traders can go long rates ahead of bull markets or short when they believe the market is overheated and rates will revert."
          />
        </div>
      </div>
    </section>
  )
}

/* ════════════════════════════════════════════════════
   HOW IT WORKS
════════════════════════════════════════════════════ */

function AgentNativeSection() {
  const [labelRef, labelInView] = useInView(0.05)
  const [bodyRef, bodyInView] = useInView(0.05)
  const [cardsRef, cardsInView] = useInView(0.05)

  const pillars = [
    {
      num: '01',
      tag: 'Signing',
      title: 'Off-Chain Gasless Operations',
      body: 'Sign trades, deposits, and delegations off-chain via Permit2. No approval transactions, no gas required from the signer. The protocol submits and settles — your agent just signs.',
    },
    {
      num: '02',
      tag: 'Delegation',
      title: 'Segregated Account Control',
      body: 'Each margin account is an NFT with on-chain operator permissions. Authorize execution agents without transferring custody. Revoke in one transaction. Ownership and execution are fully separated.',
    },
    {
      num: '03',
      tag: 'Data',
      title: 'Open Market Data Infrastructure',
      body: 'Real-time and historical rate data across lending protocols — GraphQL and WebSocket. No API keys, no rate limits, no vendor lock-in. Free infrastructure for agents and institutions alike.',
    },
  ]

  return (
    <section className="relative bg-[#050505]/95 min-h-screen flex flex-col justify-center px-8 md:px-14 py-20 border-t border-[#111]" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>
      <Grain />
      <div className="relative z-10 max-w-[1100px] mx-auto w-full">

        <div
          ref={labelRef}
          className="flex items-center gap-3 mb-14 transition-all duration-500"
          style={{ opacity: labelInView ? 1 : 0, transform: labelInView ? 'translateY(0)' : 'translateY(10px)' }}
        >
          <span className="font-jbm text-[#333] text-[11px]">|—</span>
          <span className="font-jbm text-[12px] tracking-[0.28em] uppercase text-[#333]">Programmable Infrastructure</span>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        <div
          ref={bodyRef}
          className="grid grid-cols-1 lg:grid-cols-2 gap-12 mb-16 transition-all duration-[600ms]"
          style={{ opacity: bodyInView ? 1 : 0, transform: bodyInView ? 'translateY(0)' : 'translateY(16px)' }}
        >
          <div>
            <h2
              className="font-['Space_Grotesk'] font-light text-white leading-[1.1] tracking-[-0.02em] mb-6"
              style={{ fontSize: 'clamp(28px, 3.5vw, 46px)' }}
            >
              Zero-Gas<br />
              <span className="text-[#555]">Agent Execution.</span>
            </h2>
            <p className="text-[12px] leading-[1.9] text-[#666] max-w-[460px] mb-8">
              Sign operations off-chain. The protocol handles submission
              and settlement. Deployed on Tempo — sub-cent fees,
              500ms finality, and fee sponsorship so your agents
              never need to hold gas tokens.
            </p>
            <a
              href="https://docs.rld.fi/introduction/rate-level-derivatives.html"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-6 py-[11px] border border-white
                         font-jbm text-[10px] tracking-[0.22em] uppercase text-white
                         hover:bg-white hover:text-black transition-all duration-200"
            >
              Documentation <span className="text-[#555]">↗</span>
            </a>
          </div>

          <div className="flex flex-col justify-center">
            <div className="border border-[#141414] divide-y divide-[#141414]">
              {[
                ['Signing', 'Off-chain via Permit2 — gasless'],
                ['Finality', '500ms — deterministic'],
                ['Fees', 'Sub-cent — sponsorable'],
                ['Accounts', 'NFT — segregated, portable'],
                ['Delegation', 'Operator access — revocable'],
                ['Market Data', 'GraphQL + WebSocket — open'],
              ].map(([label, value]) => (
                <div key={label} className="flex items-baseline justify-between px-6 py-4">
                  <span className="font-jbm text-[10px] tracking-[0.18em] uppercase text-[#444]">{label}</span>
                  <span className="font-jbm text-[12px] text-[#888]">{value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div ref={cardsRef} className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {pillars.map((p, i) => (
            <div
              key={p.num}
              className="relative border border-[#141414] bg-[#111] p-8 flex flex-col gap-4 transition-all duration-700"
              style={{ transitionDelay: `${i * 80}ms`, opacity: cardsInView ? 1 : 0, transform: cardsInView ? 'translateY(0)' : 'translateY(16px)' }}
            >
              <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#222]" />
              <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#222]" />
              <div className="flex items-center gap-3 mb-2">
                <span className="font-jbm text-[9px] tracking-[0.28em] text-[#333]">{p.num}</span>
                <span className="font-jbm text-[9px] tracking-[0.22em] uppercase text-[#666] border border-[#1e1e1e] px-2 py-[2px]">
                  {p.tag}
                </span>
              </div>
              <h3
                className="font-['Space_Grotesk'] font-light text-white leading-[1.15] tracking-[-0.01em]"
                style={{ fontSize: 'clamp(18px, 2vw, 24px)' }}
              >
                {p.title}
              </h3>
              <p className="text-[11px] leading-[1.85] text-[#666]">{p.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

/* ════════════════════════════════════════════════════
   BENEFITS — WHY RLD
════════════════════════════════════════════════════ */

function BenefitsSection() {
  const [labelRef, labelInView] = useInView(0.05)
  const [headRef, headInView] = useInView(0.05)
  const [gridRef, gridInView] = useInView(0.05)

  const benefits = [
    { num: '01', title: 'One Pool, Any Maturity', body: 'All bond maturities share a single liquidity pool. No fragmentation, no thin order books, no roll risk. Enter and exit any duration without moving markets.' },
    { num: '02', title: 'Rate Convexity', body: 'Interest rates have a structural floor but no ceiling. 5–10× spikes during leverage demand surges create an asymmetric payoff profile that no other on-chain asset offers.' },
    { num: '03', title: 'On-Chain Credit Default Swaps', body: 'The first trustless, parametric solvency insurance in DeFi. 100% payout on trigger. No claims process, no counterparty risk. A market that doesn\u0027t exist anywhere else.' },
    { num: '04', title: 'Unified Cross-Margin', body: 'One account for all positions — bonds, perps, LP, and TWAP orders. Collateral is cross-margined automatically. No fragmented balances across products.' },
    { num: '05', title: 'Gasless Execution', body: 'Sign operations off-chain via Permit2. No gas tokens required. Deployed on Tempo instant 500ms finality, and full fee sponsorship for frictionless onboarding and execution.' },
    { num: '06', title: 'Permissionless Exit', body: 'Close any position at any time. No lock-ups, no withdrawal queues, no cooldown periods. Your capital is always accessible — one transaction to exit.' },
  ]

  return (
    <section className="relative bg-[#050505]/95 min-h-screen flex flex-col justify-center px-8 md:px-14 py-20 border-t border-[#111]" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>
      <Grain />
      <div className="relative z-10 max-w-[1100px] mx-auto w-full">

        <div
          ref={labelRef}
          className="flex items-center gap-3 mb-14 transition-all duration-500"
          style={{ opacity: labelInView ? 1 : 0, transform: labelInView ? 'translateY(0)' : 'translateY(10px)' }}
        >
          <span className="font-jbm text-[#333] text-[11px]">|—</span>
          <span className="font-jbm text-[12px] tracking-[0.28em] uppercase text-[#333]">Why RLD</span>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        <h2
          ref={headRef}
          className="font-['Space_Grotesk'] font-light text-white leading-[1.1] tracking-[-0.02em] mb-16 max-w-[600px] transition-all duration-[600ms]"
          style={{ fontSize: 'clamp(28px, 3.5vw, 46px)', opacity: headInView ? 1 : 0, transform: headInView ? 'translateY(0)' : 'translateY(14px)' }}
        >
          The Stability Layer<br />
          <span className="text-[#555]">for DeFi.</span>
        </h2>

        <div ref={gridRef} className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {benefits.map((b, i) => (
            <div
              key={b.num}
              className="relative border border-[#141414] bg-[#111] p-8 flex flex-col gap-4 transition-all duration-700"
              style={{ transitionDelay: `${i * 60}ms`, opacity: gridInView ? 1 : 0, transform: gridInView ? 'translateY(0)' : 'translateY(16px)' }}
            >
              <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#222]" />
              <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#222]" />
              <span className="font-jbm text-[9px] tracking-[0.3em] text-[#333]">{b.num}</span>
              <h3 className="font-['Space_Grotesk'] font-light text-white text-[18px] leading-tight tracking-[-0.01em]">
                {b.title}
              </h3>
              <p className="text-[11px] leading-[1.85] text-[#666]">{b.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

/* ════════════════════════════════════════════════════
   CTA FOOTER + GLOBAL FOOTER
════════════════════════════════════════════════════ */

function CoreArchitectureSection() {
  const [ctaRef, ctaInView] = useInView(0.1)

  return (
    <section className="relative bg-[#050505]/95 border-t border-[#111] px-8 md:px-14" style={{ fontFamily: "'JetBrains Mono', 'IBM Plex Mono', Courier New, monospace" }}>
      <Grain />

      <div
        ref={ctaRef}
        className="relative z-10 max-w-[1100px] mx-auto py-24 lg:py-32 flex flex-col items-center text-center transition-all duration-700"
        style={{ opacity: ctaInView ? 1 : 0, transform: ctaInView ? 'translateY(0)' : 'translateY(20px)' }}
      >
        <h2
          className="font-['Space_Grotesk'] font-light text-white leading-[1.1] tracking-[-0.02em] mb-4"
          style={{ fontSize: 'clamp(28px, 4vw, 52px)' }}
        >
          Enter the RLD Ecosystem
        </h2>
        <p className="font-jbm text-[12px] text-[#666] tracking-[0.06em] mb-10 max-w-[400px]">
          Testnet is live. Fix yields, trade rate movements, and insure
          solvency — entirely on-chain.
        </p>

        <div className="flex items-center gap-6">
          <Link
            to="/bonds"
            className="flex items-center gap-2 px-10 py-[13px] border border-white
                       font-jbm text-[11px] tracking-[0.22em] uppercase text-white font-bold
                       hover:bg-white hover:text-black transition-all duration-200"
          >
            Launch App ↗
          </Link>
        </div>
      </div>

      {/* Footer */}
      <div className="relative z-10 border-t border-[#1e1e1e]">
        <div className="max-w-[1100px] mx-auto lg:px-0 pt-6 pb-8 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-6">
            <span className="font-jbm text-[11px] tracking-[0.3em] uppercase text-white font-bold">RLD</span>
            <span className="font-jbm text-[9px] tracking-[0.18em] uppercase text-[#888]">Ethereum Testnet</span>
            <span className="font-jbm text-[9px] tracking-[0.18em] uppercase text-[#888]">V.01</span>
          </div>
          <div className="flex items-center gap-6">
            {[
              { label: 'Twitter', href: 'https://x.com/lumisfi_' },
              { label: 'GitHub', href: 'https://github.com/leooos33/RLD' },
              { label: 'Docs', href: 'https://docs.rld.fi/introduction/rate-level-derivatives.html' },
            ].map((link) => (
              <a
                key={link.label}
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-jbm text-[10px] tracking-[0.15em] uppercase text-[#888] hover:text-white transition-colors duration-200"
              >
                {link.label}
              </a>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

/* ════════════════════════════════════════════════════
   HOMEPAGE — assembles all sections
════════════════════════════════════════════════════ */

export default function Homepage() {
  useEffect(() => {
    async function diagnose() {
      console.group('[Homepage] Font diagnosis')

      // 1. Check what /fonts.css actually contains
      try {
        const r = await fetch('/fonts.css', { cache: 'no-store' })
        const text = await r.text()
        const hasJBM = text.includes('JetBrains Mono')
        const hasSG = text.includes('Space Grotesk')
        console.log(`/fonts.css → ${r.status} ${r.statusText} | JBM: ${hasJBM ? '✅' : '❌'} | SpaceGrotesk: ${hasSG ? '✅' : '❌'} | ${text.length} bytes`)
        if (!hasJBM) console.warn('fonts.css content (first 300 chars):\n' + text.slice(0, 300))
      } catch (e) { console.error('/fonts.css fetch failed:', e) }

      // 2. Check /fonts/JetBrainsMono-latin.woff2 serves correctly
      try {
        const r = await fetch('/fonts/JetBrainsMono-latin.woff2', { cache: 'no-store', method: 'HEAD' })
        console.log(`/fonts/JetBrainsMono-latin.woff2 → ${r.status} | size: ${r.headers.get('content-length')} bytes | type: ${r.headers.get('content-type')}`)
      } catch (e) { console.error('/fonts/JetBrainsMono-latin.woff2 fetch failed:', e) }

      // 3. Force-load via FontFace API to bypass CSS pipeline
      try {
        const face = new FontFace('JBM-Debug', "url('/fonts/JetBrainsMono-latin.woff2')")
        await face.load()
        console.log(`FontFace force-load: ${face.status} — ${face.family}`)
      } catch (e) { console.error('FontFace force-load failed:', e.message) }

      // 4. List all known faces
      await document.fonts.ready
      console.log('Faces in FontFaceSet:')
      document.fonts.forEach(f => console.log(`  ${f.family} w${f.weight} → ${f.status}`))

      console.groupEnd()
    }
    diagnose()
  }, [])

  return (
    <div className="bg-[#080808] font-mono" >
      <HeroSection />
      <UseCasesSection />
      <SolvencyInsuranceSection />
      <RatePerpsSection />
      <AgentNativeSection />
      <BenefitsSection />
      <CoreArchitectureSection />
    </div>
  )
}
