import assert from "node:assert/strict";
import test from "node:test";
import {
  CHAIN_HEX,
  RPC_URL,
  buildRldChainParams,
  ensureRldChain,
  getWalletErrorMessage,
  resetCachedWalletProvider,
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

test("ensureRldChain refreshes RPC params when chain is already known", async () => {
  resetCachedWalletProvider();
  const eth = fakeEthereum(async (payload) => {
    if (payload.method === "eth_chainId") return CHAIN_HEX;
    if (payload.method === "eth_blockNumber") return "0x1";
    return null;
  });

  await ensureRldChain(eth);

  assert.deepEqual(
    eth.calls.map((call) => call.method),
    ["wallet_switchEthereumChain", "wallet_addEthereumChain", "eth_chainId", "eth_blockNumber"],
  );
  assert.deepEqual(eth.calls[0].params, [{ chainId: CHAIN_HEX }]);
  assert.equal(eth.calls[1].params[0].rpcUrls[0], RPC_URL);
});

test("ensureRldChain adds chain 31337 when MetaMask does not know it", async () => {
  resetCachedWalletProvider();
  const eth = fakeEthereum(async (payload, count) => {
    if (payload.method === "wallet_switchEthereumChain" && count === 1) {
      throw { code: 4902 };
    }
    if (payload.method === "eth_chainId") return CHAIN_HEX;
    if (payload.method === "eth_blockNumber") return "0x1";
    return null;
  });

  await ensureRldChain(eth);

  assert.deepEqual(
    eth.calls.map((call) => call.method),
    [
      "wallet_switchEthereumChain",
      "wallet_addEthereumChain",
      "wallet_switchEthereumChain",
      "eth_chainId",
      "eth_blockNumber",
    ],
  );
  assert.equal(eth.calls[1].params[0].chainId, CHAIN_HEX);
});

test("ensureRldChain rejects a wallet that remains on the wrong chain", async () => {
  resetCachedWalletProvider();
  const eth = fakeEthereum(async (payload) => {
    if (payload.method === "eth_chainId") return "0x1";
    return null;
  });

  await assert.rejects(
    () => ensureRldChain(eth),
    /expected 0x7a69/,
  );
});

test("ensureRldChain rejects an unhealthy wallet RPC before execution", async () => {
  resetCachedWalletProvider();
  const eth = fakeEthereum(async (payload) => {
    if (payload.method === "eth_chainId") return CHAIN_HEX;
    if (payload.method === "eth_blockNumber") {
      throw {
        code: -32002,
        message: "RPC endpoint returned too many errors, retrying in 0.29 minutes.",
      };
    }
    return null;
  });

  await assert.rejects(
    () => ensureRldChain(eth),
    /approve the RLD network RPC update/,
  );
});

test("wallet rejection is surfaced as a stable user-facing message", async () => {
  resetCachedWalletProvider();
  const eth = fakeEthereum(async () => {
    throw { code: 4001, message: "User rejected the request." };
  });

  await assert.rejects(
    () => ensureRldChain(eth),
    /Wallet request rejected/,
  );
  assert.equal(getWalletErrorMessage({ code: -32002 }), "Open MetaMask and finish the pending wallet request");
});

test("RPC endpoint throttle errors get a specific recovery message", () => {
  assert.match(
    getWalletErrorMessage({
      code: -32002,
      message: "RPC endpoint returned too many errors, retrying in 0.29 minutes.",
    }),
    /approve the RLD network RPC update/,
  );
});
