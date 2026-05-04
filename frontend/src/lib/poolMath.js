const MAX_UINT128 = (1n << 128n) - 1n;

export function priceToTick(price, tickSpacing = 5) {
  const numericPrice = Number(price);
  if (!Number.isFinite(numericPrice) || numericPrice <= 0) return 0;
  const spacing = Number(tickSpacing) || 1;
  const raw = Math.log(numericPrice) / Math.log(1.0001);
  return Math.floor(raw / spacing) * spacing;
}

export function safeSqrtPrice(tick) {
  const clamped = Math.max(-887270, Math.min(887270, Number(tick) || 0));
  return Math.sqrt(Math.pow(1.0001, clamped));
}

export function decodePositionInfo(infoBytes32) {
  const val = BigInt(infoBytes32);
  const tickLowerRaw = Number((val >> 8n) & 0xFFFFFFn);
  const tickUpperRaw = Number((val >> 32n) & 0xFFFFFFn);
  const tickLower = tickLowerRaw >= 0x800000 ? tickLowerRaw - 0x1000000 : tickLowerRaw;
  const tickUpper = tickUpperRaw >= 0x800000 ? tickUpperRaw - 0x1000000 : tickUpperRaw;
  const poolId = val >> 56n;
  return { tickLower, tickUpper, poolId };
}

export function liquidityToAmounts(liquidity, tickLower, tickUpper, currentTick) {
  const sqrtPL = safeSqrtPrice(tickLower);
  const sqrtPU = safeSqrtPrice(tickUpper);
  const sqrtPC = safeSqrtPrice(currentTick);
  const L = Number(liquidity);

  let amount0 = 0;
  let amount1 = 0;

  if (currentTick < tickLower) {
    amount0 = L * (1 / sqrtPL - 1 / sqrtPU);
  } else if (currentTick >= tickUpper) {
    amount1 = L * (sqrtPU - sqrtPL);
  } else {
    amount0 = L * (1 / sqrtPC - 1 / sqrtPU);
    amount1 = L * (sqrtPC - sqrtPL);
  }

  return {
    amount0: amount0 / 1e6,
    amount1: amount1 / 1e6,
  };
}

export function computeLiquidity(amount0, amount1, tickLower, tickUpper, currentTick) {
  const sqrtPL = safeSqrtPrice(tickLower);
  const sqrtPU = safeSqrtPrice(tickUpper);
  const sqrtPC = safeSqrtPrice(currentTick);
  const candidates = [];

  if (currentTick < tickLower) {
    if (amount0 > 0) {
      const denom = 1 / sqrtPL - 1 / sqrtPU;
      if (denom > 0) candidates.push(amount0 / denom);
    }
  } else if (currentTick >= tickUpper) {
    if (amount1 > 0) {
      const denom = sqrtPU - sqrtPL;
      if (denom > 0) candidates.push(amount1 / denom);
    }
  } else {
    if (amount0 > 0) {
      const denom = 1 / sqrtPC - 1 / sqrtPU;
      if (denom > 0) candidates.push(amount0 / denom);
    }
    if (amount1 > 0) {
      const denom = sqrtPC - sqrtPL;
      if (denom > 0) candidates.push(amount1 / denom);
    }
  }

  if (candidates.length === 0) return 0n;
  const L = Math.min(...candidates);
  if (!Number.isFinite(L) || L <= 0) return 0n;

  const result = BigInt(Math.floor(L));
  return result > MAX_UINT128 ? MAX_UINT128 : result;
}
