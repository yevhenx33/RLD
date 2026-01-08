import React, { useState, useMemo, useEffect } from "react";
import { Link } from "react-router-dom";
import useSWR from "swr";
import axios from "axios";
import {
  Terminal,
  Power,
  Activity,
  Wallet,
  ExternalLink,
  RefreshCw,
  Clock,
  TrendingUp,
  TrendingDown,
} from "lucide-react";
import RLDPerformanceChart from "./components/RLDChart";
import { useSymbioticOracle } from "./hooks/useSymbioticOracle";
const fetcher = (url) => axios.get(url).then((res) => res.data);

// --- HELPER FUNCTIONS ---
const getPastDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split("T")[0];
};
const getToday = () => new Date().toISOString().split("T")[0];

const formatTwarLabel = (seconds) => {
  if (!seconds) return "TWAR_OFF";
  if (seconds < 60) return `TWAR_${seconds}S`;
  if (seconds < 3600) return `TWAR_${Math.round(seconds / 60)}M`;
  if (seconds < 86400)
    return `TWAR_${parseFloat((seconds / 3600).toFixed(1))}H`;
  return `TWAR_${parseFloat((seconds / 86400).toFixed(1))}D`;
};

// --- APP COMPONENT ---
function App() {
  // Data State
  const [tempStart, setTempStart] = useState(getPastDate(90));
  const [tempEnd, setTempEnd] = useState(getToday());
  const [appliedStart, setAppliedStart] = useState(getPastDate(90));
  const [appliedEnd, setAppliedEnd] = useState(getToday());
  const [activeRange, setActiveRange] = useState("3M");
  const [resolution, setResolution] = useState("4H");

  // Analysis State
  const [showTwar, setShowTwar] = useState(true);
  const [twarWindow, setTwarWindow] = useState(3600);
  const [tempTwarInput, setTempTwarInput] = useState(3600);

  // Trading State
  const [account, setAccount] = useState(null);
  const [tradeSide, setTradeSide] = useState("LONG");
  const [collateral, setCollateral] = useState(1000);
  const [shortCR, setShortCR] = useState(150);

  // PnL Simulation State
  const [simTargetRate, setSimTargetRate] = useState(null);

  // --- NEW: Symbiotic Hook ---
  const { data: symbioticData } = useSymbioticOracle();
  const latestSymbiotic = useMemo(() => {
    return symbioticData && symbioticData.length > 0
      ? symbioticData[symbioticData.length - 1]
      : null;
  }, [symbioticData]);

  // --- ACTIONS ---
  const handleApplyDate = () => {
    setAppliedStart(tempStart);
    setAppliedEnd(tempEnd);
    setActiveRange("CUSTOM");
  };

  const handleApplyTwar = () => setTwarWindow(tempTwarInput);

  const handleQuickRange = (days, label) => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - days);
    if (days <= 3) setResolution("RAW");
    else if (days <= 30) setResolution("1H");
    else if (days <= 180) setResolution("4H");
    else setResolution("1D");
    setTempStart(start.toISOString().split("T")[0]);
    setTempEnd(end.toISOString().split("T")[0]);
    setAppliedStart(start.toISOString().split("T")[0]);
    setAppliedEnd(end.toISOString().split("T")[0]);
    setActiveRange(label);
  };

  const connectWallet = async () => {
    if (window.ethereum) {
      try {
        const accounts = await window.ethereum.request({
          method: "eth_requestAccounts",
        });
        setAccount(accounts[0]);
      } catch (err) {
        console.error("User rejected connection");
      }
    } else {
      alert("No Ethereum wallet found.");
    }
  };

  const getUrl = () => {
    let url = `http://127.0.0.1:8000/rates?resolution=${resolution}`;
    if (appliedStart) url += `&start_date=${appliedStart}`;
    if (appliedEnd) url += `&end_date=${appliedEnd}`;
    return url;
  };

  const { data: rates, error } = useSWR(getUrl(), fetcher, {
    refreshInterval: 10000,
  });

  // Calculations
  const processedData = useMemo(() => {
    if (!rates || rates.length === 0) return [];
    const result = [];
    const historyQueue = [];
    let runningArea = 0;
    let runningTime = 0;

    for (let i = 0; i < rates.length; i++) {
      const current = rates[i];
      const prevTs = i > 0 ? rates[i - 1].timestamp : current.timestamp;
      let dt = current.timestamp - prevTs;
      if (dt < 0) dt = 0;
      const stepArea = current.apy * dt;
      historyQueue.push({
        dt: dt,
        area: stepArea,
        timestamp: current.timestamp,
      });
      runningArea += stepArea;
      runningTime += dt;
      while (
        historyQueue.length > 0 &&
        current.timestamp - historyQueue[0].timestamp > twarWindow
      ) {
        const removed = historyQueue.shift();
        runningArea -= removed.area;
        runningTime -= removed.dt;
      }
      let twarValue = runningTime > 0 ? runningArea / runningTime : current.apy;
      twarValue = Math.max(0, twarValue);
      result.push({ ...current, twar: twarValue });
    }
    return result;
  }, [rates, twarWindow]);

  const stats = useMemo(() => {
    if (!rates || rates.length === 0)
      return { min: 0, max: 0, mean: 0, vol: 0 };
    const apys = rates.map((r) => r.apy);
    const mean = apys.reduce((a, b) => a + b, 0) / apys.length;
    const variance =
      apys.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / apys.length;
    return {
      min: Math.min(...apys),
      max: Math.max(...apys),
      mean,
      vol: Math.sqrt(variance),
    };
  }, [rates]);

  // 24H Change Calculation
  const dailyChange = useMemo(() => {
    if (!rates || rates.length < 2) return 0;
    const oneDaySeconds = 86400;
    const latestTs = rates[rates.length - 1].timestamp;
    const targetTs = latestTs - oneDaySeconds;

    let closest = rates[0];
    let minDiff = Math.abs(closest.timestamp - targetTs);

    for (let i = 1; i < rates.length; i++) {
      const diff = Math.abs(rates[i].timestamp - targetTs);
      if (diff < minDiff) {
        minDiff = diff;
        closest = rates[i];
      }
    }
    return rates[rates.length - 1].apy - closest.apy;
  }, [rates]);

  const isCappedRaw = resolution === "RAW" && rates && rates.length >= 30000;
  const latest =
    rates && rates.length > 0
      ? rates[rates.length - 1]
      : { apy: 0, block_number: 0, timestamp: 0 };
  const latestTwar =
    processedData.length > 0 ? processedData[processedData.length - 1].twar : 0;

  // --- TRADING CALCULATIONS ---
  const currentRate = latest.apy;

  let leverage, liqRate, notional;

  if (tradeSide === "LONG") {
    leverage = 1;
    notional = collateral;
    liqRate = null;
  } else {
    const crDecimal = shortCR / 100;
    notional = crDecimal > 0 ? collateral / crDecimal : 0;
    liqRate = currentRate * (shortCR / 110);
  }

  const handleShortAmountChange = (newAmount) => {
    if (newAmount > 0) {
      const newCR = (collateral / newAmount) * 100;
      setShortCR(Math.min(Math.max(newCR, 110), 1500));
    }
  };

  const handleLongAmountChange = (newAmount) => {
    setCollateral(newAmount);
  };

  // --- PNL SIMULATION ---
  useEffect(() => {
    if (simTargetRate === null && currentRate > 0) {
      setSimTargetRate(currentRate);
    }
  }, [currentRate, simTargetRate]);

  const calculateSimPnL = () => {
    if (!simTargetRate) return { value: 0, percent: 0 };
    let pnl = 0;
    if (tradeSide === "LONG") {
      pnl = ((simTargetRate - currentRate) / 100) * notional;
    } else {
      pnl = ((currentRate - simTargetRate) / 100) * notional;
    }
    const percent = collateral > 0 ? (pnl / collateral) * 100 : 0;
    return { value: pnl, percent };
  };

  const simPnL = calculateSimPnL();

  // --- RENDER ---
  if (error)
    return (
      <div className="h-screen flex items-center justify-center text-red-600 bg-black font-mono text-xs">
        ERR: API_DISCONNECTED
      </div>
    );
  if (!rates)
    return (
      <div className="h-screen flex items-center justify-center text-gray-500 bg-black font-mono text-xs animate-pulse">
        SYSTEM_INITIALIZING...
      </div>
    );

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      {/* HEADER */}
      <div className="sticky top-0 bg-[#050505]/95 backdrop-blur-sm z-50 w-full border-b border-transparent">
        <header className="max-w-[1800px] mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-5 pl-1">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-white"></div>
              <h1 className="text-sm font-bold tracking-widest uppercase">
                RLD
              </h1>
            </div>
            <div className="hidden md:flex text-[12px] items-center gap-1 font-bold tracking-[0.15em] uppercase">
              <span className="text-white/10">//</span>

              <span className="text-gray-400 hover:text-white transition-colors cursor-pointer px-2 tracking-widest">
                TERMINAL
              </span>

              <span className="text-white/10">|</span>

              <Link
                to="/bonds"
                className="text-gray-400 hover:text-white transition-colors cursor-pointer px-2 tracking-widest"
              >
                BONDS
              </Link>

              <span className="text-white/10">|</span>

              <a className="text-gray-400 hover:text-white transition-colors cursor-pointer px-2 tracking-widest ">
                CDS_[SOON]
              </a>
              <span className="text-white/10">|</span>
              <a
                href="https://lumisfi.notion.site/rld"
                target="_blank"
                rel="noreferrer"
                className="text-gray-400 hover:text-white transition-colors cursor-pointer px-2 tracking-widest"
              >
                RESEARCH
              </a>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="hidden md:flex items-center gap-6 text-[11px] uppercase tracking-widest text-gray-500 border-r border-white/10 pr-6 h-6">
              <span className="flex items-center gap-2">
                <div
                  className={`w-1.5 h-1.5 ${
                    rates ? "bg-green-500" : "bg-red-500"
                  }`}
                ></div>
                {isCappedRaw ? "WARN: LIMIT_ACTIVE" : "NET: STABLE"}
              </span>
              <span>BLOCK: #{latest.block_number}</span>
            </div>
            <button
              onClick={connectWallet}
              className="flex items-center gap-3 border border-white/10 bg-black hover:bg-white/5 hover:border-white/30 transition-all px-6 py-2 focus:outline-none rounded-none"
            >
              <div
                className={`w-1.5 h-1.5 rounded-full ${
                  account
                    ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]"
                    : "bg-gray-600"
                }`}
              ></div>
              <span className="text-xs font-bold tracking-widest uppercase text-white">
                {account ? `${account.substring(0, 6)}...` : "CONNECT WALLET"}
              </span>
            </button>
          </div>
        </header>
      </div>

      {/* MAIN GRID LAYOUT */}
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-stretch">
          {/* === LEFT COLUMN: DATA & CHART (Span 9) === */}
          <div className="xl:col-span-9 flex flex-col gap-4">
            {/* 1. METRICS GRID */}
            <div className="border border-white/10 grid grid-cols-1 lg:grid-cols-12">
              {/* Branding */}
              <div className="lg:col-span-4 flex flex-col justify-between p-6 border-b lg:border-b-0 lg:border-r border-white/10 h-full min-h-[180px]">
                <div>
                  <div className="text-[10px] text-gray-700 mb-6 font-mono leading-tight tracking-tight">
                    0x8787... 87 87 0B CA 3F 3F D6 33
                  </div>
                  <h2 className="text-3xl font-medium tracking-tight mb-2 leading-none">
                    AAVE V3 <br />
                    <span className="text-gray-600">USDC MARKET DATA</span>
                  </h2>
                </div>
                <div className="mt-auto pt-4 border-t border-white/10 flex items-center justify-between">
                  <span className="text-[10px] uppercase tracking-widest text-gray-500">
                    Pool Contract
                  </span>
                  <a
                    href="https://app.aave.com/reserve-overview/?underlyingAsset=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&marketName=proto_mainnet_v3"
                    target="_blank"
                    rel="noreferrer"
                    className="text-[10px] uppercase tracking-widest text-green-500 hover:text-white transition-colors flex items-center gap-1 decoration-1 underline-offset-4 hover:underline"
                  >
                    0x8787...4E2 <ExternalLink size={10} />
                  </a>
                </div>
              </div>

              <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
                {/* CARD 1: CURRENT SPOT + 24H CHANGE */}
                <MetricBox
                  label="CURRENT_SPOT"
                  value={latest.apy.toFixed(2)}
                  sub={
                    <div
                      className={`flex items-center gap-2 ${
                        dailyChange >= 0 ? "text-green-500" : "text-red-500"
                      }`}
                    >
                      {dailyChange >= 0 ? (
                        <TrendingUp size={15} />
                      ) : (
                        <TrendingDown size={15} />
                      )}
                      <span className="font-bold">
                        24H: {dailyChange > 0 ? "+" : ""}
                        {dailyChange.toFixed(2)}%
                      </span>
                    </div>
                  }
                />

                {/* CARD 2: PERIOD STATS */}
                <div className="p-6 flex flex-col justify-between h-full min-h-[180px]">
                  <div className="text-[12px] text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                    PERIOD_STATS <Activity size={15} className="opacity-90" />
                  </div>
                  <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                    <StatItem
                      label="MIN_RATE"
                      value={`${stats.min.toFixed(2)}%`}
                    />
                    <StatItem
                      label="MAX_RATE"
                      value={`${stats.max.toFixed(2)}%`}
                    />
                    <StatItem
                      label="AVG_RATE"
                      value={`${stats.mean.toFixed(2)}%`}
                    />
                    <StatItem
                      label="VOLATILITY"
                      value={`±${stats.vol.toFixed(2)}%`}
                    />
                  </div>
                </div>

                {/* CARD 3: FUNDING_RATE (Daily/Yearly) */}
                <div className="p-6 flex flex-col justify-between h-full min-h-[180px]">
                  <div className="text-[12px] text-gray-500 uppercase tracking-widest flex justify-between">
                    FUNDING_RATE <Clock size={15} className="opacity-90" />
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 mt-auto">
                    <StatItem
                      label="DAILY"
                      value={`${(latest.apy / 365).toFixed(4)}%`}
                    />
                    <StatItem
                      label="YEARLY"
                      value={`${latest.apy.toFixed(2)}%`}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* 2. CONTROLS */}
            <div className="border-y border-white/10 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 divide-y md:divide-y-0 xl:divide-y-0">
              <ControlCell label="RESOLUTION" className="pl-0">
                {["RAW", "1H", "4H", "1D"].map((res) => (
                  <button
                    key={res}
                    onClick={() => setResolution(res)}
                    className={`flex-1 text-[12px] font-bold py-1 uppercase tracking-wider transition-colors border border-transparent rounded-none focus:outline-none ${
                      resolution === res
                        ? "bg-white text-black border-white"
                        : "text-gray-500 hover:text-white hover:bg-white/5"
                    }`}
                  >
                    {res}
                  </button>
                ))}
              </ControlCell>
              <ControlCell label="TIMEFRAME">
                {[
                  { l: "1D", d: 1 },
                  { l: "1W", d: 7 },
                  { l: "1M", d: 30 },
                  { l: "3M", d: 90 },
                  { l: "1Y", d: 365 },
                  { l: "ALL", d: 9999 },
                ].map((btn) => (
                  <button
                    key={btn.l}
                    onClick={() => handleQuickRange(btn.d, btn.l)}
                    className={`flex-1 text-[12px] font-semibold py-1 uppercase tracking-wider transition-colors border border-transparent rounded-none focus:outline-none ${
                      activeRange === btn.l
                        ? "bg-white text-black border-white"
                        : "text-gray-500 hover:text-white hover:bg-white/5"
                    }`}
                  >
                    {btn.l}
                  </button>
                ))}
              </ControlCell>
              <ControlCell label="CUSTOM_RANGE">
                <div className="flex items-center justify-between h-[30px] w-full gap-2">
                  <input
                    type="date"
                    value={tempStart}
                    onChange={(e) => setTempStart(e.target.value)}
                    className="bg-transparent border-b border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-[38%] py-1 rounded-none"
                  />
                  <span className="text-gray-600 text-xs">-</span>
                  <input
                    type="date"
                    value={tempEnd}
                    onChange={(e) => setTempEnd(e.target.value)}
                    className="bg-transparent border-b border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-[38%] py-1 rounded-none"
                  />
                  <button
                    onClick={handleApplyDate}
                    className="text-xs uppercase text-gray-500 hover:text-white border border-white/10 hover:border-white px-3 h-full flex items-center rounded-none transition-colors focus:outline-none"
                  >
                    SET
                  </button>
                </div>
              </ControlCell>
              <ControlCell label="SMOOTHING (SEC)" className="pr-0">
                <div className="flex items-center justify-between gap-2 h-[30px] w-full">
                  <input
                    type="number"
                    value={tempTwarInput}
                    onChange={(e) => setTempTwarInput(Number(e.target.value))}
                    className="flex-1 bg-transparent border-b border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono h-full py-1 text-right pr-2 rounded-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                  />
                  <button
                    onClick={handleApplyTwar}
                    className="text-xs uppercase text-gray-500 hover:text-white border border-white/10 hover:border-white px-3 h-full flex items-center rounded-none transition-colors focus:outline-none"
                  >
                    SET
                  </button>
                  <button
                    onClick={() => setShowTwar(!showTwar)}
                    className={`px-3 h-full flex items-center justify-center gap-2 border text-xs uppercase font-bold tracking-wider transition-all rounded-none focus:outline-none ${
                      showTwar
                        ? "bg-white text-black border-white"
                        : "bg-transparent text-gray-600 border-white/10 hover:border-white/40"
                    }`}
                  >
                    <Power
                      size={12}
                      className={showTwar ? "text-black" : "text-gray-600"}
                    />
                  </button>
                </div>
              </ControlCell>
            </div>

            {/* 3. CHART */}
            <div className="relative flex-1 min-h-[400px]">
              <div className="flex justify-between items-end mb-4 px-1">
                <div className="flex gap-8">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 bg-cyan-400"></div>
                    <span className="text-[11px] uppercase tracking-widest">
                      Spot Rate
                    </span>
                  </div>
                  {showTwar && (
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 bg-pink-500"></div>
                      <span className="text-[11px] uppercase tracking-widest">
                        {formatTwarLabel(twarWindow)}
                      </span>
                    </div>
                  )}
                </div>
                <div className="text-[11px] font-mono text-gray-500 uppercase tracking-widest flex items-center gap-2">
                  {latestSymbiotic ? (
                    <>
                      <span className="text-pink-500">
                        SYMBIOTIC: ${latestSymbiotic.twar.toFixed(4)}
                      </span>
                      <span className="text-gray-600">
                        Updated{" "}
                        <TimeAgo timestamp={latestSymbiotic.timestamp} /> ago
                      </span>
                    </>
                  ) : (
                    "SYMBIOTIC: SYNCING..."
                  )}
                </div>
              </div>
              <div className="h-[500px] w-full border border-white/10 p-4 bg-[#080808]">
                {processedData.length === 0 ? (
                  <div className="h-full flex items-center justify-center text-gray-700 text-xs tracking-widest uppercase">
                    No Data Available
                  </div>
                ) : (
                  <RLDPerformanceChart
                    data={processedData}
                    areas={[
                      { key: "apy", name: "Spot", color: "#22d3ee" },
                      ...(showTwar
                        ? [{ key: "twar", name: "TWAR", color: "#ec4899" }]
                        : []),
                    ]}
                  />
                )}
              </div>
            </div>
          </div>

          {/* === RIGHT COLUMN: TRADING TERMINAL + PNL (Span 3) === */}
          <div className="xl:col-span-3 border border-white/10 bg-[#080808] flex flex-col h-full">
            {/* 1. Header */}
            <div className="p-4 border-b border-white/10 bg-[#0a0a0a]">
              <h3 className="text-xs font-bold tracking-widest text-white uppercase flex items-center gap-2">
                <Terminal size={15} className="text-gray-500" /> Synthetic_Rates
              </h3>
            </div>

            {/* 2. Toggle */}
            <div className="p-1 border-b border-white/10 bg-[#080808]">
              <div className="grid grid-cols-2 gap-1">
                <button
                  onClick={() => setTradeSide("LONG")}
                  className={`py-3 text-[13px] font-bold tracking-widest uppercase transition-colors focus:outline-none rounded-none ${
                    tradeSide === "LONG"
                      ? "bg-cyan-900/30 text-cyan-400"
                      : "bg-[#0f0f0f] text-gray-600 hover:text-gray-400 hover:bg-white/5"
                  }`}
                >
                  Long
                </button>
                <button
                  onClick={() => setTradeSide("SHORT")}
                  className={`py-3 text-[13px] font-bold tracking-widest uppercase transition-colors focus:outline-none rounded-none ${
                    tradeSide === "SHORT"
                      ? "bg-pink-900/30 text-pink-500"
                      : "bg-[#0f0f0f] text-gray-600 hover:text-gray-400 hover:bg-white/5"
                  }`}
                >
                  Short
                </button>
              </div>
            </div>

            {/* 3. Main Trading Logic */}
            <div className="flex-1 flex flex-col p-6 gap-6">
              {/* Collateral Input */}
              <div className="space-y-2">
                <div className="flex justify-between text-[12px] uppercase tracking-widest font-bold text-gray-500">
                  <span>Collateral</span>
                  <span>Balance: {account ? "2,450.00" : "--"} USDC</span>
                </div>
                <div className="relative group">
                  <input
                    type="number"
                    value={collateral}
                    onChange={(e) => setCollateral(Number(e.target.value))}
                    className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-2 focus:outline-none focus:border-white transition-colors placeholder-gray-800 rounded-none"
                    placeholder="0.00"
                  />
                  <span className="absolute right-0 top-2 text-sm text-gray-600">
                    USDC
                  </span>
                </div>
              </div>

              {/* LONG: Amount */}
              {tradeSide === "LONG" && (
                <div className="space-y-2">
                  <div className="text-[12px] uppercase tracking-widest font-bold text-gray-500">
                    Amount (Notional)
                  </div>
                  <div className="relative group">
                    <input
                      type="number"
                      value={
                        notional > 0 ? parseFloat(notional.toFixed(2)) : ""
                      }
                      onChange={(e) =>
                        handleLongAmountChange(Number(e.target.value))
                      }
                      className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-2 focus:outline-none focus:border-white transition-colors placeholder-gray-800 rounded-none"
                      placeholder="0.00"
                    />
                    <span className="absolute right-0 top-2 text-sm text-gray-600">
                      USDC
                    </span>
                  </div>
                </div>
              )}

              {/* SHORT: Amount & CR */}
              {tradeSide === "SHORT" && (
                <>
                  <div className="space-y-2">
                    <div className="text-[12px] uppercase tracking-widest font-bold text-gray-500">
                      Amount (Notional)
                    </div>
                    <div className="relative group">
                      <input
                        type="number"
                        value={
                          notional > 0 ? parseFloat(notional.toFixed(2)) : ""
                        }
                        onChange={(e) =>
                          handleShortAmountChange(Number(e.target.value))
                        }
                        className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-2 focus:outline-none focus:border-white transition-colors placeholder-gray-800 rounded-none"
                        placeholder="0.00"
                      />
                      <span className="absolute right-0 top-2 text-[12px] text-gray-600">
                        USDC
                      </span>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="flex justify-between text-[12px] uppercase tracking-widest font-bold text-gray-500">
                      <span>Collateral Ratio</span>
                      <span className="text-white">{shortCR.toFixed(0)}%</span>
                    </div>
                    <input
                      type="range"
                      min="110"
                      max="1500"
                      step="10"
                      value={shortCR}
                      onChange={(e) => setShortCR(Number(e.target.value))}
                      className="w-full h-0.5 bg-white/10 rounded-none appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-none"
                    />
                    <div className="flex justify-between text-[12px] text-gray-500 font-mono">
                      <span>110%</span>
                      <span>1500%</span>
                    </div>
                  </div>
                </>
              )}

              {/* Stats Box - TEXT-XS */}
              <div className="border border-white/10 p-4 space-y-2 bg-white/[0.02]">
                <div className="flex justify-between items-center text-[12px]">
                  <span className="text-gray-500 uppercase">Entry Rate</span>
                  <span className="font-mono text-white">
                    {currentRate.toFixed(2)}%
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-500 uppercase text-[12px]">
                    Liq. Rate
                  </span>
                  <span className="font-mono text-orange-500 text-[12px]">
                    {liqRate ? `${liqRate.toFixed(2)}%` : "None"}
                  </span>
                </div>
                <div className="flex justify-between items-center text-[12px]">
                  <span className="text-gray-500 uppercase">Notional</span>
                  <span className="font-mono text-white">
                    $
                    {notional.toLocaleString(undefined, {
                      maximumFractionDigits: 0,
                    })}
                  </span>
                </div>
                <div className="flex justify-between items-center text-xs">
                  <span className="text-gray-500 uppercase tracking-widest">
                    Est. Fee
                  </span>
                  <span className="font-mono text-gray-400">
                    {(notional * 0.001).toFixed(2)} USDC
                  </span>
                </div>
              </div>

              {/* Action Button */}
              <div className="mt-auto">
                {account ? (
                  <button
                    className={`w-full py-4 text-xs font-bold tracking-[0.2em] uppercase transition-all hover:opacity-90 focus:outline-none rounded-none ${
                      tradeSide === "LONG"
                        ? "bg-cyan-500 text-black hover:bg-cyan-400"
                        : "bg-pink-500 text-black hover:bg-pink-400"
                    }`}
                  >
                    {tradeSide} RATE
                  </button>
                ) : (
                  <button
                    onClick={connectWallet}
                    className="w-full py-4 border border-white/20 text-xs font-bold tracking-[0.2em] uppercase text-gray-400 hover:text-white hover:border-white transition-all focus:outline-none rounded-none"
                  >
                    Connect to Trade
                  </button>
                )}
              </div>
            </div>

            {/* 4. PnL Simulator */}
            <div className="border-t border-white/10 p-6 flex flex-col gap-4 bg-[#0a0a0a]">
              <div className="flex justify-between items-center">
                <span className="text-xs uppercase tracking-widest text-gray-500 font-bold">
                  PnL Simulator
                </span>
                <RefreshCw
                  size={15}
                  className="text-gray-600 cursor-pointer hover:text-white transition-colors"
                  onClick={() => setSimTargetRate(currentRate)}
                />
              </div>

              <div className="space-y-2">
                <div className="flex justify-between text-[13px] text-gray-500 font-mono">
                  <span>Rate_Scenario</span>
                  <span>
                    {simTargetRate ? simTargetRate.toFixed(2) : "0.00"}%
                  </span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="30"
                  step="0.1"
                  value={simTargetRate || currentRate}
                  onChange={(e) => setSimTargetRate(Number(e.target.value))}
                  className="w-full h-1 bg-white/10 rounded-none appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-none"
                />
                <div className="flex justify-between gap-1">
                  {[-50, -10, 10, 50].map((pct) => (
                    <button
                      key={pct}
                      onClick={() =>
                        setSimTargetRate(currentRate * (1 + pct / 100))
                      }
                      className="flex-1 py-1.5 bg-white/5 hover:bg-white/10 text-xs font-mono text-gray-400 focus:outline-none rounded-none"
                    >
                      {pct > 0 ? "+" : ""}
                      {pct}%
                    </button>
                  ))}
                </div>
              </div>

              <div className="">
                <div className="flex justify-between items-end">
                  <span className="text-[13px] text-gray-500">
                    Est. PnL (1Y)
                  </span>
                  <div
                    className={`text-right ${
                      simPnL.value >= 0 ? "text-green-500" : "text-red-500"
                    }`}
                  >
                    <div className="text-xl font-mono leading-none">
                      {simPnL.value >= 0 ? "+" : ""}
                      {simPnL.value.toLocaleString(undefined, {
                        maximumFractionDigits: 0,
                      })}{" "}
                      USDC
                    </div>
                    <div className="text-[12px] font-mono mt-1">
                      {simPnL.percent.toFixed(2)}% ROI
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- SUB-COMPONENTS ---
function ControlCell({ label, children, className = "" }) {
  return (
    <div className={`p-4 flex flex-col gap-3 ${className}`}>
      <span className="text-[11px] text-gray-500 uppercase tracking-[0.2em] font-bold">
        {label}
      </span>
      <div className="flex items-center w-full">{children}</div>
    </div>
  );
}

function StatItem({ label, value }) {
  return (
    <div>
      <div className="text-[10px] text-gray-400 uppercase tracking-widest mb-1">
        {label}
      </div>
      <div className="text-xl font-light text-white font-mono tracking-tighter">
        {value}
      </div>
    </div>
  );
}

function MetricBox({ label, value, sub, dimmed }) {
  return (
    <div
      className={`p-6 flex flex-col justify-between h-full min-h-[180px] ${
        dimmed ? "opacity-30" : ""
      }`}
    >
      <div className="text-[12px] text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
        {label} <Terminal size={15} className="opacity-90" />
      </div>
      <div>
        <div className="text-3xl font-light text-white mb-2 tracking-tight">
          {value}
          <span className="text-sm text-gray-600 ml-1">%</span>
        </div>
        <div className="text-[12px] text-gray-500 uppercase tracking-widest">
          {sub}
        </div>
      </div>
    </div>
  );
}

function TimeAgo({ timestamp }) {
  const [ago, setAgo] = useState("SYNC");
  useEffect(() => {
    if (!timestamp) return;
    const update = () => {
      const seconds = Math.floor(Date.now() / 1000 - timestamp);
      if (seconds < 1) setAgo("0s");
      else if (seconds < 60) setAgo(`${seconds}s`);
      else if (seconds < 3600) setAgo(`${Math.floor(seconds / 60)}m`);
      else setAgo(">1h");
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [timestamp]);
  return <span>{ago}</span>;
}

export default App;
