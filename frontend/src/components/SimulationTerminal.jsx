import React, {
  useState,
  useMemo,
  useEffect,
  useRef,
  useCallback,
} from "react";
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
} from "lucide-react";
import { useSimulation } from "../hooks/useSimulation";
import { useChartControls } from "../hooks/useChartControls";
import { useWallet } from "../context/WalletContext";

import { useBrokerAccount } from "../hooks/useBrokerAccount";
import { useSwapQuote } from "../hooks/useSwapQuote";
import { useSwapExecution } from "../hooks/useSwapExecution";
import { useOperations, formatOpAmount } from "../hooks/useOperations";
import AccountModal from "./AccountModal";
import SwapConfirmModal from "./SwapConfirmModal";
import { ToastContainer } from "./Toast";
import { useToast } from "../hooks/useToast";
import RLDPerformanceChart from "./RLDChart";
import ChartControlBar from "./ChartControlBar";
import ControlCell from "./ControlCell";
import PnlCalculatorModal from "./PnlCalculatorModal";

import BrokerPositions from "./BrokerPositions";
import StatItem from "./StatItem";
import TradingTerminal, { InputGroup, SummaryRow } from "./TradingTerminal";
import SettingsButton from "./SettingsButton";

// ── Sub-components ────────────────────────────────────────────

function SimMetricBox({ label, value, sub, Icon = Activity, dimmed }) {
  return (
    <div
      className={`p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px] ${
        dimmed ? "opacity-30" : ""
      }`}
    >
      <div className="text-[10px] md:text-[12px] text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
        {label} <Icon size={15} className="opacity-90" />
      </div>
      <div>
        <div className="text-2xl md:text-3xl font-light text-white mb-1 md:mb-2 tracking-tight">
          {value}
        </div>
        <div className="text-[10px] md:text-[12px] text-gray-500 uppercase tracking-widest">
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
      <div className="text-xs text-gray-600 uppercase tracking-widest text-center py-4">
        —
      </div>
    );
  }

  if (loading && operations.length === 0) {
    return (
      <div className="text-xs text-gray-600 uppercase tracking-widest text-center py-4">
        Loading...
      </div>
    );
  }

  if (operations.length === 0) {
    return (
      <div className="text-xs text-gray-600 uppercase tracking-widest text-center py-4">
        No operations yet
      </div>
    );
  }

  return (
    <div className="space-y-0 divide-y divide-white/5 max-h-[200px] overflow-y-auto custom-scrollbar">
      {operations.slice(0, 15).map((op) => {
        const ts = new Date(op.timestamp * 1000);
        const timeStr = ts.toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        });

        // Format amounts based on event type
        let detail = "";
        if (op.type === "LongExecuted") {
          detail = `${formatOpAmount(op.args[1])} waUSDC → ${formatOpAmount(op.args[2])} wRLP`;
        } else if (op.type === "LongClosed") {
          detail = `${formatOpAmount(op.args[1])} wRLP → ${formatOpAmount(op.args[2])} waUSDC`;
        } else if (op.type === "ShortExecuted") {
          detail = `${formatOpAmount(op.args[1])} debt · ${formatOpAmount(op.args[2])} proceeds`;
        } else if (op.type === "Deposited") {
          detail = `${formatOpAmount(op.args[1])} → ${formatOpAmount(op.args[2])} waUSDC`;
        }

        return (
          <div
            key={op.id}
            className="py-2 flex items-center justify-between gap-3"
          >
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span
                className={`text-[9px] font-bold font-mono px-1.5 py-0.5 tracking-wider w-[72px] text-center inline-block ${op.color}`}
              >
                {op.label}
              </span>
              <span className="text-[11px] text-gray-500 font-mono">
                {timeStr}
              </span>
            </div>
            <div className="text-[10px] font-mono text-gray-400 flex-shrink-0">
              {detail}
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
    events,
    blockChanged,
    blockNumber,
    totalBlocks,
    totalEvents,
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
    creating: brokerCreating,
    fetchBrokerBalance,
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

  // Trading State (must be declared before swap hooks that reference tradeSide/collateral)
  const [tradeSide, setTradeSide] = useState("LONG");
  const [tradeAction, setTradeAction] = useState("OPEN"); // OPEN or CLOSE
  const [collateral, setCollateral] = useState(1000);
  const [closeAmount, setCloseAmount] = useState(""); // wRLP to sell (close long)
  const [closeShortAmount, setCloseShortAmount] = useState(""); // waUSDC to spend (close short)
  const [closeShortDebt, setCloseShortDebt] = useState(""); // wRLP debt to repay (close short)
  const [lastCloseShortEdit, setLastCloseShortEdit] = useState(null); // 'debt' or 'collateral'
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
        const provider = new ethers.JsonRpcProvider("http://127.0.0.1:8545");
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
      setShowSwapConfirm(false);
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

  const handleShortAmountChange = (newWRLP) => {
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

  const handleLongAmountChange = (newAmount) => {
    setCollateral(newAmount);
  };

  // ── Error / Loading ─────────────────────────────────────────
  if (error && !connected) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="text-red-500 text-xs uppercase tracking-widest">
            SIM_DISCONNECTED
          </div>
          <div className="text-gray-600 text-[11px] max-w-xs">
            Cannot reach simulation indexer. Make sure the Docker simulation
            stack is running.
          </div>
          <div className="text-[10px] text-gray-700 font-mono">
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
          <span className="text-[10px] uppercase tracking-widest text-gray-500">
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
      <div className="min-h-screen bg-[#080808] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
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
                    <div className="text-[10px] text-gray-700 mb-6 font-mono leading-tight tracking-tight">
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
                    <span className="text-[10px] uppercase tracking-widest text-gray-500">
                      RLD_Core
                    </span>
                    <span className="text-[10px] uppercase tracking-widest text-cyan-500 font-mono">
                      {(market?.marketId || "0x").slice(0, 10)}...
                      {(market?.marketId || "").slice(-4)}
                    </span>
                  </div>
                </div>

                {/* Stats Cards */}
                <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
                  {/* PRICE */}
                  <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                    <div className="text-[10px] md:text-[12px] text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
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
                    <div className="text-[10px] md:text-[12px] text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
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
                    <div className="text-[10px] md:text-[12px] text-gray-500 uppercase tracking-widest flex justify-between">
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
                        className="flex items-center gap-2 px-4 h-full bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors text-xs font-mono tracking-widest uppercase w-full justify-center"
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
                        <span className="text-[11px] uppercase tracking-widest">
                          {s.label}
                        </span>
                      </div>
                    ))}
                  </div>

                  {/* Period stats */}
                  {chartStats && (
                    <div className="text-[11px] font-mono text-gray-500 uppercase tracking-widest flex items-center gap-4">
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

                <div className="h-[350px] md:h-[500px] w-full border border-white/10 p-4 bg-[#080808]">
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
                  swapExecuting ||
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
                      className={`flex-1 py-2 text-[11px] font-bold tracking-[0.2em] uppercase transition-colors ${
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
                  <div className="flex items-center justify-between text-[12px] uppercase tracking-widest font-bold text-gray-500">
                    <span>Pay_With</span>
                    <div className="relative" ref={payDropdownRef}>
                      <button
                        type="button"
                        onClick={() => setPayDropdownOpen(!payDropdownOpen)}
                        className={`
                          h-[28px] border border-white/20 bg-black flex items-center justify-between px-2 gap-2
                          text-[11px] font-mono text-white focus:outline-none uppercase tracking-widest
                          hover:border-white transition-colors
                          ${payDropdownOpen ? "border-white" : ""}
                        `}
                      >
                        <span>{closeShortRepayMode}</span>
                        <ChevronDown
                          size={12}
                          className={`transition-transform duration-200 flex-shrink-0 ${payDropdownOpen ? "rotate-180" : ""}`}
                        />
                      </button>
                      {payDropdownOpen && (
                        <div className="absolute top-full right-0 mt-1 bg-[#0a0a0a] border border-white/20 z-50 flex flex-col shadow-xl whitespace-nowrap">
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
                                  w-full flex items-center px-3 py-2 text-[11px] text-left uppercase tracking-widest transition-colors
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
                    subLabel={`Total_Debt: ${shortAmount > 0 ? shortAmount.toFixed(1) + " wRLP" : "—"}`}
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
                      }
                    }}
                    suffix="wRLP"
                    onMax={() => {
                      setCloseShortDebt(String(shortAmount || 0));
                      setLastCloseShortEdit("debt");
                      if (
                        closeShortRepayMode === "waUSDC" &&
                        currentRate > 0 &&
                        shortAmount > 0
                      ) {
                        setCloseShortAmount(
                          (shortAmount * currentRate).toFixed(2),
                        );
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
                    <div className="flex justify-between text-[12px] uppercase tracking-widest font-bold text-gray-500">
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
                    <div className="flex justify-between text-[12px] uppercase tracking-widest font-bold text-gray-500">
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
                    <div className="flex justify-between text-[12px] text-gray-500 font-mono">
                      <span>150%</span>
                      <span>1500%</span>
                    </div>
                  </div>
                </>
              )}

              {/* Stats Box */}
              <div className="border border-white/10 p-4 space-y-2 bg-white/[0.02] text-[12px]">
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
                  <span className="text-gray-500 uppercase text-[12px]">
                    Liq. Rate
                  </span>
                  <span className="font-mono text-orange-500 text-[12px]">
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
                  <div className="text-[10px] text-red-400 font-mono truncate mt-1">
                    {swapError}
                  </div>
                )}
              </div>
            </TradingTerminal>
          </div>

          {/* === BOTTOM ROW: BROKER POSITIONS | FUNDING | EVENTS (full width) === */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Broker Positions */}
            <div className="border border-white/10 bg-[#080808] flex flex-col">
              <div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center h-[50px]">
                <h3 className="text-xs font-bold tracking-widest text-white uppercase flex items-center gap-2">
                  <Layers size={14} className="text-gray-500" />
                  Broker_Positions
                </h3>
                <span className="text-[11px] text-gray-600 uppercase tracking-widest font-mono">
                  {brokers.length} Active
                </span>
              </div>
              <div className="p-4 md:p-5 flex-1">
                <BrokerPositions brokers={brokers} />
              </div>
            </div>

            {/* Funding Direction */}
            <div className="border border-white/10 bg-[#080808] flex flex-col">
              <div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center h-[50px]">
                <h3 className="text-xs font-bold tracking-widest text-white uppercase flex items-center gap-2">
                  <ArrowUpDown size={14} className="text-gray-500" />
                  Funding_Direction
                </h3>
                {funding && (
                  <span
                    className={`text-[11px] font-bold uppercase tracking-widest ${
                      funding.direction === "LONGS_PAY"
                        ? "text-green-500"
                        : "text-red-500"
                    }`}
                  >
                    {funding.direction.replace("_", " ")}
                  </span>
                )}
              </div>
              <div className="p-4 md:p-5 flex-1">
                {funding ? (
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-[11px] text-gray-500 uppercase tracking-widest mb-2">
                        Spread
                      </div>
                      <div
                        className={`text-xl font-mono font-bold ${
                          funding.spread >= 0
                            ? "text-green-400"
                            : "text-red-400"
                        }`}
                      >
                        {funding.spread >= 0 ? "+" : ""}
                        {funding.spread.toFixed(4)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-gray-500 uppercase tracking-widest mb-2">
                        Spread %
                      </div>
                      <div
                        className={`text-xl font-mono font-bold ${
                          funding.spreadPct >= 0
                            ? "text-green-400"
                            : "text-red-400"
                        }`}
                      >
                        {funding.spreadPct >= 0 ? "+" : ""}
                        {funding.spreadPct.toFixed(2)}%
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="text-gray-700 text-xs uppercase tracking-widest">
                    No funding data
                  </div>
                )}
              </div>
            </div>

            {/* Last Operations */}
            <div className="border border-white/10 bg-[#080808] flex flex-col">
              <div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center h-[50px]">
                <h3 className="text-xs font-bold tracking-widest text-white uppercase flex items-center gap-2">
                  <Activity size={14} className="text-gray-500" />
                  Last_Operations
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
                    className="text-[9px] font-mono font-bold tracking-[0.15em] uppercase px-1.5 py-0.5 border border-white/10 text-gray-500 hover:text-white hover:border-white/30 bg-white/[0.02] hover:bg-white/[0.05] transition-all flex items-center gap-1"
                  >
                    <Download size={10} />
                    CSV
                  </button>
                )}
              </div>
              <div className="p-4 md:p-5 flex-1">
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

      {/* Account Onboarding Modal */}
      <AccountModal
        isOpen={showAccountModal}
        onClose={() => setShowAccountModal(false)}
        onComplete={(addr) => {
          setShowAccountModal(false);
          // Refresh broker state & show toast
          if (addr) {
            fetchBrokerBalance(addr);
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
            executeCloseLong(parseFloat(closeAmount), (receipt) => {
              setShowSwapConfirm(false);
              if (fetchBrokerBalance && brokerAddress) {
                fetchBrokerBalance(brokerAddress);
              }
              addToast({
                type: "success",
                title: "Long Closed",
                message: `Sold ${parseFloat(closeAmount).toLocaleString()} wRLP for waUSDC`,
                duration: 5000,
              });
              setCloseAmount("");
            });
          } else if (tradeAction === "CLOSE" && tradeSide === "SHORT") {
            // Close Short flow — spend waUSDC to buy wRLP and repay debt
            executeCloseShort(parseFloat(closeShortAmount), (receipt) => {
              setShowSwapConfirm(false);
              if (fetchBrokerBalance && brokerAddress) {
                fetchBrokerBalance(brokerAddress);
              }
              addToast({
                type: "success",
                title: "Short Closed",
                message: `Spent ${parseFloat(closeShortAmount).toLocaleString()} waUSDC to repay wRLP debt`,
                duration: 5000,
              });
              setCloseShortAmount("");
            });
          } else if (tradeSide === "SHORT" && tradeAction === "OPEN") {
            // Open Short flow — shortAmount is already in wRLP
            executeShort(collateral, shortAmount, (receipt) => {
              setShowSwapConfirm(false);
              if (fetchBrokerBalance && brokerAddress) {
                fetchBrokerBalance(brokerAddress);
              }
              addToast({
                type: "success",
                title: "Short Opened",
                message: `Shorted ${notional.toLocaleString()} USDC notional at ${shortCR.toFixed(0)}% CR`,
                duration: 5000,
              });
            });
          } else {
            // Open Long flow
            executeLong(collateral, (receipt) => {
              setShowSwapConfirm(false);
              if (fetchBrokerBalance && brokerAddress) {
                fetchBrokerBalance(brokerAddress);
              }
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

function formatLiquidity(val) {
  if (val >= 1e18) return `${(val / 1e18).toFixed(1)}E`;
  if (val >= 1e15) return `${(val / 1e15).toFixed(1)}P`;
  if (val >= 1e12) return `${(val / 1e12).toFixed(1)}T`;
  if (val >= 1e9) return `${(val / 1e9).toFixed(1)}B`;
  if (val >= 1e6) return `${(val / 1e6).toFixed(1)}M`;
  if (val >= 1e3) return `${(val / 1e3).toFixed(1)}K`;
  return val.toLocaleString();
}

function formatDebt(val) {
  if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}
