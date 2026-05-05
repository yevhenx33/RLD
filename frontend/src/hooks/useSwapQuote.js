import { useCallback, useMemo, useRef, useEffect, useState } from "react";
import { ethers } from "ethers";
import useSWR from "swr";
import { rpcProvider } from "../utils/provider";
import {
  BROKER_ROUTER_ABI,
  HOOKLESS_POOL,
  buildHooklessPoolKey,
  buildQuoterExactInputSingleParams,
} from "../lib/peripheryIntegration";
import { REFRESH_INTERVALS } from "../config/refreshIntervals";

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

const ROUTE_PREVIEW_IFACE = new ethers.Interface(BROKER_ROUTER_ABI);
const NOT_AUTHORIZED_SELECTOR = "0xea8e4eb5";

function extractRevertData(error) {
  const candidates = [
    error?.data,
    error?.error?.data,
    error?.info?.error?.data,
    error?.info?.error?.error?.data,
    error?.revert?.data,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.startsWith("0x")) {
      return value;
    }
  }
  return null;
}

function parseRoutePreviewAmount(error) {
  if (error?.revert?.name === "RoutePreview") {
    return BigInt(error.revert.args?.[0] ?? 0);
  }
  const data = extractRevertData(error);
  if (!data) return null;
  try {
    const parsed = ROUTE_PREVIEW_IFACE.parseError(data);
    if (parsed?.name === "RoutePreview") {
      return BigInt(parsed.args[0]);
    }
  } catch {
    // Not a BrokerRouter preview payload.
  }
  return null;
}

function isRouteAuthorizationError(error) {
  if (error?.revert?.name === "NotAuthorized") return true;
  const data = extractRevertData(error)?.toLowerCase();
  return typeof data === "string" && data.startsWith(NOT_AUTHORIZED_SELECTOR);
}

function routeAuthorizationError() {
  const error = new Error("BrokerRouter approval required");
  error.code = "ROUTE_OPERATOR_APPROVAL_REQUIRED";
  return error;
}

function runtimeBlockReason(infrastructure) {
  if (
    infrastructure?.runtime_ready !== false &&
    infrastructure?.runtimeReady !== false
  ) {
    return null;
  }
  const reasons =
    infrastructure?.runtimeReadiness?.reasons ||
    infrastructure?.runtime_readiness?.reasons ||
    [];
  return reasons.length
    ? `Runtime not ready: ${reasons.join(", ")}`
    : "Runtime manifest is not ready";
}

/**
 * useSwapQuote — Fetch a precise V4 swap quote using the on-chain V4Quoter.
 *
 * @param {object} infrastructure - { v4_quoter, pool_fee, tick_spacing }
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
  options = {},
) {
  const normalizedOptions =
    typeof options === "number" ? { debounceMs: options } : options || {};
  const debounceMs = normalizedOptions.debounceMs ?? 500;
  const route = normalizedOptions.route || {};
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
    const blocked = runtimeBlockReason(infrastructure);
    if (blocked) {
      throw new Error(blocked);
    }

    const routeAction = route.action;
    const routeBroker = route.brokerAddress;
    const routeCaller = route.caller;
    const canRoutePreview =
      routeAction &&
      routeBroker &&
      routeCaller &&
      infrastructure?.broker_router &&
      collateralAddr &&
      positionAddr &&
      debouncedAmount > 0;

    if (
      !canRoutePreview &&
      (
        !infrastructure?.v4_quoter ||
        !collateralAddr ||
        !positionAddr ||
        !debouncedAmount ||
        debouncedAmount <= 0
      )
    ) {
      return null;
    }

    try {
      const provider = rpcProvider;
      const exactAmount = ethers.parseUnits(String(debouncedAmount), 6);

      if (canRoutePreview) {
        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          provider,
        );
        const callOverrides = { from: routeCaller };
        const poolKey = buildHooklessPoolKey(
          infrastructure,
          collateralAddr,
          positionAddr,
        );
        let amountOutRaw;
        const readPreview = async (callPreview) => {
          try {
            await callPreview();
          } catch (error) {
            const parsed = parseRoutePreviewAmount(error);
            if (parsed != null) return parsed;
            if (isRouteAuthorizationError(error)) {
              throw routeAuthorizationError();
            }
            throw error;
          }
          throw new Error("Route preview did not return a preview payload");
        };

        if (routeAction === "OPEN_LONG") {
          amountOutRaw = await readPreview(() =>
            router.previewExecuteLong.staticCall(
              routeBroker,
              exactAmount,
              poolKey,
              callOverrides,
            ),
          );
        } else if (routeAction === "CLOSE_LONG") {
          amountOutRaw = await readPreview(() =>
            router.previewCloseLong.staticCall(
              routeBroker,
              exactAmount,
              poolKey,
              callOverrides,
            ),
          );
        } else if (routeAction === "OPEN_SHORT") {
          const initialCollateral = Number(route.initialCollateral || 0);
          if (!Number.isFinite(initialCollateral) || initialCollateral <= 0) {
            return null;
          }
          const collateralWei = ethers.parseUnits(String(initialCollateral), 6);
          amountOutRaw = await readPreview(() =>
            router.previewExecuteShort.staticCall(
              routeBroker,
              collateralWei,
              exactAmount,
              poolKey,
              callOverrides,
            ),
          );
        } else if (routeAction === "CLOSE_SHORT") {
          amountOutRaw = await readPreview(() =>
            router.previewCloseShort.staticCall(
              routeBroker,
              exactAmount,
              poolKey,
              callOverrides,
            ),
          );
        } else {
          return null;
        }

        const amountOutFormatted = parseFloat(
          ethers.formatUnits(amountOutRaw, 6),
        );
        const rate =
          direction === "SELL"
            ? amountOutFormatted > 0
              ? amountOutFormatted / debouncedAmount
              : 0
            : amountOutFormatted > 0
              ? debouncedAmount / amountOutFormatted
              : 0;
        const notional =
          direction === "SELL" ? amountOutFormatted : debouncedAmount;

        return {
          amountOut: amountOutFormatted,
          entryRate: rate,
          exitRate: rate,
          notional,
          estFee: 0,
          gasEstimate: 0,
          amountOutRaw: amountOutRaw.toString(),
          direction,
          source: "route-preview",
        };
      }

      const quoter = new ethers.Contract(
        infrastructure.v4_quoter,
        QUOTER_ABI,
        provider,
      );

      // Both waUSDC and wRLP have 6 decimals
      const params = buildQuoterExactInputSingleParams(
        infrastructure,
        collateralAddr,
        positionAddr,
        exactAmount,
        direction,
      );

      // V4Quoter.quoteExactInputSingle is NOT a view function —
      // it calls PoolManager.unlock() which reverts internally.
      // Use eth_call (staticCall) to simulate without sending a tx.
      const result = await quoter.quoteExactInputSingle.staticCall(params);

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
        source: "v4-quoter",
      };
    } catch (e) {
      console.warn("Quote failed:", e);
      throw e;
    }
  }, [
    infrastructure,
    collateralAddr,
    positionAddr,
    debouncedAmount,
    direction,
    route.action,
    route.brokerAddress,
    route.caller,
    route.initialCollateral,
  ]);

  const swrKey = useMemo(() => {
    if (
      (!infrastructure?.v4_quoter && !infrastructure?.broker_router) ||
      !collateralAddr ||
      !positionAddr ||
      !(debouncedAmount > 0)
    ) {
      return null;
    }
    return [
      "swap.quote.v1",
      (infrastructure.broker_router || infrastructure.v4_quoter).toLowerCase(),
      collateralAddr.toLowerCase(),
      positionAddr.toLowerCase(),
      Number(debouncedAmount),
      direction,
      route.action || "v4",
      route.brokerAddress?.toLowerCase?.() || "",
      route.caller?.toLowerCase?.() || "",
      Number(route.initialCollateral || 0),
      buildHooklessPoolKey(infrastructure, collateralAddr, positionAddr)?.fee || 500,
      buildHooklessPoolKey(infrastructure, collateralAddr, positionAddr)?.tickSpacing || 5,
      HOOKLESS_POOL,
    ];
  }, [
    infrastructure,
    collateralAddr,
    positionAddr,
    debouncedAmount,
    direction,
    route.action,
    route.brokerAddress,
    route.caller,
    route.initialCollateral,
  ]);

  const {
    data: quote,
    isLoading,
    error: swrError,
    mutate,
  } = useSWR(swrKey, fetchQuote, {
    refreshInterval: REFRESH_INTERVALS.SWAP_QUOTE_MS,
    dedupingInterval: REFRESH_INTERVALS.SWAP_QUOTE_DEDUPE_MS,
    revalidateOnFocus: false,
    keepPreviousData: true,
  });

  const refresh = useCallback(async () => mutate(), [mutate]);

  return {
    quote: quote ?? null,
    loading: isLoading,
    error: swrError?.message || null,
    errorCode: swrError?.code || null,
    refresh,
  };
}
