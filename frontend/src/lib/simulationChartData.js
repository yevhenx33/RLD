export const RESOLUTION_SECONDS = Object.freeze({
  "1m": 60,
  "5m": 5 * 60,
  "15m": 15 * 60,
  "1h": 60 * 60,
  "4h": 4 * 60 * 60,
  "1d": 24 * 60 * 60,
  "1w": 7 * 24 * 60 * 60,
});

export function buildSnapshotChartPoint(snapshot, timestamp) {
  const markPrice = Number(snapshot?.pool?.markPrice || 0);
  const indexPrice = Number(snapshot?.market?.indexPrice || 0);
  if (markPrice <= 0 && indexPrice <= 0) return null;

  return {
    timestamp,
    indexPrice,
    markPrice,
    indexOpen: indexPrice,
    indexHigh: indexPrice,
    indexLow: indexPrice,
    markOpen: markPrice,
    markHigh: markPrice,
    markLow: markPrice,
    normalizationFactor: 0,
    totalDebt: 0,
    tick: 0,
    liquidity: 0,
    volume: 0,
    swapCount: 0,
  };
}

export function buildFlatChartData({ snapshot, chartStartTime, chartEndTime, chartResolution }) {
  const markPrice = Number(snapshot?.pool?.markPrice || 0);
  const indexPrice = Number(snapshot?.market?.indexPrice || 0);
  if (markPrice <= 0 && indexPrice <= 0) return [];

  const resolutionSeconds = RESOLUTION_SECONDS[String(chartResolution).toLowerCase()] || RESOLUTION_SECONDS["1h"];
  const end = chartEndTime || snapshot?.blockTimestamp || snapshot?.market?.blockTimestamp || Math.floor(Date.now() / 1000);
  const start = chartStartTime || Math.max(0, end - resolutionSeconds * 24);
  const safeEnd = end > start ? end : start + resolutionSeconds;

  return [
    buildSnapshotChartPoint(snapshot, start),
    buildSnapshotChartPoint(snapshot, safeEnd),
  ].filter(Boolean);
}

export function buildSimulationChartData({ candles, snapshot, chartStartTime, chartEndTime, chartResolution }) {
  if (!candles?.length) {
    return buildFlatChartData({ snapshot, chartStartTime, chartEndTime, chartResolution });
  }

  const mapped = candles.map((c) => ({
    timestamp: c.bucket,
    indexPrice: c.indexClose,
    markPrice: c.markClose,
    indexOpen: c.indexOpen,
    indexHigh: c.indexHigh,
    indexLow: c.indexLow,
    markOpen: c.markOpen,
    markHigh: c.markHigh,
    markLow: c.markLow,
    normalizationFactor: 0,
    totalDebt: 0,
    tick: 0,
    liquidity: 0,
    volume: c.volumeUsd || 0,
    swapCount: c.swapCount,
  }));

  if (mapped.length === 1) {
    const flatTail = buildFlatChartData({
      snapshot,
      chartStartTime: mapped[0].timestamp,
      chartEndTime,
      chartResolution,
    });
    return flatTail.length ? [{ ...mapped[0] }, { ...flatTail[1] }] : mapped;
  }

  const snapshotTs = snapshot?.blockTimestamp || snapshot?.market?.blockTimestamp;
  const resolutionSeconds = RESOLUTION_SECONDS[String(chartResolution).toLowerCase()] || RESOLUTION_SECONDS["1h"];
  const selectedWindowIncludesSnapshot = !chartEndTime || chartEndTime >= snapshotTs - resolutionSeconds;
  const lastPoint = mapped[mapped.length - 1];
  if (snapshotTs && selectedWindowIncludesSnapshot && lastPoint?.timestamp < snapshotTs) {
    const currentPoint = buildSnapshotChartPoint(snapshot, snapshotTs);
    if (currentPoint) return [...mapped, currentPoint];
  }

  return mapped;
}
