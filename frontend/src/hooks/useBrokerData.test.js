import assert from "node:assert/strict";
import test from "node:test";
import { BROKER_DATA_QUERY, LEGACY_BROKER_DATA_QUERY, getActiveBrokerState, isBrokerSyncedToBlock, isScopedBrokerQueryUnsupported, resolveSelectedBrokerAddress } from "./brokerDataConfig.js";

const brokers = [
  { address: "0xNewBroker", wausdcBalance: "10000000000", wrlpBalance: "2000000", debtPrincipal: "3000000", updatedBlock: 120 },
  { address: "0xOldBroker", wausdcBalance: "5000000", wrlpBalance: "0", debtPrincipal: "0", updatedBlock: 80 },
];

test("broker data query scopes brokers, profile, and operations to owner, market, and selected broker", () => {
  assert.match(BROKER_DATA_QUERY, /brokers\(marketId: \$marketId, owner: \$owner\)/);
  assert.match(
    BROKER_DATA_QUERY,
    /brokerProfile\(owner: \$owner, marketId: \$marketId, brokerAddress: \$brokerAddress\)/,
  );
  assert.match(
    BROKER_DATA_QUERY,
    /brokerOperations\(owner: \$owner, marketId: \$marketId, brokerAddress: \$brokerAddress\)/,
  );
  assert.match(BROKER_DATA_QUERY, /updatedBlock/);
});

test("resolveSelectedBrokerAddress auto-selects newest broker when selection is empty", () => {
  assert.equal(resolveSelectedBrokerAddress(brokers, null), "0xNewBroker");
});

test("resolveSelectedBrokerAddress preserves a selected broker that belongs to the current market", () => {
  assert.equal(resolveSelectedBrokerAddress(brokers, "0xOldBroker"), "0xOldBroker");
  assert.equal(resolveSelectedBrokerAddress(brokers, "0xoldbroker"), "0xoldbroker");
});

test("resolveSelectedBrokerAddress clears stale broker state when current market has no brokers", () => {
  assert.equal(resolveSelectedBrokerAddress([], "0xOldBroker"), null);
});

test("resolveSelectedBrokerAddress replaces stale selections with the newest current-market broker", () => {
  assert.equal(resolveSelectedBrokerAddress(brokers, "0xOtherMarketBroker"), "0xNewBroker");
});


test("legacy broker data query remains available while old GraphQL APIs are rolling", () => {
  assert.match(LEGACY_BROKER_DATA_QUERY, /brokerProfile\(owner: \$owner\)/);
  assert.match(LEGACY_BROKER_DATA_QUERY, /brokerOperations\(owner: \$owner\)/);
});

test("isScopedBrokerQueryUnsupported detects old GraphQL schemas", () => {
  assert.equal(
    isScopedBrokerQueryUnsupported({ errors: [{ message: "Unknown argument 'marketId' on field 'Query.brokerProfile'." }] }),
    true,
  );
  assert.equal(isScopedBrokerQueryUnsupported(new Error("network failed")), false);
});


test("active broker state derives display balances from broker list", () => {
  const state = getActiveBrokerState(brokers, "0xOldBroker", {
    address: "0xOldBroker",
    wausdcBalance: "0",
    wrlpBalance: "0",
    debtPrincipal: "0",
  });
  assert.equal(state.activeBrokerAddress, "0xOldBroker");
  assert.equal(state.brokerBalance, 5);
  assert.equal(state.wrlpBalance, 0);
  assert.equal(state.debtPrincipal, 0);
  assert.equal(state.updatedBlock, 80);
});

test("broker sync helper compares selected broker updated block to receipt block", () => {
  assert.equal(isBrokerSyncedToBlock(brokers[0], 119), true);
  assert.equal(isBrokerSyncedToBlock(brokers[0], 120), true);
  assert.equal(isBrokerSyncedToBlock(brokers[0], 121), false);
});

test("updatedBlock query fallback detects rolling schemas", () => {
  assert.equal(
    isScopedBrokerQueryUnsupported({ errors: [{ message: "Cannot query field 'updatedBlock' on type 'Broker'." }] }),
    true,
  );
});
