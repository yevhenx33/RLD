import assert from "node:assert/strict";
import test from "node:test";
import {
  CHAIN_HEX,
  RPC_URL,
  buildRldChainParams,
  ensureRldChain,
  getWalletErrorMessage,
} from "./connection.js";

function fakeEthereum(handler) {
  const calls = [];
  return {
    calls,
    request: async (payload) => {
      calls.push(payload);
      return handler(payload, calls.length);
    },
  };
}

test("RLD chain params are MetaMask-compatible", () => {
  const params = buildRldChainParams("https://demo.example/rpc");
  assert.equal(params.chainId, CHAIN_HEX);
  assert.equal(params.nativeCurrency.symbol, "ETH");
  assert.deepEqual(params.rpcUrls, ["https://demo.example/rpc"]);
});

test("RPC_URL defaults to the same-origin proxy path outside a browser", () => {
  assert.equal(RPC_URL, "/rpc");
});

test("ensureRldChain only switches when chain is already known", async () => {
  const eth = fakeEthereum(async () => null);

  await ensureRldChain(eth);

  assert.deepEqual(
    eth.calls.map((call) => call.method),
    ["wallet_switchEthereumChain"],
  );
  assert.deepEqual(eth.calls[0].params, [{ chainId: CHAIN_HEX }]);
});

test("ensureRldChain adds chain 31337 when MetaMask does not know it", async () => {
  const eth = fakeEthereum(async (payload, count) => {
    if (payload.method === "wallet_switchEthereumChain" && count === 1) {
      throw { code: 4902 };
    }
    return null;
  });

  await ensureRldChain(eth);

  assert.deepEqual(
    eth.calls.map((call) => call.method),
    [
      "wallet_switchEthereumChain",
      "wallet_addEthereumChain",
      "wallet_switchEthereumChain",
    ],
  );
  assert.equal(eth.calls[1].params[0].chainId, CHAIN_HEX);
});

test("wallet rejection is surfaced as a stable user-facing message", async () => {
  const eth = fakeEthereum(async () => {
    throw { code: 4001, message: "User rejected the request." };
  });

  await assert.rejects(
    () => ensureRldChain(eth),
    /Wallet request rejected/,
  );
  assert.equal(getWalletErrorMessage({ code: -32002 }), "Open MetaMask and finish the pending wallet request");
});
