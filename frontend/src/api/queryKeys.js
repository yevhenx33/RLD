export const queryKeys = {
  runtimeManifest: (url) => [url, "simulation.runtime-manifest.v1", null],
  simulationSnapshot: (url, market = null) => [url, "simulation.snapshot.v2", { market }],
  simulationAccount: (url, owner) =>
    owner ? [url, "simulation.account.v1", { owner: owner.toLowerCase(), status: "all" }] : null,
  simulationCandles: (url, variables) =>
    variables?.marketId ? [url, "simulation.candles.v1", variables] : null,
  bondPositions: (url, owner) =>
    owner ? [url, "simulation.bonds.v1", { owner: owner.toLowerCase() }] : null,
  coveragePositions: (url, owner, market = null) =>
    owner ? [url, "simulation.coverage-positions.v1", { owner: owner.toLowerCase(), market }] : null,
  brokerData: (url, owner, marketId) =>
    owner && marketId ? [url, "simulation.broker-data.v1", { owner, marketId }] : null,

  apiStatus: (url) => [url, "api.status.v1", null],
  apiHistoricalRates: (url, resolution, startDate, endDate, limit) => [
    url,
    "api.historical-rates.v1",
    { resolution, startDate, endDate, limit },
  ],
  apiSusdeLatest: (url) => [url, "api.latest-susde.v1", null],
  apiLendingPage: (url, displayIn, flowWindowDays = 30) => [
    url,
    "api.lending-page.v1",
    { displayIn, flowWindowDays },
  ],
  apiProtocolMarkets: (url, protocol, maxBorrowApy = null) => [
    url,
    "api.protocol-markets.v1",
    { protocol, maxBorrowApy },
  ],
  apiCompoundV3ProtocolPage: (url, flowWindowDays, timeseriesLimit, assetSymbols) => [
    url,
    "api.compound-v3-protocol-page.v1",
    { flowWindowDays, timeseriesLimit, assetSymbols },
  ],
  apiMetaMorphoVaults: (url, limit = 2000) => [
    url,
    "api.metamorpho-vaults.v1",
    { limit },
  ],
  apiFluidProductSnapshots: (url, productType = null, limit = 2000) => [
    url,
    "api.fluid-product-snapshots.v1",
    { productType, limit },
  ],
  apiFluidProductSnapshotHistory: (url, productType = null, resolution = "1D", limit = 10000) => [
    url,
    "api.fluid-product-snapshot-history.v1",
    { productType, resolution, limit },
  ],
  apiFluidVaultCompositionHistory: (url, resolution = "1D", limit = 50000) => [
    url,
    "api.fluid-vault-composition-history.v1",
    { resolution, limit },
  ],
  apiMetaMorphoVaultPage: (url, vaultAddress) =>
    vaultAddress
      ? [url, "api.metamorpho-vault-page.v1", { vaultAddress: vaultAddress.toLowerCase() }]
      : null,
  apiMarketPage: (url, protocol, marketId) =>
    protocol && marketId ? [url, "api.market-page.v1", { protocol, marketId }] : null,
  apiPendleMarketPage: (url, marketId) =>
    marketId ? [url, "api.pendle-market-page.v1", { marketId }] : null,
  apiProtocolApyHistory: (url, protocol, resolution, limit, maxBorrowApy = null) =>
    protocol
      ? [url, "api.protocol-apy-history.v1", { protocol, resolution, limit, maxBorrowApy }]
      : null,
  apiProtocolAssetApyHistory: (url, protocol, symbols, resolution, limit, maxBorrowApy = null) =>
    protocol
      ? [
        url,
        "api.protocol-asset-apy-history.v1",
        { protocol, symbols, resolution, limit, maxBorrowApy },
      ]
      : null,
  apiMorphoCuratorFlows: (url, flowWindowDays, topN, maxBorrowApy = null) => [
    url,
    "api.morpho-curator-flows.v1",
    { flowWindowDays, topN, maxBorrowApy },
  ],
  apiEulerChannelFlows: (url, flowWindowDays, topN, maxBorrowApy = null) => [
    url,
    "api.euler-channel-flows.v1",
    { flowWindowDays, topN, maxBorrowApy },
  ],
  apiMorphoCuratorAllocationHistory: (url, resolution, limit, topN, maxBorrowApy = null) => [
    url,
    "api.morpho-curator-allocation-history.v1",
    { resolution, limit, topN, maxBorrowApy },
  ],

  twammDashboard: (url, marketId) =>
    marketId ? [url, "simulation.twamm-dashboard.v1", { marketId }] : null,
  twammPositions: (url, marketId, owner) =>
    marketId && owner ? [url, "simulation.twamm-positions.v1", { marketId, owner }] : null,
};
