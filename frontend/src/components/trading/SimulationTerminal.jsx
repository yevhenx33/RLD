import React, { useState, useMemo, useEffect, useRef } from "react";
import { ethers } from "ethers";
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
import { useSimulation } from "../../hooks/useSimulation";
import { useChartControls } from "../../hooks/useChartControls";
import { useWallet } from "../../context/WalletContext";

import { useBrokerAccount } from "../../hooks/useBrokerAccount";
import { useSwapQuote } from "../../hooks/useSwapQuote";
import { useSwapExecution } from "../../hooks/useSwapExecution";
import { useOperations, formatOpAmount } from "../../hooks/useOperations";
import { useTwammPositions } from "../../hooks/useTwammPositions";
import { useTwammOrder } from "../../hooks/useTwammOrder";
import { useBrokerState } from "../../hooks/useBrokerState";
import AccountModal from "../modals/AccountModal";
import SwapConfirmModal from "../modals/SwapConfirmModal";
import { ToastContainer } from "../common/Toast";
import { useToast } from "../../hooks/useToast";
import RLDPerformanceChart from "../charts/RLDChart";
import ChartControlBar from "../charts/ChartControlBar";
import ControlCell from "../common/ControlCell";
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
  const sim = useSimulation({ pollInterval: 2000 });
  const {
    connected,
    loading,
    error,
    market,
    pool,
    funding,
    fundingFromNF,
    oracleChange24h,
    volumeData,
    protocolStats,
    marketInfo,
    brokers,
    chartData,
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
  // Broker account
  const {
    hasBroker,
    brokerAddress,
    brokerBalance,
    creating: _brokerCreating,
    fetchBrokerBalance,
    checkBroker,
  } = useBrokerAccount(
    account,
    marketInfo?.broker_factory,
    marketInfo?.collateral?.address,
  );

  // User operations (on-chain events from BrokerRouter)
  const { operations, loading: opsLoading } = useOperations(
    enrichedMarketInfo?.infrastructure?.broker_router,
    brokerAddress,
  );

  // TWAMM positions (on-chain orders from JTM hook)
  const { orders: twammOrders, refresh: refreshTwamm } = useTwammPositions(
    brokerAddress,
    marketInfo,
    30000,
    market?.indexPrice,
  );

  // TWAMM order actions (cancel + claim)
  const { cancelOrder: cancelTwammOrder, claimExpiredOrder: claimTwammOrder, executing: cancellingTwamm } = useTwammOrder(
    account,
    brokerAddress,
    marketInfo?.infrastructure,
    marketInfo?.collateral?.address,
    marketInfo?.position_token?.address,
  );

  // Broker full state (NAV, debt, health, balances)
  const { brokerState, refresh: refreshBrokerState } = useBrokerState(
    brokerAddress,
    marketInfo,
  );

  // Trading State (must be declared before swap hooks that reference tradeSide/collateral)
  const [tradeSide, setTradeSide] = useState("LONG");
  const [activeAction, setActiveAction] = useState(null);
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
        const provider = new ethers.JsonRpcProvider(`${window.location.origin}/rpc`);
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

  const [showAccountModal, setShowAccountModal] = useState(false);
  const [positionDropdown, setPositionDropdown] = useState(null);
  const [accountDropdown, setAccountDropdown] = useState(false);
  const [showSwapConfirm, setShowSwapConfirm] = useState(false);

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
      setShowSwapConfirm(false); // eslint-disable-line react-hooks/set-state-in-effect
    }
    prevSwapError.current = swapError;
  }, [swapError, showSwapConfirm, addToast]);

  // Chart controls
  const controls = useChartControls({
    defaultRange: "ALL",
    defaultDays: 9999,
    defaultResolution: "1D",
  });
  const { resolution } = controls;

  // PnL Modal State
  const [pnlModalOpen, setPnlModalOpen] = useState(false);

  // Chart series visibility
  const [hiddenSeries, setHiddenSeries] = useState([]);
  const toggleSeries = (key) => {
    setHiddenSeries((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  };

  // Chart stats
  const chartStats = useMemo(() => {
    if (!chartData.length) return null;
    const indexes = chartData
      .filter((d) => d.indexPrice != null)
      .map((d) => d.indexPrice);
    const marks = chartData
      .filter((d) => d.markPrice != null)
      .map((d) => d.markPrice);

    if (indexes.length === 0) return null;
    const mean = indexes.reduce((a, b) => a + b, 0) / indexes.length;
    const min = Math.min(...indexes);
    const max = Math.max(...indexes);
    const variance =
      indexes.reduce((s, v) => s + (v - mean) ** 2, 0) / indexes.length;
    return {
      mean,
      min,
      max,
      vol: Math.sqrt(variance),
      markMean:
        marks.length > 0 ? marks.reduce((a, b) => a + b, 0) / marks.length : 0,
    };
  }, [chartData]);

  const areas = useMemo(
    () =>
      [
        { key: "indexPrice", name: "Index Price", color: "#22d3ee" },
        { key: "markPrice", name: "Mark Price", color: "#ec4899" },
      ].filter((a) => !hiddenSeries.includes(a.key)),
    [hiddenSeries],
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
      setShortAmount(parseFloat(newAmount.toFixed(6))); // eslint-disable-line react-hooks/set-state-in-effect
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

  if (loading || !market) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
          <span className="text-sm uppercase tracking-widest text-gray-500">
            Connecting to simulation...
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
                          fundingFromNF
                            ? `${fundingFromNF.annualPct >= 0 ? "+" : ""}${fundingFromNF.annualPct.toFixed(2)}%`
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

              {/* 2. CONTROLS */}
              <ChartControlBar
                controls={controls}
                extraControls={
                  <ControlCell
                    label="PNL_CALCULATOR"
                    className="pr-0 hidden md:flex"
                  >
                    <div className="flex items-center justify-end h-[30px] w-full">
                      <button
                        onClick={() => setPnlModalOpen(true)}
                        className="flex items-center gap-2 px-4 h-full bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors text-sm font-mono tracking-widest uppercase w-full justify-center"
                      >
                        <Calculator size={14} />
                        Open
                      </button>
                    </div>
                  </ControlCell>
                }
              />

              {/* 3. CHART */}
              <div className="relative flex-1 min-h-[350px] md:min-h-[400px]">
                <div className="flex flex-col md:flex-row justify-between items-start md:items-end mb-4 px-1 gap-3 md:gap-0">
                  <div className="flex gap-4 md:gap-8 flex-wrap">
                    {[
                      {
                        key: "indexPrice",
                        label: "Index_Price",
                        bg: "bg-cyan-400",
                      },
                      {
                        key: "markPrice",
                        label: "Mark_Price",
                        bg: "bg-pink-500",
                      },
                    ].map((s) => (
                      <div
                        key={s.key}
                        className={`flex items-center gap-2 cursor-pointer transition-all ${
                          hiddenSeries.includes(s.key)
                            ? "opacity-50 line-through"
                            : "opacity-100 hover:opacity-80"
                        }`}
                        onClick={() => toggleSeries(s.key)}
                      >
                        <div className={`w-2 h-2 ${s.bg}`}></div>
                        <span className="text-sm uppercase tracking-widest">
                          {s.label}
                        </span>
                      </div>
                    ))}
                  </div>

                  {/* Period stats */}
                  {chartStats && (
                    <div className="text-sm font-mono text-gray-500 uppercase tracking-widest flex items-center gap-4">
                      <span>
                        Range:{" "}
                        <span className="text-white">
                          {chartStats.min.toFixed(2)} –{" "}
                          {chartStats.max.toFixed(2)}
                        </span>
                      </span>
                      <span>
                        Vol:{" "}
                        <span className="text-white">
                          ±{chartStats.vol.toFixed(3)}
                        </span>
                      </span>
                    </div>
                  )}
                </div>

                <div className="h-[350px] md:h-[500px] w-full border border-white/10 p-4 bg-[#050505]">
                  {chartData.length === 0 ? (
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
                      // Collateral = contract's netAccountValue (on-chain recognized: tracked LP, TWAMM, tokens - debt)
                      const collateral = brokerState ? brokerState.nav : null;

                      // NAV = true net value = all assets (collateral + untracked) - debt
                      const untrackedLPValue = (brokerState?.lpPositions || [])
                        .filter(lp => !lp.isActive)
                        .reduce((sum, lp) => sum + (lp.value || 0), 0);
                      const totalNav = collateral !== null ? collateral + untrackedLPValue - brokerState.debtValue : null;

                      // Col. ratio uses on-chain collateral (what protocol sees for risk)
                      const totalColRatio = brokerState && brokerState.debtValue > 0
                        ? brokerState.colRatio
                        : Infinity;

                      return (
                        <div className="grid grid-cols-4 divide-x divide-white/10 border-b border-white/10">
                          {[
                            { label: "NAV", value: totalNav !== null ? `$${totalNav.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—", color: "text-white" },
                            { label: "Collateral", value: collateral !== null ? `$${collateral.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—", color: "text-white" },
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
                              { name: "wRLP", value: brokerState ? `${brokerState.positionBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—", tracked: true },
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
                                      onClick={() => setPositionDropdown(null)}
                                      className="w-full text-left px-4 py-2 text-sm font-mono text-white hover:bg-white/5 transition-colors"
                                    >
                                      Deposit
                                    </button>
                                    <button
                                      onClick={() => setPositionDropdown(null)}
                                      className="w-full text-left px-4 py-2 text-sm font-mono text-white hover:bg-white/5 transition-colors border-t border-white/5"
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
                                      <span className="text-sm font-mono text-gray-400">${lp.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
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
                                        <div className="flex justify-between"><span>Value</span><span className="text-cyan-400">${lp.value.toFixed(2)}</span></div>
                                        {lp.isActive && (
                                          <div className="flex justify-between border-t border-white/5 pt-1 mt-1"><span>Status</span><span className="text-cyan-400">ACTIVE (tracked)</span></div>
                                        )}
                                      </div>
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
                                            refreshTwamm();
                                            addToast({ type: "success", title: "Order Cancelled" });
                                            refreshBrokerState?.();
                                          });
                                        }}
                                        disabled={cancellingTwamm}
                                        className="w-full text-left px-4 py-2 text-sm font-mono text-red-400 hover:bg-white/5 transition-colors"
                                      >
                                        {cancellingTwamm ? "Cancelling..." : "Cancel Order"}
                                      </button>
                                    )}
                                    {tw.isDone && tw.tracked && (
                                      <button
                                        onClick={() => {
                                          setPositionDropdown(null);
                                          claimTwammOrder(() => {
                                            refreshTwamm();
                                            refreshBrokerState?.();
                                            addToast({ type: "success", title: "Tokens Claimed", message: "Expired order tokens returned to broker" });
                                          });
                                        }}
                                        disabled={cancellingTwamm}
                                        className="w-full text-left px-4 py-2 text-sm font-mono text-green-400 hover:bg-white/5 transition-colors"
                                      >
                                        {cancellingTwamm ? "Claiming..." : "Claim Tokens"}
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
                    { id: "loop", label: "Loop", desc: "Leveraged position" },
                    { id: "batch", label: "Batch", desc: "Multi-action bundle" },
                  ].map((action) => (
                    <React.Fragment key={action.id}>
                    <button
                      onClick={() => setActiveAction(activeAction === action.id ? null : action.id)}
                      className={`w-full flex items-center justify-between px-4 py-3 hover:bg-white/5 transition-all text-left group ${
                        activeAction === action.id ? "bg-white/5" : ""
                      }`}
                    >
                      <div>
                        <div className={`text-sm font-bold uppercase tracking-widest transition-colors ${
                          activeAction === action.id ? "text-cyan-400" : "text-white group-hover:text-cyan-400"
                        }`}>
                          {action.label}
                        </div>
                        <div className="text-sm text-gray-600 font-mono mt-0.5">
                          {action.desc}
                        </div>
                      </div>
                      <ChevronDown size={14} className={`transition-all ${
                        activeAction === action.id
                          ? "text-cyan-400 rotate-0"
                          : "text-gray-600 group-hover:text-cyan-400 -rotate-90"
                      }`} />
                    </button>
                    {activeAction === action.id && (
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
                        onStateChange={refreshBrokerState}
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
                  loading={opsLoading}
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
            fetchBrokerBalance(addr);
            refreshBrokerState?.();
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
              if (fetchBrokerBalance && brokerAddress) {
                fetchBrokerBalance(brokerAddress);
              }
              refreshBrokerState?.();
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
                if (fetchBrokerBalance && brokerAddress) {
                  fetchBrokerBalance(brokerAddress);
                }
                refreshBrokerState?.();
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
                if (fetchBrokerBalance && brokerAddress) {
                  fetchBrokerBalance(brokerAddress);
                }
                refreshBrokerState?.();
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
              if (fetchBrokerBalance && brokerAddress) {
                fetchBrokerBalance(brokerAddress);
              }
              refreshBrokerState?.();
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
              if (fetchBrokerBalance && brokerAddress) {
                fetchBrokerBalance(brokerAddress);
              }
              refreshBrokerState?.();
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
      />
    </>
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
