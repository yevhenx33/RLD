import { useCallback, useMemo } from "react";
import { useBrokerAccount } from "./useBrokerAccount";
import { useOperations } from "./useOperations";
import { useTwammPositions } from "./useTwammPositions";
import { useTwammOrder } from "./useTwammOrder";
import { useBrokerState } from "./useBrokerState";
import { usePoolLiquidity } from "./usePoolLiquidity";

/**
 * usePerpsData — Coordinator hook for the Perps trading page.
 *
 * Wraps all data sources, provides a unified `ready` gate for atomic
 * initial rendering, and `refreshAll()` for post-transaction updates.
 *
 * Data stays visible during refreshes (stale-while-revalidate).
 *
 * @param {object}   params
 * @param {string}   params.account            Wallet address
 * @param {object}   params.market             Market data from useSim
 * @param {object}   params.marketInfo         Market config from useSim
 * @param {object}   params.enrichedMarketInfo Market info with swap addresses
 * @param {boolean}  params.connected          WebSocket connected flag
 */
export function usePerpsData({ account, market, marketInfo, enrichedMarketInfo, connected }) {
  // ── 1. Broker Account ────────────────────────────────────────
  const {
    hasBroker,
    brokerAddress,
    brokerBalance,
    creating: brokerCreating,
    fetchBrokerBalance,
    checkBroker,
  } = useBrokerAccount(
    account,
    marketInfo?.broker_factory,
    marketInfo?.collateral?.address,
  );

  // ── 2. User Operations (on-chain events) ─────────────────────
  const { operations, loading: opsLoading, loaded: opsLoaded } = useOperations(
    enrichedMarketInfo?.infrastructure?.broker_router,
    brokerAddress,
  );

  // ── 3. TWAMM Positions ───────────────────────────────────────
  const { orders: twammOrders, loaded: twammLoaded, refresh: refreshTwamm } = useTwammPositions(
    brokerAddress,
    marketInfo,
    30000,
    market?.indexPrice,
  );

  // ── 4. TWAMM Order Actions ───────────────────────────────────
  const {
    cancelOrder: cancelTwammOrder,
    claimExpiredOrder: claimTwammOrder,
    trackTwammOrder,
    untrackTwammOrder,
    executing: cancellingTwamm,
  } = useTwammOrder(
    account,
    brokerAddress,
    marketInfo?.infrastructure,
    marketInfo?.collateral?.address,
    marketInfo?.position_token?.address,
  );

  // ── 5. Broker Full State (NAV, debt, health, LP positions) ───
  const { brokerState, loaded: brokerStateLoaded, refresh: refreshBrokerState } = useBrokerState(
    brokerAddress,
    marketInfo,
  );

  // ── 6. LP Pool Operations (collect fees, remove liquidity) ───
  const {
    executeCollectFees,
    executeRemoveLiquidity,
    executing: lpExecuting,
    executionStep: lpStep,
    executionError: lpError,
    clearError: clearLpError,
  } = usePoolLiquidity(brokerAddress, marketInfo);

  // ── refreshAll — atomic post-transaction refresh ─────────────
  const refreshAll = useCallback(async () => {
    await Promise.all([
      refreshBrokerState?.(),
      refreshTwamm?.(),
      fetchBrokerBalance?.(),
      checkBroker?.(),
    ].filter(Boolean));
  }, [refreshBrokerState, refreshTwamm, fetchBrokerBalance, checkBroker]);

  // ── Ready gate — initial render blocked until ALL data loads ──
  const ready = useMemo(() => {
    // Core: WebSocket connected + market data present
    const coreReady = connected && market !== null && marketInfo !== null;
    if (!coreReady) return false;

    // If user has no broker yet, don't wait for broker-dependent data
    // (the page still needs to render to show "create broker" flow)
    if (!hasBroker) return true;

    // Wait for all position data sources to complete initial fetch
    return brokerStateLoaded && twammLoaded && opsLoaded;
  }, [connected, market, marketInfo, hasBroker, brokerStateLoaded, twammLoaded, opsLoaded]);

  return {
    // Gate
    ready,
    refreshAll,

    // Broker account
    hasBroker,
    brokerAddress,
    brokerBalance,
    brokerCreating,
    fetchBrokerBalance,
    checkBroker,

    // Operations
    operations,
    opsLoading,

    // TWAMM positions
    twammOrders,
    refreshTwamm,

    // TWAMM actions
    cancelTwammOrder,
    claimTwammOrder,
    trackTwammOrder,
    untrackTwammOrder,
    cancellingTwamm,

    // Broker state
    brokerState,
    refreshBrokerState,

    // LP operations
    executeCollectFees,
    executeRemoveLiquidity,
    lpExecuting,
    lpStep,
    lpError,
    clearLpError,
  };
}
