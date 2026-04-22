import { useCallback, useMemo, useRef, useEffect, useState } from "react";
import { ethers } from "ethers";
import useSWR from "swr";
import { rpcProvider } from "../utils/provider";

// Mainnet V4 Quoter ABI — quoteExactInputSingle(QuoteExactSingleParams)
// Uses struct-wrapped params matching the deployed Quoter at 0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203
const QUOTER_ABI = [
  {
    name: "quoteExactInputSingle",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      {
        name: "params",
        type: "tuple",
        components: [
          {
            name: "poolKey",
            type: "tuple",
            components: [
              { name: "currency0", type: "address" },
              { name: "currency1", type: "address" },
              { name: "fee", type: "uint24" },
              { name: "tickSpacing", type: "int24" },
              { name: "hooks", type: "address" },
            ],
          },
          { name: "zeroForOne", type: "bool" },
          { name: "exactAmount", type: "uint128" },
          { name: "hookData", type: "bytes" },
        ],
      },
    ],
    outputs: [
      { name: "amountOut", type: "uint256" },
      { name: "gasEstimate", type: "uint256" },
    ],
  },
];

/**
 * useSwapQuote — Fetch a precise V4 swap quote using the on-chain V4Quoter.
 *
 * @param {object} infrastructure - { v4_quoter, twamm_hook, pool_fee, tick_spacing }
 * @param {string} collateralAddr - waUSDC address
 * @param {string} positionAddr   - wRLP address
 * @param {number} amountIn       - Amount to swap (human units, 6 decimals)
 * @param {string} direction      - 'BUY' (waUSDC→wRLP, open long) or 'SELL' (wRLP→waUSDC, close long)
 * @param {number} debounceMs     - Debounce interval (default: 500ms)
 */
export function useSwapQuote(
  infrastructure,
  collateralAddr,
  positionAddr,
  amountIn,
  direction = "BUY",
  debounceMs = 500,
) {
  const debounceRef = useRef(null);
  const [debouncedAmount, setDebouncedAmount] = useState(amountIn);

  useEffect(() => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => setDebouncedAmount(amountIn), debounceMs);
    return () => {
      clearTimeout(debounceRef.current);
    };
  }, [amountIn, debounceMs]);

  const fetchQuote = useCallback(async () => {
    if (
      !infrastructure?.v4_quoter ||
      !collateralAddr ||
      !positionAddr ||
      !debouncedAmount ||
      debouncedAmount <= 0
    ) {
      return null;
    }

    try {
      const provider = rpcProvider;
      const quoter = new ethers.Contract(
        infrastructure.v4_quoter,
        QUOTER_ABI,
        provider,
      );

      // Build pool key — token0 must be < token1 by address
      const token0 =
        collateralAddr.toLowerCase() < positionAddr.toLowerCase()
          ? collateralAddr
          : positionAddr;
      const token1 =
        collateralAddr.toLowerCase() < positionAddr.toLowerCase()
          ? positionAddr
          : collateralAddr;

      const poolKey = {
        currency0: token0,
        currency1: token1,
        fee: infrastructure.pool_fee || 500,
        tickSpacing: infrastructure.tick_spacing || 5,
        hooks: infrastructure.twamm_hook,
      };

      // Determine swap direction
      // BUY  (open long):  sell collateral for position → zeroForOne = collateral < position
      // SELL (close long): sell position for collateral → zeroForOne = position < collateral
      const zeroForOne =
        direction === "SELL"
          ? positionAddr.toLowerCase() < collateralAddr.toLowerCase()
          : collateralAddr.toLowerCase() < positionAddr.toLowerCase();

      // Both waUSDC and wRLP have 6 decimals
      const exactAmount = ethers.parseUnits(String(debouncedAmount), 6);

      // V4Quoter.quoteExactInputSingle is NOT a view function —
      // it calls PoolManager.unlock() which reverts internally.
      // Use eth_call (staticCall) to simulate without sending a tx.
      const result = await quoter.quoteExactInputSingle.staticCall({
        poolKey,
        zeroForOne,
        exactAmount,
        hookData: "0x",
      });

      const amountOutRaw = result[0]; // BigInt
      const gasEstimateRaw = result[1]; // BigInt

      // Both tokens have 6 decimals
      const amountOutFormatted = parseFloat(
        ethers.formatUnits(amountOutRaw, 6),
      );

      // Trading fee: pool_fee / 1e6 (e.g., 500 = 0.05%)
      const poolFeeRate = (infrastructure.pool_fee || 500) / 1e6;
      const tradingFee = debouncedAmount * poolFeeRate;

      // Rate: price per wRLP in waUSDC terms
      // BUY:  entryRate = waUSDC_in / wRLP_out
      // SELL: exitRate  = waUSDC_out / wRLP_in
      const rate =
        direction === "SELL"
          ? amountOutFormatted > 0
            ? amountOutFormatted / debouncedAmount
            : 0
          : amountOutFormatted > 0
            ? debouncedAmount / amountOutFormatted
            : 0;
      const notional = direction === "SELL" ? amountOutFormatted : debouncedAmount;

      return {
        amountOut: amountOutFormatted,
        entryRate: rate,
        exitRate: rate,
        notional,
        estFee: tradingFee,
        gasEstimate: Number(gasEstimateRaw),
        amountOutRaw: amountOutRaw.toString(),
        direction,
      };
    } catch (e) {
      console.warn("Quote failed:", e);
      throw e;
    }
  }, [infrastructure, collateralAddr, positionAddr, debouncedAmount, direction]);

  const swrKey = useMemo(() => {
    if (
      !infrastructure?.v4_quoter ||
      !collateralAddr ||
      !positionAddr ||
      !(debouncedAmount > 0)
    ) {
      return null;
    }
    return [
      "swap.quote.v1",
      infrastructure.v4_quoter.toLowerCase(),
      collateralAddr.toLowerCase(),
      positionAddr.toLowerCase(),
      Number(debouncedAmount),
      direction,
      infrastructure.pool_fee || 500,
      infrastructure.tick_spacing || 5,
      infrastructure.twamm_hook?.toLowerCase() || "",
    ];
  }, [infrastructure, collateralAddr, positionAddr, debouncedAmount, direction]);

  const {
    data: quote,
    isLoading,
    error: swrError,
    mutate,
  } = useSWR(swrKey, fetchQuote, {
    refreshInterval: 12000,
    dedupingInterval: 400,
    revalidateOnFocus: false,
    keepPreviousData: true,
  });

  const refresh = useCallback(async () => mutate(), [mutate]);

  return {
    quote: quote ?? null,
    loading: isLoading,
    error: swrError?.message || null,
    refresh,
  };
}
