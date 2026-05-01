import { ethers } from "ethers";

export const HOOKLESS_POOL = ethers.ZeroAddress;

export const POOL_KEY_TUPLE = {
  name: "poolKey",
  type: "tuple",
  components: [
    { name: "currency0", type: "address" },
    { name: "currency1", type: "address" },
    { name: "fee", type: "uint24" },
    { name: "tickSpacing", type: "int24" },
    { name: "hooks", type: "address" },
  ],
};

export const BROKER_ROUTER_ABI = [
  {
    name: "executeLong",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      { name: "amountIn", type: "uint256" },
      POOL_KEY_TUPLE,
      { name: "minAmountOut", type: "uint256" },
    ],
    outputs: [{ name: "amountOut", type: "uint256" }],
  },
  {
    name: "closeLong",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      { name: "amountIn", type: "uint256" },
      POOL_KEY_TUPLE,
      { name: "minAmountOut", type: "uint256" },
    ],
    outputs: [{ name: "amountOut", type: "uint256" }],
  },
  {
    name: "executeShort",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      { name: "initialCollateral", type: "uint256" },
      { name: "targetDebtAmount", type: "uint256" },
      POOL_KEY_TUPLE,
      { name: "minProceeds", type: "uint256" },
    ],
    outputs: [{ name: "proceeds", type: "uint256" }],
  },
  {
    name: "closeShort",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      { name: "collateralToSpend", type: "uint256" },
      POOL_KEY_TUPLE,
      { name: "minDebtBought", type: "uint256" },
    ],
    outputs: [{ name: "debtRepaid", type: "uint256" }],
  },
];

export const BROKER_ROUTER_LONG_ABI = [BROKER_ROUTER_ABI[0]];

export function sortTokenPair(tokenA, tokenB) {
  return tokenA.toLowerCase() < tokenB.toLowerCase()
    ? [tokenA, tokenB]
    : [tokenB, tokenA];
}

export function buildHooklessPoolKey(infrastructure, collateralAddr, positionAddr) {
  if (!collateralAddr || !positionAddr) return null;
  const [currency0, currency1] = sortTokenPair(collateralAddr, positionAddr);
  return {
    currency0,
    currency1,
    fee: infrastructure?.pool_fee || infrastructure?.poolFee || 500,
    tickSpacing: infrastructure?.tick_spacing || infrastructure?.tickSpacing || 5,
    hooks: HOOKLESS_POOL,
  };
}

export function poolKeyToArray(poolKey) {
  return [
    poolKey.currency0,
    poolKey.currency1,
    poolKey.fee,
    poolKey.tickSpacing,
    poolKey.hooks,
  ];
}

export function buildHooklessPoolKeyArray(infrastructure, collateralAddr, positionAddr) {
  const poolKey = buildHooklessPoolKey(infrastructure, collateralAddr, positionAddr);
  return poolKey ? poolKeyToArray(poolKey) : null;
}

export function zeroForOneForDirection(collateralAddr, positionAddr, direction = "BUY") {
  return direction === "SELL"
    ? positionAddr.toLowerCase() < collateralAddr.toLowerCase()
    : collateralAddr.toLowerCase() < positionAddr.toLowerCase();
}

export function buildQuoterExactInputSingleParams(
  infrastructure,
  collateralAddr,
  positionAddr,
  exactAmount,
  direction = "BUY",
) {
  return {
    poolKey: buildHooklessPoolKey(infrastructure, collateralAddr, positionAddr),
    zeroForOne: zeroForOneForDirection(collateralAddr, positionAddr, direction),
    exactAmount,
    hookData: "0x",
  };
}

export function encodeExecuteLongCalldata(
  brokerAddress,
  amountIn,
  poolKey,
  minAmountOut = 0n,
) {
  const iface = new ethers.Interface(BROKER_ROUTER_LONG_ABI);
  return iface.encodeFunctionData("executeLong", [
    brokerAddress,
    amountIn,
    poolKey,
    minAmountOut,
  ]);
}
