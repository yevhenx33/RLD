import { useState, useCallback, useRef, useEffect } from "react";
import { ethers } from "ethers";
import { ZERO_FOR_ONE_LONG, SIM_API } from "../config/simulationConfig";

const RPC_URL = `${window.location.origin}/rpc`;
const GQL_URL = `${SIM_API}/graphql`;

// ── Minimal ABI for TWAMM enrichment only ───────────────────────────
const JTM_VIEW_ABI = [
  "function getCancelOrderState((address,address,uint24,int24,address) key, (address,uint160,bool) orderKey) view returns (uint256 buyTokensOwed, uint256 sellTokensRefund)",
];

// ── Helpers ──────────────────────────────────────────────────────────

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

// ── GQL query: one round-trip for everything ────────────────────────

const BROKER_DATA_QUERY = `
  query BrokerData($owner: String!, $marketId: String!) {
    brokerProfile(owner: $owner)
    poolSnapshot(marketId: $marketId) {
      markPrice indexPrice tick
      normalizationFactor
    }
    brokerOperations(owner: $owner)
  }
`;

async function gqlFetch(query, variables) {
  const res = await fetch(GQL_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) throw new Error(`GraphQL HTTP ${res.status}`);
  const json = await res.json();
  if (json.errors) throw new Error(json.errors[0]?.message || "GQL error");
  return json.data;
}

// ── The hook ────────────────────────────────────────────────────────

/**
 * useBrokerData — Single hook for ALL perps page data.
 *
 * Architecture:
 *   1. ONE GQL query → broker profile, TWAMM orders, pool snapshot, operations
 *   2. N RPC calls  → getCancelOrderState per active TWAMM order (settlement data)
 *   3. Client math  → sellRate, NAV, colRatio, isSolvent
 *   4. ONE setState → atomic UI update
 *
 * @param {string}  account    Connected wallet address
 * @param {object}  marketInfo Market config from useSim
 * @returns {{ data, refresh, executing, execStep, execError }}
 */
export function useBrokerData(account, marketInfo) {
  const [data, setData] = useState(null);
  const mountedRef = useRef(true);
  const fetchingRef = useRef(false);

  const marketId = marketInfo?.marketId || marketInfo?.market_id;
  const hookAddr = marketInfo?.infrastructure?.twammHook || marketInfo?.infrastructure?.twamm_hook;
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr = marketInfo?.positionToken?.address || marketInfo?.position_token?.address;

  // ── Core fetch: GQL + minimal RPC → single setState ───────────
  const fetchAll = useCallback(async () => {
    console.log("[BrokerData] fetchAll triggered. account:", account, "marketId:", marketId);
    if (!account || !marketId) return;
    if (fetchingRef.current) return;
    fetchingRef.current = true;

    try {
      console.log("[BrokerData] Fetching GQL from:", GQL_URL);
      // ── Phase 1: One GQL round-trip ─────────────────────────────
      const gql = await gqlFetch(BROKER_DATA_QUERY, {
        owner: account,
        marketId,
      });
      console.log("[BrokerData] GQL response:", gql);

      const profile = gql.brokerProfile; // null if no broker
      // TWAMM orders are nested inside brokerProfile (status-based, not isCancelled)
      const rawTwamm = (profile?.twammOrders || []).map((o) => ({
        ...o,
        isCancelled: o.status !== "active",
      }));
      const snapshot = gql.poolSnapshot;
      const operations = gql.brokerOperations || [];

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

      if (
        activeTwamm.length > 0 &&
        profile?.address &&
        hookAddr &&
        collateralAddr &&
        positionAddr
      ) {
        const provider = new ethers.JsonRpcProvider(RPC_URL);
        const block = await provider.getBlock("latest");
        const now = block.timestamp;
        const poolKey = buildPoolKey(
          marketInfo.infrastructure,
          collateralAddr,
          positionAddr,
        );
        const hook = new ethers.Contract(hookAddr, JTM_VIEW_ABI, provider);

        // Read tracked TWAMM order ID from broker
        let trackedOrderId = null;
        try {
          const broker = new ethers.Contract(
            profile.address,
            [
              "function activeTwammOrder() view returns (address, address, uint24, int24, address, address, uint160, bool, bytes32)",
            ],
            provider,
          );
          const result = await broker.activeTwammOrder();
          trackedOrderId = result[8];
          if (
            trackedOrderId ===
            "0x0000000000000000000000000000000000000000000000000000000000000000"
          )
            trackedOrderId = null;
        } catch {
          // No active order
        }

        enrichedOrders = await Promise.all(
          activeTwamm.map(async (evt) => {
            const orderKeyTuple = [
              evt.owner,
              parseInt(evt.expiration),
              evt.zeroForOne,
            ];

            let buyTokensOwed = 0n;
            let sellTokensRefund = 0n;

            try {
              const cancelState = await hook.getCancelOrderState(
                poolKey,
                orderKeyTuple,
              );
              buyTokensOwed = cancelState[0];
              sellTokensRefund = cancelState[1];
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
      const toHuman = (raw) => Number(raw || "0") / 1e6;

      const collateral = toHuman(profile?.wausdcBalance);   // waUSDC balance (human)
      const wrlpBalance = toHuman(profile?.wrlpBalance);    // wRLP balance (human)
      const debtPrincipal = toHuman(profile?.debtPrincipal); // debt principal (human)

      // True debt = principal × normalizationFactor (wRLP units)
      const trueDebt = debtPrincipal * normFactor;

      // Debt value in USD = trueDebt × indexPrice
      const debtValue = trueDebt * indexPrice;

      // Position value: wRLP balance × indexPrice (matching contract getNetAccountValue)
      const positionValue = wrlpBalance * indexPrice;

      // LP value: sum of all LP position values (already computed server-side)
      const lpValue = (profile?.lpPositions || []).reduce(
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

      // ── Phase 4: ONE atomic setState ────────────────────────────
      if (mountedRef.current) {
        setData({
          // Broker account
          hasBroker: profile !== null,
          brokerAddress: profile?.address || null,
          brokerBalance: collateral,

          // Broker state (from GQL + client math)
          collateralBalance: collateral,
          positionBalance: positionValue,
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

          // LP positions (server-computed amounts + fees)
          lpPositions: profile?.lpPositions || [],

          // TWAMM orders (GQL + RPC-enriched)
          twammOrders: enrichedOrders,

          // Operations (from indexed BrokerRouter events)
          operations,

          // Pool snapshot
          markPrice,
          indexPrice,
          currentTick,

          // Raw profile for modals
          _profile: profile,
        });
      }
    } catch (e) {
      console.warn("[BrokerData] fetchAll failed:", e);
      // On error, keep stale data — don't clear
    } finally {
      fetchingRef.current = false;
    }
  }, [account, marketId, hookAddr, collateralAddr, positionAddr, marketInfo]);

  // ── Polling: single interval ──────────────────────────────────
  useEffect(() => {
    mountedRef.current = true;
    fetchAll();
    const interval = setInterval(fetchAll, 15_000);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [fetchAll]);

  // ── refresh(): call after any transaction ─────────────────────
  const refresh = useCallback(async () => {
    // Small delay to let chain state settle after TX confirmation
    await new Promise((r) => setTimeout(r, 500));
    await fetchAll();
  }, [fetchAll]);

  return { data, refresh };
}
