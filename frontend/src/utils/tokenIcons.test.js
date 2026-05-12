import test from "node:test";
import assert from "node:assert/strict";
import { getProtocolDisplayName, getProtocolIcon } from "./tokenIcons.js";

test("Compound v3 protocol helpers preserve versioned protocol identity", () => {
  assert.equal(getProtocolDisplayName("COMPOUND_V3_MARKET"), "Compound V3");
  assert.match(getProtocolIcon("COMPOUND_V3_MARKET"), /compound-v3\.png$/);
});
