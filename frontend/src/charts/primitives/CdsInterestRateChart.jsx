import React, { useMemo } from "react";
import {
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ComposedChart,
  ReferenceDot,
  ReferenceLine
} from "recharts";
import { ArrowRight } from "lucide-react";

const CustomTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    return (
      <div className="bg-zinc-950 border border-zinc-800 p-3 rounded shadow-2xl font-mono text-sm z-50">
        <p className="text-zinc-500 mb-2 border-b border-zinc-800 pb-1 uppercase tracking-widest">
          Interest Rate Model
        </p>
        <div className="flex items-center justify-between gap-6 mb-1">
          <span className="text-zinc-300">Pool Utilization:</span>
          <span className="text-white font-bold">{data.utilization}%</span>
        </div>
        <div className="flex items-center justify-between gap-6 mb-1">
          <span className="text-zinc-300">CDS Price:</span>
          <span className="text-cyan-400 font-bold">{data.rate}%</span>
        </div>
      </div>
    );
  }
  return null;
};

const CdsInterestRateChart = ({ currentRate = 2.71, theme = "cyan" }) => {
  // Mock Aave-style interest rate curve
  const uOptimal = 80;
  const rateSlope1 = 4;
  const maxRate = 100;

  const data = useMemo(() => {
    const points = [];
    for (let u = 0; u <= 100; u += 2) {
      let r = 0;
      if (u <= uOptimal) {
        r = (u / uOptimal) * rateSlope1;
      } else {
        const excess = (u - uOptimal) / (100 - uOptimal);
        r = rateSlope1 + (excess * (maxRate - rateSlope1));
      }
      points.push({ utilization: u, rate: Number(r.toFixed(2)) });
    }
    return points;
  }, []);

  // Backsolve utilization for currentRate mock
  let currentU = 0;
  if (currentRate <= rateSlope1) {
    currentU = (currentRate / rateSlope1) * uOptimal;
  } else {
    currentU = uOptimal + ((currentRate - rateSlope1) / (maxRate - rateSlope1)) * (100 - uOptimal);
  }
  // Cap for chart display
  currentU = Math.min(Math.max(currentU, 0), 100);

  const mainColor = theme === "cyan" ? "#22d3ee" : "#ec4899";

  return (
    <div className="w-full h-full select-none bg-[#080808] border border-white/10 p-4 md:p-6 flex flex-col">
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="text-sm font-bold uppercase tracking-widest text-gray-500 mb-1">
            PRICE_CURVE
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm font-bold uppercase tracking-widest text-gray-500 mb-1 flex items-center gap-1">
            Higher_Utilization <ArrowRight size={12} strokeWidth={3} className="text-gray-500" /> Higher_Price
          </div>
        </div>
      </div>
      <div className="flex-1 min-h-0 border-white/5 bg-[#080808] p-2 relative">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={{ top: 10, right: 10, left: -20, bottom: 0 }}
          >
            <defs>
              <linearGradient id="gradientRateCurve" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={mainColor} stopOpacity={0.2} />
                <stop offset="95%" stopColor={mainColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
            <XAxis
              dataKey="utilization"
              tickLine={false}
              axisLine={false}
              tickFormatter={(u) => `${u}%`}
              stroke="#52525b"
              fontSize={12}
              type="number"
              domain={[0, 100]}
              minTickGap={20}
            />
            <YAxis
              orientation="left"
              stroke="#52525b"
              fontSize={12}
              tickLine={false}
              axisLine={false}
              tickFormatter={(val) => `${val}%`}
              domain={[0, 'dataMax']}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ stroke: "#52525b", strokeDasharray: "4 4" }} />
            <Area
              type="monotone"
              dataKey="rate"
              name="Borrow Rate"
              stroke={mainColor}
              strokeWidth={2}
              fill="url(#gradientRateCurve)"
              isAnimationActive={false}
            />
            <ReferenceLine x={currentU} stroke={mainColor} strokeDasharray="3 3" opacity={0.3} />
            <ReferenceDot x={currentU} y={currentRate} r={4} fill={mainColor} stroke="#000" strokeWidth={2} isFront={true} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default CdsInterestRateChart;
