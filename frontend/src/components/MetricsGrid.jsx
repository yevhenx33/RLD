import React from "react";
import { Terminal, BarChart3, Clock, TrendingUp, TrendingDown } from "lucide-react";
import { formatNum } from "../utils/helpers";

const formatUSD = (val) => {
  if (val == null || isNaN(val)) return "—";
  if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
  return `$${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};

const MetricCell = ({ label, Icon, content }) => (
  <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
    <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
      {label} {Icon && <Icon size={15} className="opacity-90" />}
    </div>
    {content}
  </div>
);

const StatItem = ({ label, value }) => (
  <div>
    <div className="text-sm text-gray-400 uppercase tracking-widest mb-1">
      {label}
    </div>
    <div className="text-xl font-light text-white font-mono tracking-tighter">
      {value}
    </div>
  </div>
);

const MetricsGrid = ({ latest, dailyChange, openInterest, liquidity }) => (
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
          <div className="text-sm text-gray-500 uppercase tracking-widest">
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
      label="MARKET_DEPTH"
      Icon={BarChart3}
      content={
        <div className="flex flex-col gap-6 mt-auto">
          <StatItem label="OPEN_INTEREST" value={formatUSD(openInterest)} />
          <StatItem label="LIQUIDITY" value={formatUSD(liquidity)} />
        </div>
      }
    />
    <MetricCell
      label="PARAMETERS"
      Icon={Clock}
      content={
        <div className="flex flex-col gap-6 mt-auto">
          <StatItem label="MATURITY" value="1H — 1Y" />
          <StatItem label="WITHDRAW" value="Instant" />
        </div>
      }
    />
  </div>
);

export default MetricsGrid;
