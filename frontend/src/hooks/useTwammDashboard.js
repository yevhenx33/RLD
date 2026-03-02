import { useState, useEffect, useCallback, useRef } from "react";
import { ethers } from "ethers";
import { ZERO_FOR_ONE_LONG } from "../config/simulationConfig";

const RPC_URL = `${window.location.origin}/rpc`;

// ── ABI fragments ──────────────────────────────────────────────────

const JTM_EVENTS_ABI = [
  "event SubmitOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner, uint256 amountIn, uint160 expiration, bool zeroForOne, uint256 sellRate, uint256 earningsFactorLast, uint256 startEpoch)",
  "event CancelOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner, uint256 sellTokensRefund)",
];

const JTM_VIEW_ABI = [
  "function getOrder((address,address,uint24,int24,address) key, (address,uint160,bool) orderKey) view returns (uint256 sellRate, uint256 earningsFactorLast)",
  "function getCancelOrderState((address,address,uint24,int24,address) key, (address,uint160,bool) orderKey) view returns (uint256 buyTokensOwed, uint256 sellTokensRefund)",
  "function getStreamState((address,address,uint24,int24,address) key) view returns (uint256 accrued0, uint256 accrued1, uint256 discountBps, uint256 timeSinceClear)",
  "function getStreamPool((address,address,uint24,int24,address) key, bool zeroForOne) view returns (uint256 sellRate, uint256 earningsFactor)",
  "function discountRateScaled() view returns (uint256)",
  "function maxDiscountBps() view returns (uint256)",
  "function expirationInterval() view returns (uint256)",
];

const STATE_VIEW_ABI = [
  "function getSlot0(bytes32 poolId) view returns (uint160 sqrtPriceX96, int24 tick, uint16 protocolFee, uint24 lpFee)",
];

const IFACE = new ethers.Interface(JTM_EVENTS_ABI);
const SUBMIT_TOPIC = IFACE.getEvent("SubmitOrder").topicHash;
const CANCEL_TOPIC = IFACE.getEvent("CancelOrder").topicHash;

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

// ── Hook ────────────────────────────────────────────────────────────

/**
 * useTwammDashboard — Fetch ALL TWAMM orders + system metrics.
 *
 * Unlike useTwammPositions (which filters by broker), this fetches
 * every order from event logs for a global dashboard view.
 *
 * @param {object} marketInfo  From useSimulation
 * @param {number} pollInterval  Refresh interval in ms (default 5s)
 */
export function useTwammDashboard(marketInfo, pollInterval = 5000) {
  const [orders, setOrders] = useState([]);
  const [streamState, setStreamState] = useState(null);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);
  const mountedRef = useRef(true);

  const hookAddr = marketInfo?.infrastructure?.twamm_hook;
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr = marketInfo?.position_token?.address;

  const fetchDashboard = useCallback(async () => {
    if (
      !hookAddr ||
      !collateralAddr ||
      !positionAddr ||
      !marketInfo?.infrastructure
    ) {
      return;
    }

    try {
      const provider = new ethers.JsonRpcProvider(RPC_URL);

      // 1. Scan ALL SubmitOrder events
      const submitLogs = await provider.getLogs({
        address: hookAddr,
        fromBlock: 0,
        toBlock: "latest",
        topics: [SUBMIT_TOPIC],
      });

      const submitEvents = [];
      for (const log of submitLogs) {
        try {
          const parsed = IFACE.parseLog({ topics: log.topics, data: log.data });
          if (!parsed) continue;
          submitEvents.push({
            orderId: log.topics[2],
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
          /* skip */
        }
      }

      // 2. Scan CancelOrder events
      const cancelLogs = await provider.getLogs({
        address: hookAddr,
        fromBlock: 0,
        toBlock: "latest",
        topics: [CANCEL_TOPIC],
      });

      const cancelledIds = new Set();
      for (const log of cancelLogs) {
        try {
          cancelledIds.add(log.topics[2]);
        } catch {
          /* skip */
        }
      }

      const activeSubmits = submitEvents.filter(
        (e) => !cancelledIds.has(e.orderId),
      );

      // 3. Current block timestamp
      const block = await provider.getBlock("latest");
      const now = block.timestamp;

      // 4. Fetch pool price from sqrtPriceX96 (actual AMM exchange rate)
      // NOTE: mark_price from /api/market-info is the INDEX price of the underlying,
      //       NOT the trading price of wRLP in the pool. They are very different.
      let poolPrice = 1; // waUSDC per wRLP (the actual AMM rate)
      let markPrice = 1; // kept for reference display only
      try {
        const res = await fetch(`${window.location.origin}/api/market-info`);
        if (res.ok) {
          const d = await res.json();
          markPrice = parseFloat(d.mark_price || d.index_price || "1");
        }
      } catch {
        /* fallback */
      }

      // Get sqrtPriceX96 from pool slot0 via StateView
      try {
        const stateViewAddr = marketInfo.infrastructure.v4_state_view;
        if (stateViewAddr) {
          const sv = new ethers.Contract(
            stateViewAddr,
            STATE_VIEW_ABI,
            provider,
          );
          const poolKeyArr = buildPoolKey(
            marketInfo.infrastructure,
            collateralAddr,
            positionAddr,
          );
          const poolId = ethers.keccak256(
            ethers.AbiCoder.defaultAbiCoder().encode(
              ["address", "address", "uint24", "int24", "address"],
              poolKeyArr,
            ),
          );
          const slot0 = await sv.getSlot0(poolId);
          const sqrtPriceX96 = Number(slot0[0]);
          const Q96 = 2 ** 96;
          // price = (sqrtPriceX96 / 2^96)^2 = token1 per token0
          // token0 = wRLP, token1 = waUSDC (sorted order)
          // So price = waUSDC per wRLP
          const rawPrice = (sqrtPriceX96 / Q96) ** 2;
          // Both tokens are 6 decimals, no adjustment needed
          if (rawPrice > 0) {
            // Check token order: if waUSDC < wRLP address, token0=waUSDC, token1=wRLP
            // Then price = wRLP per waUSDC, and we need 1/price for waUSDC per wRLP
            const waUSDCisToken0 =
              collateralAddr.toLowerCase() < positionAddr.toLowerCase();
            poolPrice = waUSDCisToken0 ? 1 / rawPrice : rawPrice;
          }
        }
      } catch (e) {
        console.warn("[TWAMM Dashboard] pool price fetch failed:", e.message);
      }

      // 5. Fetch stream state
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

      // 6. Fetch hook config (once, cached)
      let hookConfig = null;
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
      } catch (e) {
        console.warn("[TWAMM Dashboard] config fetch failed:", e.message);
      }

      // 7. Enrich each order with value preservation analysis
      const enriched = await Promise.all(
        activeSubmits.map(async (evt) => {
          const orderKeyTuple = [evt.owner, evt.expiration, evt.zeroForOne];

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

          // USD values — use pool price for wRLP valuation
          const earnedUsd = isBuy ? buyTokensRaw * poolPrice : buyTokensRaw;
          const remainingUsd = isBuy
            ? sellRefundRaw
            : sellRefundRaw * poolPrice;
          const depositUsd = isBuy
            ? amountInTokens
            : amountInTokens * poolPrice;
          const spentUsd = isBuy ? tokensSpent : tokensSpent * poolPrice;
          const valueUsd = earnedUsd + remainingUsd; // base (updated below with ghost)

          // ── Ghost attribution (pro-rata) ──
          // Ghost accrues in the buy-token direction:
          //   If zeroForOne=true (selling token0), ghost accrues as token1 (accrued1)
          //   If zeroForOne=false (selling token1), ghost accrues as token0 (accrued0)
          let ghostShare = 0;
          let ghostShareUsd = 0;
          const orderSellRate = Number(sellRate);
          if (stream && orderSellRate > 0) {
            if (evt.zeroForOne) {
              // selling token0 → ghost accrues in token1
              const totalSR = stream.sellRate0For1;
              if (totalSR > 0) {
                ghostShare =
                  (stream.accrued1 / 1e6) * (orderSellRate / totalSR);
                ghostShareUsd = ghostShare; // token1 = waUSDC ≈ USD
              }
            } else {
              // selling token1 → ghost accrues in token0
              const totalSR = stream.sellRate1For0;
              if (totalSR > 0) {
                ghostShare =
                  (stream.accrued0 / 1e6) * (orderSellRate / totalSR);
                ghostShareUsd = ghostShare * poolPrice; // token0 = wRLP
              }
            }
          }

          // Discount applied to ghost
          const discountBps = stream?.discountBps || 0;
          const discountedGhostUsd = ghostShareUsd * (1 - discountBps / 10000);
          const discountCostUsd = ghostShareUsd - discountedGhostUsd;

          // ── Ideal instant swap comparison ──
          // If you sold tokensSpent at current POOL price in a single atomic swap
          // (zero slippage, zero time-cost):
          //   BUY (waUSDC → wRLP): ideal wRLP = tokensSpent / poolPrice
          //   SELL (wRLP → waUSDC): ideal waUSDC = tokensSpent * poolPrice
          const idealOutputTokens = isBuy
            ? poolPrice > 0
              ? tokensSpent / poolPrice
              : 0
            : tokensSpent * poolPrice;
          const idealOutputUsd = isBuy
            ? idealOutputTokens * poolPrice // wRLP back to USD
            : idealOutputTokens; // waUSDC ≈ USD
          // Note: idealOutputUsd ≈ spentUsd (zero-slippage reference at pool price)

          // ── Value preservation ──
          // Total value = earned (cleared) + ghost (uncollected) + remaining (refundable)
          const totalValueUsd = earnedUsd + discountedGhostUsd + remainingUsd;
          // Actual total value = earned + discounted ghost (in buy-token USD terms)
          const actualOutputUsd = earnedUsd + discountedGhostUsd;
          const preservation =
            spentUsd > 0 ? (actualOutputUsd / spentUsd) * 100 : 0;

          // ── Netting benefit ──
          // Netting happens when opposing flows cancel out without AMM swap.
          // This reduces slippage to zero on the netted portion.
          // We can't directly measure it, but the ratio earned/spent gives us
          // the effective execution price vs mark price.
          const effectivePrice = isBuy
            ? buyTokensRaw > 0
              ? tokensSpent / buyTokensRaw
              : 0 // waUSDC per wRLP
            : tokensSpent > 0
              ? buyTokensRaw / tokensSpent
              : 0; // waUSDC per wRLP
          const priceImpactBps =
            poolPrice > 0
              ? Math.round(
                  (Math.abs(effectivePrice - poolPrice) / poolPrice) * 10000,
                )
              : 0;

          // Sell token label
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
            timeLeft: isPending ? `Starts in ${formatTimeLeft(startTs - now)}` : formatTimeLeft(timeLeftSec),
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
            // Value analysis
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
      console.warn("[TWAMM Dashboard] fetch failed:", e);
      if (mountedRef.current) setLoading(false);
    }
  }, [hookAddr, collateralAddr, positionAddr, marketInfo]);

  useEffect(() => {
    mountedRef.current = true;
    fetchDashboard();
    const interval = setInterval(fetchDashboard, pollInterval);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [fetchDashboard, pollInterval]);

  return {
    orders,
    streamState,
    config,
    loading,
    lastRefresh,
    refresh: fetchDashboard,
  };
}
