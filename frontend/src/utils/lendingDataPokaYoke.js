/**
 * Deterministic parsing and aggregation for Lending Data.
 * Enforces strict boundaries to prevent NaN, Infinity, and silent failures.
 */

export function parseMarketSnapshots(rawData) {
  if (!rawData || !Array.isArray(rawData)) {
    return [];
  }
  
  return rawData.map((row) => {
    // Coerce everything securely
    const supplyUsd = Math.max(0, Number(row.supplyUsd) || 0);
    const borrowUsd = Math.max(0, Number(row.borrowUsd) || 0);
    const supplyApy = Math.max(0, Number(row.supplyApy) || 0);
    const borrowApy = Math.max(0, Number(row.borrowApy) || 0);
    
    // Prevent utilization > 100% or NaN
    let utilization = 0;
    if (supplyUsd > 0) {
      utilization = Math.min(1, Math.max(0, borrowUsd / supplyUsd));
    }

    const protocolStr = String(row.protocol || "UNKNOWN_MARKET");
    const protocolKey = protocolStr.split("_")[0];

    return {
      symbol: String(row.symbol || "UNKNOWN"),
      protocol: protocolStr,
      protocolKey,
      supplyUsd,
      borrowUsd,
      supplyApy,
      borrowApy,
      utilization,
    };
  });
}

export function aggregateProtocolStats(parsedMarkets, protocolMeta = {}, protocolOrder = []) {
  const aggregate = {};
  
  parsedMarkets.forEach((market) => {
    const key = market.protocolKey;
    if (!aggregate[key]) {
      aggregate[key] = {
        key,
        supplyUsd: 0,
        borrowUsd: 0,
        supplyApyWeighted: 0,
        borrowApyWeighted: 0,
        markets: 0,
      };
    }
    aggregate[key].supplyUsd += market.supplyUsd;
    aggregate[key].borrowUsd += market.borrowUsd;
    aggregate[key].supplyApyWeighted += market.supplyApy * market.supplyUsd;
    aggregate[key].borrowApyWeighted += market.borrowApy * market.borrowUsd;
    aggregate[key].markets += 1;
  });

  const toRow = (item) => {
    const meta = protocolMeta[item.key] || {};
    const avgSupplyApy = item.supplyUsd > 0 ? item.supplyApyWeighted / item.supplyUsd : 0;
    const avgBorrowApy = item.borrowUsd > 0 ? item.borrowApyWeighted / item.borrowUsd : 0;
    const utilization = item.supplyUsd > 0 ? Math.min(1, item.borrowUsd / item.supplyUsd) : 0;

    return {
      key: item.key,
      label: meta.label || `${item.key} Market`,
      slug: meta.slug || item.key.toLowerCase(),
      color: meta.color || "#64748b",
      supplyUsd: item.supplyUsd,
      borrowUsd: item.borrowUsd,
      utilization,
      avgSupplyApy,
      avgBorrowApy,
      markets: item.markets,
    };
  };

  const ordered = protocolOrder.filter((key) => aggregate[key]).map((key) => toRow(aggregate[key]));
  const remaining = Object.keys(aggregate)
    .filter((key) => !protocolOrder.includes(key))
    .map((key) => toRow(aggregate[key]));
    
  return [...ordered, ...remaining].sort((a, b) => b.supplyUsd - a.supplyUsd);
}

export function calculateTotals(protocolStats) {
  let totalSupplyUsd = 0;
  let totalBorrowUsd = 0;
  let weightedSupplyApy = 0;
  let weightedBorrowApy = 0;
  let marketCount = 0;

  protocolStats.forEach(row => {
    totalSupplyUsd += row.supplyUsd;
    totalBorrowUsd += row.borrowUsd;
    weightedSupplyApy += row.avgSupplyApy * row.supplyUsd;
    weightedBorrowApy += row.avgBorrowApy * row.borrowUsd;
    marketCount += row.markets;
  });

  return {
    totalSupplyUsd,
    totalBorrowUsd,
    averageSupplyApy: totalSupplyUsd > 0 ? weightedSupplyApy / totalSupplyUsd : 0,
    averageBorrowApy: totalBorrowUsd > 0 ? weightedBorrowApy / totalBorrowUsd : 0,
    marketCount,
  };
}
