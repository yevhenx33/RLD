import React from "react";
import { Terminal, Activity, Clock, TrendingUp, TrendingDown } from "lucide-react";
import { formatNum } from "../utils/helpers";

const MetricCell = ({ label, Icon, content }) => (
  <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
    <div className="text-[10px] md:text-[12px] text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
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
        <div className="grid grid-cols-2 gap-x-4 mt-auto h-full items-end">
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

export default MetricsGrid;
