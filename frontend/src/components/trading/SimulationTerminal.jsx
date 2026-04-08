import React, { useState, useMemo, useEffect, useRef } from "react";
import { ethers } from "ethers";
import { getSigner } from "../../utils/connection";
import { rpcProvider } from "../../utils/provider";
import { useSimulation } from "../../hooks/useSimulation";
import {
  Loader2,
  Terminal,
  Activity,
  TrendingUp,
  TrendingDown,
  Shield,
  Layers,
  Gauge,
  ArrowUpDown,
  RefreshCw,
  Wallet,
  CheckCircle,
  Download,
  Calculator,
  ChevronDown,
  Link2,
} from "lucide-react";
import { useSim } from "../../context/SimulationContext";
import { useChartControls } from "../../hooks/useChartControls";
import { useWallet } from "../../context/WalletContext";

import { useSwapQuote } from "../../hooks/useSwapQuote";
import { useSwapExecution } from "../../hooks/useSwapExecution";
import { formatOpAmount } from "../../hooks/useOperations";
import { useBrokerData } from "../../hooks/useBrokerData";
import { useBrokerAccount } from "../../hooks/useBrokerAccount";
import { useTwammOrder } from "../../hooks/useTwammOrder";
import { usePoolLiquidity } from "../../hooks/usePoolLiquidity";
import AccountModal from "../modals/AccountModal";
import SwapConfirmModal from "../modals/SwapConfirmModal";
import ClaimFeesModal from "../modals/ClaimFeesModal";
import WithdrawModal from "../modals/WithdrawModal";
import DepositModal from "../modals/DepositModal";
import BrokerWithdrawModal from "../modals/BrokerWithdrawModal";
import { ToastContainer } from "../common/Toast";
import { useToast } from "../../hooks/useToast";
import RLDPerformanceChart from "../charts/RLDChart";
import ComboChart from "../charts/ComboChart";

import PnlCalculatorModal from "../modals/PnlCalculatorModal";

import BrokerPositions from "./BrokerPositions";
import StatItem from "../common/StatItem";
import TradingTerminal, { InputGroup, SummaryRow } from "./TradingTerminal";
import SettingsButton from "../common/SettingsButton";
import ActionForm from "./ActionForm";

// ── Sub-components ────────────────────────────────────────────

// eslint-disable-next-line no-unused-vars
function SimMetricBox({ label, value, sub, Icon = Activity, dimmed }) {
  return (
    <div
      className={`p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px] ${
        dimmed ? "opacity-30" : ""
      }`}
    >
      <div className="text-sm text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
        {label} <Icon size={15} className="opacity-90" />
      </div>
      <div>
        <div className="text-2xl md:text-3xl font-light text-white mb-1 md:mb-2 tracking-tight">
          {value}
        </div>
        <div className="text-sm text-gray-500 uppercase tracking-widest">
          {sub}
        </div>
      </div>
    </div>
  );
}

function OperationsFeed({
  operations = [],
  loading = false,
  connected = false,
}) {
  if (!connected) {
    return (
      <div className="text-sm text-gray-600 uppercase tracking-widest text-center py-4">
        —
      </div>
    );
  }

  if (loading && operations.length === 0) {
    return (
      <div className="text-sm text-gray-600 uppercase tracking-widest text-center py-4">
        Loading...
      </div>
    );
  }

  if (operations.length === 0) {
    return (
      <div className="text-sm text-gray-600 uppercase tracking-widest text-center py-4">
        No operations yet
      </div>
    );
  }

  return (
    <div className="space-y-0 divide-y divide-white/5 max-h-[280px] overflow-y-auto custom-scrollbar">
      {operations.slice(0, 15).map((op) => {
        // Format amounts based on event type
        let detail = "";
        if (op.type === "LongExecuted") {
          detail = `${formatOpAmount(op.args[1])} waUSDC → ${formatOpAmount(op.args[2])} wRLP`;
        } else if (op.type === "LongClosed") {
          detail = `${formatOpAmount(op.args[1])} wRLP → ${formatOpAmount(op.args[2])} waUSDC`;
        } else if (op.type === "ShortExecuted") {
          detail = `${formatOpAmount(op.args[1])} debt · ${formatOpAmount(op.args[2])} proceeds`;
        } else if (op.type === "ShortClosed") {
          detail = `${formatOpAmount(op.args[1])} repaid · ${formatOpAmount(op.args[2])} spent`;
        } else if (op.type === "Deposited") {
          detail = `${formatOpAmount(op.args[1])} → ${formatOpAmount(op.args[2])} waUSDC`;
        }

        return (
          <div key={op.id} className="py-2.5 flex items-center gap-3">
            {/* Left: Action badge (centered) */}
            <span
              className={`text-xs font-bold font-mono px-2 py-1 tracking-wider text-center shrink-0 w-[90px] ${op.color}`}
            >
              {op.label}
            </span>
            {/* Right: Detail */}
            <div className="flex-1 min-w-0 text-right">
              {detail && (
                <div className="text-sm font-mono text-gray-300 truncate">
                  {detail}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────

export default function SimulationTerminal() {
  const sim = useSim();
  const {
    connected,
    loading: _loading,
    error,
    market,
    pool,
    funding,
    fundingFromNF: _fundingFromNF,
    oracleChange24h,
    volumeData,
    protocolStats,
    marketInfo,
    brokers: _brokers,
    chartData: _sharedChartData,
    events: _events,
    blockChanged: _blockChanged,
    blockNumber: _blockNumber,
    totalBlocks: _totalBlocks,
    totalEvents: _totalEvents,
  } = sim;

  // Use dynamic addresses from the indexer API — no hardcoded overrides.
  // The /api/market-info endpoint returns all deployed contract addresses
  // (v4_quoter, broker_router, twamm_hook, token addresses) which update
  // automatically on each redeployment.
  const enrichedMarketInfo = useMemo(() => {
    if (!marketInfo) return null;
    return {
      ...marketInfo,
      infrastructure: {
        ...marketInfo.infrastructure,
        // Swap token addresses mirror the top-level collateral/position_token
        swap_collateral: marketInfo.collateral?.address,
        swap_position_token: marketInfo.position_token?.address,
      },
    };
  }, [marketInfo]);

  // Wallet & Faucet
  const { account, connectWallet } = useWallet();

  // ── Broker Data (single GQL + minimal RPC) ─────────────────
  // ONE hook for ALL data. Block-driven: refreshes when block changes.
  // txPauseRef pauses block updates while any TX is executing.
  const txPauseRef = useRef(false);
  const { data, refresh } = useBrokerData(
    account, marketInfo, sim.blockNumber, sim.market?.blockTimestamp, txPauseRef,
  );

  // Convenience aliases from the data object
  const hasBroker = data?.hasBroker ?? null;
  const brokerAddress = data?.brokerAddress ?? null;
  const brokerBalance = data?.brokerBalance ?? 0;
  const operations = data?.operations ?? [];
  const twammOrders = data?.twammOrders ?? [];
  const brokerState = data; // data IS the broker state now

  // ── Action hooks (TX execution only — no data fetching) ─────
  const {
    creating: _brokerCreating,
    createBroker: _createBroker,
    depositFunds: _depositFunds,
    fetchBrokerBalance: _fetchBrokerBalance,
    checkBroker,
  } = useBrokerAccount(
    account,
    marketInfo?.broker_factory,
    marketInfo?.collateral?.address,
  );

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

  const {
    executeCollectFees,
    executeRemoveLiquidity,
    executing: lpExecuting,
    executionStep: lpStep,
    executionError: lpError,
    clearError: clearLpError,
  } = usePoolLiquidity(brokerAddress, marketInfo);

  // Sync all executing flags into the pause ref so useBrokerData
  // skips block-driven fetches while any TX is pending.
  // This runs after hooks are declared but before the next render's fetch.

  // Trading State (must be declared before swap hooks that reference tradeSide/collateral)
  const [tradeSide, setTradeSide] = useState("LONG");
  const [activeAction, setActiveAction] = useState(null);

  // Collateral registration confirmation modal
  const [collateralConfirm, setCollateralConfirm] = useState(null);
  const [claimConfirm, setClaimConfirm] = useState(null);
  // { type: 'track-lp'|'untrack-lp'|'track-twamm'|'untrack-twamm', label, data }

  // LP fee claim / withdraw modals (use same components as Pool page)
  const [claimFeesLp, setClaimFeesLp] = useState(null);
  const [withdrawLp, setWithdrawLp] = useState(null);
  const [depositToken, setDepositToken] = useState(null);   // "waUSDC" | "wRLP" | null
  const [withdrawToken, setWithdrawToken] = useState(null); // "waUSDC" | "wRLP" | null
  const [actionsHeight, setActionsHeight] = useState(null);
  const actionsRef = useRef(null);

  // Sync Operations panel height to Actions panel (when inactive)
  useEffect(() => {
    const el = actionsRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      if (!activeAction) setActionsHeight(el.offsetHeight);
    });
    ro.observe(el);
    if (!activeAction) setActionsHeight(el.offsetHeight);
    return () => ro.disconnect();
  }, [activeAction]);

  const [tradeAction, setTradeAction] = useState("OPEN"); // OPEN or CLOSE
  const [collateral, setCollateral] = useState(1000);
  const [closeAmount, setCloseAmount] = useState(""); // wRLP to sell (close long)
  const [closeShortAmount, setCloseShortAmount] = useState(""); // waUSDC to spend (close short)
  const [closeShortDebt, setCloseShortDebt] = useState(""); // wRLP debt to repay (close short)
  const [_lastCloseShortEdit, setLastCloseShortEdit] = useState(null); // 'debt' or 'collateral'
  const [closeShortRepayMode, setCloseShortRepayMode] = useState("wRLP"); // 'wRLP' or 'waUSDC'
  const [payDropdownOpen, setPayDropdownOpen] = useState(false);
  const payDropdownRef = useRef(null);
  const [shortCR, setShortCR] = useState(200);
  const [shortAmount, setShortAmount] = useState(0);

  // Close PAY_WITH dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (
        payDropdownRef.current &&
        !payDropdownRef.current.contains(e.target)
      ) {
        setPayDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Broker wRLP (position token) balance — for close long
  const [brokerWrlpBalance, setBrokerWrlpBalance] = useState(null);
  useEffect(() => {
    if (!brokerAddress || !enrichedMarketInfo?.position_token?.address) return;
    const fetchWrlp = async () => {
      try {
        const provider = rpcProvider;
        const token = new ethers.Contract(
          enrichedMarketInfo.position_token.address,
          ["function balanceOf(address) view returns (uint256)"],
          provider,
        );
        const bal = await token.balanceOf(brokerAddress);
        setBrokerWrlpBalance(parseFloat(ethers.formatUnits(bal, 6)));
      } catch (e) {
        console.warn("Failed to fetch wRLP balance:", e);
      }
    };
    fetchWrlp();
    const interval = setInterval(fetchWrlp, 12000);
    return () => clearInterval(interval);
  }, [brokerAddress, enrichedMarketInfo?.position_token?.address]);

  // Determine which amount to quote and in which direction
  // Close Long = SELL wRLP; Close Short = BUY wRLP (same direction as Open Long)
  const quoteDirection =
    tradeAction === "CLOSE" && tradeSide === "LONG" ? "SELL" : "BUY";
  const quoteAmountIn =
    tradeAction === "CLOSE"
      ? tradeSide === "LONG"
        ? parseFloat(closeAmount) || 0
        : parseFloat(closeShortAmount) || 0
      : tradeSide === "LONG"
        ? collateral
        : 0;

  // Swap quote (V4Quoter on-chain)
  const {
    quote: swapQuote,
    loading: quoteLoading,
    refresh: refreshQuote,
  } = useSwapQuote(
    enrichedMarketInfo?.infrastructure,
    enrichedMarketInfo?.infrastructure?.swap_collateral,
    enrichedMarketInfo?.infrastructure?.swap_position_token,
    quoteAmountIn,
    quoteDirection,
  );

  // Swap execution (MetaMask-signed)
  const {
    executeLong,
    executeCloseLong,
    executeShort,
    executeCloseShort,
    executeRepayDebt,
    executing: swapExecuting,
    error: swapError,
    step: swapStep,
  } = useSwapExecution(
    account,
    brokerAddress,
    enrichedMarketInfo?.infrastructure,
    enrichedMarketInfo?.infrastructure?.swap_collateral,
    enrichedMarketInfo?.infrastructure?.swap_position_token,
  );

  // ── Pause block-driven data updates while any TX is executing ──
  // Updates resume automatically when all executing flags clear.
  // The explicit refresh() after TX completion bypasses the pause.
  useEffect(() => {
    txPauseRef.current = !!(swapExecuting || cancellingTwamm || lpExecuting);
  }, [swapExecuting, cancellingTwamm, lpExecuting]);

  const [showAccountModal, setShowAccountModal] = useState(false);
  const [positionDropdown, setPositionDropdown] = useState(null);
  const [accountDropdown, setAccountDropdown] = useState(false);
  const [showSwapConfirm, setShowSwapConfirm] = useState(false);
  const [chartDropdown, setChartDropdown] = useState(null);

  // Toast notifications
  const { toasts, addToast, removeToast } = useToast();

  // Track swap errors to fire toast + close modal
  const prevSwapError = useRef(swapError);
  useEffect(() => {
    if (swapError && swapError !== prevSwapError.current && showSwapConfirm) {
      addToast({
        type: "error",
        title: "Swap Failed",
        message: swapError,
        duration: 6000,
      });
      setShowSwapConfirm(false);
    }
    prevSwapError.current = swapError;
  }, [swapError, showSwapConfirm, addToast]);

  // Chart controls
  const controls = useChartControls({
    defaultRange: "ALL",
    defaultDays: 9999,
    defaultResolution: "5M",
  });
  const { resolution, appliedStart, appliedEnd } = controls;

  // Sim block timestamp for chart time anchoring (same as pools page)
  const [simBlockTs, setSimBlockTs] = useState(null);
  useEffect(() => {
    if (simBlockTs) return;
    const ts = market?.blockTimestamp;
    if (ts) setSimBlockTs(ts);
  }, [market?.blockTimestamp, simBlockTs]);

  const chartStartTime = useMemo(() => {
    if (!simBlockTs || !appliedStart) return null;
    const wallNow = Math.floor(Date.now() / 1000);
    const wallStart = Math.floor(new Date(appliedStart).getTime() / 1000);
    return simBlockTs - (wallNow - wallStart);
  }, [appliedStart, simBlockTs]);

  const chartEndTime = useMemo(() => {
    if (!simBlockTs || !appliedEnd) return null;
    const wallNow = Math.floor(Date.now() / 1000);
    const wallEnd = Math.floor(new Date(appliedEnd + "T23:59:59Z").getTime() / 1000);
    return simBlockTs - (wallNow - wallEnd);
  }, [appliedEnd, simBlockTs]);

  // Separate useSimulation for chart data (resolution/timeframe-specific)
  const simChart = useSimulation({
    pollInterval: 2000,
    chartResolution: resolution,
    chartStartTime,
    chartEndTime,
  });
  const { chartData } = simChart;

  // PnL Modal State
  const [pnlModalOpen, setPnlModalOpen] = useState(false);

  // Chart view tabs (Price / Liquidity / Volume)
  const [chartView, setChartView] = useState("PRICE");

  // Chart series visibility
  const [hiddenSeries, setHiddenSeries] = useState([]);
  const toggleSeries = (key) => {
    setHiddenSeries((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  };

  // Chart views config (matches pools page)
  const CHART_VIEWS = useMemo(
    () => ({
      PRICE: {
        label: "Price",
        areas: [
          { key: "indexPrice", name: "Index Price", color: "#22d3ee" },
          { key: "markPrice", name: "Mark Price", color: "#ec4899" },
        ],
      },
      LIQUIDITY: {
        label: "Liquidity",
        areas: [
          { key: "liquidity", name: "Active Liq", color: "#a855f7" },
        ],
      },
      VOLUME: {
        label: "Volume",
        areas: [
          { key: "volume", name: "Volume", color: "#22c55e", format: "dollar" },
        ],
      },
    }),
    [],
  );

  const activeChartConfig = CHART_VIEWS[chartView];
  const areas = useMemo(
    () => activeChartConfig.areas.filter((a) => !hiddenSeries.includes(a.key)),
    [activeChartConfig, hiddenSeries],
  );

  // ── Liquidity distribution bins (same GQL as pools page) ────
  const [liquidityBins, setLiquidityBins] = useState([]);
  useEffect(() => {
    let cancelled = false;
    async function fetchDistribution() {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const res = await fetch(`/graphql`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: `query { liquidityDistribution }` }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const json = await res.json();
          const rawBins = json?.data?.liquidityDistribution;
          if (!cancelled && rawBins?.length) {
            const curPrice = pool?.markPrice || 1;
            const bins = rawBins
              .filter((b) => b.priceHigh >= 1 && b.priceLow <= 10)
              .map((b) => {
                const priceFrom = b.priceLow;
                const priceTo = b.priceHigh;
                const midPrice = (priceFrom + priceTo) / 2;
                const liq = b.liquidity || 0;
                const sa = Math.sqrt(priceFrom), sb = Math.sqrt(priceTo);
                const sp = Math.max(sa, Math.min(Math.sqrt(curPrice), sb));
                const a0 = sp < sb ? liq * (1 / sp - 1 / sb) / 1e6 : 0;
                const a1 = sp > sa ? liq * (sp - sa) / 1e6 : 0;
                return {
                  price: midPrice.toFixed(3),
                  priceFrom, priceTo, liquidity: liq,
                  amount0: Math.max(0, a0), amount1: Math.max(0, a1),
                };
              });
            setLiquidityBins(bins);
            return;
          }
        } catch (err) {
          if (attempt < 2) {
            await new Promise(r => setTimeout(r, 2000));
            continue;
          }
          console.warn("[Perps] liquidityDistribution unavailable:", err.message);
        }
      }
    }
    fetchDistribution();
    return () => { cancelled = true; };
  }, [pool?.markPrice]);

  // ── Volume history (derived from chartData, same as pools) ──
  const volumeHistory = useMemo(
    () => chartData.map((c) => ({ timestamp: c.timestamp, volume: c.volume, swapCount: c.swapCount })),
    [chartData],
  );


  // ── Trading calculations ────────────────────────────────────
  const currentRate = market?.indexPrice || 0;

  const { notional, liqRate } = useMemo(() => {
    if (tradeSide === "LONG") {
      return { notional: collateral, liqRate: null };
    }
    // SHORT: notional = shortAmount (wRLP) × currentRate
    const notionalUSD = shortAmount * currentRate;
    return {
      notional: notionalUSD,
      liqRate: currentRate * (shortCR / 110),
    };
  }, [tradeSide, collateral, shortAmount, shortCR, currentRate]);

  const _handleShortAmountChange = (newWRLP) => {
    setShortAmount(newWRLP);
    // Recalculate CR: CR = collateral / (wRLP × rate)
    if (newWRLP > 0 && currentRate > 0) {
      const newCR = (collateral / (newWRLP * currentRate)) * 100;
      setShortCR(Math.min(Math.max(newCR, 150), 1500));
    }
  };

  // When CR slider changes, recalculate shortAmount
  // shortAmount = collateral / (CR × indexPrice)
  const handleShortCRChange = (newCR) => {
    setShortCR(newCR);
    if (currentRate > 0 && collateral > 0) {
      const crDecimal = newCR / 100;
      const newAmount = collateral / (crDecimal * currentRate);
      setShortAmount(parseFloat(newAmount.toFixed(6)));
    }
  };

  // Auto-compute shortAmount = collateral / (CR × indexPrice) whenever inputs change
  useEffect(() => {
    if (
      tradeSide === "SHORT" &&
      currentRate > 0 &&
      collateral > 0 &&
      shortCR > 0
    ) {
      const crDecimal = shortCR / 100;
      const newAmount = collateral / (crDecimal * currentRate);
      setShortAmount(parseFloat(newAmount.toFixed(6)));
    }
  }, [tradeSide, collateral, shortCR, currentRate]);

  const _handleLongAmountChange = (newAmount) => {
    setCollateral(newAmount);
  };

  // ── Error / Loading ─────────────────────────────────────────
  if (error && !connected) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="text-red-500 text-sm uppercase tracking-widest">
            SIM_DISCONNECTED
          </div>
          <div className="text-gray-600 text-sm max-w-xs">
            Cannot reach simulation indexer. Make sure the Docker simulation
            stack is running.
          </div>
          <div className="text-sm text-gray-700 font-mono">
            Expected at: http://localhost:8080
          </div>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
          <span className="text-sm uppercase tracking-widest text-gray-500">
            {!connected ? "Connecting to simulation..." : "Loading positions..."}
          </span>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Toast notifications */}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
      <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
        {/* MAIN CONTENT */}
        <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-stretch">
            {/* === LEFT COLUMN (Span 9) === */}
            <div className="xl:col-span-9 flex flex-col gap-4">
              {/* 1. METRICS GRID */}
              <div className="border border-white/10 grid grid-cols-1 lg:grid-cols-12">
                {/* Branding */}
                <div className="lg:col-span-4 flex flex-col justify-between p-6 border-b lg:border-b-0 lg:border-r border-white/10 h-full min-h-[180px]">
                  <div>
                    <div className="text-sm text-gray-700 mb-6 font-mono leading-tight tracking-tight">
                      {(market?.marketId || "").slice(0, 18)}...
                      {(market?.marketId || "").slice(-8)}
                    </div>
                    <h2 className="text-3xl font-medium tracking-tight mb-2 leading-none">
                      RLD PROTOCOL
                      <br />
                      <span className="text-gray-600">SIMULATION</span>
                    </h2>
                  </div>
                  <div className="mt-auto pt-4 border-t border-white/10 flex items-center justify-between">
                    <span className="text-sm uppercase tracking-widest text-gray-500">
                      RLD_Core
                    </span>
                    <Link2 size={14} className="text-cyan-500 hover:text-cyan-400 transition-colors cursor-pointer" />
                  </div>
                </div>

                {/* Stats Cards */}
                <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
                  {/* PRICE */}
                  <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                    <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                      PRICE <Terminal size={15} className="opacity-90" />
                    </div>
                    <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                      <StatItem
                        label="ORACLE"
                        value={market.indexPrice.toFixed(4)}
                      />
                      <StatItem
                        label="24H_CHG"
                        value={
                          oracleChange24h != null
                            ? `${oracleChange24h >= 0 ? "+" : ""}${oracleChange24h.toFixed(2)}%`
                            : "—"
                        }
                        valueClassName={
                          oracleChange24h != null
                            ? oracleChange24h >= 0
                              ? "text-green-400"
                              : "text-red-400"
                            : "text-white"
                        }
                      />
                      <StatItem
                        label="MARK"
                        value={pool ? pool.markPrice.toFixed(4) : "—"}
                      />
                      <StatItem
                        label="FUNDING_ANN"
                        value={
                          funding?.annualizedPct != null
                            ? `${funding.annualizedPct >= 0 ? "+" : ""}${funding.annualizedPct.toFixed(2)}%`
                            : "—"
                        }
                      />
                    </div>
                  </div>

                  {/* PROTOCOL */}
                  <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                    <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                      PROTOCOL <Shield size={15} className="opacity-90" />
                    </div>
                    <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                      <StatItem
                        label="TVL"
                        value={
                          protocolStats
                            ? `$${(protocolStats.totalCollateral / 1e6).toFixed(2)}M`
                            : "—"
                        }
                      />
                      <StatItem
                        label="VOL_24H"
                        value={volumeData?.volume_formatted || "—"}
                      />
                      <StatItem
                        label="TOTAL_DEBT"
                        value={
                          protocolStats
                            ? `$${(protocolStats.totalDebtUsd / 1e6).toFixed(2)}M`
                            : "—"
                        }
                      />
                      <StatItem
                        label="HEALTH"
                        value={
                          protocolStats
                            ? `${protocolStats.overCollat.toFixed(1)}%`
                            : "—"
                        }
                        valueClassName={
                          protocolStats
                            ? protocolStats.overCollat >= 200
                              ? "text-green-400"
                              : protocolStats.overCollat >= 120
                                ? "text-yellow-400"
                                : "text-red-400"
                            : "text-white"
                        }
                      />
                    </div>
                  </div>

                  {/* MARKET */}
                  <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                    <div className="text-sm text-gray-500 uppercase tracking-widest flex justify-between">
                      MARKET <Shield size={15} className="opacity-90" />
                    </div>
                    <div className="grid grid-cols-[3fr_2fr] gap-x-4 gap-y-6 mt-auto">
                      <StatItem
                        label="COLLATERAL"
                        value={marketInfo?.collateral?.name || "—"}
                        valueClassName="text-white !text-[17px] whitespace-nowrap"
                      />
                      <StatItem
                        label="MIN_COL"
                        value={
                          marketInfo?.risk_params?.min_col_ratio_pct || "—"
                        }
                      />
                      <StatItem
                        label="POS_TOKEN"
                        value={marketInfo?.position_token?.symbol || "—"}
                        valueClassName="text-white !text-[17px]"
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* 2. CHART (with inline controls — pools style) */}
              <div className="relative flex-1 min-h-[350px] md:min-h-[400px] border border-white/10">

                {/* ── Desktop controls (lg+): single row ── */}
                <div className="hidden lg:flex items-stretch border-b border-white/10">
                  {/* View switcher */}
                  <div className="flex items-center gap-1 px-4 py-2 border-r border-white/10">
                    {Object.entries(CHART_VIEWS).map(([key, view]) => (
                      <button
                        key={key}
                        onClick={() => { setChartView(key); setHiddenSeries([]); }}
                        className={`px-3 py-1 text-sm font-semibold uppercase tracking-widest transition-colors ${
                          chartView === key
                            ? "text-white bg-white/10"
                            : "text-gray-600 hover:text-gray-400"
                        }`}
                      >
                        {view.label}
                      </button>
                    ))}
                  </div>
                  {/* Resolution dropdown */}
                  <div className="relative flex items-center px-4 py-2 border-r border-white/10">
                    <button
                      onClick={() => setChartDropdown(chartDropdown === 'resolution' ? null : 'resolution')}
                      className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-widest text-gray-600 hover:text-gray-400 transition-colors"
                    >
                      Resolution: <span className="text-white">{resolution}</span>
                      <ChevronDown size={10} className={`transition-transform ${chartDropdown === 'resolution' ? 'rotate-180' : ''}`} />
                    </button>
                    {chartDropdown === 'resolution' && (
                      <>
                        <div className="fixed inset-0 z-40" onClick={() => setChartDropdown(null)} />
                        <div className="absolute left-0 top-full mt-0 z-50 bg-[#0a0a0a] border border-white/10">
                          {["5M", "1H", "4H", "1D"].map((res) => (
                            <button
                              key={res}
                              onClick={() => { controls.setResolution(res); setChartDropdown(null); }}
                              className={`block w-full text-left px-3 py-1 text-sm font-semibold uppercase tracking-widest transition-colors ${
                                resolution === res
                                  ? "text-white bg-white/10"
                                  : "text-gray-600 hover:text-gray-400"
                              }`}
                            >
                              {res}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>

                  {/* Timeframe dropdown */}
                  <div className="relative flex items-center px-4 py-2 border-r border-white/10">
                    <button
                      onClick={() => setChartDropdown(chartDropdown === 'timeframe' ? null : 'timeframe')}
                      className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-widest text-gray-600 hover:text-gray-400 transition-colors"
                    >
                      Timeframe: <span className="text-white">{controls.activeRange}</span>
                      <ChevronDown size={10} className={`transition-transform ${chartDropdown === 'timeframe' ? 'rotate-180' : ''}`} />
                    </button>
                    {chartDropdown === 'timeframe' && (
                      <>
                        <div className="fixed inset-0 z-40" onClick={() => setChartDropdown(null)} />
                        <div className="absolute left-0 top-full mt-0 z-50 bg-[#0a0a0a] border border-white/10">
                          {[
                            { l: "1D", d: 1 },
                            { l: "1W", d: 7 },
                            { l: "1M", d: 30 },
                            { l: "3M", d: 90 },
                            { l: "ALL", d: 9999 },
                          ].map((btn) => (
                            <button
                              key={btn.l}
                              onClick={() => { controls.handleQuickRange(btn.d, btn.l); setChartDropdown(null); }}
                              className={`block w-full text-left px-3 py-1 text-sm font-semibold uppercase tracking-widest transition-colors ${
                                controls.activeRange === btn.l
                                  ? "text-white bg-white/10"
                                  : "text-gray-600 hover:text-gray-400"
                              }`}
                            >
                              {btn.l}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>

                  {/* PnL Calculator */}
                  <div className="flex items-center px-4 py-2 border-r border-white/10">
                    <button
                      onClick={() => setPnlModalOpen(true)}
                      className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-widest text-gray-600 hover:text-gray-400 transition-colors"
                    >
                      <Calculator size={14} />
                      PnL
                    </button>
                  </div>

                  {/* Series legend (with toggle) */}
                  <div className="flex items-center gap-5 px-4 py-2 ml-auto">
                    {activeChartConfig.areas.map((s) => (
                      <div
                        key={s.key}
                        className={`flex items-center gap-2 cursor-pointer transition-all ${
                          hiddenSeries.includes(s.key)
                            ? "opacity-40 line-through"
                            : "opacity-100 hover:opacity-80"
                        }`}
                        onClick={() => toggleSeries(s.key)}
                      >
                        <div className="w-2 h-2" style={{ backgroundColor: s.color }} />
                        <span className="text-sm uppercase tracking-widest text-gray-400">{s.name}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* ── Mobile controls (<lg): multi-row ── */}
                {/* Row 1: View tabs */}
                <div className="lg:hidden flex items-center gap-1 px-3 py-2 border-b border-white/10">
                  {Object.entries(CHART_VIEWS).map(([key, view]) => (
                    <button
                      key={key}
                      onClick={() => { setChartView(key); setHiddenSeries([]); }}
                      className={`flex-1 px-2 py-1.5 text-xs font-semibold uppercase tracking-widest text-center transition-colors ${
                        chartView === key
                          ? "text-white bg-white/10"
                          : "text-gray-600 hover:text-gray-400"
                      }`}
                    >
                      {view.label}
                    </button>
                  ))}
                </div>
                {/* Row 2: Resolution + Timeframe */}
                <div className="lg:hidden flex items-center border-b border-white/10">
                  <div className="flex-1 flex items-center gap-1 px-3 py-2 border-r border-white/10 overflow-x-auto">
                    {["5M", "1H", "4H", "1D"].map((res) => (
                      <button
                        key={res}
                        onClick={() => controls.setResolution(res)}
                        className={`px-2 py-1 text-xs font-semibold uppercase tracking-widest transition-colors ${
                          resolution === res
                            ? "text-white bg-white/10"
                            : "text-gray-600 hover:text-gray-400"
                        }`}
                      >
                        {res}
                      </button>
                    ))}
                  </div>
                  <div className="flex-1 flex items-center gap-1 px-3 py-2 overflow-x-auto">
                    {[
                      { l: "1D", d: 1 },
                      { l: "1W", d: 7 },
                      { l: "1M", d: 30 },
                      { l: "ALL", d: 9999 },
                    ].map((btn) => (
                      <button
                        key={btn.l}
                        onClick={() => controls.handleQuickRange(btn.d, btn.l)}
                        className={`px-2 py-1 text-xs font-semibold uppercase tracking-widest transition-colors ${
                          controls.activeRange === btn.l
                            ? "text-white bg-white/10"
                            : "text-gray-600 hover:text-gray-400"
                        }`}
                      >
                        {btn.l}
                      </button>
                    ))}
                  </div>
                </div>
                {/* Row 3: Series legend */}
                <div className="lg:hidden flex items-center justify-center gap-5 px-3 py-2 border-b border-white/10">
                  {activeChartConfig.areas.map((s) => (
                    <div
                      key={s.key}
                      className={`flex items-center gap-2 cursor-pointer transition-all ${
                        hiddenSeries.includes(s.key) ? "opacity-40 line-through" : "opacity-100"
                      }`}
                      onClick={() => toggleSeries(s.key)}
                    >
                      <div className="w-2 h-2" style={{ backgroundColor: s.color }} />
                      <span className="text-xs uppercase tracking-widest text-gray-400">{s.name}</span>
                    </div>
                  ))}
                </div>

                {/* Chart body */}
                <div className="h-[350px] md:h-[500px] w-full p-4 bg-[#050505]">
                  {chartView === "LIQUIDITY" ? (
                    <ComboChart
                      bins={liquidityBins}
                      currentPrice={pool?.markPrice || 0}
                    />
                  ) : chartView === "VOLUME" ? (
                    volumeHistory.length === 0 ? (
                      <div className="h-full flex items-center justify-center">
                        <Loader2 className="animate-spin text-gray-700" />
                      </div>
                    ) : (
                      <RLDPerformanceChart
                        data={volumeHistory}
                        areas={activeChartConfig.areas}
                        resolution={resolution}
                      />
                    )
                  ) : chartData.length === 0 ? (
                    <div className="h-full flex items-center justify-center">
                      <Loader2 className="animate-spin text-gray-700" />
                    </div>
                  ) : (
                    <RLDPerformanceChart
                      data={chartData}
                      areas={areas}
                      resolution={resolution}
                    />
                  )}
                </div>
              </div>
            </div>

            {/* === RIGHT COLUMN: TRADING TERMINAL (Span 3) — matches /app layout === */}
            <TradingTerminal
              account={account}
              connectWallet={connectWallet}
              title="Synthetic_Rates"
              Icon={Terminal}
              subTitle="SIM"
              tabs={[
                {
                  id: "LONG",
                  label: "Long",
                  onClick: () => setTradeSide("LONG"),
                  isActive: tradeSide === "LONG",
                  color: "cyan",
                },
                {
                  id: "SHORT",
                  label: "Short",
                  onClick: () => setTradeSide("SHORT"),
                  isActive: tradeSide === "SHORT",
                  color: "pink",
                },
              ]}
              actionButton={{
                label:
                  !account || !hasBroker
                    ? "Create Account"
                    : swapExecuting
                      ? swapStep || "Processing..."
                      : tradeAction === "CLOSE" && tradeSide === "LONG"
                        ? "Close Long"
                        : tradeAction === "CLOSE" && tradeSide === "SHORT"
                          ? "Close Short"
                          : tradeSide === "LONG"
                            ? "Open Long"
                            : "Open Short",
                onClick:
                  !account || !hasBroker
                    ? () => setShowAccountModal(true)
                    : tradeSide === "LONG"
                      ? () => setShowSwapConfirm(true)
                      : () => setShowSwapConfirm(true),
                disabled:
                  !account || !hasBroker
                    ? false
                    : swapExecuting ||
                  (tradeSide === "LONG" &&
                    tradeAction === "OPEN" &&
                    (!collateral || quoteLoading)) ||
                  (tradeSide === "LONG" &&
                    tradeAction === "CLOSE" &&
                    (!closeAmount || quoteLoading)) ||
                  (tradeSide === "SHORT" &&
                    tradeAction === "OPEN" &&
                    (!collateral || shortAmount <= 0)) ||
                  (tradeSide === "SHORT" &&
                    tradeAction === "CLOSE" &&
                    (!closeShortAmount || quoteLoading)),
                variant:
                  tradeAction === "CLOSE"
                    ? "pink"
                    : tradeSide === "LONG"
                      ? "cyan"
                      : "pink",
              }}
              footer={null}
            >
              {/* === OPEN / CLOSE sub-toggle === */}
              {(tradeSide === "LONG" || tradeSide === "SHORT") && (
                <div className="flex border border-white/10 bg-[#060606]">
                  {["OPEN", "CLOSE"].map((action) => (
                    <button
                      key={action}
                      onClick={() => setTradeAction(action)}
                      className={`flex-1 py-2 text-sm font-bold tracking-[0.2em] uppercase transition-colors ${
                        tradeAction === action
                          ? action === "CLOSE"
                            ? "bg-pink-500/10 text-pink-400 border-b-2 border-pink-500"
                            : "bg-cyan-500/10 text-cyan-400 border-b-2 border-cyan-500"
                          : "text-gray-600 hover:text-gray-400"
                      }`}
                    >
                      {action}
                    </button>
                  ))}
                </div>
              )}

              {/* === OPEN LONG: Collateral Input + Amount Out === */}
              {tradeSide === "LONG" && tradeAction === "OPEN" && (
                <>
                  <InputGroup
                    label="Collateral"
                    subLabel={`Broker: ${brokerBalance != null ? `${parseFloat(brokerBalance).toFixed(1)} waUSDC` : hasBroker ? "..." : "—"}`}
                    value={collateral}
                    onChange={(v) => setCollateral(Number(v))}
                    suffix="USDC"
                    onMax={() => setCollateral(parseFloat(brokerBalance) || 0)}
                  />
                  {tradeSide === "LONG" && (
                    <InputGroup
                      label="Amount_Out"
                      subLabel={
                        <button
                          type="button"
                          onClick={refreshQuote}
                          className="inline-flex items-center gap-1 text-gray-500 hover:text-white transition-colors cursor-pointer"
                        >
                          <RefreshCw
                            size={10}
                            className={quoteLoading ? "animate-spin" : ""}
                          />
                        </button>
                      }
                      value={
                        swapQuote
                          ? parseFloat(swapQuote.amountOut.toFixed(4))
                          : ""
                      }
                      onChange={() => {}}
                      suffix="wRLP"
                      readOnly
                    />
                  )}
                </>
              )}

              {/* === CLOSE LONG: wRLP Input + waUSDC Out === */}
              {tradeSide === "LONG" && tradeAction === "CLOSE" && (
                <>
                  <InputGroup
                    label="Sell_wRLP"
                    subLabel={`Available: ${brokerWrlpBalance != null ? `${brokerWrlpBalance.toFixed(1)} wRLP` : "—"}`}
                    value={closeAmount}
                    onChange={(v) => setCloseAmount(v)}
                    suffix="wRLP"
                    onMax={() => setCloseAmount(String(brokerWrlpBalance || 0))}
                  />
                  <InputGroup
                    label="Amount_Out"
                    subLabel={
                      <button
                        type="button"
                        onClick={refreshQuote}
                        className="inline-flex items-center gap-1 text-gray-500 hover:text-white transition-colors cursor-pointer"
                      >
                        <RefreshCw
                          size={10}
                          className={quoteLoading ? "animate-spin" : ""}
                        />
                      </button>
                    }
                    value={
                      swapQuote
                        ? parseFloat(swapQuote.amountOut.toFixed(2))
                        : ""
                    }
                    onChange={() => {}}
                    suffix="waUSDC"
                    readOnly
                  />
                </>
              )}

              {/* === CLOSE SHORT: PAY WITH selector + mode-dependent inputs === */}
              {tradeSide === "SHORT" && tradeAction === "CLOSE" && (
                <>
                  {/* PAY WITH custom dropdown */}
                  <div className="flex items-center justify-between text-sm uppercase tracking-widest font-bold text-gray-500">
                    <span>Pay_With</span>
                    <div className="relative" ref={payDropdownRef}>
                      <button
                        type="button"
                        onClick={() => setPayDropdownOpen(!payDropdownOpen)}
                        className={`
                          h-[28px] border border-white/10 bg-[#0a0a0a] flex items-center justify-between px-2 gap-2
                          text-sm font-mono text-white focus:outline-none uppercase tracking-widest
                          hover:border-white/30 transition-colors
                          ${payDropdownOpen ? "border-white/30" : ""}
                        `}
                      >
                        <span>{closeShortRepayMode}</span>
                        <ChevronDown
                          size={12}
                          className={`transition-transform duration-200 flex-shrink-0 ${payDropdownOpen ? "rotate-180" : ""}`}
                        />
                      </button>
                      {payDropdownOpen && (
                        <div className="absolute top-full right-0 mt-1 bg-[#0a0a0a] border border-white/10 z-50 flex flex-col shadow-xl whitespace-nowrap">
                          {[
                            { value: "wRLP", label: "wRLP — Direct Repay" },
                            { value: "waUSDC", label: "waUSDC — Swap & Repay" },
                          ].map((opt) => {
                            const isSelected =
                              closeShortRepayMode === opt.value;
                            return (
                              <button
                                key={opt.value}
                                type="button"
                                onClick={() => {
                                  setCloseShortRepayMode(opt.value);
                                  setCloseShortDebt("");
                                  setCloseShortAmount("");
                                  setPayDropdownOpen(false);
                                }}
                                className={`
                                  w-full flex items-center px-3 py-2 text-sm text-left uppercase tracking-widest transition-colors
                                  ${
                                    isSelected
                                      ? "bg-cyan-500/10 text-cyan-400"
                                      : "text-gray-500 hover:bg-white/5 hover:text-gray-300"
                                  }
                                `}
                              >
                                {opt.label}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Debt_To_Repay — always shown */}
                  <InputGroup
                    label="Debt_To_Repay"
                    subLabel={`Total_Debt: ${brokerState?.debtPrincipal > 0 ? brokerState.debtPrincipal.toFixed(1) + " wRLP" : "—"}`}
                    value={closeShortDebt}
                    onChange={(v) => {
                      setCloseShortDebt(v);
                      setLastCloseShortEdit("debt");
                      if (closeShortRepayMode === "waUSDC") {
                        const num = parseFloat(v) || 0;
                        if (currentRate > 0) {
                          setCloseShortAmount(
                            num > 0 ? (num * currentRate).toFixed(2) : "",
                          );
                        }
                      } else {
                        // wRLP direct repay: amount = debt
                        setCloseShortAmount(v);
                      }
                    }}
                    suffix="wRLP"
                    onMax={() => {
                      const onChainDebt = brokerState?.debtPrincipal ?? 0;
                      setCloseShortDebt(String(onChainDebt));
                      setLastCloseShortEdit("debt");
                      if (
                        closeShortRepayMode === "waUSDC" &&
                        currentRate > 0 &&
                        onChainDebt > 0
                      ) {
                        setCloseShortAmount(
                          (onChainDebt * currentRate).toFixed(2),
                        );
                      } else {
                        // wRLP direct repay: amount = debt
                        setCloseShortAmount(String(onChainDebt));
                      }
                    }}
                  />

                  {/* Amount_To_Pay — only in waUSDC mode */}
                  {closeShortRepayMode === "waUSDC" && (
                    <InputGroup
                      label="Amount_To_Pay"
                      subLabel={`Broker: ${brokerBalance != null ? `${parseFloat(brokerBalance).toFixed(1)} waUSDC` : hasBroker ? "..." : "—"}`}
                      value={closeShortAmount}
                      onChange={(v) => {
                        setCloseShortAmount(v);
                        setLastCloseShortEdit("collateral");
                        const num = parseFloat(v) || 0;
                        if (currentRate > 0) {
                          setCloseShortDebt(
                            num > 0 ? (num / currentRate).toFixed(6) : "",
                          );
                        }
                      }}
                      suffix="waUSDC"
                      onMax={() => {
                        const max = String(parseFloat(brokerBalance) || 0);
                        setCloseShortAmount(max);
                        setLastCloseShortEdit("collateral");
                        if (currentRate > 0) {
                          setCloseShortDebt(
                            ((parseFloat(max) || 0) / currentRate).toFixed(6),
                          );
                        }
                      }}
                    />
                  )}

                  {/* wRLP mode: show broker wRLP balance info */}
                  {closeShortRepayMode === "wRLP" && (
                    <div className="flex justify-between text-sm uppercase tracking-widest font-bold text-gray-500">
                      <span>Broker_wRLP</span>
                      <span className="text-white font-mono">
                        {brokerWrlpBalance != null
                          ? brokerWrlpBalance.toFixed(1) + " wRLP"
                          : "—"}
                      </span>
                    </div>
                  )}
                </>
              )}

              {/* SHORT OPEN: Collateral, CR, computed Notional */}
              {tradeSide === "SHORT" && tradeAction === "OPEN" && (
                <>
                  <InputGroup
                    label="Collateral"
                    subLabel={`Broker: ${brokerBalance != null ? `${parseFloat(brokerBalance).toFixed(1)} waUSDC` : hasBroker ? "..." : "—"}`}
                    value={collateral}
                    onChange={(v) => setCollateral(Number(v))}
                    suffix="USDC"
                    onMax={() => setCollateral(parseFloat(brokerBalance) || 0)}
                  />

                  <InputGroup
                    label="Amount_Notional"
                    value={
                      shortAmount > 0 ? parseFloat(shortAmount.toFixed(6)) : ""
                    }
                    onChange={() => {}}
                    suffix="wRLP"
                    readOnly
                  />

                  <div className="space-y-2">
                    <div className="flex justify-between text-sm uppercase tracking-widest font-bold text-gray-500">
                      <span>Collateral_Ratio</span>
                      <span className="text-white">{shortCR.toFixed(0)}%</span>
                    </div>
                    <input
                      type="range"
                      min="150"
                      max="1500"
                      step="10"
                      value={shortCR}
                      onChange={(e) =>
                        handleShortCRChange(Number(e.target.value))
                      }
                      className="w-full h-0.5 bg-white/10 rounded-none appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-none"
                    />
                    <div className="flex justify-between text-sm text-gray-500 font-mono">
                      <span>150%</span>
                      <span>1500%</span>
                    </div>
                  </div>
                </>
              )}

              {/* Stats Box */}
              <div className="border border-white/10 p-4 space-y-2 bg-white/[0.02] text-sm">
                <SummaryRow
                  label={
                    tradeAction === "CLOSE" && tradeSide === "LONG"
                      ? "AVG_Rate"
                      : "AVG_Rate"
                  }
                  value={
                    tradeSide === "LONG" && swapQuote
                      ? `${swapQuote.entryRate.toFixed(4)}`
                      : `${currentRate.toFixed(4)}`
                  }
                />
                <div className="flex justify-between items-center">
                  <span className="text-gray-500 uppercase text-sm">
                    Liq. Rate
                  </span>
                  <span className="font-mono text-orange-500 text-sm">
                    {liqRate ? `${liqRate.toFixed(4)}` : "None"}
                  </span>
                </div>
                <SummaryRow
                  label="Notional"
                  value={
                    tradeSide === "LONG" && swapQuote
                      ? `$${swapQuote.notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                      : `$${notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                  }
                />

                {swapError && (
                  <div className="text-sm text-red-400 font-mono truncate mt-1">
                    {swapError}
                  </div>
                )}
              </div>
            </TradingTerminal>
          </div>

          {/* === BOTTOM ROW: YOUR POSITION | OPERATIONS + PLACEHOLDER === */}
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
            {/* Left: Position + Actions (col-span-9) */}
            <div className="xl:col-span-9 grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
            {/* Your Position (2/3 width) */}
            <div className="lg:col-span-2 border border-white/10 flex flex-col">
              <div className="px-6 py-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center h-[53px]">
                <h3 className="text-sm font-bold tracking-widest text-white uppercase flex items-center gap-2">
                  <Wallet size={14} className="text-gray-500" />
                  Your Position
                </h3>
                {account && (
                  <div className="relative">
                    <button
                      onClick={() => setAccountDropdown(!accountDropdown)}
                      className="flex items-center gap-1.5 text-sm font-mono text-gray-400 hover:text-white transition-colors"
                    >
                      Select Account
                      <ChevronDown size={12} className={`transition-transform ${accountDropdown ? "rotate-180" : ""}`} />
                    </button>
                    {accountDropdown && (
                      <div className="absolute right-0 top-full mt-2 z-50 border border-white/10 bg-[#0a0a0a] min-w-[200px]">
                        {[
                          { label: "Broker #1", addr: "0x1a2b...3c4d", active: true },
                          { label: "Broker #2", addr: "0x5e6f...7a8b", active: false },
                        ].map((b) => (
                          <button
                            key={b.addr}
                            onClick={() => setAccountDropdown(false)}
                            className={`w-full text-left px-4 py-2.5 text-sm font-mono hover:bg-white/5 transition-colors border-b border-white/5 flex items-center justify-between ${
                              b.active ? "text-cyan-400" : "text-gray-400"
                            }`}
                          >
                            <span>{b.label}</span>
                            <span className="text-xs text-gray-600">{b.addr}</span>
                          </button>
                        ))}
                        <button
                          onClick={() => setAccountDropdown(false)}
                          className="w-full text-left px-4 py-2.5 text-sm font-mono text-white hover:bg-white/5 transition-colors"
                        >
                          + Create New
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div className="flex-1">
                {!account ? (
                  <div className="text-sm text-gray-600 uppercase tracking-widest text-center py-12">
                    Connect wallet to view
                  </div>
                ) : (
                  <>
                    {/* Top metrics row */}
                    {(() => {
                      // totalAssets = all tracked and untracked assets in the broker
                      const totalAssets = brokerState ? brokerState.nav : null;

                      // NAV = true net value = totalAssets - debt
                      const netWorth = totalAssets !== null ? totalAssets - (brokerState.debtValue || 0) : null;

                      // Col. ratio uses totalAssets / debtValue
                      const totalColRatio = brokerState && brokerState.debtValue > 0
                        ? brokerState.colRatio
                        : Infinity;

                      return (
                        <div className="grid grid-cols-4 divide-x divide-white/10 border-b border-white/10">
                          {[
                            { label: "NAV", value: netWorth !== null ? `$${netWorth.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—", color: "text-white" },
                            { label: "Assets", value: totalAssets !== null ? `$${totalAssets.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—", color: "text-white" },
                            { label: "Debt Value", value: brokerState && brokerState.debtValue > 0 ? `$${brokerState.debtValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "$0", color: "text-red-400" },
                            { label: "Col. Ratio", value: totalColRatio === Infinity ? "∞" : `${totalColRatio.toFixed(0)}%`, color: totalColRatio < 150 ? "text-red-400" : totalColRatio < 200 ? "text-yellow-400" : "text-green-400" },
                          ].map((m) => (
                            <div key={m.label} className="p-4 text-center">
                              <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">{m.label}</div>
                              <div className={`text-lg font-light font-mono tracking-tight ${m.color}`}>{m.value}</div>
                            </div>
                          ))}
                        </div>
                      );
                    })()}

                    {/* Two-column: Assets | Debt */}
                    <div className="grid grid-cols-2 divide-x divide-white/10">
                      {/* Left: Assets */}
                      <div className="py-6 space-y-5">
                        {/* Column heading */}
                        <div className="flex items-center justify-between px-6">
                          <span className="text-sm text-gray-500 uppercase tracking-widest">Collateral</span>
                          <div className="flex items-center gap-3">
                            <div className="flex items-center gap-1.5">
                              <span className="w-1.5 h-1.5 rounded-full bg-cyan-500" />
                              <span className="text-xs text-gray-600">Tracked</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                              <span className="w-1.5 h-1.5 rounded-full bg-gray-600" />
                              <span className="text-xs text-gray-600">Untracked</span>
                            </div>
                          </div>
                        </div>

                        {/* Tokens */}
                        <div>
                          <div className="text-sm text-gray-500 uppercase tracking-widest mb-3 px-6">Tokens</div>
                          <div className="space-y-1">
                            {[
                              { name: "waUSDC", value: brokerState ? `$${brokerState.collateralBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—", tracked: true },
                              { name: "wRLP", value: brokerState ? `${(brokerState.wrlpTokenBalance ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—", tracked: true },
                            ].map((t) => (
                              <div key={t.name} className="relative">
                                <button
                                  onClick={() => setPositionDropdown(positionDropdown === `tk-${t.name}` ? null : `tk-${t.name}`)}
                                  className="w-full flex items-center justify-between py-1.5 hover:bg-white/5 px-6 transition-colors group"
                                >
                                  <div className="flex items-center gap-2">
                                    <span className={`w-1.5 h-1.5 rounded-full ${t.tracked ? "bg-cyan-500" : "bg-gray-600"}`} />
                                    <span className="text-sm font-mono text-white">{t.name}</span>
                                  </div>
                                  <div className="flex items-center gap-1">
                                    <span className="text-sm font-mono text-gray-400">{t.value}</span>
                                    <ChevronDown size={12} className={`text-gray-600 group-hover:text-gray-400 transition-all ${positionDropdown === `tk-${t.name}` ? "rotate-180" : ""}`} />
                                  </div>
                                </button>
                                {positionDropdown === `tk-${t.name}` && (
                                  <div className="border border-white/10 bg-[#0a0a0a] mb-1">
                                    <button
                                      onClick={() => {
                                        setPositionDropdown(null);
                                        setDepositToken(t.name);
                                      }}
                                      className="w-full text-left px-4 py-2 text-sm font-mono text-cyan-400 hover:bg-cyan-500/5 transition-colors"
                                    >
                                      Deposit
                                    </button>
                                    <button
                                      onClick={() => {
                                        setPositionDropdown(null);
                                        setWithdrawToken(t.name);
                                      }}
                                      className="w-full text-left px-4 py-2 text-sm font-mono text-orange-400 hover:bg-orange-500/5 transition-colors border-t border-white/5"
                                    >
                                      Withdraw
                                    </button>
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* LP Positions */}
                        <div>
                          <div className="text-sm text-gray-500 uppercase tracking-widest mb-3 px-6">LP Positions</div>
                          <div className="space-y-1">
                            {(brokerState?.lpPositions?.length > 0) ? brokerState.lpPositions.map((lp) => {
                              const lpKey = `lp-${lp.tokenId}`;
                              return (
                                <div key={lpKey} className="relative">
                                  <button
                                    onClick={() => setPositionDropdown(positionDropdown === lpKey ? null : lpKey)}
                                    className="w-full flex items-center justify-between py-1.5 hover:bg-white/5 px-6 transition-colors group"
                                  >
                                    <div className="flex items-center gap-2">
                                      <span className={`w-1.5 h-1.5 rounded-full ${lp.isActive ? "bg-cyan-500" : "bg-gray-600"}`} />
                                      {lp.priceLower && (
                                        <span className="text-sm text-gray-600 font-mono">{lp.priceLower} — {lp.priceUpper}</span>
                                      )}
                                      {lp.inRange !== undefined && (
                                        <span className={`text-xs px-1.5 py-0.5 font-mono ${lp.inRange ? "text-green-400 bg-green-500/10" : "text-orange-400 bg-orange-500/10"}`}>
                                          {lp.inRange ? "IN RANGE" : "OUT"}
                                        </span>
                                      )}
                                    </div>
                                    <div className="flex items-center gap-1">
                                      <span className="text-sm font-mono text-gray-400">${(lp.valueUsd || lp.value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                                      <ChevronDown size={12} className={`text-gray-600 group-hover:text-gray-400 transition-all ${positionDropdown === lpKey ? "rotate-180" : ""}`} />
                                    </div>
                                  </button>
                                  {positionDropdown === lpKey && (
                                    <div className="border border-white/10 bg-[#0a0a0a] mb-1">
                                      <div className="px-4 py-2 text-xs text-gray-600 font-mono space-y-1">
                                        {lp.priceLower && (
                                          <>
                                            <div className="flex justify-between"><span>Range</span><span>{lp.priceLower} — {lp.priceUpper}</span></div>
                                            <div className="flex justify-between"><span>Current Price</span><span>{lp.currentPrice}</span></div>
                                          </>
                                        )}
                                        {lp.amount0 !== undefined && (
                                          <>
                                            <div className="flex justify-between"><span>Token0 (wRLP)</span><span>{lp.amount0.toFixed(2)}</span></div>
                                            <div className="flex justify-between"><span>Token1 (waUSDC)</span><span>{lp.amount1.toFixed(2)}</span></div>
                                          </>
                                        )}
                                        {lp.entryPrice && (
                                          <div className="flex justify-between"><span>Entry Price</span><span>{lp.entryPrice}</span></div>
                                        )}
                                        <div className="flex justify-between"><span>Value</span><span className="text-cyan-400">${(lp.valueUsd || 0).toFixed(2)}</span></div>
                                        {lp.isActive && (
                                          <div className="flex justify-between border-t border-white/5 pt-1 mt-1"><span>Status</span><span className="text-cyan-400">ACTIVE (tracked)</span></div>
                                        )}
                                      </div>
                                      {/* Claim Fees */}
                                      <button
                                        onClick={() => {
                                          setPositionDropdown(null);
                                          setClaimFeesLp({
                                            id: lp.tokenId?.toString(),
                                            tokenId: lp.tokenId,
                                            priceLower: parseFloat(lp.priceLower),
                                            priceUpper: parseFloat(lp.priceUpper),
                                            feesEarned0: "0.00",
                                            feesEarned1: "0.00",
                                          });
                                        }}
                                        className="w-full text-left px-4 py-2 text-sm font-mono text-green-400 hover:bg-green-500/5 transition-colors border-t border-white/5"
                                      >
                                        Claim Fees
                                      </button>
                                      {/* Track / Untrack as collateral */}
                                      {!lp.isActive ? (
                                        <button
                                          onClick={() => {
                                            setPositionDropdown(null);
                                            setCollateralConfirm({
                                              type: 'track-lp',
                                              label: `Track LP #${lp.tokenId?.toString()} as collateral?`,
                                              sub: `Range: ${lp.priceLower || '?'} — ${lp.priceUpper || '?'}  •  Value: $${(lp.valueUsd || 0).toFixed(0)}`,
                                              data: lp,
                                            });
                                          }}
                                          className="w-full text-left px-4 py-2 text-sm font-mono text-cyan-400 hover:bg-cyan-500/5 transition-colors border-t border-white/5"
                                        >
                                          Track as Collateral
                                        </button>
                                      ) : (
                                        <button
                                          onClick={() => {
                                            setPositionDropdown(null);
                                            setCollateralConfirm({
                                              type: 'untrack-lp',
                                              label: `Untrack LP #${lp.tokenId?.toString()} from collateral?`,
                                              sub: 'This LP position will no longer count toward your collateral ratio.',
                                              data: lp,
                                            });
                                          }}
                                          className="w-full text-left px-4 py-2 text-sm font-mono text-orange-400 hover:bg-orange-500/5 transition-colors border-t border-white/5"
                                        >
                                          Untrack from Collateral
                                        </button>
                                      )}
                                      {/* Withdraw (Remove Liquidity) */}
                                      <button
                                        onClick={() => {
                                          setPositionDropdown(null);
                                          setWithdrawLp({
                                            id: lp.tokenId?.toString(),
                                            tokenId: lp.tokenId,
                                            priceLower: parseFloat(lp.priceLower),
                                            priceUpper: parseFloat(lp.priceUpper),
                                            token0Amount: lp.amount0?.toFixed(2) || "0",
                                            token1Amount: lp.amount1?.toFixed(2) || "0",
                                            feesEarned0: "0.00",
                                            feesEarned1: "0.00",
                                            value: lp.valueUsd || 0,
                                          });
                                        }}
                                        className="w-full text-left px-4 py-2 text-sm font-mono text-red-400 hover:bg-red-500/5 transition-colors border-t border-white/5"
                                      >
                                        Withdraw Liquidity
                                      </button>
                                    </div>
                                  )}
                                </div>
                              );
                            }) : (
                              <div className="text-sm text-gray-600 font-mono px-6 py-2">No LP positions</div>
                            )}
                          </div>
                        </div>

                        {/* TWAMM Orders */}
                        <div>
                          <div className="text-sm text-gray-500 uppercase tracking-widest mb-3 px-6">TWAMM Orders</div>
                          <div className="space-y-1">
                            {twammOrders.length === 0 ? (
                              <div className="text-sm text-gray-600 font-mono px-6 py-4">No active orders</div>
                            ) : twammOrders.map((tw, i) => (
                              <div key={tw.orderId || i} className="relative">
                                <button
                                  onClick={() => setPositionDropdown(positionDropdown === `tw-${i}` ? null : `tw-${i}`)}
                                  className="w-full hover:bg-white/5 py-1.5 px-6 transition-colors group"
                                >
                                  <div className="flex items-center justify-between mb-1">
                                    <div className="flex items-center gap-2">
                                      <span className={`w-1.5 h-1.5 rounded-full ${tw.tracked ? "bg-cyan-500" : "bg-gray-600"}`} />
                                      <span className="text-sm font-mono text-white">{tw.direction}</span>
                                    </div>
                                    <div className="flex items-center gap-1">
                                      <span className="text-sm font-mono text-gray-400">${tw.valueUsd.toFixed(0)}</span>
                                      <ChevronDown size={12} className={`text-gray-600 group-hover:text-gray-400 transition-all ${positionDropdown === `tw-${i}` ? "rotate-180" : ""}`} />
                                    </div>
                                  </div>
                                  <div className="flex items-center gap-3">
                                    <div className="flex-1 h-1 bg-white/5 overflow-hidden">
                                      <div
                                        className={`h-full ${tw.tracked ? "bg-cyan-500/40" : "bg-white/10"}`}
                                        style={{ width: `${tw.progress}%` }}
                                      />
                                    </div>
                                    <span className="text-sm text-gray-600 font-mono whitespace-nowrap">
                                      {tw.progress}% · {tw.timeLeft}
                                    </span>
                                  </div>
                                </button>
                                {positionDropdown === `tw-${i}` && (
                                  <div className="border border-white/10 bg-[#0a0a0a] mb-1">
                                    <div className="px-4 py-2 text-xs text-gray-600 font-mono space-y-1 border-b border-white/5">
                                      <div className="flex justify-between"><span>Deposit</span><span className="text-gray-400">{tw.amountIn.toFixed(2)} {tw.sellToken}</span></div>
                                      <div className="flex justify-between"><span>Converted</span><span>{tw.tokensSpent > 0 ? <><span className="text-gray-400">{tw.tokensSpent.toFixed(2)} {tw.sellToken}</span><span className="text-gray-600"> → </span><span className="text-green-400">{tw.convertedBuyEstimate.toFixed(4)} {tw.buyToken}</span></> : <span className="text-gray-600">—</span>}</span></div>
                                      {tw.sellRefund > 0 && (
                                        <div className="flex justify-between"><span>Unsold</span><span className="text-gray-400">{tw.sellRefund.toFixed(2)} {tw.sellToken}</span></div>
                                      )}
                                      <div className="flex justify-between border-t border-white/5 pt-1 mt-1"><span>Order Value</span><span className="text-white">${tw.valueUsd.toFixed(2)}</span></div>
                                    </div>
                                    {!tw.isDone && (
                                      <button
                                        onClick={() => {
                                          setPositionDropdown(null);
                                          cancelTwammOrder(() => {
                                            refresh();
                                            addToast({ type: "success", title: "Order Cancelled" });
                                            refresh();
                                          });
                                        }}
                                        disabled={cancellingTwamm}
                                        className="w-full text-left px-4 py-2 text-sm font-mono text-red-400 hover:bg-white/5 transition-colors"
                                      >
                                        {cancellingTwamm ? "Cancelling..." : "Cancel Order"}
                                      </button>
                                    )}
                                    {/* Track / Untrack as collateral */}
                                    {!tw.isDone && !tw.tracked && (
                                      <button
                                        onClick={() => {
                                          setPositionDropdown(null);
                                          setCollateralConfirm({
                                            type: 'track-twamm',
                                            label: `Track this TWAMM order as collateral?`,
                                            sub: `${tw.direction}  •  $${tw.valueUsd.toFixed(0)}  •  ${tw.progress}% complete`,
                                            data: tw,
                                          });
                                        }}
                                        className="w-full text-left px-4 py-2 text-sm font-mono text-cyan-400 hover:bg-cyan-500/5 transition-colors border-t border-white/5"
                                      >
                                        Track as Collateral
                                      </button>
                                    )}
                                    {tw.tracked && (
                                      <button
                                        onClick={() => {
                                          setPositionDropdown(null);
                                          setCollateralConfirm({
                                            type: 'untrack-twamm',
                                            label: `Untrack this TWAMM order from collateral?`,
                                            sub: 'This order will no longer count toward your collateral ratio.',
                                            data: tw,
                                          });
                                        }}
                                        className="w-full text-left px-4 py-2 text-sm font-mono text-orange-400 hover:bg-orange-500/5 transition-colors border-t border-white/5"
                                      >
                                        Untrack from Collateral
                                      </button>
                                    )}
                                    {tw.isDone && (
                                      <button
                                        onClick={() => {
                                          setPositionDropdown(null);
                                          setClaimConfirm(tw);
                                        }}
                                        className="w-full text-left px-4 py-2 text-sm font-mono text-green-400 hover:bg-white/5 transition-colors"
                                      >
                                        Claim Tokens
                                      </button>
                                    )}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>

                      </div>

                      {/* Right: Debt */}
                      <div className="p-6 space-y-4">
                        <div className="text-sm text-gray-500 uppercase tracking-widest mb-3">Debt</div>
                        {[
                          { label: "Principal", value: brokerState ? `${brokerState.debtPrincipal.toFixed(2)} wRLP` : "—", color: "text-white" },
                          { label: "True Debt", value: brokerState ? `${brokerState.trueDebt.toFixed(2)} wRLP` : "—", color: "text-white" },
                          { label: "Debt Value", value: brokerState && brokerState.debtValue > 0 ? `$${brokerState.debtValue.toFixed(2)}` : "$0.00", color: "text-red-400" },
                        ].map((d) => (
                          <div key={d.label} className="flex justify-between items-center">
                            <span className="text-sm text-gray-500 uppercase tracking-widest">{d.label}</span>
                            <span className={`text-sm font-mono font-bold ${d.color}`}>{d.value}</span>
                          </div>
                        ))}

                        {/* Risk gauge */}
                        <div className="pt-4 mt-4 border-t border-white/5 space-y-3">
                          <div className="text-sm text-gray-500 uppercase tracking-widest">Risk</div>
                          <div>
                            <div className="flex justify-between mb-1">
                              <span className="text-sm text-gray-600">Min Col. Ratio</span>
                              <span className="text-sm font-mono text-gray-400">{marketInfo?.risk_params?.min_col_ratio_pct || "—"}</span>
                            </div>
                            <div className="flex justify-between mb-1">
                              <span className="text-sm text-gray-600">Maintenance</span>
                              <span className="text-sm font-mono text-gray-400">{marketInfo?.risk_params?.maintenance_margin_pct || "—"}</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-sm text-gray-600">Your Ratio</span>
                              <span className={`text-sm font-mono font-bold ${brokerState && brokerState.colRatio < 150 ? "text-red-400" : brokerState && brokerState.colRatio < 200 ? "text-yellow-400" : "text-green-400"}`}>
                                {brokerState ? (brokerState.colRatio === Infinity ? "∞" : `${brokerState.colRatio.toFixed(0)}%`) : "—"}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Actions panel (1/3 of col-span-9) */}
            <div ref={actionsRef} className="border border-white/10 flex flex-col">
                <div className="px-6 py-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center h-[53px]">
                  <h3 className="text-sm font-bold tracking-widest text-white uppercase flex items-center gap-2">
                    <Layers size={14} className="text-gray-500" />
                    Actions
                  </h3>
                  <span className="text-sm text-gray-700 tracking-[0.15em]">::EXEC</span>
                </div>
                <div className="p-4 flex flex-col gap-2">
                  {[
                    { id: "mint", label: "Mint", desc: "Mint wRLP from collateral" },
                    { id: "twap", label: "TWAP", desc: "Time-weighted swap" },
                    { id: "lp", label: "LP", desc: "Provide liquidity" },
                    { id: "loop", label: "Loop", desc: "Leveraged position", soon: true },
                    { id: "batch", label: "Batch", desc: "Multi-action bundle", soon: true },
                  ].map((action) => (
                    <React.Fragment key={action.id}>
                    <button
                      onClick={() => !action.soon && setActiveAction(activeAction === action.id ? null : action.id)}
                      className={`w-full flex items-center justify-between px-4 py-3 transition-all text-left group ${
                        action.soon ? "opacity-40 cursor-default" :
                        activeAction === action.id ? "bg-white/5 hover:bg-white/5" : "hover:bg-white/5"
                      }`}
                    >
                      <div>
                        <div className={`text-sm font-bold uppercase tracking-widest transition-colors flex items-center gap-2 ${
                          action.soon ? "text-gray-500" :
                          activeAction === action.id ? "text-cyan-400" : "text-white group-hover:text-cyan-400"
                        }`}>
                          {action.label}
                          {action.soon && (
                            <span className="text-[9px] px-1.5 py-0.5 bg-white/5 border border-white/10 text-gray-500 tracking-[0.15em] font-medium">
                              SOON
                            </span>
                          )}
                        </div>
                        <div className={`text-sm font-mono mt-0.5 ${action.soon ? "text-gray-700" : "text-gray-600"}`}>
                          {action.desc}
                        </div>
                      </div>
                      {!action.soon && (
                        <ChevronDown size={14} className={`transition-all ${
                          activeAction === action.id
                            ? "text-cyan-400 rotate-0"
                            : "text-gray-600 group-hover:text-cyan-400 -rotate-90"
                        }`} />
                      )}
                    </button>
                    {activeAction === action.id && !action.soon && (
                      <ActionForm
                        type={action.id}
                        onClose={() => setActiveAction(null)}
                        brokerBalance={brokerBalance}
                        brokerWrlpBalance={brokerWrlpBalance}
                        currentRate={currentRate}
                        brokerAddress={brokerAddress}
                        marketId={market?.marketId}
                        account={account}
                        addToast={addToast}
                        marketInfo={marketInfo}
                        onStateChange={refresh}
                        txPauseRef={txPauseRef}
                        onTwammRefresh={refresh}
                      />
                    )}
                    </React.Fragment>
                  ))}
                </div>
            </div>
            </div>

            {/* Right: Operations panel (col-span-3) */}
            <div className="xl:col-span-3">
              <div className="border border-white/10 flex flex-col" style={actionsHeight && !activeAction ? { maxHeight: actionsHeight, overflow: 'hidden' } : undefined}>
              <div className="px-6 py-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center h-[53px]">
                <h3 className="text-sm font-bold tracking-widest text-white uppercase flex items-center gap-2">
                  <Activity size={14} className="text-gray-500" />
                  Operations
                </h3>
                {operations.length > 0 && (
                  <button
                    onClick={() => {
                      const header = "Date,Type,Amount_In,Amount_Out,Tx_Hash";
                      const rows = operations.map((op) => {
                        const date = new Date(
                          op.timestamp * 1000,
                        ).toISOString();
                        const amtIn = (Number(op.args[1]) / 1e6).toFixed(2);
                        const amtOut = (Number(op.args[2]) / 1e6).toFixed(2);
                        return `${date},${op.label},${amtIn},${amtOut},${op.txHash}`;
                      });
                      const csv = [header, ...rows].join("\n");
                      const blob = new Blob([csv], { type: "text/csv" });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = "rld_operations.csv";
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                    className="text-sm font-mono font-bold tracking-widest uppercase px-2 py-1 border border-white/10 text-gray-500 hover:text-white hover:border-white/30 transition-all flex items-center gap-1.5"
                  >
                    <Download size={10} />
                    CSV
                  </button>
                )}
              </div>
              <div className="p-6 flex-1 overflow-y-auto">
                <OperationsFeed
                  operations={operations}
                  loading={false}
                  connected={!!account}
                />
              </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Account Onboarding Modal */}
      <AccountModal
        isOpen={showAccountModal}
        onClose={() => setShowAccountModal(false)}
        onComplete={(addr) => {
          setShowAccountModal(false);
          // Re-check broker ownership so hasBroker flips to true
          checkBroker();
          // Refresh broker state & show toast
          if (addr) {
                        refresh();
            addToast({
              type: "success",
              title: "Account Created",
              message: `Broker deployed & funded successfully`,
              duration: 5000,
            });
          }
        }}
        brokerFactoryAddr={marketInfo?.broker_factory}
        waUsdcAddr={marketInfo?.collateral?.address}
        externalContracts={marketInfo?.external_contracts}
      />
      {/* Swap Confirmation Modal */}
      {/* PnL Calculator Modal */}
      <PnlCalculatorModal
        isOpen={pnlModalOpen}
        onClose={() => setPnlModalOpen(false)}
        currentRate={currentRate}
      />
      {/* Swap Confirmation Modal */}
      <SwapConfirmModal
        isOpen={showSwapConfirm}
        onClose={() => setShowSwapConfirm(false)}
        onConfirm={() => {
          if (tradeAction === "CLOSE" && tradeSide === "LONG") {
            // Close Long flow
            executeCloseLong(parseFloat(closeAmount), () => {
              setShowSwapConfirm(false);
                            refresh();
              addToast({
                type: "success",
                title: "Long Closed",
                message: `Sold ${parseFloat(closeAmount).toLocaleString()} wRLP for waUSDC`,
                duration: 5000,
              });
              setCloseAmount("");
            });
          } else if (tradeAction === "CLOSE" && tradeSide === "SHORT") {
            // Close Short flow
            if (closeShortRepayMode === "wRLP") {
              // Direct repay: burn wRLP to reduce debt
              executeRepayDebt(parseFloat(closeShortDebt), () => {
                setShowSwapConfirm(false);
                                refresh();
                addToast({
                  type: "success",
                  title: "Debt Repaid",
                  message: `Repaid ${parseFloat(closeShortDebt).toLocaleString()} wRLP debt directly`,
                  duration: 5000,
                });
                setCloseShortDebt("");
                setCloseShortAmount("");
              });
            } else {
              // waUSDC mode: spend waUSDC to buy wRLP and repay debt
              executeCloseShort(parseFloat(closeShortAmount), () => {
                setShowSwapConfirm(false);
                                refresh();
                addToast({
                  type: "success",
                  title: "Short Closed",
                  message: `Spent ${parseFloat(closeShortAmount).toLocaleString()} waUSDC to repay wRLP debt`,
                  duration: 5000,
                });
                setCloseShortAmount("");
              });
            }
          } else if (tradeSide === "SHORT" && tradeAction === "OPEN") {
            // Open Short flow — shortAmount is already in wRLP
            executeShort(collateral, shortAmount, () => {
              setShowSwapConfirm(false);
                            refresh();
              addToast({
                type: "success",
                title: "Short Opened",
                message: `Shorted ${notional.toLocaleString()} USDC notional at ${shortCR.toFixed(0)}% CR`,
                duration: 5000,
              });
            });
          } else {
            // Open Long flow
            executeLong(collateral, () => {
              setShowSwapConfirm(false);
                            refresh();
              addToast({
                type: "success",
                title: "Long Opened",
                message: `Swapped ${Number(collateral).toLocaleString()} waUSDC for wRLP`,
                duration: 5000,
              });
            });
          }
        }}
        tradeSide={tradeSide}
        tradeAction={tradeAction}
        collateral={
          tradeAction === "CLOSE" && tradeSide === "LONG"
            ? parseFloat(closeAmount) || 0
            : tradeAction === "CLOSE" && tradeSide === "SHORT"
              ? parseFloat(closeShortAmount) || 0
              : collateral
        }
        amountOut={
          tradeSide === "SHORT" && tradeAction === "OPEN"
            ? shortAmount
            : swapQuote?.amountOut
        }
        entryRate={currentRate}
        liqRate={liqRate}
        notional={tradeSide === "SHORT" ? notional : undefined}
        shortCR={tradeSide === "SHORT" ? shortCR : undefined}
        fee={
          swapQuote?.gasEstimate
            ? ((swapQuote.gasEstimate * 30e9) / 1e18) * 2500
            : 0
        }
        executing={swapExecuting}
        executionStep={swapStep}
        executionError={swapError}
        repayMode={tradeAction === "CLOSE" && tradeSide === "SHORT" && closeShortRepayMode === "wRLP"}
        repayAmount={parseFloat(closeShortDebt) || 0}
        currentDebt={brokerState?.trueDebt || 0}
      />

      {/* ── Collateral registration confirmation modal ── */}
      {collateralConfirm && (
        <CollateralConfirmModal
          label={collateralConfirm.label}
          sub={collateralConfirm.sub}
          onCancel={() => setCollateralConfirm(null)}
          onConfirm={async () => {
            const { type, data } = collateralConfirm;
            setCollateralConfirm(null);

            if (type === 'track-twamm') {
              trackTwammOrder(data, () => {
                refresh();
                refresh();
                addToast({ type: 'success', title: 'Order tracked as collateral' });
              });
            } else if (type === 'untrack-twamm') {
              untrackTwammOrder(() => {
                refresh();
                refresh();
                addToast({ type: 'success', title: 'Order untracked from collateral' });
              });
            } else if (type === 'track-lp') {
              try {
                const signer = await getSigner();
                const broker = new ethers.Contract(brokerAddress, [
                  'function setActiveV4Position(uint256 newTokenId) external',
                ], signer);
                const tx = await broker.setActiveV4Position(data.tokenId, { gasLimit: 300_000n });
                await tx.wait();
                refresh();
                addToast({ type: 'success', title: 'LP tracked as collateral' });
              } catch (e) {
                console.error('[LP] track failed:', e);
                addToast({ type: 'error', title: e.reason || e.shortMessage || 'Track failed' });
              }
            } else if (type === 'untrack-lp') {
              try {
                const signer = await getSigner();
                const broker = new ethers.Contract(brokerAddress, [
                  'function setActiveV4Position(uint256 newTokenId) external',
                ], signer);
                const tx = await broker.setActiveV4Position(0, { gasLimit: 300_000n });
                await tx.wait();
                refresh();
                addToast({ type: 'success', title: 'LP untracked from collateral' });
              } catch (e) {
                console.error('[LP] untrack failed:', e);
                addToast({ type: 'error', title: e.reason || e.shortMessage || 'Untrack failed' });
              }
            } else if (type === 'claim-fees') {
              // handled by ClaimFeesModal now
            } else if (type === 'withdraw-lp') {
              // handled by WithdrawModal now
            }
          }}
        />
      )}

      {/* ── Claim Tokens confirmation modal ── */}
      {claimConfirm && (
        <ClaimConfirmModal
          order={claimConfirm}
          executing={cancellingTwamm}
          onCancel={() => !cancellingTwamm && setClaimConfirm(null)}
          onConfirm={() => {
            claimTwammOrder(claimConfirm, () => {
              setClaimConfirm(null);
              refresh();
              refresh();
              addToast({ type: "success", title: "Tokens Claimed", message: "Expired order tokens returned to broker" });
            });
          }}
        />
      )}

      {/* Claim Fees Modal (same design as Pool page) */}
      <ClaimFeesModal
        isOpen={!!claimFeesLp}
        onClose={() => {
          if (!lpExecuting) {
            setClaimFeesLp(null);
            clearLpError();
          }
        }}
        onConfirm={() => {
          if (!claimFeesLp?.tokenId) return;
          executeCollectFees(
            claimFeesLp.tokenId,
            () => {
              setClaimFeesLp(null);
              refresh();
              addToast({ type: "success", title: "Fees Collected", message: `Collected fees from LP #${claimFeesLp.id}` });
            },
          );
        }}
        position={claimFeesLp}
        token0={{ symbol: "wRLP" }}
        token1={{ symbol: "waUSDC" }}
        executing={lpExecuting}
        executionStep={lpStep}
        executionError={lpError}
      />

      {/* Withdraw Modal (same design as Pool page) */}
      <WithdrawModal
        isOpen={!!withdrawLp}
        onClose={() => {
          if (!lpExecuting) {
            setWithdrawLp(null);
            clearLpError();
          }
        }}
        onConfirm={(percent) => {
          if (!withdrawLp?.tokenId) return;
          executeRemoveLiquidity(
            withdrawLp.tokenId,
            percent,
            () => {
              const posId = withdrawLp.id;
              setWithdrawLp(null);
              refresh();
              addToast({ type: "success", title: "Liquidity Removed", message: `Removed ${percent}% from LP #${posId}` });
            },
          );
        }}
        position={withdrawLp}
        token0={{ symbol: "wRLP" }}
        token1={{ symbol: "waUSDC" }}
        executing={lpExecuting}
        executionStep={lpStep}
        executionError={lpError}
      />

      {/* Deposit Modal */}
      <DepositModal
        isOpen={!!depositToken}
        onClose={() => setDepositToken(null)}
        brokerAddress={brokerAddress}
        tokenAddress={
          depositToken === "waUSDC"
            ? marketInfo?.collateral?.address
            : enrichedMarketInfo?.position_token?.address
        }
        tokenSymbol={depositToken || "waUSDC"}
        tokenDecimals={6}
        txPauseRef={txPauseRef}
        onSuccess={() => {
          refresh();
          addToast({ type: "success", title: `${depositToken} Deposited`, message: "Tokens transferred to broker" });
        }}
      />

      {/* Broker Withdraw Modal */}
      <BrokerWithdrawModal
        isOpen={!!withdrawToken}
        onClose={() => setWithdrawToken(null)}
        brokerAddress={brokerAddress}
        tokenSymbol={withdrawToken || "waUSDC"}
        brokerTokenBalance={
          withdrawToken === "waUSDC"
            ? parseFloat(brokerBalance || 0)
            : brokerWrlpBalance ?? 0
        }
        txPauseRef={txPauseRef}
        onSuccess={() => {
          refresh();
          addToast({ type: "success", title: `${withdrawToken} Withdrawn`, message: "Tokens sent to your wallet" });
        }}
      />
    </>
  );
}

// ── Collateral Registration Confirmation Modal ────────────────

function CollateralConfirmModal({ label, sub, onConfirm, onCancel }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onCancel}>
      <div
        className="bg-[#0a0b0d] border border-white/10 shadow-2xl w-full max-w-sm mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-white/5">
          <div className="text-sm font-mono text-white font-bold">{label}</div>
          {sub && <div className="text-xs font-mono text-gray-500 mt-1">{sub}</div>}
        </div>
        <div className="px-5 py-4 text-xs text-gray-500 font-mono">
          This will send a transaction to update which position is counted toward your collateral ratio. A solvency check will be performed.
        </div>
        <div className="flex border-t border-white/5">
          <button
            onClick={onCancel}
            disabled={busy}
            className="flex-1 px-4 py-3 text-sm font-mono text-gray-400 hover:bg-white/5 transition-colors border-r border-white/5"
          >
            Cancel
          </button>
          <button
            onClick={async () => {
              setBusy(true);
              try { await onConfirm(); } finally { setBusy(false); }
            }}
            disabled={busy}
            className="flex-1 px-4 py-3 text-sm font-mono text-cyan-400 hover:bg-cyan-500/5 transition-colors font-bold"
          >
            {busy ? "Sending..." : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ClaimConfirmModal({ order, executing, onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onCancel}>
      <div
        className="bg-[#0a0b0d] border border-white/10 shadow-2xl w-full max-w-sm mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-white/5">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]" />
            <span className="text-sm font-mono text-white font-bold">Claim Expired Order</span>
          </div>
        </div>

        {/* Order details */}
        <div className="px-5 py-4 space-y-2 text-xs font-mono">
          <div className="flex justify-between">
            <span className="text-gray-500">Direction</span>
            <span className="text-white">{order.direction}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Deposit</span>
            <span className="text-gray-400">{order.amountIn.toFixed(2)} {order.sellToken}</span>
          </div>
          {order.earned > 0 && (
            <div className="flex justify-between">
              <span className="text-gray-500">Earned</span>
              <span className="text-green-400">{order.earned.toFixed(4)} {order.buyToken}</span>
            </div>
          )}
          {order.sellRefund > 0 && (
            <div className="flex justify-between">
              <span className="text-gray-500">Unsold Refund</span>
              <span className="text-gray-400">{order.sellRefund.toFixed(2)} {order.sellToken}</span>
            </div>
          )}
          <div className="flex justify-between border-t border-white/5 pt-2">
            <span className="text-gray-500">Total Value</span>
            <span className="text-white">${order.valueUsd.toFixed(2)}</span>
          </div>
        </div>

        {/* Description */}
        <div className="px-5 pb-4 text-xs text-gray-500 font-mono">
          Tokens will be returned to your broker account.
        </div>

        {/* Actions */}
        <div className="flex border-t border-white/5">
          <button
            onClick={onCancel}
            disabled={executing}
            className="flex-1 px-4 py-3 text-sm font-mono text-gray-400 hover:bg-white/5 transition-colors border-r border-white/5"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={executing}
            className="flex-1 px-4 py-3 text-sm font-mono text-green-400 hover:bg-green-500/5 transition-colors font-bold"
          >
            {executing ? "Claiming..." : "Confirm Claim"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────

function _formatLiquidity(val) {
  if (val >= 1e18) return `${(val / 1e18).toFixed(1)}E`;
  if (val >= 1e15) return `${(val / 1e15).toFixed(1)}P`;
  if (val >= 1e12) return `${(val / 1e12).toFixed(1)}T`;
  if (val >= 1e9) return `${(val / 1e9).toFixed(1)}B`;
  if (val >= 1e6) return `${(val / 1e6).toFixed(1)}M`;
  if (val >= 1e3) return `${(val / 1e3).toFixed(1)}K`;
  return val.toLocaleString();
}

function _formatDebt(val) {
  if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}
