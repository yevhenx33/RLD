import test from "node:test";
import assert from "node:assert/strict";
import { API_PROTOCOLS, marketRouteFor, normalizeMarketIdForApi } from "./protocolConfig.js";

test("Euler protocol config supports market pages and address routes", () => {
  assert.equal(API_PROTOCOLS.euler.supportsMarketPage, true);
  assert.equal(marketRouteFor("EULER_MARKET", "0xba98fc35c9dfd69178ad5dce9fa29c64554783b5"), "/data/euler/ba98fc35c9dfd69178ad5dce9fa29c64554783b5");
  assert.equal(normalizeMarketIdForApi("euler", "ba98fc35c9dfd69178ad5dce9fa29c64554783b5"), "0xba98fc35c9dfd69178ad5dce9fa29c64554783b5");
});

test("Spark protocol config reuses pooled reserve address routes", () => {
  assert.equal(API_PROTOCOLS.spark.apiProtocol, "SPARK_MARKET");
  assert.equal(marketRouteFor("SPARK_MARKET", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"), "/data/spark/c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2");
  assert.equal(normalizeMarketIdForApi("spark", "c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"), "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2");
});

test("Compound v3 protocol config supports comet address routes", () => {
  const comet = "0xc3d688b66703497daa19211eedff47f25384cdc3";
  assert.equal(API_PROTOCOLS["compound-v3"].apiProtocol, "COMPOUND_V3_MARKET");
  assert.equal(API_PROTOCOLS["compound-v3"].displayName, "Compound V3");
  assert.equal(marketRouteFor("COMPOUND_V3_MARKET", comet), "/data/compound-v3/c3d688b66703497daa19211eedff47f25384cdc3");
  assert.equal(normalizeMarketIdForApi("compound-v3", comet.slice(2)), comet);
});
