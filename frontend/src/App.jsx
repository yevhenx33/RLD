import React, { useState, useMemo, useEffect } from "react";
import useSWR from "swr";
import axios from "axios";
import { Terminal, Power, Activity, Wallet } from "lucide-react";
import RLDPerformanceChart from "./components/RLDChart";

const fetcher = (url) => axios.get(url).then((res) => res.data);

// Helper for initial date calculation
const getPastDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split("T")[0];
};
const getToday = () => new Date().toISOString().split("T")[0];

// --- SMART LABEL HELPER ---
const formatTwarLabel = (seconds) => {
  if (!seconds) return "TWAR_OFF";
  if (seconds < 60) return `TWAR_${seconds}S`;
  if (seconds < 3600) return `TWAR_${Math.round(seconds / 60)}M`;
  if (seconds < 86400)
    return `TWAR_${parseFloat((seconds / 3600).toFixed(1))}H`;
  return `TWAR_${parseFloat((seconds / 86400).toFixed(1))}D`;
};

function App() {
  // --- STATE ---
  const [tempStart, setTempStart] = useState(getPastDate(90));
  const [tempEnd, setTempEnd] = useState(getToday());
  const [appliedStart, setAppliedStart] = useState(getPastDate(90));
  const [appliedEnd, setAppliedEnd] = useState(getToday());

  const [activeRange, setActiveRange] = useState("3M");
  const [resolution, setResolution] = useState("4H");

  // TWAR State
  const [showTwar, setShowTwar] = useState(true);
  const [twarWindow, setTwarWindow] = useState(3600);
  const [tempTwarInput, setTempTwarInput] = useState(3600);

  // Wallet State
  const [account, setAccount] = useState(null);

  // --- ACTIONS ---
  const handleApplyDate = () => {
    setAppliedStart(tempStart);
    setAppliedEnd(tempEnd);
    setActiveRange("CUSTOM");
  };

  const handleApplyTwar = () => {
    setTwarWindow(tempTwarInput);
  };

  const handleQuickRange = (days, label) => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - days);

    // Auto-Resolution Logic
    if (days <= 3) setResolution("RAW");
    else if (days <= 30) setResolution("1H");
    else if (days <= 180) setResolution("4H");
    else setResolution("1D");

    const sStr = start.toISOString().split("T")[0];
    const eStr = end.toISOString().split("T")[0];

    setTempStart(sStr);
    setTempEnd(eStr);
    setAppliedStart(sStr);
    setAppliedEnd(eStr);
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
      alert("No Ethereum wallet found. Please install MetaMask.");
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

  // --- MATH ENGINE ---
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

  // --- STATISTICS ENGINE ---
  const stats = useMemo(() => {
    if (!rates || rates.length === 0)
      return { min: 0, max: 0, mean: 0, vol: 0 };

    const apys = rates.map((r) => r.apy);
    const min = Math.min(...apys);
    const max = Math.max(...apys);
    const sum = apys.reduce((a, b) => a + b, 0);
    const mean = sum / apys.length;

    const variance =
      apys.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / apys.length;
    const vol = Math.sqrt(variance);

    return { min, max, mean, vol };
  }, [rates]);

  // --- HELPERS ---
  const getDateRangeString = () => {
    if (!rates || rates.length === 0) return "WAITING_FOR_DATA...";
    const opts = { month: "2-digit", day: "2-digit", year: "2-digit" };
    const start = new Date(rates[0].timestamp * 1000);
    const end = new Date(rates[rates.length - 1].timestamp * 1000);
    return `${start.toLocaleDateString(
      "en-GB",
      opts
    )} -> ${end.toLocaleDateString("en-GB", opts)}`;
  };

  const isCappedRaw = resolution === "RAW" && rates && rates.length >= 30000;
  const latest =
    rates && rates.length > 0
      ? rates[rates.length - 1]
      : { apy: 0, block_number: 0, timestamp: 0 };
  const latestTwar =
    processedData.length > 0 ? processedData[processedData.length - 1].twar : 0;

  // --- RENDER ---
  if (error)
    return (
      <div className="h-screen flex items-center justify-center text-red-600 bg-black font-mono tracking-widest text-xs">
        ERR: API_DISCONNECTED
      </div>
    );
  if (!rates)
    return (
      <div className="h-screen flex items-center justify-center text-gray-500 bg-black font-mono tracking-widest text-xs animate-pulse">
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

            {/* NAV LINKS */}
            <div className="hidden md:flex text-[12px] items-center gap-1 font-bold tracking-[0.15em] uppercase">
              <span className="text-white/10">//</span>
              <span className="text-gray-600 hover:text-white transition-colors cursor-pointer px-2">
                TERMINAL
              </span>
              <span className="text-white/10">|</span>
              <span className="text-gray-600 hover:text-white transition-colors cursor-pointer px-2">
                BONDS
              </span>
              <span className="text-white/10">|</span>
              <span className="text-gray-600 hover:text-white transition-colors cursor-pointer px-2">
                CDS [SOON]
              </span>
              <span className="text-white/10">|</span>
              <span className="text-gray-600 hover:text-white transition-colors cursor-pointer px-2">
                RESEARCH
              </span>
            </div>
          </div>

          {/* CONNECT WALLET (Bigger Size) */}
          <button
            onClick={connectWallet}
            className="flex items-center gap-3 border border-white/10 bg-black hover:bg-white/5 hover:border-white/30 transition-all px-6 py-2 focus:outline-none"
          >
            <div
              className={`w-1.5 h-1.5 rounded-full ${
                account
                  ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]"
                  : "bg-gray-600"
              }`}
            ></div>
            <span className="text-[11px] font-bold tracking-widest uppercase text-white">
              {account
                ? `${account.substring(0, 6)}...${account.substring(
                    account.length - 4
                  )}`
                : "CONNECT WALLET"}
            </span>
          </button>
        </header>
      </div>

      {/* MAIN CONTENT */}
      <main className="max-w-[1800px] mx-auto w-full px-6 pb-12 flex flex-col gap-4 pt-0">
        {/* SECTION 1: METRICS & BRANDING */}
        <div className="border border-white/10 grid grid-cols-1 lg:grid-cols-12">
          {/* LEFT: Branding (Reduced Height) */}
          <div className="lg:col-span-4 flex flex-col justify-between p-6 border-b lg:border-b-0 lg:border-r border-white/10 h-full min-h-[180px]">
            <div>
              <div className="text-[10px] text-gray-700 mb-5 font-mono leading-tight tracking-tight">
                {/* Keep only the first line */}
                0x8787... 87 87 0B CA 3F 3F D6 33
              </div>
              <h2 className="text-3xl font-medium tracking-tight mb-3 leading-none">
                AAVE V3 <br />
                <span className="text-gray-600">USDC MARKET DATA</span>
              </h2>
            </div>

            <div className="mt-auto pt-4 border-t border-white/10 flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-widest text-gray-500">
                System Status
              </span>
              <span className="text-[10px] uppercase tracking-widest text-green-500">
                Operational
              </span>
            </div>
          </div>

          {/* RIGHT: Metric Cards Grid */}
          <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
            {/* Card 1: Spot */}
            <MetricBox
              label="CURRENT_SPOT"
              value={latest.apy.toFixed(2)}
              sub={
                <div className="flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-green-500 animate-pulse"></span>
                  <span>
                    LAST UPDATE: <TimeAgo timestamp={latest.timestamp} />
                  </span>
                </div>
              }
            />

            {/* Card 2: TWAR */}
            <MetricBox
              label={formatTwarLabel(twarWindow)}
              value={showTwar ? latestTwar.toFixed(2) : "OFF"}
              sub="MOVING AVG"
              dimmed={!showTwar}
            />

            {/* Card 3: Statistics */}
            <div className="p-6 flex flex-col justify-between h-full min-h-[180px]">
              <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                PERIOD_STATS
                <Activity size={10} className="opacity-20" />
              </div>

              <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                <StatItem label="MIN_RATE" value={`${stats.min.toFixed(2)}%`} />
                <StatItem label="MAX_RATE" value={`${stats.max.toFixed(2)}%`} />
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
          </div>
        </div>

        {/* SECTION 2: CONTROLS BAR */}
        <div className="border-y border-white/10 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 divide-y md:divide-y-0 md:divide-x divide-white/10">
          {/* Cell 1: Resolution */}
          <div className="p-4 flex flex-col gap-3">
            <Label text="RESOLUTION" />
            <div className="flex items-center w-full">
              {["RAW", "1H", "4H", "1D"].map((res) => (
                <button
                  key={res}
                  onClick={() => setResolution(res)}
                  className={`flex-1 text-xs py-1.5 uppercase tracking-wider transition-colors border border-transparent rounded-none focus:outline-none ${
                    resolution === res
                      ? "bg-white text-black border-white"
                      : "text-gray-500 hover:text-white hover:bg-white/5"
                  }`}
                >
                  {res}
                </button>
              ))}
            </div>
          </div>

          {/* Cell 2: Timeframe */}
          <div className="p-4 flex flex-col gap-3">
            <Label text="TIMEFRAME" />
            <div className="flex items-center w-full">
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
                  className={`flex-1 text-xs py-1.5 uppercase tracking-wider transition-colors border border-transparent rounded-none focus:outline-none ${
                    activeRange === btn.l
                      ? "bg-white text-black border-white"
                      : "text-gray-500 hover:text-white hover:bg-white/5"
                  }`}
                >
                  {btn.l}
                </button>
              ))}
            </div>
          </div>

          {/* Cell 3: Custom Range */}
          <div className="p-4 flex flex-col gap-3">
            <Label text="CUSTOM_RANGE" />
            <div className="flex items-center justify-between h-[30px]">
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
          </div>

          {/* Cell 4: Smoothing */}
          <div className="p-4 flex flex-col gap-3">
            <Label text="SMOOTHING (SEC)" />
            <div className="flex items-center justify-between gap-4 h-[30px]">
              {/* Input Group */}
              <div className="flex-1 flex items-center gap-2 h-full">
                <input
                  type="number"
                  value={tempTwarInput}
                  onChange={(e) => setTempTwarInput(Number(e.target.value))}
                  className="flex-1 bg-transparent border-b border-white/20 text-white font-mono text-sm h-full py-1 focus:outline-none focus:border-white text-right pr-2 rounded-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none"
                />
                <button
                  onClick={handleApplyTwar}
                  className="text-xs uppercase text-gray-500 hover:text-white border border-white/10 hover:border-white px-3 h-full flex items-center rounded-none transition-colors focus:outline-none"
                >
                  SET
                </button>
              </div>

              {/* Toggle Button */}
              <button
                onClick={() => setShowTwar(!showTwar)}
                className={`flex-1 h-full flex items-center justify-center gap-2 border text-xs uppercase font-bold tracking-wider transition-all rounded-none focus:outline-none ${
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
          </div>
        </div>

        {/* SECTION 3: CHART AREA */}
        <div className="relative">
          <div className="flex justify-between items-end mb-4 px-1">
            <div className="flex gap-8">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-cyan-400"></div>
                <span className="text-[10px] uppercase tracking-widest">
                  Spot Rate
                </span>
              </div>
              {showTwar && (
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 bg-pink-500"></div>
                  <span className="text-[10px] uppercase tracking-widest">
                    {formatTwarLabel(twarWindow)}
                  </span>
                </div>
              )}
            </div>
            <div className="text-[10px] font-mono text-gray-500 uppercase tracking-widest">
              RANGE: {getDateRangeString()}
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
                referenceLines={[
                  { y: 4.0, label: "FLOOR", stroke: "#10b981" },
                  { y: 20.0, label: "KINK", stroke: "#ef4444" },
                ]}
              />
            )}
          </div>
        </div>

        {/* FOOTER */}
        <div className="flex justify-between items-center text-[9px] text-gray-700 font-mono uppercase tracking-widest border-white/5">
          {/* LEFT: Network Stats */}
          <div className="flex items-center gap-6">
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

          {/* RIGHT: Meta Info */}
          <div className="flex gap-6">
            <div>Connected: Localhost:8000</div>
            <div>Pool: 0x8787...4E2</div>
          </div>
        </div>
      </main>
    </div>
  );
}

// --- SUB-COMPONENTS ---

function Label({ text }) {
  return (
    <span className="text-[10px] text-gray-600 uppercase tracking-[0.2em] font-bold">
      {text}
    </span>
  );
}

function StatItem({ label, value }) {
  return (
    <div>
      <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
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
      <div className="text-[11px] text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
        {label}
        <Terminal size={10} className="opacity-20" />
      </div>
      <div>
        <div className="text-3xl font-light text-white mb-2 tracking-tight">
          {value}
          <span className="text-xs text-gray-600 ml-1">%</span>
        </div>
        <div className="text-[9px] text-gray-500 uppercase tracking-widest">
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
