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

  envioStatus: (url) => [url, "envio.status.v1", null],
  envioHistoricalRates: (url, resolution, startDate, endDate, limit) => [
    url,
    "envio.historical-rates.v1",
    { resolution, startDate, endDate, limit },
  ],
  envioSusdeLatest: (url) => [url, "envio.latest-susde.v1", null],

  twammDashboard: (url, marketId) =>
    marketId ? [url, "simulation.twamm-dashboard.v1", { marketId }] : null,
  twammPositions: (url, marketId, owner) =>
    marketId && owner ? [url, "simulation.twamm-positions.v1", { marketId, owner }] : null,
};
