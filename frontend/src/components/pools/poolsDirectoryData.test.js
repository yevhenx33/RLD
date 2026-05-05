import assert from "node:assert/strict";
import test from "node:test";
import {
  buildPoolsDirectoryRows,
  formatUSD,
  shortId,
} from "./poolsDirectoryData.js";

const sampleData = {
  perpInfo: {
    type: "perp",
    marketId: "0xperpmarket",
    poolId: "0xperppool000000000000000000000000000000000000000000000000000000",
    position_symbol: "wRLP",
    collateral_symbol: "waUSDC",
    poolFee: 500,
  },
  perpSnapshot: {
    market: { marketId: "0xperpmarket" },
    pool: { poolId: "0xperppool", tvlUsd: 1000 },
    derived: {
      poolTvlUsd: 1000,
      volume24hUsd: 2000,
      swapCount24h: 3,
    },
  },
  cdsInfo: {
    type: "cds",
    market_id: "0xcdsmarket",
    pool_id: "0xcdspool0000000000000000000000000000000000000000000000000000000",
    position_token: { symbol: "wCDSUSDC" },
    collateral: { symbol: "USDC" },
    pool_fee: 500,
  },
  cdsSnapshot: {
    market: { marketId: "0xcdsmarket" },
    pool: { poolId: "0xcdspool", tvlUsd: 500 },
    derived: {
      poolTvlUsd: 500,
      volume24hUsd: 0,
      swapCount24h: 0,
    },
  },
};

test("buildPoolsDirectoryRows exposes perp and CDS pool rows", () => {
  const rows = buildPoolsDirectoryRows(sampleData);

  assert.equal(rows.length, 2);
  assert.equal(rows[0].address, "0xperpmarket");
  assert.equal(rows[0].pair, "wRLP / waUSDC");
  assert.equal(rows[0].typeLabel, "PERP");
  assert.equal(rows[0].fees24h, 1);
  assert.equal(rows[1].address, "0xcdsmarket");
  assert.equal(rows[1].poolId, sampleData.cdsInfo.pool_id);
  assert.equal(rows[1].pair, "wCDSUSDC / USDC");
  assert.equal(rows[1].typeLabel, "CDS");
});

test("pool directory format helpers keep compact table labels stable", () => {
  assert.equal(formatUSD(877483.29), "$877.5K");
  assert.equal(formatUSD(null), "-");
  assert.equal(shortId("0xc9be776d440fe1afdb79b61679e2d33dd9e02dcd21d56acb37b8f7b71a546bdb"), "0xc9be...6bdb");
});
