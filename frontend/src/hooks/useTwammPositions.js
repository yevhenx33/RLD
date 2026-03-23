import { useState, useEffect, useCallback, useRef } from "react";
import useSWR from "swr";
import { ethers } from "ethers";
import { ZERO_FOR_ONE_LONG, SIM_API } from "../config/simulationConfig";
import { rpcProvider } from "../utils/provider";
const GQL_URL = `${SIM_API}/graphql`;

// ── ABI: only view functions (no event scanning) ──────────────────

const JTM_VIEW_ABI = [
  "function getOrder((address,address,uint24,int24,address) key, (address,uint160,bool,uint256) orderKey) view returns (uint256 sellRate, uint256 earningsFactorLast)",
  "function getCancelOrderState((address,address,uint24,int24,address) key, (address,uint160,bool,uint256) orderKey) view returns (uint256 buyTokensOwed, uint256 sellTokensRefund)",
];

const BROKER_ACTIVE_ORDER_ABI = [
  // Solidity auto-getter flattens TwammOrderInfo { PoolKey key; OrderKey orderKey; bytes32 orderId }
  // PoolKey = (address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks)
  // OrderKey = (address owner, uint160 expiration, bool zeroForOne, uint256 nonce)
  "function activeTwammOrder() view returns (address, address, uint24, int24, address, address, uint160, bool, uint256, bytes32)",
];

// ── Helpers ───────────────────────────────────────────────────────

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

const gqlFetcher = ([url, query, variables]) => {
  const body = { query };
  if (variables) body.variables = variables;
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
    .then((r) => {
      if (!r.ok) throw new Error(`GraphQL HTTP ${r.status}`);
      return r.json();
    })
    .then((r) => r.data);
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

  const hookAddr = marketInfo?.infrastructure?.twamm_hook;
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr = marketInfo?.position_token?.address;

  // ── Fetch base orders via GraphQL (SWR with dedup) ──────────────
  const { data: gqlData, mutate: refreshGql } = useSWR(
    brokerAddress && hookAddr && marketInfo?.marketId
      ? [GQL_URL, TWAMM_ORDERS_QUERY, { marketId: marketInfo.marketId, owner: brokerAddress }]
      : null,
    gqlFetcher,
    {
      refreshInterval: pollInterval,
      revalidateOnFocus: false,
      dedupingInterval: 2000,
      keepPreviousData: true,
    },
  );

  // ── Enrich with on-chain data ───────────────────────────────────
  const enrichOrders = useCallback(async () => {
    if (
      !brokerAddress ||
      !hookAddr ||
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
        trackedOrderId = result[9]; // orderId is the 10th return value (index 9)
        if (
          trackedOrderId ===
          "0x0000000000000000000000000000000000000000000000000000000000000000"
        )
          trackedOrderId = null;
      } catch {
        // No active order or function doesn't exist
      }

      const markPrice = oraclePrice > 0 ? oraclePrice : 1;

      const poolKey = buildPoolKey(
        marketInfo.infrastructure,
        collateralAddr,
        positionAddr,
      );
      const hook = new ethers.Contract(hookAddr, JTM_VIEW_ABI, provider);

      const enrichedOrders = await Promise.all(
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
            const orderResult = await hook.getOrder(poolKey, orderKeyTuple);
            sellRate = orderResult[0];
          } catch (e) {
            console.warn("[TWAMM] getOrder failed:", e.message);
          }

          try {
            const cancelState = await hook.getCancelOrderState(
              poolKey,
              orderKeyTuple,
            );
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

          const isBuy = evt.zeroForOne === ZERO_FOR_ONE_LONG;
          const direction = isBuy ? "waUSDC → wRLP" : "wRLP → waUSDC";

          const buyTokensRaw = Number(buyTokensOwed) / 1e6;
          const sellRefundRaw = refundNum / 1e6;
          const amountInTokens = amountInNum / 1e6;
          const tokensSpent = amountInTokens - sellRefundRaw;

          const earnedUsd = isBuy ? buyTokensRaw * markPrice : buyTokensRaw;
          const remainingUsd = isBuy
            ? sellRefundRaw
            : sellRefundRaw * markPrice;
          const valueUsd = earnedUsd + remainingUsd;

          const sellToken = isBuy ? "waUSDC" : "wRLP";
          const buyToken = isBuy ? "wRLP" : "waUSDC";

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
  }, [brokerAddress, hookAddr, collateralAddr, positionAddr, marketInfo, oraclePrice, gqlData]);

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
