import assert from "node:assert/strict";
import test from "node:test";
import { ethers } from "ethers";
import {
  BROKER_ROUTER_ABI,
  HOOKLESS_POOL,
  buildHooklessPoolKey,
  buildQuoterExactInputSingleParams,
  encodeExecuteLongCalldata,
  zeroForOneForDirection,
} from "./peripheryIntegration.js";

const collateral = "0x00000000000000000000000000000000000000c0";
const position = "0x00000000000000000000000000000000000000f0";
const broker = "0x0000000000000000000000000000000000000b0b";
const infrastructure = { pool_fee: 500, tick_spacing: 5 };

test("buildHooklessPoolKey sorts tokens and strips hooks", () => {
  const key = buildHooklessPoolKey(infrastructure, position, collateral);

  assert.equal(key.currency0, collateral);
  assert.equal(key.currency1, position);
  assert.equal(key.fee, 500);
  assert.equal(key.tickSpacing, 5);
  assert.equal(key.hooks, HOOKLESS_POOL);
});

test("BrokerRouter calldata keeps current min-out signatures", () => {
  const iface = new ethers.Interface(BROKER_ROUTER_ABI);
  const poolKey = buildHooklessPoolKey(infrastructure, collateral, position);

  const executeLong = encodeExecuteLongCalldata(broker, 1_000_000n, poolKey, 900_000n);
  const decodedLong = iface.decodeFunctionData("executeLong", executeLong);
  assert.equal(decodedLong.broker.toLowerCase(), broker);
  assert.equal(decodedLong.amountIn, 1_000_000n);
  assert.equal(decodedLong.minAmountOut, 900_000n);
  assert.equal(decodedLong.poolKey.hooks, HOOKLESS_POOL);

  const closeLong = iface.encodeFunctionData("closeLong", [
    broker,
    2_000_000n,
    poolKey,
    1_900_000n,
  ]);
  const decodedCloseLong = iface.decodeFunctionData("closeLong", closeLong);
  assert.equal(decodedCloseLong.minAmountOut, 1_900_000n);

  const executeShort = iface.encodeFunctionData("executeShort", [
    broker,
    3_000_000n,
    4_000_000n,
    poolKey,
    3_900_000n,
  ]);
  const decodedShort = iface.decodeFunctionData("executeShort", executeShort);
  assert.equal(decodedShort.initialCollateral, 3_000_000n);
  assert.equal(decodedShort.targetDebtAmount, 4_000_000n);
  assert.equal(decodedShort.minProceeds, 3_900_000n);

  const closeShort = iface.encodeFunctionData("closeShort", [
    broker,
    5_000_000n,
    poolKey,
    4_900_000n,
  ]);
  const decodedCloseShort = iface.decodeFunctionData("closeShort", closeShort);
  assert.equal(decodedCloseShort.collateralToSpend, 5_000_000n);
  assert.equal(decodedCloseShort.minDebtBought, 4_900_000n);

  const previewLong = iface.encodeFunctionData("previewExecuteLong", [
    broker,
    1_000_000n,
    poolKey,
  ]);
  const decodedPreviewLong = iface.decodeFunctionData("previewExecuteLong", previewLong);
  assert.equal(decodedPreviewLong.amountIn, 1_000_000n);

  const previewPayload = iface.encodeErrorResult("RoutePreview", [900_000n]);
  const decodedPreview = iface.parseError(previewPayload);
  assert.equal(decodedPreview.name, "RoutePreview");
  assert.equal(decodedPreview.args.amountOut, 900_000n);
});

test("quoter params use hookless keys and expected directions", () => {
  const buyParams = buildQuoterExactInputSingleParams(
    infrastructure,
    collateral,
    position,
    1_000_000n,
    "BUY",
  );
  assert.equal(buyParams.poolKey.hooks, HOOKLESS_POOL);
  assert.equal(buyParams.zeroForOne, true);
  assert.equal(buyParams.exactAmount, 1_000_000n);
  assert.equal(buyParams.hookData, "0x");

  assert.equal(zeroForOneForDirection(collateral, position, "SELL"), false);
});
