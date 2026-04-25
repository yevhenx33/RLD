import React, { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import CdsInterestRateChart from "../../charts/primitives/CdsInterestRateChart";

const formatPrice = (value) => {
  const num = Number(value);
  if (!Number.isFinite(num)) return "--";
  return `$${num.toFixed(4)}`;
};

const formatTime = (timestamp) => {
  if (!timestamp) return "--";
  return new Date(timestamp * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

function HistoricalTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;

  return (
    <div className="bg-[#0a0a0a] border border-white/10 p-3 shadow-2xl font-mono text-xs">
      <div className="text-gray-500 pb-2 mb-2 border-b border-white/10">
        {formatTime(label)}
      </div>
      {payload.map((entry) => (
        <div key={entry.dataKey} className="flex items-center justify-between gap-6 mb-1 last:mb-0">
          <span className="flex items-center gap-2 text-gray-400">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            {entry.name}
          </span>
          <span className="text-white font-bold">{formatPrice(entry.value)}</span>
        </div>
      ))}
    </div>
  );
}

export default function CdsDataModule({ latestApy, chartData = [] }) {
  const [activeView, setActiveView] = useState("SIMULATION");

  const historicalData = useMemo(
    () => (chartData || [])
      .filter((point) => point?.timestamp && (point.markPrice > 0 || point.indexPrice > 0))
      .map((point) => ({
        timestamp: point.timestamp,
        markPrice: Number(point.markPrice || 0),
        indexPrice: Number(point.indexPrice || 0),
      })),
    [chartData],
  );

  const latestPoint = historicalData[historicalData.length - 1];

  return (
    <div className="w-full h-full border border-white/10 bg-[#080808] flex flex-col pt-4">
      {/* Module Header / Tabs */}
      <div className="px-6 flex items-center gap-6 border-b border-white/10 pb-4">
        <button
          onClick={() => setActiveView("SIMULATION")}
          className={`text-sm font-bold uppercase tracking-widest transition-colors pb-4 ${activeView === "SIMULATION" ? "text-cyan-400 border-cyan-400 -mb-[18px]" : "text-gray-500 hover:text-white -mb-[18px]"
            }`}
        >
          Payout Simulation
        </button>
        <button
          onClick={() => setActiveView("HISTORICAL")}
          className={`text-sm font-bold uppercase tracking-widest transition-colors pb-4 ${activeView === "HISTORICAL" ? "text-cyan-400 border-cyan-400 -mb-[18px]" : "text-gray-500 hover:text-white -mb-[18px]"
            }`}
        >
          Historical Prices
        </button>
      </div>

      {/* Module Content */}
      <div className="flex-1 relative">
        {activeView === "SIMULATION" ? (
          <div className="h-full w-full">
            <CdsInterestRateChart currentRate={latestApy} theme="cyan" />
          </div>
        ) : historicalData.length > 0 ? (
          <div className="h-full p-6 flex flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-[10px] text-gray-600 uppercase tracking-widest">
                  CDS Market Price History
                </div>
              </div>
              <div className="flex items-center gap-4 text-sm font-mono">
                <div>
                  <span className="text-gray-600 uppercase tracking-widest mr-2">MARK</span>
                  <span className="text-cyan-400">{formatPrice(latestPoint?.markPrice)}</span>
                </div>
                <div>
                  <span className="text-gray-600 uppercase tracking-widest mr-2">ORACLE</span>
                  <span className="text-white">{formatPrice(latestPoint?.indexPrice)}</span>
                </div>
              </div>
            </div>

            <div className="flex-1 min-h-0">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={historicalData} margin={{ top: 10, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="#1f2937" strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={formatTime}
                    stroke="#52525b"
                    tick={{ fill: "#71717a", fontSize: 11, fontFamily: "monospace" }}
                    axisLine={{ stroke: "#27272a" }}
                    tickLine={false}
                    minTickGap={36}
                  />
                  <YAxis
                    tickFormatter={(value) => `$${Number(value).toFixed(2)}`}
                    stroke="#52525b"
                    tick={{ fill: "#71717a", fontSize: 11, fontFamily: "monospace" }}
                    axisLine={false}
                    tickLine={false}
                    domain={["dataMin - 0.05", "dataMax + 0.05"]}
                    width={56}
                  />
                  <Tooltip content={<HistoricalTooltip />} />
                  <Line
                    type="monotone"
                    dataKey="markPrice"
                    name="CDS Mark"
                    stroke="#22d3ee"
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="indexPrice"
                    name="Oracle Index"
                    stroke="#e5e7eb"
                    strokeWidth={1.5}
                    strokeDasharray="4 4"
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <div className="h-full m-6 flex items-center justify-center border border-white/5 border-dashed relative">
            <p className="text-sm font-mono text-gray-600 uppercase tracking-widest text-center">
              No historical CDS price candles yet.<br /><br />
              <span className="text-cyan-900 border border-cyan-900/40 px-2 py-1">Waiting for market history</span>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
