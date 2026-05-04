import assert from "node:assert/strict";
import test from "node:test";
import { computeLiquidity, decodePositionInfo, liquidityToAmounts, priceToTick, safeSqrtPrice } from "./poolMath.js";

test("priceToTick aligns positive prices to spacing", () => {
  assert.equal(priceToTick(1, 5), 0);
  assert.equal(priceToTick(1.0001 ** 12, 5), 10);
  assert.equal(priceToTick(0, 5), 0);
  assert.equal(priceToTick(Number.NaN, 5), 0);
});

test("safeSqrtPrice clamps extreme ticks", () => {
  assert.ok(Number.isFinite(safeSqrtPrice(1_000_000)));
  assert.ok(safeSqrtPrice(-1_000_000) > 0);
});

test("decodePositionInfo sign-extends int24 ticks", () => {
  const tickLower = 0xFFFF9Cn;
  const tickUpper = 200n;
  const poolId = 0x12345n;
  const packed = (poolId << 56n) | (tickUpper << 32n) | (tickLower << 8n);
  const decoded = decodePositionInfo(`0x${packed.toString(16)}`);
  assert.equal(decoded.tickLower, -100);
  assert.equal(decoded.tickUpper, 200);
  assert.equal(decoded.poolId, poolId);
});

test("liquidity math returns bounded positive values in range", () => {
  const liquidity = computeLiquidity(1_000_000, 1_000_000, -100, 100, 0);
  assert.ok(liquidity > 0n);
  const amounts = liquidityToAmounts(liquidity, -100, 100, 0);
  assert.ok(amounts.amount0 > 0);
  assert.ok(amounts.amount1 > 0);
});

test("computeLiquidity returns zero for invalid empty amounts", () => {
  assert.equal(computeLiquidity(0, 0, -100, 100, 0), 0n);
});
