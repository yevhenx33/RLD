import Link from 'next/link';

/* ─── Link data ──────────────────────────────────────────── */

const userLinks = [
  {
    num: '01',
    title: 'Introduction',
    desc: 'What is RLD and why interest rate derivatives matter.',
    href: '/docs',
  },
  {
    num: '02',
    title: 'Synthetic Bonds',
    desc: 'Fix your yield or borrowing cost — any maturity, one pool.',
    href: '/docs',
  },
  {
    num: '03',
    title: 'Credit Default Swaps',
    desc: 'Parametric solvency insurance with 100% payout on trigger.',
    href: '/docs',
  },
  {
    num: '04',
    title: 'Rate Perpetuals',
    desc: 'Trade interest rates as a volatility instrument.',
    href: '/docs',
  },
  {
    num: '05',
    title: 'Getting Started',
    desc: 'Connect wallet, open a margin account, and place your first trade.',
    href: '/docs',
  },
];

const devLinks = [
  {
    num: '01',
    title: 'Architecture Overview',
    desc: 'PrimeBroker, settlement engine, and margin system internals.',
    href: '/docs',
  },
  {
    num: '02',
    title: 'Smart Contracts',
    desc: 'Contract addresses, ABIs, and integration reference.',
    href: '/docs',
  },
  {
    num: '03',
    title: 'API & Data Feeds',
    desc: 'GraphQL, WebSocket, and REST endpoints for real-time rate data.',
    href: '/docs',
  },
  {
    num: '04',
    title: 'Permit2 Integration',
    desc: 'Gasless off-chain signing for trades, deposits, and delegations.',
    href: '/docs',
  },
  {
    num: '05',
    title: 'Deployment Guide',
    desc: 'Run a local node, deploy contracts, and connect the indexer.',
    href: '/docs',
  },
];

/* ─── Card component ─────────────────────────────────────── */

function DocCard({
  num,
  title,
  desc,
  href,
}: {
  num: string;
  title: string;
  desc: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="group relative block border border-[#141414] bg-[#0d0d0d] p-6
                 transition-all duration-300 hover:border-[#333] hover:bg-[#111]"
    >
      {/* corner brackets */}
      <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-[#1e1e1e] group-hover:border-[#444] transition-colors" />
      <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-[#1e1e1e] group-hover:border-[#444] transition-colors" />

      <span className="block font-mono text-[9px] tracking-[0.3em] text-[#333] mb-3">
        {num}
      </span>
      <h3 className="font-[family-name:var(--font-space)] font-light text-white text-[17px] leading-tight tracking-[-0.01em] mb-2 group-hover:text-[#eee]">
        {title}
      </h3>
      <p className="font-mono text-[11px] leading-[1.8] text-[#555] group-hover:text-[#777] transition-colors">
        {desc}
      </p>

      {/* subtle arrow */}
      <span className="absolute bottom-5 right-6 font-mono text-[11px] text-[#222] group-hover:text-[#666] transition-colors">
        ↗
      </span>
    </Link>
  );
}

/* ─── Section label ──────────────────────────────────────── */

function SectionLabel({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-3 mb-6">
      <span className="font-mono text-[#333] text-[11px]">|—</span>
      <span className="font-mono text-[12px] tracking-[0.28em] uppercase text-[#333]">
        {text}
      </span>
      <span className="flex-1 h-px bg-[#141414]" />
    </div>
  );
}

/* ─── Page ───────────────────────────────────────────────── */

export default function HomePage() {
  return (
    <div className="relative min-h-screen bg-[#050505] overflow-hidden">
      {/* grain overlay */}
      <div
        className="pointer-events-none absolute inset-0 opacity-25"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.08'/%3E%3C/svg%3E")`,
          backgroundSize: '192px 192px',
        }}
      />

      <div className="relative z-10 max-w-[1100px] mx-auto px-8 md:px-14 pt-24 pb-20">
        {/* Header */}
        <div className="mb-16">
          <h1
            className="font-[family-name:var(--font-space)] font-light text-white leading-[1.1] tracking-[-0.025em] mb-4"
            style={{ fontSize: 'clamp(28px, 4vw, 48px)' }}
          >
            Documentation
          </h1>
          <p className="font-mono text-[12px] text-[#666] tracking-[0.05em] max-w-[520px] leading-[1.8]">
            Everything you need to understand, use, and build on the RLD
            protocol — interest rate derivatives for on-chain finance.
          </p>
        </div>

        {/* Two-column grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 lg:gap-16">
          {/* LEFT — Users */}
          <div>
            <SectionLabel text="For Users" />
            <div className="flex flex-col gap-3">
              {userLinks.map((l) => (
                <DocCard key={l.num} {...l} />
              ))}
            </div>
          </div>

          {/* RIGHT — Developers */}
          <div>
            <SectionLabel text="For Developers" />
            <div className="flex flex-col gap-3">
              {devLinks.map((l) => (
                <DocCard key={l.num} {...l} />
              ))}
            </div>
          </div>
        </div>

        {/* Footer strip */}
        <div className="mt-20 pt-6 border-t border-[#141414] flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-6">
            <span className="font-mono text-[11px] tracking-[0.3em] uppercase text-white font-bold">
              RLD
            </span>
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-[#555]">
              Documentation
            </span>
          </div>
          <div className="flex items-center gap-6">
            {[
              { label: 'Protocol', href: 'https://rld.fi' },
              { label: 'GitHub', href: 'https://github.com/leooos33/RLD' },
              { label: 'X / Twitter', href: 'https://x.com/rld_fi' },
            ].map((link) => (
              <a
                key={link.label}
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-[10px] tracking-[0.15em] uppercase text-[#555] hover:text-white transition-colors duration-200"
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
