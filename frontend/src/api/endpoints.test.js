import assert from "node:assert/strict";
import test from "node:test";
import {
  API_BASE_URL,
  ANALYTICS_GRAPHQL_URL,
  FAUCET_API_URL,
  RUNTIME_MANIFEST_URL,
  RPC_URL,
  SIM_GRAPHQL_URL,
} from "./endpoints.js";

test("browser API endpoints default to same-origin proxy paths", () => {
  assert.equal(API_BASE_URL, "/api");
  assert.equal(SIM_GRAPHQL_URL, "/graphql");
  assert.equal(RUNTIME_MANIFEST_URL, "/api/runtime-manifest");
  assert.equal(ANALYTICS_GRAPHQL_URL, "/analytics/graphql");
  assert.equal(RPC_URL, "/rpc");
  assert.equal(FAUCET_API_URL, "/api/faucet");
});
