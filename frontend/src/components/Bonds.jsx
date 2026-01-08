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
  RefreshCw,
  Clock,
  TrendingUp,
  TrendingDown,
  Shield,
  Percent,
} from "lucide-react";

// --- CONSTANTS & UTILS ---

const API_URL = "http://127.0.0.1:8000";

const fetcher = (url) => axios.get(url).then((res) => res.data);

const getPastDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split("T")[0];
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

    // Wave parameters for simulation
    const volatility = 0.01; // Amplitude of the wave relative to collateral
    const cycleSpeed = 0.2; // Frequency of the wave

    for (let i = 0; i <= days; i++) {
      // 1. Fixed Path (Linear Growth)
      const fixedBalance = collateral * (1 + fixedRateDaily * i);

      // 2. Variable Path (Wave + Noise around linear trend)
      // We take the fixed trend and add a sine wave + random noise
      const trend = collateral * (1 + fixedRateDaily * 1.2 * i); // Variable tends to be slightly higher/lower on avg
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

const CustomWealthTooltip = ({ active, payload, label }) => {
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

const WealthProjectionChart = ({ data, collateral }) => {
  if (!data || data.length === 0) return null;

  // Calculate final metrics from the last data point
  const finalPoint = data[data.length - 1];
  const valueAtMaturity = finalPoint.fixed;
  const calculatedWealth = valueAtMaturity - collateral;

  return (
    <div className="w-full h-full select-none bg-[#050505] border border-white/10 p-6 flex flex-col">
      {/* Top Header Metrics */}
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="text-[11px] font-bold uppercase tracking-widest text-gray-500 mb-1">
            Value_at_Maturity
          </div>
          <div className="text-3xl font-light text-white font-mono tracking-tight">
            ${formatNum(valueAtMaturity, 2)}
          </div>
        </div>

        <div className="text-right">
          <div className="text-[11px] font-bold uppercase tracking-widest text-gray-500 mb-1">
            Calculated_Wealth
          </div>
          <div className="text-xl text-green-500 font-mono tracking-tight">
            +${formatNum(calculatedWealth, 2)}
          </div>
        </div>
      </div>

      {/* Legend & Chart Title */}
      <div className="flex justify-between items-center mb-4">
        <div className="text-[10px] text-gray-600 uppercase tracking-widest">
          Simulated_90_Day_Path
        </div>
        <div className="flex gap-4">
          <div className="flex items-center gap-2 text-[10px] text-cyan-400 uppercase tracking-wider">
            <div className="w-2 h-0.5 bg-cyan-400"></div> Fixed
          </div>
          <div className="flex items-center gap-2 text-[10px] text-gray-400 uppercase tracking-wider">
            <div className="w-2 h-0.5 bg-gray-400 border border-dashed"></div>{" "}
            Variable
          </div>
        </div>
      </div>

      {/* Chart */}
      <div className="flex-1 min-h-0 border-white/5 bg-[#050505] relative">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={{ top: 10, right: 10, left: 10, bottom: 0 }}
          >
            <defs>
              <linearGradient id="gradientFixed" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.2} />
                <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
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

            {/* Variable Path (Simulated Wave) */}
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

            {/* Fixed Path (Linear Bond) */}
            <Area
              type="monotone"
              dataKey="fixed"
              name="Fixed"
              stroke="#22d3ee"
              strokeWidth={2}
              fill="url(#gradientFixed)"
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

// --- CUSTOM HOOKS (Preserved) ---

function useMarketData(resolution = "4H") {
  const [dates] = useState({ start: getPastDate(90), end: getToday() });
  const getUrl = () => {
    let url = `${API_URL}/rates?resolution=${resolution}`;
    if (dates.start) url += `&start_date=${dates.start}`;
    if (dates.end) url += `&end_date=${dates.end}`;
    return url;
  };
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
  const [tradeSide, setTradeSide] = useState("LONG");
  const [collateral, setCollateral] = useState(10000);
  const [shortCR, setShortCR] = useState(150);
  const [simTargetRate, setSimTargetRate] = useState(null);
  useEffect(() => {
    if (simTargetRate === null && currentRate > 0)
      setSimTargetRate(currentRate);
  }, [currentRate, simTargetRate]);
  let notional, liqRate;
  if (tradeSide === "LONG") {
    notional = collateral;
    liqRate = null;
  } else {
    const crDecimal = shortCR / 100;
    notional = crDecimal > 0 ? collateral / crDecimal : 0;
    liqRate = currentRate * (shortCR / 110);
  }
  const simPnL = useMemo(() => {
    if (!simTargetRate) return { value: 0, percent: 0 };
    let pnl = 0;
    if (tradeSide === "LONG")
      pnl = ((simTargetRate - currentRate) / 100) * notional;
    else pnl = ((currentRate - simTargetRate) / 100) * notional;
    const percent = collateral > 0 ? (pnl / collateral) * 100 : 0;
    return { value: pnl, percent };
  }, [simTargetRate, currentRate, tradeSide, notional, collateral]);
  const setShortAmount = (amount) => {
    if (amount > 0) {
      const newCR = (collateral / amount) * 100;
      setShortCR(Math.min(Math.max(newCR, 110), 1500));
    }
  };
  return {
    state: {
      tradeSide,
      collateral,
      shortCR,
      simTargetRate,
      notional,
      liqRate,
      simPnL,
    },
    actions: {
      setTradeSide,
      setCollateral,
      setShortCR,
      setSimTargetRate,
      setShortAmount,
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
          <span className="text-gray-600 px-2 tracking-widest cursor-not-allowed">
            CDS_[SOON]
          </span>
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
  <div className="grid grid-cols-1 md:grid-cols-3 h-full border border-white/10 bg-[#050505] divide-y md:divide-y-0 md:divide-x divide-white/10">
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
    tradeSide,
    collateral,
    shortCR,
    simTargetRate,
    notional,
    liqRate,
    simPnL,
  } = state;
  const {
    setTradeSide,
    setCollateral,
    setShortCR,
    setSimTargetRate,
    setShortAmount,
  } = actions;
  return (
    <div className="xl:col-span-3 border border-white/10 bg-[#080808] flex flex-col h-full">
      <div className="p-4 border-b border-white/10 bg-[#0a0a0a]">
        <h3 className="text-xs font-bold tracking-widest text-white uppercase flex items-center gap-2">
          <Terminal size={15} className="text-gray-500" /> Synthetic_Rates
        </h3>
      </div>
      <div className="p-1 border-b border-white/10 bg-[#080808]">
        <div className="grid grid-cols-2 gap-1">
          {["LONG", "SHORT"].map((side) => (
            <button
              key={side}
              onClick={() => setTradeSide(side)}
              className={`py-3 text-[13px] font-bold tracking-widest uppercase transition-colors focus:outline-none rounded-none ${
                tradeSide === side
                  ? side === "LONG"
                    ? "bg-cyan-900/30 text-cyan-400"
                    : "bg-pink-900/30 text-pink-500"
                  : "bg-[#0f0f0f] text-gray-600 hover:text-gray-400 hover:bg-white/5"
              }`}
            >
              {side}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 flex flex-col p-6 gap-6">
        <InputGroup
          label="Collateral"
          subLabel={`Balance: ${account ? "2,450.00" : "--"} USDC`}
          value={collateral}
          onChange={(v) => setCollateral(Number(v))}
          suffix="USDC"
        />
        <div className="space-y-2">
          <div className="text-[12px] uppercase tracking-widest font-bold text-gray-500">
            Amount (Notional)
          </div>
          <div className="relative group">
            <input
              type="number"
              value={notional > 0 ? parseFloat(notional.toFixed(2)) : ""}
              onChange={(e) =>
                tradeSide === "LONG"
                  ? setCollateral(Number(e.target.value))
                  : setShortAmount(Number(e.target.value))
              }
              className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-2 focus:outline-none focus:border-white transition-colors placeholder-gray-800 rounded-none"
              placeholder="0.00"
            />
            <span className="absolute right-0 top-2 text-[12px] text-gray-600">
              USDC
            </span>
          </div>
        </div>
        {tradeSide === "SHORT" && (
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
          </div>
        )}
        <div className="border border-white/10 p-4 space-y-2 bg-white/[0.02]">
          <SummaryRow label="Entry Rate" value={`${formatNum(currentRate)}%`} />
          <SummaryRow
            label="Liq. Rate"
            value={liqRate ? `${formatNum(liqRate)}%` : "None"}
            valueColor="text-orange-500"
          />
          <SummaryRow label="Notional" value={`$${formatNum(notional, 0)}`} />
          <SummaryRow
            label="Est. Fee"
            value={`${formatNum(notional * 0.001)} USDC`}
            valueColor="text-gray-400"
          />
        </div>
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
            <span>{formatNum(simTargetRate)}%</span>
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
                onClick={() => setSimTargetRate(currentRate * (1 + pct / 100))}
                className="flex-1 py-1.5 bg-white/5 hover:bg-white/10 text-xs font-mono text-gray-400 focus:outline-none rounded-none"
              >
                {pct > 0 ? "+" : ""}
                {pct}%
              </button>
            ))}
          </div>
        </div>
        <div className="flex justify-between items-end">
          <span className="text-[13px] text-gray-500">Est. PnL (1Y)</span>
          <div
            className={`text-right ${
              simPnL.value >= 0 ? "text-green-500" : "text-red-500"
            }`}
          >
            <div className="text-xl font-mono leading-none">
              {simPnL.value >= 0 ? "+" : ""}
              {formatNum(simPnL.value, 0)} USDC
            </div>
            <div className="text-[12px] font-mono mt-1">
              {formatNum(simPnL.percent)}% ROI
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// --- HELPER COMPONENTS (Preserved) ---
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

  // Project Wealth Logic (90 Days)
  const projectionData = useWealthProjection(
    tradeLogic.state.collateral,
    latest.apy,
    90
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
                  theme="pink"
                  title="FIXED_YIELD"
                  badge="Synthetic Bond"
                  Icon={Percent}
                  desc="Transform volatile rates into a fixed-income product. Short RLP + TWAMM."
                  onClick={() => tradeLogic.actions.setTradeSide("SHORT")}
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
                  theme="cyan"
                  title="FIXED_BORROW"
                  badge="Fixed-Term Debt"
                  Icon={Shield}
                  desc="Immunize your debt against Aave rate spikes. Long RLP (Hedge)."
                  onClick={() => tradeLogic.actions.setTradeSide("LONG")}
                />
              </div>
              <div className="lg:col-span-8 h-[500px]">
                <WealthProjectionChart
                  data={projectionData}
                  collateral={tradeLogic.state.collateral}
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
