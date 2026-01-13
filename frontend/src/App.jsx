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
  FileDown,
  ChevronDown,
} from "lucide-react";
import RLDPerformanceChart from "./components/RLDChart";

import { useWallet } from "./context/WalletContext";
import Header from "./components/Header";
import TradingTerminal, { InputGroup, SummaryRow } from "./components/TradingTerminal";
import SettingsButton from "./components/SettingsButton";
import MobileDropdown from "./components/MobileDropdown";
const API_KEY = import.meta.env.VITE_API_KEY;
const authHeaders = API_KEY ? { "X-API-Key": API_KEY } : {};

const fetcher = (url) => axios.get(url, { headers: authHeaders }).then((res) => res.data);

// --- HELPER FUNCTIONS ---
const getPastDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split("T")[0];
};
const getToday = () => new Date().toISOString().split("T")[0];

const formatTwarLabel = (seconds) => {
  if (!seconds) return "RATE_TWAR_OFF";
  if (seconds < 60) return `RATE_TWAR_${seconds}S`;
  if (seconds < 3600) return `RATE_TWAR_${Math.round(seconds / 60)}M`;
  if (seconds < 86400)
    return `RATE_TWAR_${parseFloat((seconds / 3600).toFixed(1))}H`;
  return `RATE_TWAR_${parseFloat((seconds / 86400).toFixed(1))}D`;
};

const calculateCorrelation = (x, y) => {
    if (x.length !== y.length || x.length === 0) return 0;
    const n = x.length;
    const sumX = x.reduce((a, b) => a + b, 0);
    const sumY = y.reduce((a, b) => a + b, 0);
    const sumXY = x.reduce((sum, xi, i) => sum + xi * y[i], 0);
    const sumX2 = x.reduce((sum, xi) => sum + xi * xi, 0);
    const sumY2 = y.reduce((sum, yi) => sum + yi * yi, 0);

    const numerator = n * sumXY - sumX * sumY;
    const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
    
    if (denominator === 0) return 0;
    return numerator / denominator;
};

// --- APP COMPONENT ---


// --- APP COMPONENT ---
function App() {
  // Data State
  const [tempStart, setTempStart] = useState(getPastDate(9999));
  const [tempEnd, setTempEnd] = useState(getToday());
  const [appliedStart, setAppliedStart] = useState(getPastDate(9999));
  const [appliedEnd, setAppliedEnd] = useState(getToday());
  const [activeRange, setActiveRange] = useState("ALL");
  const [resolution, setResolution] = useState("1D");

  // Analysis State
  const [showTwar, setShowTwar] = useState(true);
  const [twarWindow, setTwarWindow] = useState(3600);
  const [tempTwarInput, setTempTwarInput] = useState(3600);

  // Trading State
  const { account, connectWallet, usdcBalance } = useWallet();
  const [tradeSide, setTradeSide] = useState("LONG");
  const [collateral, setCollateral] = useState(1000);
  const [shortCR, setShortCR] = useState(150);

  // PnL Simulation State
  const [simTargetRate, setSimTargetRate] = useState(null);



  // --- VISIBILITY STATE ---
  const [hiddenSeries, setHiddenSeries] = useState([]);
  const [visibleChartData, setVisibleChartData] = useState([]); // Zoomed data from chart

  const toggleSeries = (key) => {
    setHiddenSeries(prev => 
      prev.includes(key) 
        ? prev.filter(k => k !== key)
        : [...prev, key]
    );
  };

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
    if (days <= 3) setResolution("1H"); // 24H - 3D -> 1H
    else if (days <= 14) setResolution("1H"); // 1W - 2W -> 1H (High Res)
    else if (days <= 90) setResolution("4H"); // 1M - 3M -> 4H (Medium Res)
    else setResolution("1D"); // 1Y - ALL -> 1D (Low Res)
    setTempStart(start.toISOString().split("T")[0]);
    setTempEnd(end.toISOString().split("T")[0]);
    setAppliedStart(start.toISOString().split("T")[0]);
    setAppliedEnd(end.toISOString().split("T")[0]);
    setActiveRange(label);
  };

  const getUrl = () => {
    const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
    let url = `${API_BASE}/rates?resolution=${resolution}`;
    if (appliedStart) url += `&start_date=${appliedStart}`;
    if (appliedEnd) url += `&end_date=${appliedEnd}`;
    return url;
  };

  const handleDownloadCSV = async () => {
    try {
      const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
      // Fetch full history (approx 45k hours since 2021). Limit to 100k safe.
      const url = `${API_BASE}/rates?resolution=1H&limit=100000`;
      const res = await axios.get(url);
      const data = res.data;

      if (!data || data.length === 0) {
        alert("No data available to download");
        return;
      }

      const headers = "Timestamp,Date (UTC),APY (%),RATE_TWAR (%),ETH Price ($)\n";
      
      // Calculate TWAR for CSV
      const historyQueue = [];
      let runningArea = 0;
      let runningTime = 0;
      
      const rows = data.map((row, i) => {
        // TWAR Logic
        const prevTs = i > 0 ? data[i - 1].timestamp : row.timestamp;
        let dt = row.timestamp - prevTs;
        if (dt < 0) dt = 0;
        
        const apy = row.apy !== null && row.apy !== undefined ? row.apy : 0;
        const stepArea = apy * dt;
        historyQueue.push({ dt, area: stepArea, timestamp: row.timestamp });
        runningArea += stepArea;
        runningTime += dt;
        
        while (historyQueue.length > 0 && row.timestamp - historyQueue[0].timestamp > twarWindow) {
             const removed = historyQueue.shift();
             runningArea -= removed.area;
             runningTime -= removed.dt;
        }
        let twar = runningTime > 0 ? runningArea / runningTime : apy;
        twar = Math.max(0, twar);

        const date = new Date(row.timestamp * 1000).toISOString().replace("T", " ").replace("Z", "");
        const price = row.eth_price ? row.eth_price.toFixed(2) : "";
        return `${row.timestamp},${date},${apy.toFixed(4)},${twar.toFixed(4)},${price}`;
      }).join("\n");

      const csvContent = headers + rows;
      const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
      const link = document.createElement("a");
      const urlObj = URL.createObjectURL(blob);
      
      link.setAttribute("href", urlObj);
      link.setAttribute("download", `aave_usdc_rates_full_history_${getToday()}.csv`);
      link.style.visibility = "hidden";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch (err) {
      console.error("CSV Download Failed:", err);
      alert("Failed to download CSV data.");
    }
  };

  // Fetch Rates
  const { data: rates, error } = useSWR(getUrl(), fetcher, {
    refreshInterval: 10000,
  });

  // --- SYNC DATE INPUTS WITH ACTUAL DATA ---
  useEffect(() => {
    if (activeRange !== "CUSTOM" && rates && rates.length > 0) {
      const firstTs = rates[0].timestamp;
      const lastTs = rates[rates.length - 1].timestamp;
      
      const startStr = new Date(firstTs * 1000).toISOString().split("T")[0];
      const endStr = new Date(lastTs * 1000).toISOString().split("T")[0];
      
      setTempStart(startStr);
      setTempEnd(endStr);
    }
  }, [rates, activeRange]);

  // Fetch ETH Prices
  const getEthUrl = () => {
    const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
    let url = `${API_BASE}/eth-prices?resolution=${resolution}`;
    if (appliedStart) url += `&start_date=${appliedStart}`;
    if (appliedEnd) url += `&end_date=${appliedEnd}`;
    return url;
  };
  const { data: ethPrices } = useSWR(getEthUrl(), fetcher);

  // Calculations
  const processedData = useMemo(() => {
    if (!rates || rates.length === 0) return [];
    
    // ETH Price Map
    const priceMap = new Map();
    if (ethPrices && ethPrices.length > 0) {
        ethPrices.forEach(p => priceMap.set(p.timestamp, p.price));
    }

    const result = [];
    const historyQueue = [];
    let runningArea = 0;
    let runningTime = 0;
    
    // Determine bucket size for matching
    // If RAW, we match to the nearest hour (3600)
    // If others, we match to that resolution
    const bucketSize = resolution === "1W" ? 604800 :
                       resolution === "4H" ? 14400 : 
                       resolution === "1D" ? 86400 : 3600;

    for (let i = 0; i < rates.length; i++) {
        const current = rates[i];
        
        // --- TWAR Logic ---
        const prevTs = i > 0 ? rates[i - 1].timestamp : current.timestamp;
        let dt = current.timestamp - prevTs;
        if (dt < 0) dt = 0;
        
        const apy = current.apy !== null && current.apy !== undefined ? current.apy : 0;
        const stepArea = apy * dt;
        
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
        let twarValue = runningTime > 0 ? runningArea / runningTime : apy;
        twarValue = Math.max(0, twarValue);

        // Lookup Price (Loose Matching)
        // 1. Check if backend already provided it (Aggregated Mode)
        let price = current.eth_price;

        if (price === undefined || price === null) {
            // 2. Fallback: Client-side Map Lookup
            price = priceMap.get(current.timestamp);
            
            if (price === undefined) {
                 const bucketTs = Math.floor(current.timestamp / bucketSize) * bucketSize;
                 price = priceMap.get(bucketTs);
            }
        }

        result.push({ 
            ...current, 
            twar: twarValue,
            ethPrice: price || null
        });
    }
    return result;
  }, [rates, ethPrices, twarWindow, resolution]);

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

  // Downsample data for rendering performance
  // Recharts crashes with >3-5k points (SVG overload).
  // We limit to ~2000 visual points.
  const chartData = useMemo(() => {
     if (processedData.length <= 2000) return processedData;
     
     const step = Math.ceil(processedData.length / 2000);
     return processedData.filter((_, i) => i % step === 0);
  }, [processedData]);

  // const isCappedRaw = resolution === "RAW" && rates && rates.length >= 30000;
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
      <div className="h-screen w-screen flex items-center justify-center text-red-600 bg-black font-mono text-xs">
        ERR: API_DISCONNECTED
      </div>
    );
  if (!rates)
    return (
      <div className="h-screen w-screen flex items-center justify-center text-gray-500 bg-black font-mono text-xs animate-pulse">
        SYNCING...
      </div>
    );

  return (
    <div className="min-h-screen bg-[#080808] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      {/* MAIN GRID LAYOUT */}
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12 ">
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
                    Pool_Contract
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
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]"> 
                    {/* MOBILE LAYOUT (Grid Row) */}
                    <div className="md:hidden h-full flex flex-col justify-between">
                         <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
                            CURRENT_SPOT <Terminal size={15} className="opacity-90" />
                          </div>
                          <div className="grid grid-cols-2 gap-x-4 mt-auto">
                            <StatItem
                              label="SPOT_RATE"
                              value={`${latest.apy.toFixed(2)}%`}
                            />
                            <div>
                                <div className="text-[10px] text-gray-400 uppercase tracking-widest mb-1">
                                    24H_CHANGE
                                </div>
                                <div className={`text-xl font-light font-mono tracking-tighter flex items-center gap-1 ${dailyChange >= 0 ? "text-green-500" : "text-red-500"}`}>
                                    {dailyChange > 0 ? "+" : ""}{dailyChange.toFixed(2)}%
                                </div>
                            </div>
                          </div>
                    </div>

                    {/* DESKTOP LAYOUT (Original MetricBox Content) */}
                    <div className="hidden md:block h-full">
                        <div className="text-[12px] text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
                            CURRENT_SPOT <Terminal size={15} className="opacity-90" />
                        </div>
                        <div>
                            <div className="text-3xl font-light text-white mb-2 tracking-tight">
                            {latest.apy.toFixed(2)}
                            <span className="text-sm text-gray-600 ml-1">%</span>
                            </div>
                            <div className="text-[12px] text-gray-500 uppercase tracking-widest">
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
                            </div>
                        </div>
                    </div>
                </div>

                {/* CARD 2: PERIOD STATS */}
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                  <div className="text-[10px] md:text-[12px] text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                    PERIOD_STATS <Activity size={15} className="opacity-90" />
                  </div>
                  
                  {/* MOBILE: Range & Volatility Only (Row Layout like Funding Rate) */}
                  <div className="md:hidden grid grid-cols-2 gap-x-4 mt-auto">
                    <StatItem
                      label="RANGE[MIN - MAX]"
                      value={`${stats.min.toFixed(2)} - ${stats.max.toFixed(2)}`}
                    />
                    <StatItem
                      label="VOLATILITY"
                      value={`±${stats.vol.toFixed(2)}%`}
                    />
                  </div>

                  {/* DESKTOP: Full Grid */}
                  <div className="hidden md:grid grid-cols-2 gap-y-6 gap-x-4">
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
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                  <div className="text-[10px] md:text-[12px] text-gray-500 uppercase tracking-widest flex justify-between">
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
            <div className="order-last md:order-none border-y border-white/10 grid grid-cols-2 xl:grid-cols-4">
              <ControlCell label="TIMEFRAME" className="pl-0 border-r md:border-r-0 border-white/10 pr-4 md:pr-4">
                {/* Mobile Dropdown */}
                <MobileDropdown 
                    value={activeRange} 
                    options={[
                        { label: "1D", value: { d: 1, l: "1D" } },
                        { label: "1W", value: { d: 7, l: "1W" } },
                        { label: "1M", value: { d: 30, l: "1M" } },
                        { label: "3M", value: { d: 90, l: "3M" } },
                        { label: "1Y", value: { d: 365, l: "1Y" } },
                        { label: "ALL", value: { d: 9999, l: "ALL" } },
                    ].map(o => ({ label: o.label, value: o.value }))}
                    onChange={(v) => handleQuickRange(v.d, v.l)}
                />
                
                {/* Desktop Buttons */}
                <div className="hidden md:flex w-full gap-0">
                    {[
                    { l: "1D", d: 1 },
                    { l: "1W", d: 7 },
                    { l: "1M", d: 30 },
                    { l: "3M", d: 90 },
                    { l: "1Y", d: 365 },
                    { l: "ALL", d: 9999 },
                    ].map((btn) => (
                    <SettingsButton
                        key={btn.l}
                        onClick={() => handleQuickRange(btn.d, btn.l)}
                        isActive={activeRange === btn.l}
                        className="flex-1"
                    >
                        {btn.l}
                    </SettingsButton>
                    ))}
                </div>
              </ControlCell>
              <ControlCell label="RESOLUTION" className="pl-4 md:pl-4 ">
                {/* Mobile Dropdown */}
                <MobileDropdown 
                    value={resolution} 
                    options={["1H", "4H", "1D", "1W"].map(r => ({ label: r, value: r }))}
                    onChange={(v) => setResolution(v)}
                />

                {/* Desktop Buttons */}
                <div className="hidden md:flex w-full gap-0">
                    {["1H", "4H", "1D", "1W"].map((res) => (
                    <SettingsButton
                        key={res}
                        onClick={() => setResolution(res)}
                        isActive={resolution === res}
                        className="flex-1"
                    >
                        {res}
                    </SettingsButton>
                    ))}
                </div>
              </ControlCell>
              <ControlCell label="CUSTOM_RANGE" className="hidden md:flex">
                <div className="flex flex-col sm:flex-row items-center justify-between h-auto sm:h-[30px] w-full gap-2">
                  <div className="flex items-center w-full sm:w-[76%] gap-2">
                      <input
                        type="date"
                        value={tempStart}
                        onChange={(e) => setTempStart(e.target.value)}
                        className="bg-transparent border-b border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-1/2 py-1 rounded-none"
                      />
                      <span className="text-gray-600 text-xs">-</span>
                      <input
                        type="date"
                        value={tempEnd}
                        onChange={(e) => setTempEnd(e.target.value)}
                        className="bg-transparent border-b border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-1/2 py-1 rounded-none"
                      />
                  </div>
                  <SettingsButton
                    onClick={handleApplyDate}
                    className="px-3 h-full flex items-center w-full sm:w-auto mt-2 sm:mt-0"
                  >
                    SET
                  </SettingsButton>
                </div>
              </ControlCell>
              <ControlCell label="TWAR_SMOOTHING_[SEC]" className="pr-0 hidden md:flex">
                <div className="flex items-center justify-end md:justify-between gap-2 h-[30px] w-full">
                  <input
                    type="number"
                    value={tempTwarInput}
                    onChange={(e) => setTempTwarInput(Number(e.target.value))}
                    className="hidden md:block flex-1 bg-transparent border-b border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono h-full py-1 text-right pr-2 rounded-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                  />
                  <SettingsButton
                    onClick={handleApplyTwar}
                    className="hidden md:flex px-3 h-full items-center"
                  >
                    SET
                  </SettingsButton>
                  <SettingsButton
                    onClick={() => setShowTwar(!showTwar)}
                    isActive={showTwar}
                    className="w-full md:w-auto px-3 h-full flex items-center justify-center gap-2"
                  >
                    <Power
                      size={12}
                      className={showTwar ? "text-black" : "text-gray-600"}
                    />
                    <span className="md:hidden text-[10px] uppercase font-bold tracking-widest">
                        {showTwar ? "TWAR: ON" : "TWAR: OFF"}
                    </span>
                  </SettingsButton>
                </div>
              </ControlCell>
            </div>

            {/* 3. CHART */}
            <div className="relative flex-1 min-h-[350px] md:min-h-[400px]">
              <div className="flex flex-col md:flex-row justify-between items-start md:items-end mb-4 px-1 gap-3 md:gap-0">
                <div className="flex gap-4 md:gap-8 flex-wrap">
                  <div 
                    className={`flex items-center gap-2 cursor-pointer transition-all ${hiddenSeries.includes('apy') ? 'opacity-50 line-through' : 'opacity-100 hover:opacity-80'}`}
                    onClick={() => toggleSeries('apy')}
                  >
                    <div className="w-2 h-2 bg-cyan-400"></div>
                    <span className="text-[11px] uppercase tracking-widest">
                      Spot_Rate
                    </span>
                  </div>
                  {showTwar && (
                    <div 
                        className={`flex items-center gap-2 cursor-pointer transition-all ${hiddenSeries.includes('twar') ? 'opacity-50 line-through' : 'opacity-100 hover:opacity-80'}`}
                        onClick={() => toggleSeries('twar')}
                    >
                      <div className="w-2 h-2 bg-pink-500"></div>
                      <span className="text-[11px] uppercase tracking-widest">
                        {formatTwarLabel(twarWindow)}
                      </span>
                    </div>
                  )}
                  {/* ETH Price Legend */}
                  <div 
                    className={`flex items-center gap-2 cursor-pointer transition-all ${hiddenSeries.includes('ethPrice') ? 'opacity-50 line-through' : 'opacity-100 hover:opacity-80'}`}
                    onClick={() => toggleSeries('ethPrice')}
                  >
                    <div className="w-2 h-2 bg-zinc-400"></div>
                    <span className="text-[11px] uppercase tracking-widest">
                      ETH_Price
                    </span>
                  </div>
                  {/* CSV Download Button */}
                  <button 
                    onClick={handleDownloadCSV}
                    className="flex items-center gap-2 text-[11px] uppercase tracking-widest text-gray-500 hover:text-white transition-colors focus:outline-none group"
                    title="Download Full History (CSV)"
                  >
                    <FileDown size={12} className="group-hover:text-cyan-400 transition-colors" />
                    <span className="group-hover:underline decoration-cyan-400 underline-offset-4">CSV</span>
                  </button>
                </div>
                  <div className="text-[11px] font-mono text-gray-500 uppercase tracking-widest flex items-center gap-4">
                    {/* Correlation Display */}
                    {processedData.length > 0 && (
                         <span className={(() => {
                            const source = visibleChartData.length > 0 ? visibleChartData : processedData;
                            const apys = source.map(d => d.apy || 0);
                            const prices = source.map(d => d.ethPrice || 0);
                            const corr = calculateCorrelation(apys, prices);
                            return corr > 0 ? "text-green-500" : "text-red-500";
                         })()}>
                            CORRELATION: {(() => {
                                const source = visibleChartData.length > 0 ? visibleChartData : processedData;
                                const apys = source.map(d => d.apy || 0);
                                const prices = source.map(d => d.ethPrice || 0);
                                return calculateCorrelation(apys, prices).toFixed(2);
                            })()}
                         </span>
                    )}


                </div>
              </div>
              <div className="h-[350px] md:h-[500px] w-full border border-white/10 p-4 bg-[#080808]">
                {processedData.length === 0 ? (
                  <div className="h-full flex items-center justify-center text-gray-700 text-xs tracking-widest uppercase">
                    No Data Available
                  </div>
                ) : (
                  <RLDPerformanceChart
                    data={chartData}
                    onDataChange={setVisibleChartData}
                    areas={[
                      { key: "apy", name: "Spot", color: "#22d3ee" },
                      { key: "ethPrice", name: "ETH Price", color: "#a1a1aa", yAxisId: "right" },
                      ...(showTwar
                        ? [{ key: "twar", name: "TWAR", color: "#ec4899" }]
                        : []),
                    ].filter(a => !hiddenSeries.includes(a.key))}
                  />
                )}
              </div>
            </div>
          </div>

          {/* === RIGHT COLUMN: TRADING TERMINAL + PNL (Span 3) === */}
          <TradingTerminal
            account={account}
            connectWallet={connectWallet}
            title="Synthetic_Rates"
            Icon={Terminal}
            tabs={[
                { id: "LONG", label: "Long", onClick: () => setTradeSide("LONG"), isActive: tradeSide === "LONG", color: "cyan" },
                { id: "SHORT", label: "Short", onClick: () => setTradeSide("SHORT"), isActive: tradeSide === "SHORT", color: "pink" }
            ]}
            actionButton={{
                label: `${tradeSide} RATE`,
                onClick: () => {}, 
                variant: tradeSide === "LONG" ? "cyan" : "pink",
            }}
            footer={
               <div className="border-t border-white/10 p-6 flex flex-col gap-4 bg-[#0a0a0a]">
                  <div className="flex justify-between items-center">
                    <span className="text-xs uppercase tracking-widest text-gray-500 font-bold">
                      PnL_Simulator
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
                        <SettingsButton
                          key={pct}
                          onClick={() =>
                            setSimTargetRate(currentRate * (1 + pct / 100))
                          }
                          className="flex-1"
                        >
                          {pct > 0 ? "+" : ""}
                          {pct}%
                        </SettingsButton>
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
            }
          >
             {/* Collateral Input */}
             <InputGroup 
               label="Collateral"
               subLabel={`Balance: ${account ? parseFloat(usdcBalance).toFixed(2) : "--"} USDC`}
               value={collateral}
               onChange={(v) => setCollateral(Number(v))} 
               suffix="USDC"
             />

             {/* LONG: Amount */}
             {tradeSide === "LONG" && (
                <InputGroup 
                   label="Amount_Notional"
                   value={notional > 0 ? parseFloat(notional.toFixed(2)) : ""}
                   onChange={(v) => handleLongAmountChange(Number(v))}
                   suffix="USDC"
                />
             )}

             {/* SHORT: Amount & CR */}
             {tradeSide === "SHORT" && (
                <>
                   <InputGroup 
                      label="Amount_Notional"
                      value={notional > 0 ? parseFloat(notional.toFixed(2)) : ""}
                      onChange={(v) => handleShortAmountChange(Number(v))}
                      suffix="USDC"
                   />
                   
                   <div className="space-y-2">
                    <div className="flex justify-between text-[12px] uppercase tracking-widest font-bold text-gray-500">
                      <span>Collateral_Ratio</span>
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

             {/* Stats Box */}
              <div className="border border-white/10 p-4 space-y-2 bg-white/[0.02] text-[12px]">
                <SummaryRow label="Entry_Rate" value={`${currentRate.toFixed(2)}%`} />
                
                <div className="flex justify-between items-center">
                  <span className="text-gray-500 uppercase text-[12px]">
                    Liq. Rate
                  </span>
                  <span className="font-mono text-orange-500 text-[12px]">
                    {liqRate ? `${liqRate && liqRate.toFixed(2)}%` : "None"}
                  </span>
                </div>
                
                <SummaryRow label="Notional" value={`$${notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
                
                <div className="flex justify-between items-center text-xs">
                  <span className="text-gray-500 uppercase text-[12px]">
                    Est. Fee
                  </span>{" "}
                  <span className="font-mono text-gray-400">
                    {(notional * 0.001).toFixed(2)} USDC
                  </span>
                </div>
              </div>
          </TradingTerminal>
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



export default App;
