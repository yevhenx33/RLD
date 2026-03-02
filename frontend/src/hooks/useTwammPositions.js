import { useState, useEffect, useCallback, useRef } from "react";
import { ethers } from "ethers";
import { ZERO_FOR_ONE_LONG } from "../config/simulationConfig";

const RPC_URL = `${window.location.origin}/rpc`;

// ── ABI fragments ────────────────────────────────────────────────

const JTM_EVENTS_ABI = [
  "event SubmitOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner, uint256 amountIn, uint160 expiration, bool zeroForOne, uint256 sellRate, uint256 earningsFactorLast, uint256 startEpoch)",
  "event CancelOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner, uint256 sellTokensRefund)",
];

const JTM_VIEW_ABI = [
  "function getOrder((address,address,uint24,int24,address) key, (address,uint160,bool) orderKey) view returns (uint256 sellRate, uint256 earningsFactorLast)",
  "function getCancelOrderState((address,address,uint24,int24,address) key, (address,uint160,bool) orderKey) view returns (uint256 buyTokensOwed, uint256 sellTokensRefund)",
];

const BROKER_ACTIVE_ORDER_ABI = [
  "function activeTwammOrder() view returns ((address,address,uint24,int24,address) key, (address,uint160,bool) orderKey, bytes32 orderId)",
];

const IFACE = new ethers.Interface(JTM_EVENTS_ABI);
const SUBMIT_TOPIC = IFACE.getEvent("SubmitOrder").topicHash;

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
  if (seconds <= 0) return "Done";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h >= 24) {
    const d = Math.floor(h / 24);
    const remH = h % 24;
    return `${d}d ${remH}h`;
  }
  return `${h}h ${String(m).padStart(2, "0")}m`;
}

// ── Hook ──────────────────────────────────────────────────────────

/**
 * useTwammPositions — Fetch all TWAMM orders for a broker from on-chain events.
 *
 * Returns an array of enriched order objects with real-time progress, values, and status.
 *
 * @param {string} brokerAddress PrimeBroker contract address
 * @param {object} marketInfo    From useSimulation (has infrastructure, collateral, position_token)
 * @param {number} pollInterval  Refresh interval in ms (default 30s)
 */
export function useTwammPositions(
  brokerAddress,
  marketInfo,
  pollInterval = 30000,
) {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(false);
  const mountedRef = useRef(true);

  const hookAddr = marketInfo?.infrastructure?.twamm_hook;
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr = marketInfo?.position_token?.address;

  const fetchOrders = useCallback(async () => {
    if (!brokerAddress || !hookAddr || !collateralAddr || !positionAddr) {
      setOrders([]);
      return;
    }

    try {
      setLoading(true);
      const provider = new ethers.JsonRpcProvider(RPC_URL);

      // 1. Scan SubmitOrder events from JTM hook
      const logs = await provider.getLogs({
        address: hookAddr,
        fromBlock: 0,
        toBlock: "latest",
        topics: [SUBMIT_TOPIC],
      });

      // Parse and filter by broker as owner
      const submitEvents = [];
      for (const log of logs) {
        try {
          const parsed = IFACE.parseLog({
            topics: log.topics,
            data: log.data,
          });
          if (!parsed) continue;
          // Owner check (case-insensitive)
          if (
            parsed.args.owner.toLowerCase() !== brokerAddress.toLowerCase()
          )
            continue;

          submitEvents.push({
            orderId: log.topics[2], // indexed orderId
            owner: parsed.args.owner,
            amountIn: parsed.args.amountIn,
            expiration: parsed.args.expiration,
            zeroForOne: parsed.args.zeroForOne,
            sellRate: parsed.args.sellRate,
            startEpoch: parsed.args.startEpoch,
            blockNumber: log.blockNumber,
            txHash: log.transactionHash,
          });
        } catch {
          // skip unparsable
        }
      }

      if (submitEvents.length === 0) {
        if (mountedRef.current) setOrders([]);
        return;
      }

      // Check for CancelOrder events to filter out cancelled orders
      const cancelTopic = IFACE.getEvent("CancelOrder").topicHash;
      const cancelLogs = await provider.getLogs({
        address: hookAddr,
        fromBlock: 0,
        toBlock: "latest",
        topics: [cancelTopic],
      });

      const cancelledOrderIds = new Set();
      for (const log of cancelLogs) {
        try {
          const parsed = IFACE.parseLog({
            topics: log.topics,
            data: log.data,
          });
          if (
            parsed &&
            parsed.args.owner.toLowerCase() === brokerAddress.toLowerCase()
          ) {
            cancelledOrderIds.add(log.topics[2]);
          }
        } catch {
          // skip
        }
      }

      // Filter out cancelled orders
      const activeSubmits = submitEvents.filter(
        (e) => !cancelledOrderIds.has(e.orderId),
      );

      // 2. Get current block timestamp
      const block = await provider.getBlock("latest");
      const now = block.timestamp;

      // 3. Read activeTwammOrder from broker
      let trackedOrderId = null;
      try {
        const broker = new ethers.Contract(
          brokerAddress,
          BROKER_ACTIVE_ORDER_ABI,
          provider,
        );
        const result = await broker.activeTwammOrder();
        trackedOrderId = result[2]; // orderId (bytes32)
        if (
          trackedOrderId ===
          "0x0000000000000000000000000000000000000000000000000000000000000000"
        )
          trackedOrderId = null;
      } catch {
        // No active order or function doesn't exist
      }

      // 4. Fetch current mark price for token value conversion
      let markPrice = 1; // fallback: 1:1
      try {
        const priceRes = await fetch(`${window.location.origin}/api/market-info`);
        if (priceRes.ok) {
          const priceData = await priceRes.json();
          markPrice = parseFloat(priceData.mark_price || priceData.index_price || "1");
        }
      } catch {
        console.warn("[TWAMM] Failed to fetch mark price");
      }

      // 5. For each order, fetch current state
      const poolKey = buildPoolKey(
        marketInfo.infrastructure,
        collateralAddr,
        positionAddr,
      );
      const hook = new ethers.Contract(hookAddr, JTM_VIEW_ABI, provider);

      const enrichedOrders = await Promise.all(
        activeSubmits.map(async (evt) => {
          const orderKeyTuple = [
            evt.owner,
            evt.expiration,
            evt.zeroForOne,
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

          // Compute progress based on actual active period [startEpoch, expiration]
          const amountInNum = Number(evt.amountIn);
          const refundNum = Number(sellTokensRefund);
          const startTs = Number(evt.startEpoch || 0);
          const expTs = Number(evt.expiration);
          const totalDuration = expTs - startTs;
          const elapsed = Math.max(0, now - startTs);
          const progress =
            totalDuration > 0
              ? Math.min(100, Math.round((elapsed / totalDuration) * 100))
              : 0;

          // Status: Pending (before startEpoch), Active, Done
          const isPending = now < startTs;
          const timeLeftSec = Math.max(0, expTs - now);
          const isExpired = timeLeftSec === 0;
          const isDone = isExpired || sellRate === 0n;

          // Direction
          // zeroForOne=true: selling token0. Check if that's collateral or position
          const isBuy = evt.zeroForOne === ZERO_FOR_ONE_LONG;
          const direction = isBuy ? "waUSDC → wRLP" : "wRLP → waUSDC";

          // Token amounts (raw ÷ 1e6)
          const buyTokensRaw = Number(buyTokensOwed) / 1e6;
          const sellRefundRaw = refundNum / 1e6;
          const amountInTokens = Number(evt.amountIn) / 1e6;

          // Value in USD — total order value = earned + ghost (uncollected) + remaining
          // BUY order (selling waUSDC, getting wRLP):
          //   earned = wRLP × price, remaining = waUSDC (already USD)
          //   ghost = waUSDC sold but not yet cleared → estimate at face value
          // SELL order (selling wRLP, getting waUSDC):
          //   earned = waUSDC (already USD), remaining = wRLP × price
          //   ghost = wRLP sold but not yet cleared → estimate at mark price
          const tokensSpent = amountInTokens - sellRefundRaw;
          const earnedUsd = isBuy
            ? buyTokensRaw * markPrice
            : buyTokensRaw;
          const remainingUsd = isBuy
            ? sellRefundRaw                // waUSDC remaining
            : sellRefundRaw * markPrice;   // wRLP remaining × price
          // Order value = what you'd actually receive if you cancelled now
          // earnedUsd = buyTokensOwed × price (real on-chain cleared value)
          // remainingUsd = refundable sell tokens
          const valueUsd = earnedUsd + remainingUsd;

          // Sell/buy token labels
          const sellToken = isBuy ? "waUSDC" : "wRLP";
          const buyToken = isBuy ? "wRLP" : "waUSDC";

          // Is this the tracked order?
          const tracked =
            trackedOrderId != null && trackedOrderId === evt.orderId;

          // Actual buy-side output from getCancelOrderState (what you'd receive on cancel)
          // This is the real on-chain value, not an estimate
          const convertedBuyEstimate = buyTokensRaw;

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
            convertedBuyEstimate,
            progress: isDone && progress < 100 ? 100 : progress,
            timeLeft: isPending ? `Starts in ${formatTimeLeft(startTs - now)}` : formatTimeLeft(timeLeftSec),
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

      // Sort: tracked first, then by expiration (soonest first)
      enrichedOrders.sort((a, b) => {
        if (a.tracked !== b.tracked) return a.tracked ? -1 : 1;
        return a.expiration - b.expiration;
      });

      if (mountedRef.current) {
        setOrders(enrichedOrders);
      }
    } catch (e) {
      console.warn("[TWAMM] fetchOrders failed:", e);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [brokerAddress, hookAddr, collateralAddr, positionAddr, marketInfo]);

  useEffect(() => {
    mountedRef.current = true;
    fetchOrders();
    const interval = setInterval(fetchOrders, pollInterval);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [fetchOrders, pollInterval]);

  return { orders, loading, refresh: fetchOrders };
}
