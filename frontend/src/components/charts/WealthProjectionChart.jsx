import React from "react";
import {
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ComposedChart,
} from "recharts";
import { formatNum } from "../../utils/helpers";

const CustomWealthTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-zinc-950 border border-zinc-800 p-3 rounded shadow-2xl font-mono text-sm z-50">
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
              {entry.value.toLocaleString("en-US", {
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

const WealthProjectionChart = ({ data, collateral, apy, theme = "cyan" }) => {
  if (!data || data.length === 0) return null;

  const finalPoint = data[data.length - 1];
  const valueAtMaturity = finalPoint.fixed;
  const calculatedWealth = valueAtMaturity - collateral;

  // Define colors based on theme
  const mainColor = theme === "pink" ? "#ec4899" : "#22d3ee"; // Pink-500 vs Cyan-400
  const labelColor = theme === "pink" ? "text-pink-500" : "text-cyan-400";
  const bgColor = theme === "pink" ? "bg-pink-500" : "bg-cyan-400";

  return (
    <div className="w-full h-full select-none bg-[#080808] border border-white/10 p-4 md:p-6 flex flex-col">
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="text-sm font-bold uppercase tracking-widest text-gray-500 mb-1">
            Value_at_Maturity
          </div>
          <div className="text-3xl font-light text-white font-mono tracking-tight">
            ${formatNum(valueAtMaturity, 2)}
          </div>
        </div>
        <div className="flex items-center gap-8">
          <div className="text-right">
            <div className="text-sm font-bold uppercase tracking-widest text-gray-500 mb-1">
              {theme === "pink" ? "Projected_Hedge" : "Calculated_Wealth"}
            </div>
            <div
              className={`text-3xl font-light ${
                theme === "pink" ? "text-pink-500" : "text-green-500"
              } font-mono tracking-tight`}
            >
              +${formatNum(calculatedWealth, 2)}
            </div>
          </div>
          <div className="text-right border-l border-white/10 pl-8">
            <div className="text-sm font-bold uppercase tracking-widest text-gray-500 mb-1">
              Fixed_APY
            </div>
            <div className="text-3xl font-light text-cyan-400 font-mono tracking-tight">
              {formatNum(apy, 2)}%
            </div>
          </div>
        </div>
      </div>



      <div className="flex-1 min-h-0 border-white/5 bg-[#080808] p-2 relative">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={{ top: 0, right: 10, left: 10, bottom: 0 }}
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
              fontSize={12}
              minTickGap={50}
            />
            <YAxis
              orientation="right"
              stroke="#52525b"
              fontSize={12}
              tickLine={false}
              axisLine={false}
              tickFormatter={(val) =>
                `$${val.toLocaleString("en-US", {
                  maximumFractionDigits: 0,
                })}`
              }
              domain={["auto", "auto"]}
              width={50}
            />
            <Tooltip
              content={<CustomWealthTooltip />}
              cursor={{ stroke: "#52525b", strokeDasharray: "4 4" }}
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

export default WealthProjectionChart;
