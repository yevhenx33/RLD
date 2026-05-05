import { useState, useEffect, useCallback, useRef } from "react";
import useSWR from "swr";
import { ethers } from "ethers";
import { SIM_GRAPHQL_URL } from "../api/endpoints";
import { postGraphQL } from "../api/graphqlClient";
import { queryKeys } from "../api/queryKeys";
import { rpcProvider } from "../utils/provider";
import { REFRESH_INTERVALS } from "../config/refreshIntervals";

// ── ABI: only view functions needed for enrichment (no event scanning) ──

const TWAP_ENGINE_VIEW_ABI = [
  "function streamOrders(bytes32 marketId, bytes32 orderId) view returns (address owner, uint256 sellRate, uint256 earningsFactorLast, uint256 startEpoch, uint256 expiration, bool zeroForOne)",
  "function getCancelOrderState(bytes32 marketId, bytes32 orderId) view returns (uint256 buyTokensOwed, uint256 sellTokensRefund)",
  "function states(bytes32 marketId) view returns (uint256 streamGhostT0, uint256 streamGhostT1, uint256 lastUpdateTime, uint256 lastClearTime, uint256 epochInterval)",
  "function streamPools(bytes32 marketId, bool zeroForOne) view returns (uint256 sellRateCurrent, uint256 earningsFactorCurrent)",
  "function discountRateScaled() view returns (uint256)",
  "function maxDiscountBps() view returns (uint256)",
  "function expirationInterval() view returns (uint256)",
];

// ── Helpers ─────────────────────────────────────────────────────────

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

function resolveBuyPositionZeroForOne(marketInfo) {
  const infrastructure = marketInfo?.infrastructure || {};
  if (typeof marketInfo?.twamm?.buyPositionZeroForOne === "boolean") {
    return marketInfo.twamm.buyPositionZeroForOne;
  }
  if (typeof infrastructure.buyPositionZeroForOne === "boolean") {
    return infrastructure.buyPositionZeroForOne;
  }
  if (typeof marketInfo?.zeroForOneLong === "boolean") {
    return marketInfo.zeroForOneLong;
  }
  if (typeof marketInfo?.zero_for_one_long === "boolean") {
    return marketInfo.zero_for_one_long;
  }
  return false;
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

const gqlFetcher = ([url, , variables]) =>
  postGraphQL(url, { query: TWAMM_QUERY, variables });

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

  const twapEngineAddr =
    marketInfo?.twamm?.engine ||
    marketInfo?.infrastructure?.twapEngine ||
    marketInfo?.infrastructure?.twap_engine;
  const twammMarketId =
    marketInfo?.twamm?.marketId ||
    marketInfo?.infrastructure?.twammMarketId ||
    marketInfo?.infrastructure?.twamm_market_id ||
    marketInfo?.poolId ||
    marketInfo?.pool_id ||
    marketInfo?.marketId;
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr = marketInfo?.positionToken?.address || marketInfo?.position_token?.address;
  const collateralSymbol = marketInfo?.collateral?.symbol || "waUSDC";
  const positionSymbol =
    marketInfo?.positionToken?.symbol ||
    marketInfo?.position_token?.symbol ||
    "wRLP";
  const buyPositionZeroForOne = resolveBuyPositionZeroForOne(marketInfo);

  // ── Fetch base orders via GraphQL (SWR with dedup) ──────────────
  const { data: gqlData, mutate: refreshGql } = useSWR(
    queryKeys.twammDashboard(SIM_GRAPHQL_URL, marketInfo?.marketId),
    gqlFetcher,
    {
      refreshInterval: pollInterval,
      revalidateOnFocus: false,
      dedupingInterval: REFRESH_INTERVALS.POSITION_DEDUPE_MS,
      keepPreviousData: true,
    },
  );

  // ── Enrich orders with on-chain state (stream + per-order) ──────
  const enrichOrders = useCallback(async () => {
    if (
      !twapEngineAddr ||
      !twammMarketId ||
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
      const twapEngine = new ethers.Contract(twapEngineAddr, TWAP_ENGINE_VIEW_ABI, provider);

      let stream = null;
      try {
        const [state, pool0For1, pool1For0] = await Promise.all([
          twapEngine.states(twammMarketId),
          twapEngine.streamPools(twammMarketId, true),
          twapEngine.streamPools(twammMarketId, false),
        ]);
        const lastClear = Number(state[3]);
        stream = {
          accrued0: Number(state[0]),
          accrued1: Number(state[1]),
          discountBps: 0,
          timeSinceClear: lastClear > 0 ? Math.max(0, now - lastClear) : 0,
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
            twapEngine.discountRateScaled(),
            twapEngine.maxDiscountBps(),
            twapEngine.expirationInterval(),
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
          let sellRate = 0n;
          let buyTokensOwed = 0n;
          let sellTokensRefund = 0n;
          const orderId = evt.orderId?.startsWith("0x") ? evt.orderId : `0x${evt.orderId}`;

          try {
            const r = await twapEngine.streamOrders(twammMarketId, orderId);
            sellRate = r.sellRate ?? r[1];
          } catch {
            /* skip */
          }

          try {
            const r = await twapEngine.getCancelOrderState(twammMarketId, orderId);
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

          const isBuy = evt.zeroForOne === buyPositionZeroForOne;
          const direction = isBuy
            ? `${collateralSymbol} → ${positionSymbol}`
            : `${positionSymbol} → ${collateralSymbol}`;

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

          const sellToken = isBuy ? collateralSymbol : positionSymbol;
          const buyToken = isBuy ? positionSymbol : collateralSymbol;

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
  }, [
    twapEngineAddr,
    twammMarketId,
    collateralAddr,
    positionAddr,
    collateralSymbol,
    positionSymbol,
    buyPositionZeroForOne,
    marketInfo,
    gqlData,
  ]);

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
