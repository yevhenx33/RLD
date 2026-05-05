export const POOLS_QUERY = `
  query PoolsDirectoryMarkets {
    perpInfo: marketInfo(market: "perp")
    perpSnapshot: snapshot(market: "perp")
    cdsInfo: marketInfo(market: "cds")
    cdsSnapshot: snapshot(market: "cds")
  }
`;

export function formatUSD(val) {
  const num = Number(val);
  if (val == null || Number.isNaN(num)) return "-";
  if (Math.abs(num) >= 1e9) return `$${(num / 1e9).toFixed(2)}B`;
  if (Math.abs(num) >= 1e6) return `$${(num / 1e6).toFixed(2)}M`;
  if (Math.abs(num) >= 1e3) return `$${(num / 1e3).toFixed(1)}K`;
  return `$${num.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function shortId(id) {
  if (!id || id.length < 14) return id || "-";
  return `${id.slice(0, 6)}...${id.slice(-4)}`;
}

const getMarketType = (info, fallback) =>
  (info?.type || info?.market_type || fallback || "perp").toLowerCase();

const getFeeTier = (info, pool) =>
  Number(
    pool?.fee ??
      info?.poolFee ??
      info?.pool_fee ??
      info?.infrastructure?.poolFee ??
      info?.infrastructure?.pool_fee ??
      500,
  );

const getPositionSymbol = (info, marketType) =>
  info?.position_token?.symbol ||
  info?.positionToken?.symbol ||
  info?.position_symbol ||
  info?.positionSymbol ||
  info?.wrlpSymbol ||
  (marketType === "cds" ? "wCDSUSDC" : "wRLP");

const getCollateralSymbol = (info, marketType) =>
  info?.collateral?.symbol ||
  info?.collateral_symbol ||
  info?.collateralSymbol ||
  info?.wausdcSymbol ||
  (marketType === "cds" ? "USDC" : "waUSDC");

export function buildPoolRow({ type, info, snapshot }) {
  const market = snapshot?.market;
  const pool = snapshot?.pool;
  if (!info || !market || !pool) return null;

  const marketType = getMarketType(info, type);
  const marketId = info?.marketId || info?.market_id || market?.marketId;
  const poolId =
    info?.poolId ||
    info?.pool_id ||
    info?.infrastructure?.poolId ||
    info?.infrastructure?.pool_id ||
    pool?.poolId;
  if (!marketId || !poolId) return null;

  const token0Symbol = getPositionSymbol(info, marketType);
  const token1Symbol = getCollateralSymbol(info, marketType);
  const feeTier = getFeeTier(info, pool);
  const feeRate = feeTier / 1_000_000;
  const derived = snapshot?.derived || {};
  const tvl = Number(derived.poolTvlUsd ?? pool?.tvlUsd ?? 0);
  const volume24h = Number(derived.volume24hUsd ?? 0);
  const fees24h = volume24h * feeRate;
  const apr7d = tvl > 0 ? (fees24h * 365 / tvl) * 100 : 0;

  return {
    id: `${marketType}:${marketId}`,
    address: marketId,
    poolId,
    pair: `${token0Symbol} / ${token1Symbol}`,
    protocol: "Uniswap V4",
    typeLabel: marketType.toUpperCase(),
    feeTier: `${(feeTier / 10000).toFixed(2)}%`,
    tvl,
    volume24h,
    fees24h,
    apr7d: Math.min(apr7d, 999),
    apr30d: Math.min(apr7d * 0.9, 999),
    swapCount: Number(derived.swapCount24h ?? 0),
  };
}

export function buildPoolsDirectoryRows(data) {
  return [
    buildPoolRow({
      type: "perp",
      info: data?.perpInfo,
      snapshot: data?.perpSnapshot,
    }),
    buildPoolRow({
      type: "cds",
      info: data?.cdsInfo,
      snapshot: data?.cdsSnapshot,
    }),
  ].filter(Boolean);
}
