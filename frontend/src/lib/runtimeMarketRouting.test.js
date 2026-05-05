import test from "node:test";
import assert from "node:assert/strict";
import { runtimeMarketKeyForPath } from "./runtimeMarketRouting.js";

test("runtime market routing keeps bond detail pages on the perp market", () => {
  assert.equal(runtimeMarketKeyForPath("/bonds/0xabcDEF", "0xabcDEF"), "perp");
});

test("runtime market routing resolves explicit market detail paths", () => {
  assert.equal(runtimeMarketKeyForPath("/markets/cds", null), "cds");
  assert.equal(runtimeMarketKeyForPath("/markets/cds/cds", "cds"), "cds");
  assert.equal(runtimeMarketKeyForPath("/markets/perps/perp", "perp"), "perp");
  assert.equal(runtimeMarketKeyForPath("/markets/pools/0xPOOL", "0xPOOL"), "0xpool");
});
