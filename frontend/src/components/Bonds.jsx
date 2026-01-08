import React, { useState, useMemo, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import useSWR from "swr";
import axios from "axios";
import {
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ComposedChart,
} from "recharts";
import {
  Terminal,
  Activity,
  Clock,
  TrendingUp,
  TrendingDown,
  Shield,
  Percent,
  Calendar,
  Settings,
  AlertTriangle,
} from "lucide-react";

// --- CONSTANTS & UTILS ---

const API_URL = "http://127.0.0.1:8000";

const fetcher = (url) => axios.get(url).then((res) => res.data);

const getPastDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split("T")[0];
};

const getFutureDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().split("T")[0];
};

const getDaysDiff = (dateStr) => {
  const d1 = new Date();
  const d2 = new Date(dateStr);
  const diffTime = Math.abs(d2 - d1);
  return Math.ceil(diffTime / (1000 * 60 * 60 * 24));
};

const getToday = () => new Date().toISOString().split("T")[0];

const formatNum = (num, digits = 2, symbol = "") => {
  if (num === null || num === undefined) return "--";
  return `${symbol}${num.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
};

// --- WEALTH PROJECTION LOGIC ---

function useWealthProjection(collateral, currentRate, days = 90) {
  return useMemo(() => {
    const data = [];
    const fixedRateDaily = currentRate / 100 / 365;
    const volatility = 0.02;
    const cycleSpeed = 0.1;

    for (let i = 0; i <= days; i++) {
      const fixedBalance = collateral * (1 + fixedRateDaily * i);
      const trend = collateral * (1 + fixedRateDaily * 1.2 * i);
      const wave = Math.sin(i * cycleSpeed) * (collateral * volatility);
      const noise = (Math.random() - 0.5) * (collateral * 0.005);
      const variableBalance = trend + wave + noise;

      data.push({
        day: i,
        fixed: fixedBalance,
        variable: variableBalance,
        label: `Day ${i}`,
      });
    }
    return data;
  }, [collateral, currentRate, days]);
}

const CustomWealthTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-zinc-950 border border-zinc-800 p-3 rounded shadow-2xl font-mono text-xs z-50">
        <p className="text-zinc-500 mb-2 border-b border-zinc-800 pb-1">
          {payload[0].payload.label} Projection
        </p>
        {payload.map((entry, index) => (
          <div
            key={index}
            className="flex items-center justify-between gap-4 mb-1"
          >
            <div className="flex items-center gap-2">
              <div
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-zinc-300 font-medium capitalize">
                {entry.name}:
              </span>
            </div>
            <span className="text-white font-bold">
              $
              {entry.value.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

const WealthProjectionChart = ({ data, collateral, theme = "cyan" }) => {
  if (!data || data.length === 0) return null;

  const finalPoint = data[data.length - 1];
  const valueAtMaturity = finalPoint.fixed;
  const calculatedWealth = valueAtMaturity - collateral;

  // Define colors based on theme
  const mainColor = theme === "pink" ? "#ec4899" : "#22d3ee"; // Pink-500 vs Cyan-400
  const labelColor = theme === "pink" ? "text-pink-500" : "text-cyan-400";
  const bgColor = theme === "pink" ? "bg-pink-500" : "bg-cyan-400";

  return (
    <div className="w-full h-full select-none bg-[#080808] border border-white/10 p-6 flex flex-col">
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="text-[11px] font-bold uppercase tracking-widest text-gray-500 mb-1">
            Value at Maturity
          </div>
          <div className="text-3xl font-light text-white font-mono tracking-tight">
            ${formatNum(valueAtMaturity, 2)}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[11px] font-bold uppercase tracking-widest text-gray-500 mb-1">
            {theme === "pink" ? "Projected Hedge" : "Calculated Wealth"}
          </div>
          <div
            className={`text-xl ${
              theme === "pink" ? "text-pink-500" : "text-green-500"
            } font-mono tracking-tight`}
          >
            +${formatNum(calculatedWealth, 2)}
          </div>
        </div>
      </div>

      <div className="flex justify-between items-center mb-4">
        <div className="text-[10px] text-gray-600 uppercase tracking-widest">
          Simulated Path
        </div>
        <div className="flex gap-4">
          <div
            className={`flex items-center gap-2 text-[10px] ${labelColor} uppercase tracking-wider`}
          >
            <div className={`w-2 h-0.5 ${bgColor}`}></div> Fixed
          </div>
          <div className="flex items-center gap-2 text-[10px] text-gray-400 uppercase tracking-wider">
            <div className="w-2 h-0.5 bg-gray-400 border border-dashed"></div>{" "}
            Variable
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 border border-white/5 bg-[#0a0a0a] p-2 relative">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={{ top: 10, right: 10, left: 10, bottom: 0 }}
          >
            <defs>
              <linearGradient
                id={`gradientFixed-${theme}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="5%" stopColor={mainColor} stopOpacity={0.2} />
                <stop offset="95%" stopColor={mainColor} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradientVar" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#9ca3af" stopOpacity={0.1} />
                <stop offset="95%" stopColor="#9ca3af" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="#27272a"
              vertical={false}
            />
            <XAxis
              dataKey="day"
              tickLine={false}
              axisLine={false}
              tickFormatter={(d) => `D${d}`}
              stroke="#52525b"
              fontSize={10}
              minTickGap={30}
            />
            <YAxis
              orientation="right"
              stroke="#52525b"
              fontSize={10}
              tickLine={false}
              axisLine={false}
              tickFormatter={(val) =>
                `$${val.toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })}`
              }
              domain={["auto", "auto"]}
              width={40}
            />
            <Tooltip
              content={<CustomWealthTooltip />}
              cursor={{ stroke: "#52525b", strokeDasharray: "4 4" }}
            />
            <Area
              type="monotone"
              dataKey="variable"
              name="Variable"
              stroke="#9ca3af"
              strokeWidth={1}
              strokeDasharray="3 3"
              fill="url(#gradientVar)"
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="fixed"
              name="Fixed"
              stroke={mainColor}
              strokeWidth={2}
              fill={`url(#gradientFixed-${theme})`}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

// --- CUSTOM HOOKS ---

function useMarketData(resolution = "4H") {
  const [dates] = useState({ start: getPastDate(90), end: getToday() });
  const getUrl = () =>
    `${API_URL}/rates?resolution=${resolution}&start_date=${dates.start}&end_date=${dates.end}`;
  const {
    data: rates,
    error,
    isLoading,
  } = useSWR(getUrl(), fetcher, { refreshInterval: 10000 });

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

  const dailyChange = useMemo(() => {
    if (!rates || rates.length < 2) return 0;
    const latestTs = rates[rates.length - 1].timestamp;
    const targetTs = latestTs - 86400;
    const closest = rates.reduce((prev, curr) =>
      Math.abs(curr.timestamp - targetTs) < Math.abs(prev.timestamp - targetTs)
        ? curr
        : prev
    );
    return rates[rates.length - 1].apy - closest.apy;
  }, [rates]);

  const latest =
    rates && rates.length > 0
      ? rates[rates.length - 1]
      : { apy: 0, block_number: 0, timestamp: 0 };
  const isCappedRaw = resolution === "RAW" && rates && rates.length >= 30000;
  return { rates, error, isLoading, stats, dailyChange, latest, isCappedRaw };
}

function useTradeLogic(currentRate) {
  const [activeProduct, setActiveProduct] = useState("FIXED_YIELD");
  const [activeTab, setActiveTab] = useState("OPEN");

  const [notional, setNotional] = useState(1000);
  const [maturityDays, setMaturityDays] = useState(90);
  const [maturityDate, setMaturityDate] = useState(getFutureDate(90));
  const [slippage, setSlippage] = useState(0.5);

  const handleDaysChange = (days) => {
    setMaturityDays(days);
    setMaturityDate(getFutureDate(days));
  };

  const handleDateChange = (date) => {
    setMaturityDate(date);
    setMaturityDays(getDaysDiff(date));
  };

  return {
    state: {
      activeProduct,
      activeTab,
      notional,
      maturityDays,
      maturityDate,
      slippage,
    },
    actions: {
      setActiveProduct,
      setActiveTab,
      setNotional,
      handleDaysChange,
      handleDateChange,
      setSlippage,
    },
  };
}

// --- UI COMPONENTS ---

const AppHeader = ({
  latest,
  isCapped,
  account,
  connectWallet,
  ratesLoaded,
}) => (
  <div className="sticky top-0 bg-[#050505]/95 backdrop-blur-sm z-50 w-full border-b border-transparent">
    <header className="max-w-[1800px] mx-auto px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-5 pl-1">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 bg-white"></div>
          <h1 className="text-sm font-bold tracking-widest uppercase">RLD</h1>
        </div>
        <nav className="hidden md:flex text-[12px] items-center gap-1 font-bold tracking-[0.15em] uppercase">
          <span className="text-white/10">//</span>
          <Link
            to="/"
            className="text-gray-400 hover:text-white transition-colors px-2 tracking-widest"
          >
            TERMINAL
          </Link>
          <span className="text-white/10">|</span>
          <span className="text-white px-2 tracking-widest cursor-default">
            BONDS
          </span>
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
        </nav>
      </div>
      <div className="flex items-center gap-6">
        <div className="hidden md:flex items-center gap-6 text-[11px] uppercase tracking-widest text-gray-500 border-r border-white/10 pr-6 h-6">
          <span className="flex items-center gap-2">
            <div
              className={`w-1.5 h-1.5 ${
                ratesLoaded ? "bg-green-500" : "bg-red-500"
              }`}
            ></div>
            {isCapped ? "WARN: LIMIT_ACTIVE" : "NET: STABLE"}
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
);

const MetricsGrid = ({ latest, dailyChange, stats }) => (
  <div className="grid grid-cols-1 md:grid-cols-3 h-full border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
    <MetricCell
      label="CURRENT_SPOT"
      Icon={Terminal}
      content={
        <div>
          <div className="text-3xl font-light text-white mb-2 tracking-tight">
            {formatNum(latest.apy)}
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
                {formatNum(dailyChange)}%
              </span>
            </div>
          </div>
        </div>
      }
    />
    <MetricCell
      label="PERIOD_STATS"
      Icon={Activity}
      content={
        <div className="grid grid-cols-2 gap-y-6 gap-x-4">
          <StatItem label="MIN_RATE" value={`${formatNum(stats.min)}%`} />
          <StatItem label="MAX_RATE" value={`${formatNum(stats.max)}%`} />
          <StatItem label="AVG_RATE" value={`${formatNum(stats.mean)}%`} />
          <StatItem label="VOLATILITY" value={`±${formatNum(stats.vol)}%`} />
        </div>
      }
    />
    <MetricCell
      label="FUNDING_RATE"
      Icon={Clock}
      content={
        <div className="grid grid-cols-2 gap-x-4 mt-auto h-full items-end pb-1">
          <StatItem
            label="DAILY"
            value={`${formatNum(latest.apy / 365, 4)}%`}
          />
          <StatItem label="YEARLY" value={`${formatNum(latest.apy)}%`} />
        </div>
      }
    />
  </div>
);

const TradingTerminal = ({
  account,
  connectWallet,
  currentRate,
  state,
  actions,
}) => {
  const {
    activeProduct,
    activeTab,
    notional,
    maturityDays,
    maturityDate,
    slippage,
  } = state;
  const {
    setActiveTab,
    setNotional,
    handleDaysChange,
    handleDateChange,
    setSlippage,
  } = actions;

  // Logic for Close View Mock
  const accruedYield = useMemo(() => {
    // Mock: Assume held for 30 days at current rate
    return notional * (currentRate / 100) * (30 / 365);
  }, [notional, currentRate]);

  return (
    <div className="xl:col-span-3 border border-white/10 bg-[#080808] flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center">
        <h3 className="text-xs font-bold tracking-widest text-white uppercase flex items-center gap-2">
          <Terminal size={15} className="text-gray-500" /> {activeProduct}
        </h3>
        <span className="text-[10px] text-gray-600 uppercase tracking-widest">
          {activeProduct === "FIXED_YIELD" ? "SHORT RLP" : "LONG RLP"}
        </span>
      </div>

      {/* Tabs (Monochrome) */}
      <div className="grid grid-cols-2 border-b border-white/10">
        {["OPEN", "CLOSE"].map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`py-3 text-[12px] font-bold tracking-widest uppercase transition-colors focus:outline-none rounded-none ${
              activeTab === tab
                ? "bg-white text-black"
                : "bg-[#080808] text-gray-500 hover:text-white hover:bg-white/5"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Inputs Area */}
      <div className="flex-1 flex flex-col p-6 gap-6">
        {/* --- OPEN TAB (SHARED LOGIC) --- */}
        {activeTab === "OPEN" && (
          <>
            <InputGroup
              label="Notional Amount"
              subLabel={`Bal: ${account ? "2,450" : "--"} USDC`}
              value={notional}
              onChange={(v) => setNotional(Number(v))}
              suffix="USDC"
            />

            <div className="space-y-3">
              <div className="flex justify-between items-end">
                <span className="text-[11px] text-gray-500 uppercase tracking-widest font-bold">
                  Maturity Date
                </span>
                <span
                  className={`text-[11px] font-mono ${
                    activeProduct === "FIXED_BORROW"
                      ? "text-pink-500"
                      : "text-cyan-400"
                  }`}
                >
                  {maturityDays} Days
                </span>
              </div>

              <div className="relative group">
                <div className="flex items-center gap-2 border-b border-white/20 pb-1">
                  <Calendar size={14} className="text-gray-500" />
                  <input
                    type="date"
                    value={maturityDate}
                    onChange={(e) => handleDateChange(e.target.value)}
                    className="bg-transparent text-sm font-mono text-white focus:outline-none w-full uppercase [&::-webkit-calendar-picker-indicator]:invert"
                  />
                </div>
              </div>

              <div className="pt-2">
                <input
                  type="range"
                  min="7"
                  max="365"
                  step="1"
                  value={maturityDays}
                  onChange={(e) => handleDaysChange(Number(e.target.value))}
                  className="w-full h-0.5 bg-white/10 rounded-none appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-none hover:[&::-webkit-slider-thumb]:scale-125 transition-all"
                />
                <div className="flex justify-between text-[9px] text-gray-600 font-mono mt-1">
                  <span>1W</span>
                  <span>1Y</span>
                </div>
              </div>
            </div>

            <div className="border border-white/10 p-4 space-y-3 bg-white/[0.02]">
              <SummaryRow
                label="Entry Rate"
                value={`${formatNum(currentRate)}%`}
              />
              <div className="flex justify-between items-center pt-2">
                <div className="flex items-center gap-1.5 text-[11px] text-gray-500 uppercase tracking-widest">
                  <Settings size={12} /> Slippage
                </div>
                <div className="flex gap-1">
                  {[0.1, 0.5, 1.0].map((s) => (
                    <button
                      key={s}
                      onClick={() => setSlippage(s)}
                      className={`text-[10px] px-2 py-0.5 font-mono border transition-colors ${
                        slippage === s
                          ? "border-white text-white"
                          : "border-white/10 text-gray-500 hover:border-white/30"
                      }`}
                    >
                      {s}%
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}

        {/* --- CLOSE TAB (SHARED LOGIC) --- */}
        {activeTab === "CLOSE" && (
          <>
            <InputGroup
              label="Amount to Close"
              subLabel={`Max: ${formatNum(notional)} USDC`}
              value={notional}
              onChange={(v) => setNotional(Number(v))}
              suffix="USDC"
            />

            <div className="border border-white/10 p-4 space-y-4 bg-white/[0.02]">
              <div className="flex justify-between items-center">
                <span className="text-[11px] text-gray-500 uppercase tracking-widest font-bold">
                  {activeProduct === "FIXED_BORROW"
                    ? "Accrued Hedge"
                    : "Accrued Yield"}
                </span>
                <span
                  className={`text-xl font-mono tracking-tight ${
                    activeProduct === "FIXED_BORROW"
                      ? "text-pink-500"
                      : "text-green-500"
                  }`}
                >
                  + {formatNum(accruedYield)}{" "}
                  <span className="text-xs">USDC</span>
                </span>
              </div>
              <div className="flex justify-between items-center border-t border-white/5 pt-4">
                <span className="text-[11px] text-gray-500 uppercase tracking-widest font-bold">
                  Time to Maturity
                </span>
                <span className="font-mono text-white text-sm">
                  {maturityDays - 30 > 0 ? maturityDays - 30 : 0} Days
                </span>
              </div>
            </div>

            {/* Slippage Warning */}
            <div className="bg-yellow-900/10 border border-yellow-700/30 p-4 flex gap-3">
              <AlertTriangle
                size={16}
                className="text-yellow-600 shrink-0 mt-0.5"
              />
              <div>
                <div className="text-[10px] text-yellow-500 font-bold uppercase tracking-widest mb-1">
                  Early Exit Notice
                </div>
                <p className="text-[10px] text-gray-400 leading-relaxed font-mono">
                  You can exit your position at any time. However, early exits
                  are subject to slippage based on TWAMM liquidity availability.
                </p>
              </div>
            </div>
          </>
        )}

        {/* Action Button */}
        <div className="mt-auto">
          {account ? (
            <button
              className={`w-full py-4 text-black hover:opacity-90 text-xs font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none ${
                activeProduct === "FIXED_BORROW" ? "bg-pink-500" : "bg-cyan-400"
              }`}
            >
              {activeTab} POSITION
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
    </div>
  );
};

// --- HELPER COMPONENTS ---
const MetricCell = ({ label, Icon, content }) => (
  <div className="p-6 flex flex-col justify-between h-full min-h-[180px]">
    <div className="text-[12px] text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
      {label} {Icon && <Icon size={15} className="opacity-90" />}
    </div>
    {content}
  </div>
);
const StatItem = ({ label, value }) => (
  <div>
    <div className="text-[10px] text-gray-400 uppercase tracking-widest mb-1">
      {label}
    </div>
    <div className="text-xl font-light text-white font-mono tracking-tighter">
      {value}
    </div>
  </div>
);
const SummaryRow = ({ label, value, valueColor = "text-white" }) => (
  <div className="flex justify-between items-center text-[12px]">
    <span className="text-gray-500 uppercase">{label}</span>
    <span className={`font-mono ${valueColor}`}>{value}</span>
  </div>
);
const InputGroup = ({ label, subLabel, value, onChange, suffix }) => (
  <div className="space-y-2">
    <div className="flex justify-between text-[12px] uppercase tracking-widest font-bold text-gray-500">
      <span>{label}</span>
      <span>{subLabel}</span>
    </div>
    <div className="relative group">
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-2 focus:outline-none focus:border-white transition-colors placeholder-gray-800 rounded-none"
        placeholder="0.00"
      />
      <span className="absolute right-0 top-2 text-sm text-gray-600">
        {suffix}
      </span>
    </div>
  </div>
);
const ProductCard = ({ theme = "pink", title, desc, badge, Icon, onClick }) => {
  const themes = {
    pink: {
      text: "text-pink-500",
      bg: "bg-pink-500/10",
      border: "border-pink-500/20",
    },
    cyan: {
      text: "text-cyan-400",
      bg: "bg-cyan-400/10",
      border: "border-cyan-400/20",
    },
  };
  const c = themes[theme];
  return (
    <div
      onClick={onClick}
      className="border border-white/10 bg-[#050505] p-6 hover:bg-[#0a0a0a] transition-colors cursor-pointer group min-h-[180px] h-full flex flex-col justify-between"
    >
      <div>
        <div className="flex justify-between items-center mb-6">
          <span
            className={`text-[10px] font-bold uppercase tracking-widest ${c.text} ${c.bg} px-2 py-1`}
          >
            {badge}
          </span>
          <div className={`${c.border}`}>
            <Icon size={20} className={c.text} />
          </div>
        </div>
        <h3 className="text-lg font-mono text-white mb-2 tracking-tight">
          {title}
        </h3>
        <p className="text-xs text-gray-500 font-mono mb-4 leading-relaxed">
          {desc}
        </p>
      </div>
    </div>
  );
};

// --- MAIN PAGE COMPONENT ---

export default function BondsPage() {
  const [account, setAccount] = useState(null);
  const { rates, error, isLoading, stats, dailyChange, latest, isCappedRaw } =
    useMarketData();
  const tradeLogic = useTradeLogic(latest.apy);

  const projectionData = useWealthProjection(
    tradeLogic.state.notional,
    latest.apy,
    tradeLogic.state.maturityDays
  );

  const connectWallet = useCallback(async () => {
    if (window.ethereum) {
      try {
        const accounts = await window.ethereum.request({
          method: "eth_requestAccounts",
        });
        setAccount(accounts[0]);
      } catch (err) {
        console.error("Connection rejected");
      }
    } else {
      alert("No Ethereum wallet found.");
    }
  }, []);

  if (error)
    return (
      <div className="h-screen flex items-center justify-center text-red-600 bg-black font-mono text-xs">
        ERR: API_DISCONNECTED
      </div>
    );
  if (isLoading || !rates)
    return (
      <div className="h-screen flex items-center justify-center text-gray-500 bg-black font-mono text-xs animate-pulse">
        SYSTEM_INITIALIZING...
      </div>
    );

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <AppHeader
        latest={latest}
        isCapped={isCappedRaw}
        account={account}
        connectWallet={connectWallet}
        ratesLoaded={!!rates}
      />
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          <div className="xl:col-span-9 flex flex-col gap-6">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
              <div className="lg:col-span-4 h-full">
                <ProductCard
                  theme="cyan"
                  title="FIXED_YIELD"
                  badge="Synthetic Bond"
                  Icon={Percent}
                  desc="Transform volatile rates into a fixed-income product. Short RLP + TWAMM."
                  onClick={() =>
                    tradeLogic.actions.setActiveProduct("FIXED_YIELD")
                  }
                />
              </div>
              <div className="lg:col-span-8 h-full">
                <MetricsGrid
                  latest={latest}
                  dailyChange={dailyChange}
                  stats={stats}
                />
              </div>
              <div className="lg:col-span-4 h-[200px]">
                <ProductCard
                  theme="pink"
                  title="FIXED_BORROW"
                  badge="Fixed-Term Debt"
                  Icon={Shield}
                  desc="Immunize your debt against Aave rate spikes. Long RLP (Hedge)."
                  onClick={() =>
                    tradeLogic.actions.setActiveProduct("FIXED_BORROW")
                  }
                />
              </div>
              <div className="lg:col-span-8 h-[500px]">
                <WealthProjectionChart
                  data={projectionData}
                  collateral={tradeLogic.state.notional}
                  theme={
                    tradeLogic.state.activeProduct === "FIXED_BORROW"
                      ? "pink"
                      : "cyan"
                  }
                />
              </div>
            </div>
          </div>
          <TradingTerminal
            account={account}
            connectWallet={connectWallet}
            currentRate={latest.apy}
            state={tradeLogic.state}
            actions={tradeLogic.actions}
          />
        </div>
      </div>
    </div>
  );
}
