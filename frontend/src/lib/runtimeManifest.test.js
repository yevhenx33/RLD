import assert from "node:assert/strict";
import test from "node:test";
import {
  getRuntimeMarket,
  normalizeRuntimeManifest,
  runtimeExecutionBlockReason,
  runtimeMarketToMarketInfo,
} from "./runtimeManifest.js";

const manifestV1 = {
  schemaVersion: 1,
  deploymentId: "demo-1",
  chainId: 31337,
  rpcUrl: "/rpc",
  faucetUrl: "/api/faucet",
  indexerBlock: 99,
  chainBlock: 100,
  readiness: {
    ready: true,
    status: "ready",
    reasons: [],
    indexerLagBlocks: 1,
    maxIndexerLagBlocks: 12,
  },
  contracts: {
    rldCore: "0x0000000000000000000000000000000000000001",
  },
  globalContracts: {
    rldCore: "0x0000000000000000000000000000000000000001",
    ghostRouter: "0x00000000000000000000000000000000000000b4",
    twapEngine: "0x00000000000000000000000000000000000000b5",
  },
  markets: {
    perp: {
      type: "perp",
      marketId: "0xperp",
      poolId: "0xpool-perp",
      zeroForOneLong: false,
      collateral: { symbol: "waUSDC", address: "0x00000000000000000000000000000000000000c0" },
      positionToken: { symbol: "wRLP", address: "0x00000000000000000000000000000000000000f0" },
      brokerFactory: "0x00000000000000000000000000000000000000b0",
      brokerRouter: "0x00000000000000000000000000000000000000b1",
      brokerExecutor: "0x00000000000000000000000000000000000000b2",
      bondFactory: "0x00000000000000000000000000000000000000b3",
      ghostRouter: "0x00000000000000000000000000000000000000b4",
      twapEngine: "0x00000000000000000000000000000000000000b5",
      poolManager: "0x00000000000000000000000000000000000000b6",
      v4Quoter: "0x00000000000000000000000000000000000000b7",
      v4PositionManager: "0x00000000000000000000000000000000000000b8",
      poolFee: 500,
      tickSpacing: 5,
      twamm: {
        engine: "0x00000000000000000000000000000000000000b5",
        marketId: "0xpool-perp",
        buyPositionZeroForOne: false,
      },
      featureFlags: { perps: true, bonds: true, liquidity: true },
      riskParams: { funding_period_sec: 2592000 },
    },
    cds: {
      type: "cds",
      marketId: "0xcds",
      poolId: "0xpool-cds",
      collateral: { symbol: "USDC", address: "0x00000000000000000000000000000000000000d0" },
      positionToken: { symbol: "wCDSUSDC", address: "0x00000000000000000000000000000000000000d1" },
      zeroForOneLong: true,
      contracts: {
        brokerFactory: "0x00000000000000000000000000000000000000d2",
        brokerRouter: "0x00000000000000000000000000000000000000d4",
        cdsCoverageFactory: "0x00000000000000000000000000000000000000d3",
      },
      pool: {
        token0: "0x00000000000000000000000000000000000000d0",
        token1: "0x00000000000000000000000000000000000000d1",
        key: {
          currency0: "0x00000000000000000000000000000000000000d0",
          currency1: "0x00000000000000000000000000000000000000d1",
          fee: 500,
          tickSpacing: 5,
          hooks: "0x0000000000000000000000000000000000000000",
        },
      },
      execution: {
        brokerRouter: "0x00000000000000000000000000000000000000d4",
        buyPositionZeroForOne: true,
        sellPositionZeroForOne: false,
      },
      twamm: {
        engine: "0x00000000000000000000000000000000000000b5",
        marketId: "0xpool-cds",
        buyPositionZeroForOne: true,
        sellPositionZeroForOne: false,
      },
      poolFee: 500,
      tickSpacing: 5,
      featureFlags: { cdsCoverage: true, liquidity: true },
    },
  },
};

test("normalizeRuntimeManifest preserves v1 chain, readiness, and markets", () => {
  const manifest = normalizeRuntimeManifest(manifestV1);

  assert.equal(manifest.schemaVersion, 1);
  assert.equal(manifest.chainId, 31337);
  assert.equal(manifest.readiness.ready, true);
  assert.equal(manifest.globalContracts.twapEngine, manifestV1.globalContracts.twapEngine);
  assert.equal(getRuntimeMarket(manifest, "cds").positionToken.symbol, "wCDSUSDC");
  assert.equal(getRuntimeMarket(manifest, "cds").contracts.brokerRouter, manifestV1.markets.cds.contracts.brokerRouter);
  assert.equal(getRuntimeMarket(manifest, "cds").twamm.buyPositionZeroForOne, true);
  assert.equal(getRuntimeMarket(manifest, "perp").twamm.buyPositionZeroForOne, false);
  assert.equal(getRuntimeMarket(manifest, "0xpool-perp").type, "perp");
});

test("runtimeMarketToMarketInfo exposes execution addresses with legacy aliases", () => {
  const manifest = normalizeRuntimeManifest(manifestV1);
  const marketInfo = runtimeMarketToMarketInfo(manifest, "perp");

  assert.equal(marketInfo.market_id, "0xperp");
  assert.equal(marketInfo.pool_id, "0xpool-perp");
  assert.equal(marketInfo.infrastructure.broker_router, manifestV1.markets.perp.brokerRouter);
  assert.equal(marketInfo.infrastructure.brokerRouter, manifestV1.markets.perp.brokerRouter);
  assert.equal(marketInfo.infrastructure.twammMarketId, "0xpool-perp");
  assert.equal(marketInfo.infrastructure.buyPositionZeroForOne, false);
  assert.equal(marketInfo.zeroForOneLong, false);
  assert.equal(
    marketInfo.infrastructure.twamm_hook,
    "0x0000000000000000000000000000000000000000",
  );
  assert.equal(marketInfo.infrastructure.runtime_ready, true);
  assert.equal(marketInfo.risk_params.funding_period_sec, 2592000);
});

test("runtimeMarketToMarketInfo exposes separated CDS execution and TWAMM config", () => {
  const manifest = normalizeRuntimeManifest(manifestV1);
  const marketInfo = runtimeMarketToMarketInfo(manifest, "cds");

  assert.equal(marketInfo.infrastructure.broker_router, manifestV1.markets.cds.contracts.brokerRouter);
  assert.equal(marketInfo.infrastructure.twammMarketId, "0xpool-cds");
  assert.equal(marketInfo.infrastructure.buyPositionZeroForOne, true);
  assert.equal(marketInfo.infrastructure.sellPositionZeroForOne, false);
  assert.equal(marketInfo.zeroForOneLong, true);
  assert.equal(marketInfo.twamm.buyPositionZeroForOne, true);
  assert.equal(marketInfo.pool.key.currency0, manifestV1.markets.cds.pool.key.currency0);
});

test("runtimeExecutionBlockReason blocks degraded manifests and missing markets", () => {
  const degraded = normalizeRuntimeManifest({
    ...manifestV1,
    readiness: { ready: false, reasons: ["indexer_lag"] },
  });
  assert.match(runtimeExecutionBlockReason(degraded, "perp"), /indexer_lag/);

  const manifest = normalizeRuntimeManifest(manifestV1);
  assert.match(runtimeExecutionBlockReason(manifest, "missing"), /missing/);
  assert.equal(runtimeExecutionBlockReason(manifest, "perp"), null);
});

test("normalizeRuntimeManifest rejects unsupported schema versions", () => {
  assert.throws(
    () => normalizeRuntimeManifest({ schemaVersion: 2 }),
    /Unsupported runtime manifest schema/,
  );
});
