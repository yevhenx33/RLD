import { useState, useEffect, useCallback, useRef } from "react";
import useSWR from "swr";
import { ethers } from "ethers";
import { ZERO_FOR_ONE_LONG, SIM_API } from "../config/simulationConfig";
import { rpcProvider } from "../utils/provider";
const GQL_URL = `${SIM_API}/graphql`;

// ── ABI: only view functions needed for enrichment (no event scanning) ──

const JTM_VIEW_ABI = [
  "function getOrder((address,address,uint24,int24,address) key, (address,uint160,bool,uint256) orderKey) view returns (uint256 sellRate, uint256 earningsFactorLast)",
  "function getCancelOrderState((address,address,uint24,int24,address) key, (address,uint160,bool,uint256) orderKey) view returns (uint256 buyTokensOwed, uint256 sellTokensRefund)",
  "function getStreamState((address,address,uint24,int24,address) key) view returns (uint256 accrued0, uint256 accrued1, uint256 discountBps, uint256 timeSinceClear)",
  "function getStreamPool((address,address,uint24,int24,address) key, bool zeroForOne) view returns (uint256 sellRate, uint256 earningsFactor)",
  "function discountRateScaled() view returns (uint256)",
  "function maxDiscountBps() view returns (uint256)",
  "function expirationInterval() view returns (uint256)",
];

// ── Helpers ─────────────────────────────────────────────────────────

function buildPoolKey(infrastructure, collateralAddr, positionAddr) {
  const token0 =
    collateralAddr.toLowerCase() < positionAddr.toLowerCase()
      ? collateralAddr
      : positionAddr;
  const token1 =
    collateralAddr.toLowerCase() < positionAddr.toLowerCase()
      ? positionAddr
      : collateralAddr;
  return [
    token0,
    token1,
    infrastructure.pool_fee || 500,
    infrastructure.tick_spacing || 5,
    infrastructure.twamm_hook,
  ];
}

function formatTimeLeft(seconds) {
  if (seconds <= 0) return "Expired";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h >= 24) {
    const d = Math.floor(h / 24);
    const remH = h % 24;
    return `${d}d ${remH}h`;
  }
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

function shortenAddress(addr) {
  if (!addr) return "—";
  return `${addr.substring(0, 6)}…${addr.substring(addr.length - 4)}`;
}

// ── GraphQL query replaces getLogs scanning ─────────────────────────

const TWAMM_QUERY = `
  query TwammDashboard($marketId: String!) {
    twammOrders(marketId: $marketId) {
      orderId owner amountIn nonce
      expiration startEpoch zeroForOne
      blockNumber txHash isCancelled
    }
  }
`;

const gqlFetcher = ([url, query, variables]) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  })
    .then((r) => r.json())
    .then((r) => {
      if (r.errors) console.error("GraphQL Errors:", r.errors);
      return r.data;
    });

// ── Hook ────────────────────────────────────────────────────────────

/**
 * useTwammDashboard — Fetch ALL TWAMM orders + system metrics.
 *
 * BEFORE: 2 getLogs calls + 2 REST calls + N×2 getOrder/getCancelOrder RPC calls
 * AFTER:  1 GraphQL query + 3 stream RPC calls + N×2 order enrichment RPC calls
 *
 * The event scanning (getLogs for Submit/Cancel) is completely eliminated
 * by reading from the indexer DB via GraphQL.
 */
export function useTwammDashboard(marketInfo, pollInterval = 5000) {
  const [orders, setOrders] = useState([]);
  const [streamState, setStreamState] = useState(null);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);
  const mountedRef = useRef(true);
  const configCacheRef = useRef(null); // cache hook config across refreshes

  const hookAddr = marketInfo?.infrastructure?.twamm_hook;
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr = marketInfo?.position_token?.address;

  // ── Fetch base orders via GraphQL (SWR with dedup) ──────────────
  const { data: gqlData, mutate: refreshGql } = useSWR(
    hookAddr && marketInfo?.marketId ? [GQL_URL, TWAMM_QUERY, { marketId: marketInfo.marketId }] : null,
    gqlFetcher,
    {
      refreshInterval: pollInterval,
      revalidateOnFocus: false,
      dedupingInterval: 2000,
      keepPreviousData: true,
    },
  );

  // ── Enrich orders with on-chain state (stream + per-order) ──────
  const enrichOrders = useCallback(async () => {
    if (
      !hookAddr ||
      !collateralAddr ||
      !positionAddr ||
      !marketInfo?.infrastructure ||
      !gqlData?.twammOrders
    ) {
      return;
    }

    try {
      const rawOrders = gqlData.twammOrders;
      const activeOrders = rawOrders.filter((o) => !o.isCancelled);

      const provider = rpcProvider;
      const block = await provider.getBlock("latest");
      const now = block.timestamp;

      // Get pool price from the current pool state (from useSim context)
      // The pool.markPrice is passed via marketInfo, or we compute from sqrtPriceX96
      let poolPrice = 1;
      let markPrice = marketInfo?.mark_price || 1;

      // Pool price from pool state if available
      if (marketInfo?.pool_mark_price) {
        poolPrice = marketInfo.pool_mark_price;
      }

      // Fetch stream state (3 RPC calls — down from 2 getLogs + N×2 event parsing)
      const poolKey = buildPoolKey(
        marketInfo.infrastructure,
        collateralAddr,
        positionAddr,
      );
      const hook = new ethers.Contract(hookAddr, JTM_VIEW_ABI, provider);

      let stream = null;
      try {
        const [state, pool0For1, pool1For0] = await Promise.all([
          hook.getStreamState(poolKey),
          hook.getStreamPool(poolKey, true),
          hook.getStreamPool(poolKey, false),
        ]);
        stream = {
          accrued0: Number(state[0]),
          accrued1: Number(state[1]),
          discountBps: Number(state[2]),
          timeSinceClear: Number(state[3]),
          sellRate0For1: Number(pool0For1[0]),
          sellRate1For0: Number(pool1For0[0]),
          earningsFactor0For1: pool0For1[1],
          earningsFactor1For0: pool1For0[1],
        };
      } catch (e) {
        console.warn("[TWAMM Dashboard] getStreamState failed:", e.message);
      }

      // Fetch hook config (cached — only once per mount)
      let hookConfig = configCacheRef.current;
      if (!hookConfig) {
        try {
          const [discountRate, maxDiscount, interval] = await Promise.all([
            hook.discountRateScaled(),
            hook.maxDiscountBps(),
            hook.expirationInterval(),
          ]);
          hookConfig = {
            discountRateScaled: Number(discountRate),
            maxDiscountBps: Number(maxDiscount),
            expirationInterval: Number(interval),
          };
          configCacheRef.current = hookConfig;
        } catch (e) {
          console.warn("[TWAMM Dashboard] config fetch failed:", e.message);
        }
      }

      // Enrich each order with value preservation analysis
      const enriched = await Promise.all(
        activeOrders.map(async (evt) => {
          const orderKeyTuple = [
            evt.owner,
            parseInt(evt.expiration),
            evt.zeroForOne,
            parseInt(evt.nonce || "0"),
          ];

          let sellRate = 0n;
          let buyTokensOwed = 0n;
          let sellTokensRefund = 0n;

          try {
            const r = await hook.getOrder(poolKey, orderKeyTuple);
            sellRate = r[0];
          } catch {
            /* skip */
          }

          try {
            const r = await hook.getCancelOrderState(poolKey, orderKeyTuple);
            buyTokensOwed = r[0];
            sellTokensRefund = r[1];
          } catch {
            /* skip */
          }

          const amountInNum = Number(BigInt(evt.amountIn));
          const refundNum = Number(sellTokensRefund);
          const startTs = Number(evt.startEpoch || 0);
          const expTs = Number(evt.expiration);
          const totalDuration = expTs - startTs;
          const elapsed = Math.max(0, now - startTs);
          const progress =
            totalDuration > 0
              ? Math.min(100, Math.round((elapsed / totalDuration) * 100))
              : 0;

          const isPending = now < startTs;
          const timeLeftSec = Math.max(0, expTs - now);
          const isExpired = timeLeftSec === 0;
          const isDone = isExpired || sellRate === 0n;

          const isBuy = evt.zeroForOne === ZERO_FOR_ONE_LONG;
          const direction = isBuy ? "waUSDC → wRLP" : "wRLP → waUSDC";

          const buyTokensRaw = Number(buyTokensOwed) / 1e6;
          const sellRefundRaw = refundNum / 1e6;
          const amountInTokens = amountInNum / 1e6;
          const tokensSpent = amountInTokens - sellRefundRaw;

          // USD values
          const earnedUsd = isBuy ? buyTokensRaw * poolPrice : buyTokensRaw;
          const remainingUsd = isBuy
            ? sellRefundRaw
            : sellRefundRaw * poolPrice;
          const depositUsd = isBuy
            ? amountInTokens
            : amountInTokens * poolPrice;
          const spentUsd = isBuy ? tokensSpent : tokensSpent * poolPrice;

          // Ghost attribution
          let ghostShare = 0;
          let ghostShareUsd = 0;
          const orderSellRate = Number(sellRate);
          if (stream && orderSellRate > 0) {
            if (evt.zeroForOne) {
              const totalSR = stream.sellRate0For1;
              if (totalSR > 0) {
                ghostShare =
                  (stream.accrued1 / 1e6) * (orderSellRate / totalSR);
                ghostShareUsd = ghostShare;
              }
            } else {
              const totalSR = stream.sellRate1For0;
              if (totalSR > 0) {
                ghostShare =
                  (stream.accrued0 / 1e6) * (orderSellRate / totalSR);
                ghostShareUsd = ghostShare * poolPrice;
              }
            }
          }

          const discountBps = stream?.discountBps || 0;
          const discountedGhostUsd =
            ghostShareUsd * (1 - discountBps / 10000);
          const discountCostUsd = ghostShareUsd - discountedGhostUsd;

          const idealOutputTokens = isBuy
            ? poolPrice > 0
              ? tokensSpent / poolPrice
              : 0
            : tokensSpent * poolPrice;
          const idealOutputUsd = isBuy
            ? idealOutputTokens * poolPrice
            : idealOutputTokens;

          const totalValueUsd = earnedUsd + discountedGhostUsd + remainingUsd;
          const actualOutputUsd = earnedUsd + discountedGhostUsd;
          const preservation =
            spentUsd > 0 ? (actualOutputUsd / spentUsd) * 100 : 0;

          const effectivePrice = isBuy
            ? buyTokensRaw > 0
              ? tokensSpent / buyTokensRaw
              : 0
            : tokensSpent > 0
              ? buyTokensRaw / tokensSpent
              : 0;
          const priceImpactBps =
            poolPrice > 0
              ? Math.round(
                  (Math.abs(effectivePrice - poolPrice) / poolPrice) * 10000,
                )
              : 0;

          const sellToken = isBuy ? "waUSDC" : "wRLP";
          const buyToken = isBuy ? "wRLP" : "waUSDC";

          return {
            orderId: evt.orderId,
            owner: evt.owner,
            ownerShort: shortenAddress(evt.owner),
            direction,
            isBuy,
            amountIn: amountInTokens,
            sellToken,
            buyToken,
            earned: buyTokensRaw,
            earnedUsd,
            valueUsd: totalValueUsd,
            depositUsd,
            remainingUsd,
            spentUsd,
            sellRefund: sellRefundRaw,
            tokensSpent,
            progress: isDone && progress < 100 ? 100 : progress,
            timeLeft: isPending
              ? `Starts in ${formatTimeLeft(startTs - now)}`
              : formatTimeLeft(timeLeftSec),
            timeLeftSec,
            isPending,
            isExpired,
            isDone,
            startEpoch: startTs,
            expiration: expTs,
            zeroForOne: evt.zeroForOne,
            txHash: evt.txHash,
            markPrice,
            poolPrice,
            ghostShare,
            ghostShareUsd,
            discountedGhostUsd,
            discountCostUsd,
            idealOutputTokens,
            idealOutputUsd,
            actualOutputUsd,
            preservation,
            effectivePrice,
            priceImpactBps,
            orderSellRate,
          };
        }),
      );

      // Sort: active first (by time remaining asc), then expired
      enriched.sort((a, b) => {
        if (a.isDone !== b.isDone) return a.isDone ? 1 : -1;
        if (!a.isDone && !b.isDone) return a.timeLeftSec - b.timeLeftSec;
        return b.expiration - a.expiration;
      });

      if (mountedRef.current) {
        setOrders(enriched);
        setStreamState(stream);
        setConfig(hookConfig);
        setLastRefresh(new Date());
        setLoading(false);
      }
    } catch (e) {
      console.warn("[TWAMM Dashboard] enrich failed:", e);
      if (mountedRef.current) setLoading(false);
    }
  }, [hookAddr, collateralAddr, positionAddr, marketInfo, gqlData]);

  // Re-enrich whenever GraphQL data updates
  useEffect(() => {
    if (gqlData?.twammOrders) {
      enrichOrders();
    }
  }, [gqlData, enrichOrders]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  return {
    orders,
    streamState,
    config,
    loading,
    lastRefresh,
    refresh: () => {
      refreshGql();
      enrichOrders();
    },
  };
}
