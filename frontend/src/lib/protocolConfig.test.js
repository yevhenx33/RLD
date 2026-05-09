import test from "node:test";
import assert from "node:assert/strict";
import { API_PROTOCOLS, marketRouteFor, normalizeMarketIdForApi } from "./protocolConfig.js";

test("Euler protocol config supports market pages and address routes", () => {
  assert.equal(API_PROTOCOLS.euler.supportsMarketPage, true);
  assert.equal(marketRouteFor("EULER_MARKET", "0xba98fc35c9dfd69178ad5dce9fa29c64554783b5"), "/data/euler/ba98fc35c9dfd69178ad5dce9fa29c64554783b5");
  assert.equal(normalizeMarketIdForApi("euler", "ba98fc35c9dfd69178ad5dce9fa29c64554783b5"), "0xba98fc35c9dfd69178ad5dce9fa29c64554783b5");
});
