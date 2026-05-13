import { finiteNumber, hasAnyFiniteValue } from "./analyticsFormatters.js";

const RATE_VALUE_KEYS = ["supplyApy", "borrowApy", "supplyUsd", "borrowUsd", "utilization"];

export const normalizeRatePoint = (point) => ({
  timestamp: finiteNumber(point?.timestamp),
  supplyApy: finiteNumber(point?.supplyApy),
  borrowApy: finiteNumber(point?.borrowApy),
  utilization: finiteNumber(point?.utilization),
  supplyUsd: finiteNumber(point?.supplyUsd),
  borrowUsd: finiteNumber(point?.borrowUsd),
});

export const normalizeMarketSnapshot = (rawMarket, fallbackProtocol, extraFields = {}) => {
  if (!rawMarket) return null;
  const supplyUsd = Math.max(0, Number(rawMarket.supplyUsd) || 0);
  const borrowUsd = Math.max(0, Number(rawMarket.borrowUsd) || 0);
  return {
    symbol: String(rawMarket.symbol || "UNKNOWN"),
    protocol: String(rawMarket.protocol || fallbackProtocol),
    supplyUsd,
    borrowUsd,
    supplyApy: Math.max(0, finiteNumber(rawMarket.supplyApy)),
    borrowApy: Math.max(0, finiteNumber(rawMarket.borrowApy)),
    utilization: supplyUsd > 0 ? Math.min(1, borrowUsd / supplyUsd) : 0,
    lltv: rawMarket.lltv != null ? Number(rawMarket.lltv) : null,
    lltvMin: rawMarket.lltvMin != null ? Number(rawMarket.lltvMin) : null,
    lltvMax: rawMarket.lltvMax != null ? Number(rawMarket.lltvMax) : null,
    loanPriceUsd: rawMarket.loanPriceUsd != null ? Number(rawMarket.loanPriceUsd) : null,
    collateralPriceUsd: rawMarket.collateralPriceUsd != null ? Number(rawMarket.collateralPriceUsd) : null,
    oracleSupport: rawMarket.oracleSupport || null,
    collateralSymbol: rawMarket.collateralSymbol || "",
    collateralUsd: rawMarket.collateralUsd != null ? Number(rawMarket.collateralUsd) : null,
    oracle: rawMarket.oracle || null,
    loanAsset: rawMarket.loanAsset || "",
    loanToken: rawMarket.loanToken || null,
    collateralToken: rawMarket.collateralToken || null,
    ...extraFields,
  };
};

export const normalizeSignedFlowPoint = (point) => {
  const supplyOutflowAbs = Math.max(0, finiteNumber(point?.supplyOutflowUsd));
  const borrowOutflowAbs = Math.max(0, finiteNumber(point?.borrowOutflowUsd));
  return {
    timestamp: finiteNumber(point?.timestamp),
    supplyInflowUsd: Math.max(0, finiteNumber(point?.supplyInflowUsd)),
    supplyOutflowUsd: -supplyOutflowAbs,
    netSupplyFlowUsd: finiteNumber(point?.netSupplyFlowUsd),
    borrowInflowUsd: Math.max(0, finiteNumber(point?.borrowInflowUsd)),
    borrowOutflowUsd: -borrowOutflowAbs,
    netBorrowFlowUsd: finiteNumber(point?.netBorrowFlowUsd),
    cumulativeSupplyNetInflowUsd: finiteNumber(point?.cumulativeSupplyNetInflowUsd, NaN),
    cumulativeBorrowNetInflowUsd: finiteNumber(point?.cumulativeBorrowNetInflowUsd, NaN),
  };
};

export const normalizeUnsignedFlowPoint = (point) => ({
  timestamp: finiteNumber(point?.timestamp),
  supplyInflowUsd: finiteNumber(point?.supplyInflowUsd),
  supplyOutflowUsd: finiteNumber(point?.supplyOutflowUsd),
  borrowInflowUsd: finiteNumber(point?.borrowInflowUsd),
  borrowOutflowUsd: finiteNumber(point?.borrowOutflowUsd),
  netSupplyFlowUsd: Number(point?.netSupplyFlowUsd) || 0,
  netBorrowFlowUsd: Number(point?.netBorrowFlowUsd) || 0,
});

export const normalizeRateChart = (rows = []) => rows
  .map(normalizeRatePoint)
  .filter((point) => point.timestamp > 0 && hasAnyFiniteValue(point, RATE_VALUE_KEYS))
  .sort((a, b) => a.timestamp - b.timestamp);

export const buildCumulativeFlow = (rows = []) => rows.reduce(
  (acc, point) => {
    const cumulativeSupplyNetInflowUsd = Number.isFinite(point.cumulativeSupplyNetInflowUsd)
      ? point.cumulativeSupplyNetInflowUsd
      : acc.cumulativeSupply + point.netSupplyFlowUsd;
    const cumulativeBorrowNetInflowUsd = Number.isFinite(point.cumulativeBorrowNetInflowUsd)
      ? point.cumulativeBorrowNetInflowUsd
      : acc.cumulativeBorrow + point.netBorrowFlowUsd;
    acc.cumulativeSupply = cumulativeSupplyNetInflowUsd;
    acc.cumulativeBorrow = cumulativeBorrowNetInflowUsd;
    acc.rows.push({ ...point, cumulativeSupplyNetInflowUsd, cumulativeBorrowNetInflowUsd });
    return acc;
  },
  { cumulativeSupply: 0, cumulativeBorrow: 0, rows: [] },
).rows;

const normalizeCollateralBreakdown = (rows = []) => rows.map((row) => ({
  asset: String(row?.asset || ""),
  symbol: String(row?.symbol || "UNKNOWN"),
  priceFeed: row?.priceFeed || null,
  borrowCollateralFactor: finiteNumber(row?.borrowCollateralFactor),
  liquidateCollateralFactor: finiteNumber(row?.liquidateCollateralFactor),
  liquidationFactor: finiteNumber(row?.liquidationFactor),
  supplyCapTokens: finiteNumber(row?.supplyCapTokens),
  totalCollateralTokens: finiteNumber(row?.totalCollateralTokens),
  collateralUsd: finiteNumber(row?.collateralUsd),
  capacity: finiteNumber(row?.supplyCapTokens) > 0
    ? finiteNumber(row?.totalCollateralTokens) / finiteNumber(row?.supplyCapTokens)
    : null,
  borrowEnabled: Boolean(row?.borrowEnabled),
}));

export const buildStandardMarketPageModel = (
  page = {},
  { fallbackProtocol = "AAVE_MARKET", includeCollateralBreakdown = false, includeVaultBreakdown = false } = {},
) => {
  const market = normalizeMarketSnapshot(page?.market, fallbackProtocol);
  const rateChart = normalizeRateChart(page?.rateChart || []);
  const flowBase = (page?.flowChart || [])
    .map(normalizeSignedFlowPoint)
    .filter((point) => point.timestamp > 0)
    .sort((a, b) => a.timestamp - b.timestamp);
  const flow = buildCumulativeFlow(flowBase);
  const genesisPoint = flow.find((point) => point.cumulativeSupplyNetInflowUsd > 0);
  const genesisTs = genesisPoint ? genesisPoint.timestamp : 0;

  const model = {
    market,
    tsData: genesisTs > 0 ? rateChart.filter((point) => point.timestamp >= genesisTs) : rateChart,
    flowData: genesisTs > 0 ? flow.filter((point) => point.timestamp >= genesisTs) : flow,
    genesisTs,
  };

  if (includeCollateralBreakdown) {
    model.collateralBreakdown = normalizeCollateralBreakdown(page?.collateralBreakdown || []);
  }
  if (includeVaultBreakdown) {
    model.vaultBreakdown = [...(page?.vaultBreakdown || [])].sort(
      (a, b) => finiteNumber(b?.supplyUsd) - finiteNumber(a?.supplyUsd),
    );
  }

  return model;
};

export const buildFluidVaultPageModel = (page = {}) => {
  const market = normalizeMarketSnapshot(page?.market, "FLUID_VAULT", { protocol: "FLUID_VAULT" });
  const rateChart = normalizeRateChart(page?.rateChart || []);
  const rawFlow = (page?.flowChart || [])
    .map(normalizeUnsignedFlowPoint)
    .filter((point) => point.timestamp > 0)
    .sort((a, b) => a.timestamp - b.timestamp);
  const firstPositiveIndex = rawFlow.findIndex(
    (point) => point.netSupplyFlowUsd > 0 || point.netBorrowFlowUsd > 0,
  );
  const flowData = firstPositiveIndex >= 0 ? rawFlow.slice(firstPositiveIndex) : rawFlow;
  const cumulativeFlowData = buildCumulativeFlow(flowData);
  return { market, tsData: rateChart, flowData, cumulativeFlowData };
};


export const buildMorphoMarketPageModel = (page = {}) => {
  const model = buildStandardMarketPageModel(page, {
    fallbackProtocol: "MORPHO_MARKET",
  });
  return {
    ...model,
    allocationColumnar: page?.allocationColumnar || null,
  };
};

export const normalizeMetaMorphoHistoryPoint = (point) => ({
  timestamp: finiteNumber(point?.timestamp),
  totalDepositsUsd: finiteNumber(point?.totalDepositsUsd),
  allocatedUsd: finiteNumber(point?.allocatedUsd),
  liquidityUsd: finiteNumber(point?.liquidityUsd),
  utilization: finiteNumber(point?.utilization),
  utilizationPct: finiteNumber(point?.utilization) * 100,
  sharePriceUsd: finiteNumber(point?.sharePriceUsd),
  netApy: finiteNumber(point?.netApy),
  netApyPct: finiteNumber(point?.netApy) * 100,
});

export const buildMetaMorphoVaultPageModel = (page = {}) => {
  const vault = page?.vault || null;
  const totalDeposits = finiteNumber(vault?.tvlUsd);
  const history = (page?.history || [])
    .map(normalizeMetaMorphoHistoryPoint)
    .filter((point) => point.timestamp > 0)
    .sort((a, b) => a.timestamp - b.timestamp);
  const exposures = (page?.exposures || [])
    .map((row) => ({
      ...row,
      suppliedUsd: finiteNumber(row?.suppliedUsd),
      allocationShare: totalDeposits > 0
        ? finiteNumber(row?.suppliedUsd) / totalDeposits
        : finiteNumber(row?.allocationShare),
      liquidityUsd: finiteNumber(row?.liquidityUsd),
      supplyApy: finiteNumber(row?.supplyApy),
      borrowApy: finiteNumber(row?.borrowApy),
      utilization: finiteNumber(row?.utilization),
    }))
    .filter((row) => row.marketId && row.suppliedUsd > 0)
    .sort((a, b) => b.suppliedUsd - a.suppliedUsd);
  const byTimestamp = new Map();
  (page?.flowChart || []).forEach((row) => {
    const timestamp = finiteNumber(row?.timestamp);
    if (timestamp <= 0) return;
    const point = byTimestamp.get(timestamp) || {
      timestamp,
      inflowUsd: 0,
      outflowUsd: 0,
      netFlowUsd: 0,
    };
    const inflow = Math.max(0, finiteNumber(row?.depositUsd));
    const outflow = Math.max(0, finiteNumber(row?.withdrawUsd));
    point.inflowUsd += inflow;
    point.outflowUsd -= outflow;
    point.netFlowUsd += finiteNumber(row?.netFlowUsd, inflow - outflow);
    byTimestamp.set(timestamp, point);
  });
  const flowData = [...byTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp);

  return {
    vault,
    totalDeposits,
    history,
    exposures,
    flowLinks: page?.flowLinks || [],
    flowData,
  };
};
