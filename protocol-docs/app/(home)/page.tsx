import Link from 'next/link';

/* ─── Link data ──────────────────────────────────────────── */

const userLinks = [
  { title: 'Getting Started', desc: 'Connect, deposit, and trade', href: '/docs' },
  { title: 'Rate Perpetuals', desc: 'Trade rates as volatility', href: '/docs' },
  { title: 'Synthetic Bonds', desc: 'Fix your yield — any maturity, one pool', href: '/docs' },
  { title: 'Credit Default Swaps', desc: 'Parametric insurance against protocol defaults', href: '/docs' },
];

const devLinks = [
  { title: 'Architecture', desc: 'Contracts, indices, markets, settlement, and margin', href: '/docs' },
  { title: 'Smart Contracts', desc: 'Addresses, ABIs, and integration reference', href: '/docs' },
  { title: 'Data & APIs', desc: 'GraphQL, WebSocket, and REST endpoints', href: '/docs' },

];

const researchLinks = [
  { title: 'Executive Summary', desc: 'RLD and Parametric CDS — unified infrastructure for on-chain fixed-income', href: '/docs' },
  { title: 'Rate-Level Perpetuals', desc: 'Transforming volatile DeFi yield into a tradable, persistent derivative', href: '/docs' },
  { title: 'Synthetic Bonds', desc: 'Fixed-yield via continuous-time perpetuals and deterministic execution', href: '/docs' },
  { title: 'JIT Matching Engine', desc: 'Ghost execution — Hub-and-spoke DEX architecture with composable liquidity', href: '/docs' },
  { title: 'Parametric CDS', desc: 'On-chain solvency insurance via rate-bounded everlasting options', href: '/docs' },
];

/* ─── Big panel ──────────────────────────────────────────── */

function Panel({
  label,
  title,
  subtitle,
  links,
  index,
}: {
  label: string;
  title: string;
  subtitle: string;
  links: { title: string; desc: string; href: string }[];
  index: string;
}) {
  return (
    <div className="group/panel relative border border-[#141414] bg-[#0a0a0a] flex flex-col h-full transition-colors duration-300 hover:border-[#222]">
      {/* corner brackets */}
      <span className="absolute top-0 left-0 w-3 h-3 border-t border-l border-[#1e1e1e] group-hover/panel:border-[#333] transition-colors" />
      <span className="absolute top-0 right-0 w-3 h-3 border-t border-r border-[#1e1e1e] group-hover/panel:border-[#333] transition-colors" />
      <span className="absolute bottom-0 left-0 w-3 h-3 border-b border-l border-[#1e1e1e] group-hover/panel:border-[#333] transition-colors" />
      <span className="absolute bottom-0 right-0 w-3 h-3 border-b border-r border-[#1e1e1e] group-hover/panel:border-[#333] transition-colors" />

      {/* Panel header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#141414]">
        <div className="flex items-center gap-3 mb-5">
          <span className="font-mono text-[9px] tracking-[0.3em] text-[#333]">{index}</span>
        </div>
        <h2
          className="font-[family-name:var(--font-space)] font-light text-white leading-[1.1] tracking-[-0.02em] mb-2"
          style={{ fontSize: 'clamp(24px, 3vw, 36px)' }}
        >
          {title}
        </h2>
        <p className="font-mono text-[12px] leading-[1.7] text-[#555] max-w-[380px]">
          {subtitle}
        </p>
      </div>

      {/* Link list */}
      <div className="flex-1 divide-y divide-[#111]">
        {links.map((l) => (
          <Link
            key={l.title}
            href={l.href}
            className="group/link flex items-center justify-between px-8 py-4 transition-colors duration-200 hover:bg-[#111]"
          >
            <div className="min-w-0">
              <span className="block font-[family-name:var(--font-space)] text-[15px] font-light text-[#ccc] group-hover/link:text-white transition-colors">
                {l.title}
              </span>
              <span className="block font-mono text-[12px] text-[#444] group-hover/link:text-[#666] transition-colors mt-0.5">
                {l.desc}
              </span>
            </div>
            <span className="font-mono text-[12px] text-[#222] group-hover/link:text-[#666] transition-colors ml-4 shrink-0">
              ↗
            </span>
          </Link>
        ))}
      </div>

      {/* Panel footer CTA */}
      <div className="px-8 py-5 border-t border-[#141414]">
        <Link
          href="/docs"
          className="inline-flex items-center gap-2 font-mono text-[11px] tracking-[0.22em] uppercase text-[#555] hover:text-white transition-colors duration-200"
        >
          Browse all {label.toLowerCase()} docs
          <span className="text-[#333]">→</span>
        </Link>
      </div>
    </div>
  );
}

/* ─── Page ───────────────────────────────────────────────── */

export default function HomePage() {
  return (
    <div className="relative min-h-screen bg-[#050505] overflow-hidden flex flex-col">
      {/* grain overlay */}
      <div
        className="pointer-events-none absolute inset-0 opacity-25 z-0"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.08'/%3E%3C/svg%3E")`,
          backgroundSize: '192px 192px',
        }}
      />

      {/* Main content */}
      <div className="relative z-10 max-w-[1100px] mx-auto px-8 md:px-14 pt-20 pb-16 flex-1">
        {/* Header */}
        <div className="mb-10">
          <div className="flex items-center gap-3 mb-6">
            <span className="font-mono text-[#333] text-[11px]">|—</span>
            <span className="font-mono text-[12px] tracking-[0.28em] uppercase text-[#333]">
              Documentation
            </span>
            <span className="flex-1 h-px bg-[#141414]" />
          </div>
          <h1
            className="font-[family-name:var(--font-space)] font-light text-white leading-[1.1] tracking-[-0.025em] mb-3"
            style={{ fontSize: 'clamp(28px, 4vw, 48px)' }}
          >
            RLD Protocol
          </h1>
          <p className="font-mono text-[12px] text-[#555] tracking-[0.04em] max-w-[480px] leading-[1.8]">
            Interest rate derivatives for on-chain finance — fix yields, trade
            rates, and insure solvency.
          </p>
        </div>

        {/* Top row — two panels */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <Panel
            index="01"
            label="Use"
            title="For Traders"
            subtitle="Understand products, manage positions, and navigate the interface."
            links={userLinks}
          />
          <Panel
            index="02"
            label="Developers"
            title="For Builders"
            subtitle="Integrate contracts, consume data feeds, and deploy infrastructure."
            links={devLinks}
          />
        </div>

        {/* Bottom row — full-width research panel */}
        <Panel
          index="03"
          label="Research"
          title="Whitepapers"
          subtitle="Technical research, economic models, and protocol design rationale."
          links={researchLinks}
        />
      </div>

      {/* Footer pinned to viewport bottom */}
      <div className="relative z-10 border-t border-[#141414]">
        <div className="max-w-[1100px] mx-auto px-8 md:px-22 py-5 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-6 text-[12px]">
            <span className="font-mono  tracking-[0.3em] uppercase text-white font-bold">
              RLD
            </span>
            <span className="font-mono tracking-[0.18em] uppercase text-[#444]">
              Documentation
            </span>
          </div>
          <div className="flex items-center gap-6 ">
            {[
              { label: 'Protocol', href: 'https://rld.fi' },
              { label: 'GitHub', href: 'https://github.com/yevhenx33' },
              { label: 'X / Twitter', href: 'https://x.com/rld_fi' },
            ].map((link) => (
              <a
                key={link.label}
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-[12px] tracking-[0.15em] uppercase text-[#444] hover:text-white transition-colors duration-200"
              >
                {link.label}
              </a>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
