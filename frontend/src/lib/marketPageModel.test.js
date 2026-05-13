import assert from "node:assert/strict";
import test from "node:test";
import { buildFluidVaultPageModel, buildMetaMorphoVaultPageModel, buildMorphoMarketPageModel, buildStandardMarketPageModel } from "./marketPageModel.js";

test("buildStandardMarketPageModel normalizes chart and signed cumulative flow", () => {
  const model = buildStandardMarketPageModel({
    market: {
      symbol: "USDC",
      protocol: "AAVE_MARKET",
      supplyUsd: 1000,
      borrowUsd: 250,
      supplyApy: 0.04,
      borrowApy: 0.08,
    },
    rateChart: [
      { timestamp: 20, supplyApy: 0.05, borrowApy: 0.09, supplyUsd: 1200, borrowUsd: 300 },
      { timestamp: 10, supplyApy: 0.04, borrowApy: 0.08, supplyUsd: 1000, borrowUsd: 250 },
    ],
    flowChart: [
      { timestamp: 10, supplyInflowUsd: 100, supplyOutflowUsd: 20, netSupplyFlowUsd: 80, borrowInflowUsd: 50, borrowOutflowUsd: 10, netBorrowFlowUsd: 40 },
      { timestamp: 20, supplyInflowUsd: 0, supplyOutflowUsd: 5, netSupplyFlowUsd: -5, borrowInflowUsd: 0, borrowOutflowUsd: 2, netBorrowFlowUsd: -2 },
    ],
  });

  assert.equal(model.market.symbol, "USDC");
  assert.equal(model.market.utilization, 0.25);
  assert.deepEqual(model.tsData.map((point) => point.timestamp), [10, 20]);
  assert.equal(model.flowData[0].supplyOutflowUsd, -20);
  assert.equal(model.flowData[1].cumulativeSupplyNetInflowUsd, 75);
  assert.equal(model.flowData[1].cumulativeBorrowNetInflowUsd, 38);
});

test("buildFluidVaultPageModel keeps outflows unsigned and starts at first positive net flow", () => {
  const model = buildFluidVaultPageModel({
    market: { symbol: "ETH/USDC", supplyUsd: 500, borrowUsd: 100 },
    rateChart: [{ timestamp: 1, supplyUsd: 500, borrowUsd: 100 }],
    flowChart: [
      { timestamp: 1, supplyOutflowUsd: 5, netSupplyFlowUsd: -5, netBorrowFlowUsd: 0 },
      { timestamp: 2, supplyInflowUsd: 20, supplyOutflowUsd: 3, netSupplyFlowUsd: 17, netBorrowFlowUsd: 4 },
    ],
  });

  assert.equal(model.market.protocol, "FLUID_VAULT");
  assert.deepEqual(model.flowData.map((point) => point.timestamp), [2]);
  assert.equal(model.flowData[0].supplyOutflowUsd, 3);
  assert.equal(model.cumulativeFlowData[0].cumulativeSupplyNetInflowUsd, 17);
});

test("buildMorphoMarketPageModel preserves allocation columnar data", () => {
  const allocationColumnar = { timestamps: [1], vaults: [], suppliedUsd: [] };
  const model = buildMorphoMarketPageModel({
    market: {
      symbol: "USDC",
      collateralSymbol: "WETH",
      protocol: "MORPHO_MARKET",
      supplyUsd: 1000,
      borrowUsd: 400,
      collateralUsd: 1500,
      oracle: "0xoracle",
      loanToken: "0xloan",
      collateralToken: "0xcollateral",
    },
    rateChart: [{ timestamp: 1, supplyUsd: 1000, borrowUsd: 400 }],
    flowChart: [{ timestamp: 1, supplyInflowUsd: 50, netSupplyFlowUsd: 50 }],
    allocationColumnar,
  });

  assert.equal(model.market.collateralSymbol, "WETH");
  assert.equal(model.market.collateralUsd, 1500);
  assert.equal(model.market.oracle, "0xoracle");
  assert.equal(model.allocationColumnar, allocationColumnar);
});

test("buildMetaMorphoVaultPageModel normalizes history, exposure, and flow rows", () => {
  const model = buildMetaMorphoVaultPageModel({
    vault: { tvlUsd: 1000 },
    history: [{ timestamp: 2, totalDepositsUsd: 1000, utilization: 0.7, netApy: 0.05 }],
    exposures: [
      { marketId: "m1", suppliedUsd: 250, liquidityUsd: 100, supplyApy: 0.04, borrowApy: 0.08, utilization: 0.5 },
      { marketId: "empty", suppliedUsd: 0 },
    ],
    flowLinks: [{ action: "Net Inflow", valueUsd: 5 }],
    flowChart: [
      { timestamp: 2, depositUsd: 20, withdrawUsd: 5, netFlowUsd: 15 },
      { timestamp: 2, depositUsd: 1, withdrawUsd: 2, netFlowUsd: -1 },
    ],
  });

  assert.equal(model.totalDeposits, 1000);
  assert.equal(model.history[0].netApyPct, 5);
  assert.equal(model.exposures.length, 1);
  assert.equal(model.exposures[0].allocationShare, 0.25);
  assert.equal(model.flowLinks.length, 1);
  assert.equal(model.flowData[0].inflowUsd, 21);
  assert.equal(model.flowData[0].outflowUsd, -7);
  assert.equal(model.flowData[0].netFlowUsd, 14);
});
