import assert from "node:assert/strict";
import test from "node:test";
import { buildFlatChartData, buildSimulationChartData, buildSnapshotChartPoint } from "./simulationChartData.js";

const snapshot = { blockTimestamp: 2000, market: { indexPrice: 100, blockTimestamp: 2000 }, pool: { markPrice: 101 } };

test("buildSnapshotChartPoint maps current prices into OHLC shape", () => {
  const point = buildSnapshotChartPoint(snapshot, 1234);
  assert.equal(point.timestamp, 1234);
  assert.equal(point.indexOpen, 100);
  assert.equal(point.markPrice, 101);
});

test("buildFlatChartData returns empty without usable prices", () => {
  assert.deepEqual(buildFlatChartData({ snapshot: {}, chartResolution: "1H" }), []);
});

test("buildFlatChartData creates two stable points", () => {
  const points = buildFlatChartData({ snapshot, chartStartTime: 1000, chartEndTime: 2000, chartResolution: "1H" });
  assert.equal(points.length, 2);
  assert.equal(points[0].timestamp, 1000);
  assert.equal(points[1].timestamp, 2000);
});

test("buildSimulationChartData appends current snapshot when candles are stale", () => {
  const points = buildSimulationChartData({
    candles: [{ bucket: 1000, indexClose: 99, markClose: 100, indexOpen: 99, indexHigh: 99, indexLow: 99, markOpen: 100, markHigh: 100, markLow: 100 }],
    snapshot,
    chartResolution: "1H",
  });
  assert.equal(points.length, 2);
  assert.equal(points[1].timestamp, 2000);
});
