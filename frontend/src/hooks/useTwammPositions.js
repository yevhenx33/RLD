import { useState, useEffect, useCallback, useRef } from "react";
import useSWR from "swr";
import { ethers } from "ethers";
import { SIM_GRAPHQL_URL } from "../api/endpoints";
import { postGraphQL } from "../api/graphqlClient";
import { queryKeys } from "../api/queryKeys";
import { rpcProvider } from "../utils/provider";
import { REFRESH_INTERVALS } from "../config/refreshIntervals";

// ── ABI: only view functions (no event scanning) ──────────────────

const TWAP_ENGINE_VIEW_ABI = [
  "function streamOrders(bytes32 marketId, bytes32 orderId) view returns (address owner, uint256 sellRate, uint256 earningsFactorLast, uint256 startEpoch, uint256 expiration, bool zeroForOne)",
  "function getCancelOrderState(bytes32 marketId, bytes32 orderId) view returns (uint256 buyTokensOwed, uint256 sellTokensRefund)",
];

const BROKER_ACTIVE_ORDER_ABI = [
  "function activeTwammOrder() view returns (bytes32 marketId, bytes32 orderId)",
];

// ── Helpers ───────────────────────────────────────────────────────

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

// ── GraphQL query replaces getLogs scanning ────────────────────────

const TWAMM_ORDERS_QUERY = `
  query TwammPositions($marketId: String!, $owner: String) {
    twammOrders(marketId: $marketId, owner: $owner) {
      orderId owner amountIn nonce
      expiration startEpoch zeroForOne
      blockNumber txHash isCancelled
    }
  }
`;

const gqlFetcher = ([url, , variables]) => {
  return postGraphQL(url, { query: TWAMM_ORDERS_QUERY, variables });
};

/**
 * useTwammPositions — Fetch TWAMM orders for a specific broker.
 *
 * BEFORE: 2 getLogs calls scanning ALL on-chain events + N×2 getOrder RPC
 * AFTER:  1 GraphQL query (filtered by owner in DB) + N×2 order enrichment RPC
 *
 * Event scanning completely eliminated by reading indexed data via GraphQL.
 */
export function useTwammPositions(
  brokerAddress,
  marketInfo,
  pollInterval = 30000,
  oraclePrice,
) {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const mountedRef = useRef(true);
  const initialLoadDone = useRef(false);

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
    queryKeys.twammPositions(SIM_GRAPHQL_URL, marketInfo?.marketId, brokerAddress),
    gqlFetcher,
    {
      refreshInterval: pollInterval,
      revalidateOnFocus: false,
      dedupingInterval: REFRESH_INTERVALS.POSITION_DEDUPE_MS,
      keepPreviousData: true,
    },
  );

  // ── Enrich with on-chain data ───────────────────────────────────
  const enrichOrders = useCallback(async () => {
    if (
      !brokerAddress ||
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
      if (!initialLoadDone.current) setLoading(true);

      const rawOrders = gqlData.twammOrders;
      const activeOrders = rawOrders.filter((o) => !o.isCancelled);

      if (activeOrders.length === 0) {
        if (mountedRef.current) {
          setOrders([]);
          initialLoadDone.current = true;
          setLoaded(true);
        }
        return;
      }

      const provider = rpcProvider;
      const block = await provider.getBlock("latest");
      const now = block.timestamp;

      // Read activeTwammOrder from broker
      let trackedOrderId = null;
      try {
        const broker = new ethers.Contract(
          brokerAddress,
          BROKER_ACTIVE_ORDER_ABI,
          provider,
        );
        const result = await broker.activeTwammOrder();
        trackedOrderId = result[1];
        if (
          trackedOrderId ===
          "0x0000000000000000000000000000000000000000000000000000000000000000"
        )
          trackedOrderId = null;
      } catch {
        // No active order or function doesn't exist
      }

      const markPrice = oraclePrice > 0 ? oraclePrice : 1;

      const twapEngine = new ethers.Contract(twapEngineAddr, TWAP_ENGINE_VIEW_ABI, provider);

      const enrichedOrders = await Promise.all(
        activeOrders.map(async (evt) => {
          let sellRate = 0n;
          let buyTokensOwed = 0n;
          let sellTokensRefund = 0n;
          const orderId = evt.orderId?.startsWith("0x") ? evt.orderId : `0x${evt.orderId}`;

          try {
            const orderResult = await twapEngine.streamOrders(twammMarketId, orderId);
            sellRate = orderResult.sellRate ?? orderResult[1];
          } catch (e) {
            console.warn("[TWAMM] streamOrders failed:", e.message);
          }

          try {
            const cancelState = await twapEngine.getCancelOrderState(twammMarketId, orderId);
            buyTokensOwed = cancelState[0];
            sellTokensRefund = cancelState[1];
          } catch (e) {
            console.warn("[TWAMM] getCancelOrderState failed:", e.message);
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

          // DEFERRED-START FIX: pending orders cannot have earnings.
          // getCancelOrderState may return phantom buyTokensOwed from
          // earningsFactor drift between submit and start epoch.
          if (isPending) {
            buyTokensOwed = 0n;
            sellTokensRefund = BigInt(evt.amountIn);
          }
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

          const earnedUsd = isBuy ? buyTokensRaw * markPrice : buyTokensRaw;
          const remainingUsd = isBuy
            ? sellRefundRaw
            : sellRefundRaw * markPrice;
          const valueUsd = earnedUsd + remainingUsd;

          const sellToken = isBuy ? collateralSymbol : positionSymbol;
          const buyToken = isBuy ? positionSymbol : collateralSymbol;

          const tracked =
            trackedOrderId != null &&
            trackedOrderId.replace(/^0x/, "").toLowerCase() ===
              (evt.orderId || "").replace(/^0x/, "").toLowerCase();

          return {
            orderId: evt.orderId,
            direction,
            isBuy,
            amountIn: amountInTokens,
            sellToken,
            buyToken,
            earned: buyTokensRaw,
            earnedUsd,
            valueUsd,
            remainingUsd,
            sellRefund: sellRefundRaw,
            tokensSpent,
            convertedBuyEstimate: buyTokensRaw,
            progress: isDone && progress < 100 ? 100 : progress,
            timeLeft: isPending
              ? `Starts in ${formatTimeLeft(startTs - now)}`
              : formatTimeLeft(timeLeftSec),
            timeLeftSec,
            tracked,
            isPending,
            isExpired,
            isDone,
            startEpoch: startTs,
            expiration: expTs,
            zeroForOne: evt.zeroForOne,
            txHash: evt.txHash,
          };
        }),
      );

      enrichedOrders.sort((a, b) => {
        if (a.tracked !== b.tracked) return a.tracked ? -1 : 1;
        return a.expiration - b.expiration;
      });

      const visibleOrders = enrichedOrders.filter(
        (o) =>
          !(o.isDone && o.earned === 0 && o.sellRefund === 0 && o.valueUsd === 0),
      );

      if (mountedRef.current) {
        setOrders(visibleOrders);
        initialLoadDone.current = true;
        setLoaded(true);
      }
    } catch (e) {
      console.warn("[TWAMM] enrichOrders failed:", e);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [
    brokerAddress,
    twapEngineAddr,
    twammMarketId,
    collateralAddr,
    positionAddr,
    collateralSymbol,
    positionSymbol,
    buyPositionZeroForOne,
    marketInfo,
    oraclePrice,
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
    loading,
    loaded,
    refresh: () => {
      refreshGql();
      enrichOrders();
    },
  };
}
