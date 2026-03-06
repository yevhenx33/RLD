import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

export default withMermaid(
  defineConfig({
    title: 'RLD Protocol',
    description: 'Rate-Level Derivatives — On-chain synthetic bonds and interest rate trading',
    base: '/docs/',
    head: [
      ['meta', { name: 'theme-color', content: '#0f0f0f' }],
      ['meta', { property: 'og:title', content: 'RLD Protocol Documentation' }],
      ['meta', { property: 'og:description', content: 'Trade interest rates. Mint synthetic bonds. Hedge lending risk.' }],
    ],

    themeConfig: {
      logo: '/logo.svg',
      siteTitle: 'RLD Protocol',

      nav: [
        { text: 'Guide', link: '/introduction/rate-level-derivatives' },
        { text: 'Architecture', link: '/architecture/system-overview' },
        { text: 'JTM Engine', link: '/jtm/design-evolution' },
        { text: 'Contracts', link: '/contracts/overview' },
      ],

      sidebar: [
        {
          text: 'Introduction',
          collapsed: false,
          items: [
            { text: 'Rate-Level Derivatives', link: '/introduction/rate-level-derivatives' },
            { text: 'Key Concepts', link: '/introduction/key-concepts' },
            { text: 'Use Cases', link: '/introduction/use-cases' },
            { text: 'Glossary', link: '/introduction/glossary' },
          ],
        },
        {
          text: 'Architecture',
          collapsed: false,
          items: [
            { text: 'System Overview', link: '/architecture/system-overview' },
            { text: 'Prime Broker', link: '/architecture/prime-broker' },
            { text: 'Flash Accounting', link: '/architecture/flash-accounting' },
          ],
        },
        {
          text: 'Protocol Mechanics',
          collapsed: false,
          items: [
            { text: 'Market Structure', link: '/protocol/market-structure' },
            { text: 'Positions & Solvency', link: '/protocol/positions-and-solvency' },
            { text: 'Funding Mechanism', link: '/protocol/funding-mechanism' },
            { text: 'Liquidation', link: '/protocol/liquidation' },
            { text: 'Oracles', link: '/protocol/oracles' },
          ],
        },
        {
          text: 'User Guides',
          collapsed: false,
          items: [
            { text: 'Getting Started', link: '/guides/getting-started' },
            { text: 'Going Long', link: '/guides/going-long' },
            { text: 'Going Short', link: '/guides/going-short' },
            { text: 'Synthetic Bonds', link: '/guides/synthetic-bonds' },
            { text: 'Providing Liquidity', link: '/guides/providing-liquidity' },
          ],
        },
        {
          text: 'JTM Engine',
          collapsed: false,
          items: [
            { text: 'V4 Hooks Architecture', link: '/jtm/v4-hooks-architecture' },
            { text: 'Design Evolution', link: '/jtm/design-evolution' },
            { text: 'Streaming Orders (TWAP)', link: '/jtm/streaming-orders' },
            { text: 'Limit Orders', link: '/jtm/limit-orders' },
            { text: 'Clearing & Arbitrage', link: '/jtm/clearing-and-arbitrage' },
          ],
        },
        {
          text: 'Contracts Reference',
          collapsed: true,
          items: [
            { text: 'Overview', link: '/contracts/overview' },
            { text: 'Core Contracts', link: '/contracts/core' },
            { text: 'Broker & Periphery', link: '/contracts/broker-periphery' },
            { text: 'Integration Guide', link: '/contracts/integration-guide' },
          ],
        },
        {
          text: 'Risk & Security',
          collapsed: true,
          items: [
            { text: 'Risk Parameters', link: '/risk/risk-parameters' },
            { text: 'Security Model', link: '/risk/security-model' },
          ],
        },
        {
          text: 'Reference',
          collapsed: true,
          items: [
            { text: 'FAQ', link: '/faq' },
            { text: 'Deployed Addresses', link: '/reference/deployed-addresses' },
          ],
        },
      ],

      socialLinks: [
        { icon: 'github', link: 'https://github.com/rld-protocol' },
      ],

      footer: {
        message: 'RLD Protocol Documentation',
      },

      search: {
        provider: 'local',
      },

      outline: {
        level: [2, 3],
      },
    },

    appearance: 'dark',

    markdown: {
      math: true,
    },

    ignoreDeadLinks: [
      /DEPLOYMENT/,
      /TWAMM_INITIALIZATION/,
      /JTM_INITIALIZATION/,
    ],

    mermaid: {},
  })
)
