import assert from "node:assert/strict";
import test from "node:test";
import { REFRESH_INTERVALS } from "./refreshIntervals.js";

test("refresh interval constants preserve current production cadence", () => {
  assert.equal(REFRESH_INTERVALS.SIMULATION_SNAPSHOT_MS, 2000);
  assert.equal(REFRESH_INTERVALS.SIMULATION_ACCOUNT_MS, 15000);
  assert.equal(REFRESH_INTERVALS.SIMULATION_CHART_MS, 30000);
  assert.equal(REFRESH_INTERVALS.ANALYTICS_PAGE_MS, 30000);
  assert.equal(REFRESH_INTERVALS.SWAP_QUOTE_MS, 12000);
  assert.equal(REFRESH_INTERVALS.LP_BALANCE_MS, 5000);
  assert.equal(REFRESH_INTERVALS.TWAMM_LOGS_MS, 5000);
});
