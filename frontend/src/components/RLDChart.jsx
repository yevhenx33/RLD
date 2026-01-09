import React from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    const dateStr = new Date(label * 1000).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });

    return (
      <div className="bg-zinc-950 border border-zinc-800 p-3 rounded shadow-2xl font-mono text-xs z-50">
        <p className="text-zinc-500 mb-2 border-b border-zinc-800 pb-1">
          {dateStr}
        </p>
        {payload.map((entry, index) => (
          <div key={index} className="flex items-center gap-2 mb-1">
            <div
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-zinc-300 font-medium">{entry.name}:</span>
            <span className="text-white font-bold">
              {entry.name && (entry.name.includes("Price") || entry.name.includes("ETH"))
                ? `$${Number(entry.value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}` 
                : `${Number(entry.value).toFixed(2)}%`}
            </span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

const RLDPerformanceChart = ({ data, areas = [], referenceLines = [] }) => {
  if (!data || data.length === 0) return null;

  const startTs = data[0].timestamp;
  const endTs = data[data.length - 1].timestamp;
  const durationSeconds = endTs - startTs;

  const formatTick = (unix) => {
    const date = new Date(unix * 1000);
    if (durationSeconds < 172800) {
      return date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
    }
    if (durationSeconds < 15552000) {
      return date.toLocaleDateString([], { month: "short", day: "numeric" });
    }
    return date.toLocaleDateString([], { month: "short", year: "2-digit" });
  };

  return (
    <div className="w-full h-full select-none outline-none focus:outline-none">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          // FIX: Increased margins to prevent overlap with container
          margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
        >
          <defs>
            {areas.map((area, index) => (
              <linearGradient
                key={index}
                id={`gradient-${area.key}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="5%" stopColor={area.color} stopOpacity={0.33} />
                <stop offset="95%" stopColor={area.color} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>

          <CartesianGrid
            strokeDasharray="3 3"
            stroke="#27272a"
            vertical={false}
          />

          <XAxis
            dataKey="timestamp"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={formatTick}
            stroke="#71717a"
            fontSize={12}
            tickMargin={12}
            minTickGap={60}
          />

          <YAxis
            stroke="#71717a"
            fontSize={12}
            domain={["auto", "auto"]}
            tickFormatter={(val) => `${val}%`}
            width={50}
          />
          {/* Secondary YAxis for Price */}
          {areas.some((a) => a.yAxisId === "right") && (
            <YAxis
              yAxisId="right"
              orientation="right"
              stroke="#71717a"
              fontSize={12}
              domain={["auto", "auto"]}
              tickFormatter={(val) => `$${val}`}
              width={60}
            />
          )}

          <Tooltip
            content={<CustomTooltip />}
            cursor={{ stroke: "#52525b", strokeDasharray: "4 4" }}
          />

          {areas.map((area, index) => (
            <Area
              key={index}
              {...(area.yAxisId ? { yAxisId: area.yAxisId } : {})}
              type="monotone"
              dataKey={area.key}
              stroke={area.color}
              strokeWidth={2}
              fill={`url(#gradient-${area.key})`}
              name={area.name}
              isAnimationActive={false}
            />
          ))}

          {referenceLines.map((line, index) => (
            <ReferenceLine
              key={index}
              y={line.y}
              stroke={line.stroke || "#ef4444"}
              strokeDasharray="3 3"
              label={{
                position: "right",
                value: line.label,
                fill: line.stroke,
                fontSize: 10,
              }}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

export default RLDPerformanceChart;
