import { useState, useCallback, useRef, useEffect } from "react";
import { ethers } from "ethers";
import { ZERO_FOR_ONE_LONG } from "../config/simulationConfig";
import { SIM_GRAPHQL_URL } from "../api/endpoints";
import { postGraphQL } from "../api/graphqlClient";
import { rpcProvider } from "../utils/provider";
import { BROKER_DATA_QUERY, LEGACY_BROKER_DATA_QUERY, getActiveBrokerState, isBrokerSyncedToBlock, isScopedBrokerQueryUnsupported } from "./brokerDataConfig.js";

// ── Minimal ABI for TWAMM enrichment only ───────────────────────────
const TWAP_ENGINE_VIEW_ABI = [
  "function getCancelOrderState(bytes32 marketId, bytes32 orderId) view returns (uint256 buyTokensOwed, uint256 sellTokensRefund)",
];

// ── Helpers ──────────────────────────────────────────────────────────

function normalizeOrderId(orderId) {
  if (!orderId) return null;
  const value = String(orderId);
  const hex = value.startsWith("0x") ? value : `0x${value}`;
  return /^0x[0-9a-fA-F]{64}$/.test(hex) ? hex : null;
}

function orderIdKey(orderId) {
  const normalized = normalizeOrderId(orderId);
  if (normalized) return normalized.toLowerCase();
  return String(orderId || "").toLowerCase();
}

function isUsableAddress(addr) {
  return (
    !!addr &&
    ethers.isAddress(addr) &&
    addr.toLowerCase() !== ethers.ZeroAddress.toLowerCase()
  );
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

// ── Uniswap V3 concentrated liquidity math ──────────────────────────
// Given liquidity L, tickLower, tickUpper, and currentTick,
// compute token amounts. Both tokens are 6 decimals.
function tickToSqrtPrice(tick) {
  return Math.sqrt(1.0001 ** tick);
}

function liquidityToAmounts(liquidity, tickLower, tickUpper, currentTick) {
  const L = Number(liquidity);
  if (L === 0 || tickLower == null || tickUpper == null) return { amount0: 0, amount1: 0 };

  const sqrtCur = tickToSqrtPrice(currentTick);
  const sqrtLo = tickToSqrtPrice(tickLower);
  const sqrtHi = tickToSqrtPrice(tickUpper);

  let amount0 = 0, amount1 = 0;

  if (currentTick < tickLower) {
    // All token0
    amount0 = L * (1 / sqrtLo - 1 / sqrtHi);
  } else if (currentTick >= tickUpper) {
    // All token1
    amount1 = L * (sqrtHi - sqrtLo);
  } else {
    // In range — both tokens
    amount0 = L * (1 / sqrtCur - 1 / sqrtHi);
    amount1 = L * (sqrtCur - sqrtLo);
  }

  // Raw amounts → human (6 decimals)
  return { amount0: amount0 / 1e6, amount1: amount1 / 1e6 };
}

// ── GQL query: one round-trip for everything ────────────────────────

export { BROKER_DATA_QUERY, LEGACY_BROKER_DATA_QUERY, resolveSelectedBrokerAddress } from "./brokerDataConfig.js";

async function gqlFetch(query, variables) {
  return postGraphQL(SIM_GRAPHQL_URL, { query, variables });
}

// ── The hook ────────────────────────────────────────────────────────

/**
 * useBrokerData — Single hook for ALL perps page data (Pattern A).
 *
 * Architecture:
 *   1. ONE GQL query → broker profile, TWAMM orders, pool snapshot, operations
 *   2. N RPC calls  → getCancelOrderState per active TWAMM order (settlement data)
 *   3. Client math  → sellRate, NAV, colRatio, isSolvent
 *   4. ONE setState → atomic UI update
 *
 * Updates are block-driven: refreshes when blockNumber changes (from useSim).
 * After user operations, call refresh() which waits 500ms then fetches.
 *
 * @param {string}       account        Connected wallet address
 * @param {object}       marketInfo     Market config from useSim
 * @param {number}       blockNumber    Current block from useSim (drives refresh)
 * @param {number}       blockTimestamp Current block timestamp from useSim
 * @param {React.RefObject} pauseRef    When .current is truthy, block updates are paused
 * @returns {{ data, refresh }}
 */
export function useBrokerData(account, marketInfo, blockNumber, blockTimestamp, pauseRef, selectedBrokerAddress) {
  const [data, setData] = useState(null);
  const mountedRef = useRef(true);
  const fetchingRef = useRef(false);
  const queuedFetchRef = useRef(false);
  const queuedForceRef = useRef(false);
  const selectedBrokerAddressRef = useRef(null);
  const syncTargetRef = useRef(null);

  useEffect(() => {
    selectedBrokerAddressRef.current = selectedBrokerAddress;
  }, [selectedBrokerAddress]);

  const marketId = marketInfo?.marketId || marketInfo?.market_id;
  const twammMarketId = marketInfo?.poolId || marketInfo?.pool_id || marketId;
  const twapEngineAddr =
    marketInfo?.infrastructure?.twapEngine ||
    marketInfo?.infrastructure?.twap_engine ||
    marketInfo?.infrastructure?.twammHook ||
    marketInfo?.infrastructure?.twamm_hook;
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr = marketInfo?.positionToken?.address || marketInfo?.position_token?.address;
  const collateralSymbol = marketInfo?.collateral?.symbol || "waUSDC";
  const positionSymbol = marketInfo?.position_token?.symbol || marketInfo?.positionToken?.symbol || "wRLP";

  // ── Core fetch: GQL + minimal RPC → single setState ───────────
  const fetchAll = useCallback(async (force = false, brokerAddressOverride = undefined) => {
    if (!account || !marketId) return;
    // Skip block-driven updates while TX is executing (unless forced by refresh())
    if (!force && pauseRef?.current) return;
    if (fetchingRef.current) {
      queuedFetchRef.current = true;
      queuedForceRef.current = queuedForceRef.current || force;
      return;
    }
    if (brokerAddressOverride !== undefined) {
      selectedBrokerAddressRef.current = brokerAddressOverride;
    }
    fetchingRef.current = true;

    try {
      // ── Phase 1: One GQL round-trip ─────────────────────────────
      let usedLegacyBrokerQuery = false;
      let gql;
      try {
        gql = await gqlFetch(BROKER_DATA_QUERY, {
          owner: account,
          marketId,
          brokerAddress: selectedBrokerAddressRef.current || null,
        });
      } catch (e) {
        if (!isScopedBrokerQueryUnsupported(e)) throw e;
        usedLegacyBrokerQuery = true;
        gql = await gqlFetch(LEGACY_BROKER_DATA_QUERY, {
          owner: account,
          marketId,
        });
      }

      const brokerAccounts = (gql.brokers || []).filter(
        (broker) => broker.owner?.toLowerCase() === account.toLowerCase(),
      );
      let profile = gql.brokerProfile; // null if no broker for this market/selection
      let operations = gql.brokerOperations || [];
      const activeBrokerState = getActiveBrokerState(
        brokerAccounts,
        selectedBrokerAddressRef.current,
        profile,
      );
      const activeBrokerAddress = activeBrokerState.activeBrokerAddress;
      const syncTarget = syncTargetRef.current;
      const syncApplies = !!(
        syncTarget?.minBlock &&
        activeBrokerAddress &&
        syncTarget.brokerAddress?.toLowerCase() === activeBrokerAddress.toLowerCase()
      );
      const isBrokerSyncing = syncApplies && !isBrokerSyncedToBlock(
        activeBrokerState.activeBroker,
        syncTarget.minBlock,
      );
      if (syncApplies && !isBrokerSyncing) {
        syncTargetRef.current = null;
      }
      if (usedLegacyBrokerQuery && profile) {
        const selectedBroker = selectedBrokerAddressRef.current?.toLowerCase();
        const profileBroker = profile.address?.toLowerCase();
        const profileInCurrentMarket = brokerAccounts.some(
          (broker) => broker.address?.toLowerCase() === profileBroker,
        );
        const profileMatchesSelection = !selectedBroker || profileBroker === selectedBroker;
        if (!profileInCurrentMarket || !profileMatchesSelection) {
          profile = null;
          operations = [];
        }
      }
      // TWAMM orders are nested inside brokerProfile (status-based, not isCancelled)
      const rawTwamm = (profile?.twammOrders || []).map((o) => ({
        ...o,
        isCancelled: o.status !== "active",
      }));
      const snapshot = gql.poolSnapshot;

      // Pool snapshot data for client-side math
      const markPrice = snapshot?.markPrice || 0;
      const indexPrice = snapshot?.indexPrice || 0;
      const rawNorm = snapshot?.normalizationFactor;
      const normNum = Number(rawNorm);
      const normFactor = rawNorm
        ? normNum > 1e12 ? normNum / 1e18 : normNum
        : 1;
      const currentTick = snapshot?.tick || 0;

      // ── Phase 2: TWAMM enrichment (only RPC calls) ──────────────
      let enrichedOrders = [];
      const activeTwamm = rawTwamm.filter((o) => !o.isCancelled);
      const now = blockTimestamp || Math.floor(Date.now() / 1000);

      // Read tracked TWAMM order ID from GQL response (no RPC needed)
      let trackedOrderId = profile?.activeTwammOrderId || null;
      if (trackedOrderId === "" || trackedOrderId === "0x" + "0".repeat(64))
        trackedOrderId = null;

      // RPC-free fallback so UI updates to currently active order IDs even if
      // settlement enrichment lags or fails for a poll cycle.
      const fallbackActiveOrders = activeTwamm.map((evt) => {
        const amountInNum = Number(BigInt(evt.amountIn || "0"));
        const amountInTokens = amountInNum / 1e6;
        const startTs = Number(evt.startEpoch || 0);
        const expTs = Number(evt.expiration || 0);
        const totalDuration = Math.max(0, expTs - startTs);
        const elapsed = Math.max(0, now - startTs);
        const progress = totalDuration > 0
          ? Math.min(100, Math.round((elapsed / totalDuration) * 100))
          : 0;
        const isPending = now < startTs;
        const timeLeftSec = Math.max(0, expTs - now);
        const isExpired = timeLeftSec === 0;
        const isDone = isExpired;
        const isBuy = evt.zeroForOne === ZERO_FOR_ONE_LONG;
        const direction = isBuy
          ? `${collateralSymbol} → ${positionSymbol}`
          : `${positionSymbol} → ${collateralSymbol}`;
        const tracked =
          trackedOrderId != null &&
          orderIdKey(trackedOrderId) === orderIdKey(evt.orderId);

        const fallbackValueUsd = isBuy
          ? amountInTokens
          : amountInTokens * markPrice;

        return {
          orderId: evt.orderId,
          direction,
          isBuy,
          amountIn: amountInTokens,
          sellToken: isBuy ? collateralSymbol : positionSymbol,
          buyToken: isBuy ? positionSymbol : collateralSymbol,
          earned: 0,
          earnedUsd: 0,
          valueUsd: fallbackValueUsd,
          remainingUsd: fallbackValueUsd,
          sellRefund: isPending ? amountInTokens : 0,
          tokensSpent: isPending ? 0 : amountInTokens,
          convertedBuyEstimate: 0,
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
          nonce: parseInt(evt.nonce || "0"),
          txHash: evt.txHash,
        };
      });

      fallbackActiveOrders.sort((a, b) => {
        if (a.tracked !== b.tracked) return a.tracked ? -1 : 1;
        return a.expiration - b.expiration;
      });

      if (
        activeTwamm.length > 0 &&
        profile?.address &&
        isUsableAddress(twapEngineAddr) &&
        twammMarketId &&
        collateralAddr &&
        positionAddr
      ) {
        const provider = rpcProvider;
        const twapEngine = new ethers.Contract(
          twapEngineAddr,
          TWAP_ENGINE_VIEW_ABI,
          provider,
        );

        enrichedOrders = await Promise.all(
          activeTwamm.map(async (evt) => {
            let buyTokensOwed = 0n;
            let sellTokensRefund = 0n;

            try {
              const orderId = normalizeOrderId(evt.orderId);
              if (orderId) {
                const cancelState = await twapEngine.getCancelOrderState(
                  twammMarketId,
                  orderId,
                );
                buyTokensOwed = cancelState[0];
                sellTokensRefund = cancelState[1];
              }
            } catch (e) {
              console.warn("[BrokerData] getCancelOrderState failed:", e.message);
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

            // Client-side sellRate calc: amountIn / duration
            const sellRate =
              totalDuration > 0 ? amountInNum / totalDuration : 0;

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
            // isDone: expired OR fully consumed (no refund + no rate)
            const isDone =
              isExpired ||
              (sellRate === 0 && buyTokensOwed === 0n && sellTokensRefund === 0n);

            const isBuy = evt.zeroForOne === ZERO_FOR_ONE_LONG;
            const direction = isBuy ? "waUSDC → wRLP" : "wRLP → waUSDC";

            // All tokens are 6 decimals
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
              nonce: parseInt(evt.nonce || "0"),
              txHash: evt.txHash,
            };
          }),
        );

        // Sort: tracked first, then by expiration
        enrichedOrders.sort((a, b) => {
          if (a.tracked !== b.tracked) return a.tracked ? -1 : 1;
          return a.expiration - b.expiration;
        });

        // Filter fully-consumed zero-value orders
        enrichedOrders = enrichedOrders.filter(
          (o) =>
            !(
              o.isDone &&
              o.earned === 0 &&
              o.sellRefund === 0 &&
              o.valueUsd === 0
            ),
        );
      }

      // ── Phase 3: Client-side NAV math ───────────────────────────
      // Balances are raw uint256 strings from the indexer DB (6 decimals).
      // Convert to human-readable floats for client math.
      const collateral = activeBrokerState.brokerBalance;   // waUSDC balance (human)
      const wrlpBalance = activeBrokerState.wrlpBalance;    // wRLP balance (human)
      const debtPrincipal = activeBrokerState.debtPrincipal; // debt principal (human)

      // True debt = principal × normalizationFactor (wRLP units)
      const trueDebt = debtPrincipal * normFactor;

      // Debt value in USD = trueDebt × indexPrice
      const debtValue = trueDebt * indexPrice;

      // Position value: wRLP balance × indexPrice (matching contract getNetAccountValue)
      const positionValue = wrlpBalance * indexPrice;

      // LP positions: enrich with client-side token amounts and USD value
      // token0 = wRLP (priced at markPrice), token1 = waUSDC ($1)
      const enrichedLps = (profile?.lpPositions || []).map((lp) => {
        const { amount0, amount1 } = liquidityToAmounts(
          lp.liquidity, lp.tickLower, lp.tickUpper, currentTick
        );
        const valueUsd = amount0 * markPrice + amount1;
        const inRange = lp.tickLower != null && lp.tickUpper != null
          ? lp.tickLower <= currentTick && currentTick < lp.tickUpper
          : false;

        // Compute human-readable prices from ticks
        // price = 1.0001^tick  (token0 = wRLP, token1 = waUSDC, same decimals)
        const priceLower = lp.tickLower != null
          ? (1.0001 ** lp.tickLower).toFixed(4)
          : null;
        const priceUpper = lp.tickUpper != null
          ? (1.0001 ** lp.tickUpper).toFixed(4)
          : null;
        const currentPrice = (1.0001 ** currentTick).toFixed(4);

        return {
          ...lp,
          amount0: Math.round(amount0 * 1e4) / 1e4,
          amount1: Math.round(amount1 * 1e4) / 1e4,
          valueUsd: Math.round(valueUsd * 100) / 100,
          inRange,
          priceLower,
          priceUpper,
          currentPrice,
        };
      });

      // LP value: sum of client-computed values
      const lpValue = enrichedLps.reduce(
        (sum, lp) => sum + (lp.valueUsd || 0),
        0,
      );

      // TWAMM value: sum of all TWAMM order values
      const twammValue = enrichedOrders.reduce(
        (sum, o) => sum + (o.valueUsd || 0),
        0,
      );

      // Total Assets (which the contract calls "Net Account Value" / NAV)
      // Note: Contract does NOT subtract debt in getNetAccountValue()
      const nav = collateral + positionValue + lpValue + twammValue;
      const colRatio = debtValue > 0 ? (nav / debtValue) * 100 : Infinity;

      // Parse health factor from broker table (WAD 1e18 string)
      const healthRaw = profile?.healthFactor || "0";
      const healthNum = Number(healthRaw);
      const isMaxHealth = healthNum > 1e30;
      const healthFactor = isMaxHealth
        ? Infinity
        : healthNum > 1e15
          ? healthNum / 1e18
          : healthNum; // already human if < 1e15

      // Maintenance margin from marketInfo
      const maintMargin = marketInfo?.maintenance_margin
        ? Number(marketInfo.maintenance_margin) / 1e18
        : 1.1;
      const isSolvent = healthFactor > maintMargin;

      if (!profile) {
        if (mountedRef.current) {
          setData({
            brokerAccounts,
            hasBroker: activeBrokerState.hasTradingBroker,
            hasTradingBroker: activeBrokerState.hasTradingBroker,
            brokerAddress: activeBrokerState.activeBrokerAddress,
            activeBrokerAddress: activeBrokerState.activeBrokerAddress,
            activeBroker: activeBrokerState.activeBroker,
            brokerBalance: activeBrokerState.brokerBalance,
            collateralBalance: activeBrokerState.brokerBalance,
            positionBalance: 0,
            wrlpTokenBalance: activeBrokerState.wrlpBalance,
            debtPrincipal: activeBrokerState.debtPrincipal,
            trueDebt: 0,
            debtValue: 0,
            nav: 0,
            v4LPValue: 0,
            healthFactor: Infinity,
            isSolvent: true,
            colRatio: Infinity,
            normFactor,
            activeTokenId: 0,
            lpPositions: [],
            twammOrders: [],
            operations,
            markPrice,
            indexPrice,
            currentTick,
            isBrokerSyncing,
            brokerSyncTargetBlock: syncTarget?.minBlock || null,
            brokerUpdatedBlock: activeBrokerState.updatedBlock,
            _profile: null,
          });
        }
        return;
      }

      // ── Phase 4: ONE atomic setState ────────────────────────────
      if (mountedRef.current) {
        setData((prev) => ({
          ...prev,

          // Broker account
          brokerAccounts,
          hasBroker: activeBrokerState.hasTradingBroker,
          hasTradingBroker: activeBrokerState.hasTradingBroker,
          brokerAddress: activeBrokerState.activeBrokerAddress,
          activeBrokerAddress: activeBrokerState.activeBrokerAddress,
          activeBroker: activeBrokerState.activeBroker,
          brokerBalance: collateral,

          // Broker state (from GQL + client math)
          collateralBalance: collateral,
          positionBalance: positionValue,
          wrlpTokenBalance: wrlpBalance,
          debtPrincipal,
          trueDebt,
          debtValue,
          nav,
          v4LPValue: lpValue,
          healthFactor,
          isSolvent,
          colRatio,
          normFactor,
          activeTokenId: profile?.activeTokenId || 0,

          // LP positions (client-computed amounts + values)
          lpPositions: enrichedLps,

          // TWAMM orders: derive from current active rows only.
          // This prevents stale claimed/cancelled orders from sticking around.
          twammOrders: enrichedOrders.length > 0
            ? enrichedOrders
            : (activeTwamm.length > 0 ? fallbackActiveOrders : []),

          // Operations (from indexed BrokerRouter events)
          operations,

          // Pool snapshot
          markPrice,
          indexPrice,
          currentTick,
          isBrokerSyncing,
          brokerSyncTargetBlock: syncTarget?.minBlock || null,
          brokerUpdatedBlock: activeBrokerState.updatedBlock,

          // Raw profile for modals
          _profile: profile,
        }));
      }
    } catch (e) {
      console.warn("[BrokerData] fetchAll failed:", e);
      // On error, keep stale data — don't clear
    } finally {
      fetchingRef.current = false;
      if (queuedFetchRef.current && mountedRef.current) {
        const queuedForce = queuedForceRef.current;
        queuedFetchRef.current = false;
        queuedForceRef.current = false;
        fetchAll(queuedForce);
      } else if (syncTargetRef.current && mountedRef.current) {
        const target = syncTargetRef.current;
        if (Date.now() < target.deadlineMs) {
          setTimeout(() => {
            if (mountedRef.current && syncTargetRef.current === target) {
              fetchAll(true, target.brokerAddress);
            }
          }, 750);
        } else {
          syncTargetRef.current = null;
        }
      }
    }
  }, [
    account,
    marketId,
    twammMarketId,
    twapEngineAddr,
    collateralAddr,
    positionAddr,
    collateralSymbol,
    positionSymbol,
    marketInfo,
    blockTimestamp,
    pauseRef,
    selectedBrokerAddress,
  ]);

  // ── Block-driven: refresh when new block arrives ──────────────
  useEffect(() => {
    mountedRef.current = true;
    if (blockNumber && account && marketId) {
      fetchAll();
    } else {
      setData(null);
    }
    return () => {
      mountedRef.current = false;
    };
  }, [blockNumber, fetchAll, account, marketId]);

  // ── refresh(): call after any transaction ─────────────────────
  const refresh = useCallback(async (brokerAddressOverride = undefined, minUpdatedBlock = null) => {
    if (minUpdatedBlock && brokerAddressOverride) {
      syncTargetRef.current = {
        brokerAddress: brokerAddressOverride,
        minBlock: Number(minUpdatedBlock),
        deadlineMs: Date.now() + 12_000,
      };
    }
    // Small delay to let chain state settle after TX confirmation
    await new Promise((r) => setTimeout(r, 500));
    await fetchAll(true, brokerAddressOverride); // force=true bypasses executing pause
  }, [fetchAll]);

  return { data, refresh };
}
