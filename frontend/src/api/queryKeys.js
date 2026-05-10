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
  apiProtocolMarkets: (url, protocol) => [url, "api.protocol-markets.v1", { protocol }],
  apiMarketPage: (url, protocol, marketId) =>
    protocol && marketId ? [url, "api.market-page.v1", { protocol, marketId }] : null,
  apiPendleMarketPage: (url, marketId) =>
    marketId ? [url, "api.pendle-market-page.v1", { marketId }] : null,

  twammDashboard: (url, marketId) =>
    marketId ? [url, "simulation.twamm-dashboard.v1", { marketId }] : null,
  twammPositions: (url, marketId, owner) =>
    marketId && owner ? [url, "simulation.twamm-positions.v1", { marketId, owner }] : null,
};
