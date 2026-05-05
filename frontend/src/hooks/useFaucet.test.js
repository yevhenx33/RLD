import assert from "node:assert/strict";
import test from "node:test";
import { hasFundedTokenBalance, requestFaucetFunds } from "./useFaucet.js";

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  };
}

test("requestFaucetFunds posts the normalized wallet address", async () => {
  const calls = [];
  const result = await requestFaucetFunds(
    "/api/faucet",
    "0xabc",
    {
      fetchImpl: async (url, options) => {
        calls.push({ url, options });
        return response(200, JSON.stringify({ success: true, ok: true, tx: "0x1" }));
      },
    },
  );

  assert.equal(result.success, true);
  assert.equal(calls[0].url, "/api/faucet");
  assert.deepEqual(JSON.parse(calls[0].options.body), { address: "0xabc" });
});

test("requestFaucetFunds surfaces faucet JSON errors", async () => {
  await assert.rejects(
    () => requestFaucetFunds(
      "/api/faucet",
      "0xabc",
      {
        fetchImpl: async () => response(429, JSON.stringify({
          success: false,
          error: "Rate limited. Try again in 60s",
        })),
      },
    ),
    /Rate limited/,
  );
});

test("requestFaucetFunds rejects invalid JSON responses", async () => {
  await assert.rejects(
    () => requestFaucetFunds(
      "/api/faucet",
      "0xabc",
      { fetchImpl: async () => response(502, "<html>bad gateway</html>") },
    ),
    /invalid JSON/,
  );
});

test("hasFundedTokenBalance applies the configured token threshold", () => {
  assert.equal(hasFundedTokenBalance({ waRaw: 999_999n, usdcRaw: 0n }), false);
  assert.equal(hasFundedTokenBalance({ waRaw: 1_000_000n, usdcRaw: 0n }), true);
  assert.equal(hasFundedTokenBalance({ waRaw: 0n, usdcRaw: "1000000" }), true);
});
