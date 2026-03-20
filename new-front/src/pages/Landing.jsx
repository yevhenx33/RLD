/**
 * Landing Page — v2 redesign
 *
 * This is the blank slate. We'll iterate on this step by step.
 */

import { useState, useEffect } from "react";

export default function Landing() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="min-h-screen bg-[#050505] text-white font-mono">

      {/* ── Header ─────────────────────────────────────────── */}
      <header
        className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
          scrolled
            ? "bg-[#050505]/90 backdrop-blur-md border-b border-[#1a1a1a]"
            : "bg-transparent border-b border-transparent"
        }`}
      >
        <div className="max-w-[1200px] mx-auto px-8 md:px-14 h-16 flex items-center justify-between">

          {/* Logo + Nav */}
          <div className="flex items-center gap-10">
            <a href="/" className="flex items-center gap-2 group">
              <span className="text-[14px] tracking-[0.35em] uppercase text-white font-bold group-hover:text-[#ccc] transition-colors">
                RLD
              </span>
              <span className="text-[9px] tracking-[0.2em] uppercase text-[#333] border border-[#222] px-1.5 py-px">
                Beta
              </span>
            </a>

            <nav className="hidden md:flex items-center gap-8">
              {[
                { label: "Bonds", href: "#bonds" },
                { label: "CDS", href: "#cds" },
                { label: "Perps", href: "#perps" },
                { label: "Docs", href: "https://docs.rld.fi/introduction/rate-level-derivatives.html", external: true },
              ].map((link) => (
                <a
                  key={link.label}
                  href={link.href}
                  {...(link.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                  className="text-[11px] tracking-[0.2em] uppercase text-[#666] hover:text-white transition-colors duration-200"
                >
                  {link.label}
                </a>
              ))}
            </nav>
          </div>

          {/* CTA */}
          <button className="px-6 py-2 border border-[#444] text-[11px] tracking-[0.2em] uppercase text-white hover:border-white hover:bg-white hover:text-black transition-all duration-200">
            Launch App ↗
          </button>
        </div>
      </header>

      {/* ── Hero ──────────────────────────────────────────── */}
      <section className="min-h-screen flex flex-col items-center justify-center px-8 pt-16">
        <p className="text-[12px] tracking-[0.3em] uppercase text-[#444] mb-6">
          V2 Design — Work in Progress
        </p>
        <h1
          className="font-display font-light text-center leading-[1.08] tracking-[-0.025em] mb-6"
          style={{ fontSize: "clamp(38px, 5.5vw, 68px)" }}
        >
          <span className="block text-white">Interest Rate Derivatives</span>
          <span className="block text-[#555]">for On-Chain Finance</span>
        </h1>
        <p className="text-[14px] text-[#666] tracking-[0.04em] text-center max-w-[520px] mb-10">
          Fix yields. Trade rates. Insure solvency.
        </p>
        <div className="flex items-center gap-6">
          <button className="px-10 py-3 border border-[#444] text-[12px] tracking-[0.2em] uppercase hover:border-white hover:bg-white hover:text-black transition-all duration-200">
            Launch App ↗
          </button>
          <a
            href="https://docs.rld.fi/introduction/rate-level-derivatives.html"
            className="text-[12px] tracking-[0.2em] uppercase text-[#555] hover:text-white transition-colors duration-200"
          >
            Docs ↗
          </a>
        </div>

        {/* ── Product Highlights ──────────────────────────── */}
        <div className="mt-16 grid grid-cols-1 md:grid-cols-3 gap-px bg-[#1a1a1a] border border-[#1a1a1a] max-w-[820px]">

          {/* Synthetic Bonds */}
          <div className="bg-[#0a0a0a] p-6 flex flex-col gap-3">
            <span className="text-[10px] tracking-[0.3em] uppercase text-[#444]">01</span>
            <h3 className="font-display font-light text-white text-[19px] leading-tight tracking-[-0.01em]">
              Synthetic Bonds
            </h3>
            <p className="text-[12px] leading-[1.8] text-[#555]">
              Lock in today's rates — fix your yield or borrowing cost for any maturity.
              One pool, no liquidity fragmentation, no rolls.
            </p>
          </div>

          {/* CDS */}
          <div className="bg-[#0a0a0a] p-6 flex flex-col gap-3">
            <span className="text-[10px] tracking-[0.3em] uppercase text-[#444]">02</span>
            <h3 className="font-display font-light text-white text-[19px] leading-tight tracking-[-0.01em]">
              Credit Default Swaps
            </h3>
            <p className="text-[12px] leading-[1.8] text-[#555]">
              Insure protocol solvency with 100% payout on bankruptcy.
              Parametric trigger, trustless execution, instant settlement.
            </p>
          </div>

          {/* Rate Perps */}
          <div className="bg-[#0a0a0a] p-6 flex flex-col gap-3">
            <span className="text-[10px] tracking-[0.3em] uppercase text-[#444]">03</span>
            <h3 className="font-display font-light text-white text-[19px] leading-tight tracking-[-0.01em]">
              Rate Perpetuals
            </h3>
            <p className="text-[12px] leading-[1.8] text-[#555]">
              Trade interest rates as a volatility instrument. Capitalize on
              rate spikes and crypto-rate cointegration with leveraged perps.
            </p>
          </div>

        </div>
      </section>

      {/* ── Synthetic Bonds ───────────────────────────────── */}
      <section className="relative border-t border-[#111] px-8 md:px-14 py-24 lg:py-32">
        <div className="max-w-[1100px] mx-auto">

          {/* Section label */}
          <div className="flex items-center gap-3 mb-16">
            <span className="text-[#333] text-[12px]">|—</span>
            <span className="text-[13px] tracking-[0.28em] uppercase text-[#333]">Synthetic Bonds</span>
            <span className="flex-1 h-px bg-[#141414]" />
          </div>

          {/* Two-column: copy + metrics */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 mb-20">

            {/* Left — headline + body */}
            <div>
              <h2
                className="font-display font-light text-white leading-[1.1] tracking-[-0.02em] mb-6"
                style={{ fontSize: "clamp(30px, 3.5vw, 48px)" }}
              >
                Fix Your Yield.<br />
                <span className="text-[#555]">Any Maturity. One Pool.</span>
              </h2>
              <p className="text-[13px] leading-[1.9] text-[#666] max-w-[460px] mb-4">
                Deposit into an RLD pool and take the fixed-rate side of an interest rate swap.
                Your yield is locked at entry — regardless of how floating rates move.
                No liquidity fragmentation, no roll risk, permissionless exit.
              </p>
              <p className="text-[13px] leading-[1.9] text-[#666] max-w-[460px] mb-10">
                Bonds settle on-chain against live oracle rates, so there's no counterparty
                risk beyond the smart contract itself. Maturities from 1 hour to 1 year.
              </p>
              <button className="px-8 py-3 border border-white text-[11px] tracking-[0.22em] uppercase text-white hover:bg-white hover:text-black transition-all duration-200">
                Explore Bonds ↗
              </button>
            </div>

            {/* Right — metrics */}
            <div className="flex flex-col justify-center">
              <div className="border border-[#141414] divide-y divide-[#141414]">
                {[
                  ["Underlying Yield", "Lending protocols & T-bills"],
                  ["Maturity", "Any — from 1 hour to 1 year"],
                  ["Deposit Token", "USDC / USDT / SOFR rates"],
                  ["Settlement", "Instant, on-chain"],
                  ["Exit", "Permissionless, no lockup"],
                  ["Roll Risk", "None — single pool design"],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-baseline justify-between px-6 py-4">
                    <span className="text-[11px] tracking-[0.18em] uppercase text-[#444]">{label}</span>
                    <span className="text-[13px] text-[#888]">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Use-case cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-px bg-[#1a1a1a]">

            {/* Fixed Yield */}
            <div className="bg-[#0a0a0a] p-8">
              <div className="flex items-center gap-3 mb-5">
                <span className="text-[10px] tracking-[0.28em] text-[#333]">01</span>
                <span className="text-[10px] tracking-[0.22em] uppercase text-[#555] border border-[#1a1a1a] px-2 py-[2px]">
                  Yield
                </span>
              </div>
              <h3
                className="font-display font-light text-white leading-[1.15] tracking-[-0.01em] mb-3"
                style={{ fontSize: "clamp(20px, 2vw, 26px)" }}
              >
                Fixed Yield
              </h3>
              <p className="text-[12px] leading-[1.85] text-[#555]">
                Lock in a predictable return on stablecoins. Deposit USDC, receive a fixed rate
                for your chosen maturity. No impermanent loss, no rebalancing, no governance risk.
                Your yield is your yield.
              </p>
            </div>

            {/* Fixed-Rate Leverage */}
            <div className="bg-[#0a0a0a] p-8">
              <div className="flex items-center gap-3 mb-5">
                <span className="text-[10px] tracking-[0.28em] text-[#333]">02</span>
                <span className="text-[10px] tracking-[0.22em] uppercase text-[#555] border border-[#1a1a1a] px-2 py-[2px]">
                  Leverage
                </span>
              </div>
              <h3
                className="font-display font-light text-white leading-[1.15] tracking-[-0.01em] mb-3"
                style={{ fontSize: "clamp(20px, 2vw, 26px)" }}
              >
                Fixed-Rate Leverage
              </h3>
              <p className="text-[12px] leading-[1.85] text-[#555]">
                Running a delta-neutral basis trade? Fix your borrow cost so you can
                receive bull‑market funding while paying a predictable rate.
                Eliminates rate-spike risk that compresses P&L.
              </p>
            </div>

          </div>
        </div>
      </section>

      {/* ── Credit Default Swaps ──────────────────────────── */}
      <section className="relative border-t border-[#111] px-8 md:px-14 py-24 lg:py-32">
        <div className="max-w-[1100px] mx-auto">

          {/* Section label */}
          <div className="flex items-center gap-3 mb-16">
            <span className="text-[#333] text-[12px]">|—</span>
            <span className="text-[13px] tracking-[0.28em] uppercase text-[#333]">Solvency Insurance</span>
            <span className="flex-1 h-px bg-[#141414]" />
          </div>

          {/* Two-column: copy + stats */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 mb-20">

            {/* Left — headline + body */}
            <div>
              <h2
                className="font-display font-light text-white leading-[1.1] tracking-[-0.02em] mb-6"
                style={{ fontSize: "clamp(30px, 3.5vw, 48px)" }}
              >
                Insure Protocol<br />
                <span className="text-[#555]">Solvency On-Chain</span>
              </h2>
              <p className="text-[13px] leading-[1.9] text-[#666] max-w-[460px] mb-4">
                Protocol insolvency is DeFi's largest unpriced tail risk.
                RLD Credit Default Swaps let you hedge it — parametric trigger,
                trustless execution, and 100% notional payout.
              </p>
              <p className="text-[13px] leading-[1.9] text-[#666] max-w-[460px] mb-10">
                No manual claims, no governance votes, no delays. When the health factor
                breaches the threshold, settlement fires automatically.
              </p>
              <div className="inline-flex items-center gap-3 px-8 py-3 border border-[#1e1e1e] cursor-not-allowed">
                <span className="text-[11px] tracking-[0.22em] uppercase text-[#333]">Explore CDS</span>
                <span className="text-[10px] tracking-[0.2em] uppercase text-cyan-700 border border-cyan-900/40 px-1.5 py-px">
                  Soon
                </span>
              </div>
            </div>

            {/* Right — stat blocks */}
            <div className="flex flex-col justify-center">
              <div className="grid grid-cols-2 gap-y-10 gap-x-8">
                {[
                  ["100%", "Payout on trigger"],
                  ["Instant", "Settlement"],
                  ["Parametric", "Trigger mechanism"],
                  ["Trustless", "No manual claim"],
                ].map(([value, label]) => (
                  <div key={label} className="border-l border-[#1e1e1e] pl-5">
                    <div
                      className="font-display font-light text-white leading-none mb-1"
                      style={{ fontSize: "clamp(26px, 2.8vw, 38px)" }}
                    >
                      {value}
                    </div>
                    <div className="text-[11px] tracking-[0.18em] uppercase text-[#444]">{label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Risk scenario cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-px bg-[#1a1a1a]">
            {[
              {
                index: 1,
                title: "Depeg Event",
                body: "Stablecoin or LST depegs from its target, collapsing collateral value faster than liquidations can fire.",
              },
              {
                index: 2,
                title: "Oracle Failure",
                body: "Manipulated or stale price feed triggers mass liquidations at incorrect prices, leaving the protocol insolvent.",
              },
              {
                index: 3,
                title: "Security Exploit",
                body: "Funds drained via reentrancy, logic bug, or upgrade vulnerability. If health factor collapses, CDS fires automatically.",
              },
              {
                index: 4,
                title: "Bad Debt",
                body: "Underwater positions accumulate past reserves — common in high-leverage or illiquid markets. Instant settlement on breach.",
              },
            ].map((card) => (
              <div key={card.index} className="relative bg-[#0a0a0a] p-7 flex flex-col gap-4">
                <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#1e1e1e]" />
                <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#1e1e1e]" />

                <div className="flex items-start justify-between">
                  <span className="text-[10px] tracking-[0.3em] text-[#252525]">0{card.index}</span>
                  <span className="text-[9px] tracking-[0.22em] uppercase text-[#1e1e1e] border border-[#181818] px-2 py-[2px]">
                    Covered
                  </span>
                </div>

                <h3
                  className="font-display font-light text-white leading-[1.15] tracking-[-0.01em]"
                  style={{ fontSize: "clamp(20px, 2vw, 26px)" }}
                >
                  {card.title}
                </h3>

                <p className="text-[12px] leading-[1.85] text-[#555] flex-1">{card.body}</p>
                <div className="h-px w-8 bg-[#222]" />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Rate Perpetuals ───────────────────────────────── */}
      <section className="relative border-t border-[#111] px-8 md:px-14 py-24 lg:py-32">
        <div className="max-w-[1100px] mx-auto">

          {/* Section label */}
          <div className="flex items-center gap-3 mb-16">
            <span className="text-[#333] text-[12px]">|—</span>
            <span className="text-[13px] tracking-[0.28em] uppercase text-[#333]">Rate Perpetuals</span>
            <span className="flex-1 h-px bg-[#141414]" />
          </div>

          {/* Two-column: copy + chart */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 mb-20">

            {/* Left — headline + body */}
            <div>
              <h2
                className="font-display font-light text-white leading-[1.1] tracking-[-0.02em] mb-6"
                style={{ fontSize: "clamp(30px, 3.5vw, 48px)" }}
              >
                Trade Interest Rates<br />
                <span className="text-[#555]">as a Volatility Asset</span>
              </h2>
              <p className="text-[13px] leading-[1.9] text-[#666] max-w-[440px] mb-4">
                DeFi borrow rates are not symmetric. They have a hard floor at the
                risk-free rate but can surge 5–10× during periods of high leverage
                demand — creating a structurally asymmetric payoff profile.
              </p>
              <p className="text-[13px] leading-[1.9] text-[#666] max-w-[440px] mb-10">
                Rate perps let you take leveraged positions on interest rate movements.
                Go long ahead of bull markets, or short when rates look overextended.
              </p>
              <button className="px-8 py-3 border border-white text-[11px] tracking-[0.22em] uppercase text-white hover:bg-white hover:text-black transition-all duration-200">
                Trade Rates <span className="text-[#555]">↗</span>
              </button>
            </div>

            {/* Right — illustrative rate chart */}
            <div className="flex flex-col justify-center">
              <div className="border border-[#141414] bg-[#0a0a0a] p-6">
                <div className="flex items-center justify-between mb-5">
                  <span className="text-[10px] tracking-[0.28em] uppercase text-[#2a2a2a]">Borrow Rate / Time</span>
                  <span className="text-[10px] tracking-[0.2em] uppercase text-[#444]">Illustrative</span>
                </div>
                <svg viewBox="0 0 320 140" className="w-full" preserveAspectRatio="none">
                  {[0, 35, 70, 105, 140].map((y) => (
                    <line key={y} x1="0" y1={y} x2="320" y2={y} stroke="#161616" strokeWidth="1" />
                  ))}
                  <line x1="0" y1="115" x2="320" y2="115" stroke="#222" strokeWidth="1" strokeDasharray="4 4" />
                  <text x="6" y="111" fill="#444" fontSize="7" fontFamily="monospace">FED FLOOR</text>
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
                    <span className="text-[10px] text-[#444]">Demand spike range</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-px bg-[#333]" />
                    <span className="text-[10px] text-[#333]">Baseline range</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Feature cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-[#1a1a1a]">
            {[
              {
                index: 1,
                label: "Asymmetry",
                title: "Floored by policy, spiked by demand",
                body: "The FED rate sets a structural floor — rates almost never go below it. But during increased demand, borrow rates can spike 5–10× overnight. This asymmetry creates an attractive payoff profile.",
              },
              {
                index: 2,
                label: "Cross-Margin",
                title: "Unified margin across your account",
                body: "Margin with ERC20 assets, open limit & TWAP orders, and Uniswap V4 LP positions — all inside a single PrimeBroker. One account, full cross-margin efficiency.",
              },
              {
                index: 3,
                label: "Volatility",
                title: "Rates co-move with market sentiment",
                body: "High correlation between interest rates and market sentiment. Go long rates ahead of bull markets or short when the market is overheated and rates will revert.",
              },
            ].map((feat) => (
              <div key={feat.index} className="bg-[#0a0a0a] p-8 border-t border-[#141414]">
                <div className="flex items-center gap-3 mb-4">
                  <span className="text-[10px] tracking-[0.28em] text-[#555]">0{feat.index}</span>
                  <span className="text-[10px] tracking-[0.22em] uppercase text-[#555] border border-[#1a1a1a] px-2 py-[2px]">
                    {feat.label}
                  </span>
                </div>
                <h3
                  className="font-display font-light text-white leading-[1.15] tracking-[-0.015em] mb-3"
                  style={{ fontSize: "clamp(20px, 2vw, 26px)" }}
                >
                  {feat.title}
                </h3>
                <p className="text-[12px] leading-[1.9] text-[#555]">{feat.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How It Works ──────────────────────────────────── */}
      <section className="relative border-t border-[#111] px-8 md:px-14 py-24 lg:py-32">
        <div className="max-w-[1100px] mx-auto">

          {/* Section label */}
          <div className="flex items-center gap-3 mb-16">
            <span className="text-[#333] text-[12px]">|—</span>
            <span className="text-[13px] tracking-[0.28em] uppercase text-[#333]">How It Works</span>
            <span className="flex-1 h-px bg-[#141414]" />
          </div>

          <h2
            className="font-display font-light text-white leading-[1.1] tracking-[-0.02em] mb-20 max-w-[600px]"
            style={{ fontSize: "clamp(30px, 3.5vw, 48px)" }}
          >
            Four Steps.<br />
            <span className="text-[#555]">Fully On-Chain.</span>
          </h2>

          {/* Steps */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-0">
            {[
              {
                step: "01",
                title: "Open a Broker",
                body: "Create a PrimeBroker account — your unified margin hub. One account manages all positions, collateral, and orders.",
              },
              {
                step: "02",
                title: "Deposit Collateral",
                body: "Fund with USDC, USDT, or any whitelisted ERC20. Your collateral is cross-margined across all products automatically.",
              },
              {
                step: "03",
                title: "Choose a Product",
                body: "Mint a bond for fixed yield, open a perp for rate exposure, buy CDS for solvency insurance, or LP for trading fees.",
              },
              {
                step: "04",
                title: "Manage & Exit",
                body: "Monitor positions in real-time. Close any position permissionlessly at any time. Withdraw collateral instantly.",
              },
            ].map((s, i) => (
              <div key={s.step} className="relative flex flex-col p-8 border border-[#141414] bg-[#0a0a0a]">
                {/* Connector line */}
                {i < 3 && (
                  <div className="hidden md:block absolute top-1/2 -right-px w-6 h-px bg-[#333] z-10" />
                )}

                <div className="flex items-center gap-3 mb-6">
                  <span
                    className="flex items-center justify-center w-8 h-8 border border-[#333] text-[11px] tracking-[0.1em] text-[#666]"
                  >
                    {s.step}
                  </span>
                  {i < 3 && (
                    <span className="hidden md:block flex-1 h-px bg-[#1a1a1a]" />
                  )}
                </div>

                <h3 className="font-display font-light text-white text-[20px] leading-tight tracking-[-0.01em] mb-3">
                  {s.title}
                </h3>
                <p className="text-[12px] leading-[1.85] text-[#555] flex-1">{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Benefits ──────────────────────────────────────── */}
      <section className="relative border-t border-[#111] px-8 md:px-14 py-24 lg:py-32">
        <div className="max-w-[1100px] mx-auto">

          {/* Section label */}
          <div className="flex items-center gap-3 mb-16">
            <span className="text-[#333] text-[12px]">|—</span>
            <span className="text-[13px] tracking-[0.28em] uppercase text-[#333]">Why RLD</span>
            <span className="flex-1 h-px bg-[#141414]" />
          </div>

          {/* Headline */}
          <h2
            className="font-display font-light text-white leading-[1.1] tracking-[-0.02em] mb-16 max-w-[600px]"
            style={{ fontSize: "clamp(30px, 3.5vw, 48px)" }}
          >
            Built Different.<br />
            <span className="text-[#555]">By Design.</span>
          </h2>

          {/* 3×2 benefit grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-px bg-[#1a1a1a] border border-[#1a1a1a]">
            {[
              {
                num: "01",
                title: "Single Pool Design",
                body: "All maturities share one liquidity pool. No fragmentation, no thin order books, no roll risk. Deeper liquidity for every participant.",
              },
              {
                num: "02",
                title: "On-Chain Settlement",
                body: "Every bond, swap, and CDS settles trustlessly against live oracle rates. No counterparty risk beyond the smart contract itself.",
              },
              {
                num: "03",
                title: "Cross-Margin Efficiency",
                body: "One PrimeBroker account for all positions. ERC20 collateral, LP positions, limit orders, and TWAP — unified margin across everything.",
              },
              {
                num: "04",
                title: "No Governance Risk",
                body: "No multi-sig, no admin keys, no upgrade proxy. Protocol parameters are immutable at deployment. What you see is what you get.",
              },
              {
                num: "05",
                title: "Permissionless Exit",
                body: "Close any position at any time. No lock-ups, no withdrawal queues, no cooldown periods. Your capital is always accessible.",
              },
              {
                num: "06",
                title: "Oracle-Native Pricing",
                body: "Index prices from Chainlink and on-chain rate feeds. Mark prices from Uniswap V4 TWAP. No off-chain dependencies, no trusted operators.",
              },
            ].map((b) => (
              <div key={b.num} className="bg-[#0a0a0a] p-8 flex flex-col gap-4">
                <span className="text-[10px] tracking-[0.3em] text-[#333]">{b.num}</span>
                <h3 className="font-display font-light text-white text-[20px] leading-tight tracking-[-0.01em]">
                  {b.title}
                </h3>
                <p className="text-[12px] leading-[1.85] text-[#555]">{b.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA + Footer ─────────────────────────────────── */}
      <section className="relative border-t border-[#111] px-8 md:px-14">

        {/* CTA */}
        <div className="max-w-[1100px] mx-auto py-24 lg:py-32 flex flex-col items-center text-center">
          <h2
            className="font-display font-light text-white leading-[1.1] tracking-[-0.02em] mb-4"
            style={{ fontSize: "clamp(30px, 4vw, 54px)" }}
          >
            Start Trading Rates
          </h2>
          <p className="text-[13px] text-[#666] tracking-[0.06em] mb-10 max-w-[400px]">
            Testnet is live. Fix yields, trade rate movements, and insure
            solvency — entirely on-chain.
          </p>
          <div className="flex items-center gap-6">
            <button className="px-10 py-3 border border-white text-[12px] tracking-[0.22em] uppercase text-white font-bold hover:bg-white hover:text-black transition-all duration-200">
              Launch App ↗
            </button>
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-[#1e1e1e]">
          <div className="max-w-[1100px] mx-auto pt-6 pb-8 flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-6">
              <span className="text-[12px] tracking-[0.3em] uppercase text-white font-bold">RLD</span>
              <span className="text-[10px] tracking-[0.18em] uppercase text-[#666]">Ethereum Testnet</span>
              <span className="text-[10px] tracking-[0.18em] uppercase text-[#666]">V.01</span>
            </div>
            <div className="flex items-center gap-6">
              {[
                { label: "Twitter", href: "https://x.com/lumisfi_" },
                { label: "GitHub", href: "https://github.com/leooos33/RLD" },
                { label: "Docs", href: "https://docs.rld.fi/introduction/rate-level-derivatives.html" },
              ].map((link) => (
                <a
                  key={link.label}
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[11px] tracking-[0.15em] uppercase text-[#666] hover:text-white transition-colors duration-200"
                >
                  {link.label}
                </a>
              ))}
            </div>
          </div>
        </div>
      </section>

    </div>
  );
}

