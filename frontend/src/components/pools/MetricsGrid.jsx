import React from "react";
import { Terminal, BarChart3, Clock, TrendingUp, TrendingDown } from "lucide-react";
import { formatNum } from "../../utils/helpers";

const formatUSD = (val) => {
  if (val == null || isNaN(val)) return "—";
  if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
  return `$${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};

export const MetricCell = ({ label, Icon, content, hideLabelOnMobile }) => (
  <div className="p-3 md:p-6 flex flex-col justify-between h-full min-h-[100px] md:min-h-[180px]">
    <div className={`${hideLabelOnMobile ? 'hidden md:flex' : 'flex'} text-[9px] md:text-sm text-gray-500 uppercase tracking-widest mb-2 md:mb-4 justify-between`}>
      {label} {Icon && <Icon size={15} className="md:opacity-90 hidden md:block" />}
    </div>
    {content}
  </div>
);

export const StatItem = ({ label, value }) => (
  <div>
    <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-0.5 md:mb-1">
      {label}
    </div>
    <div className="text-base md:text-xl font-light text-white font-mono tracking-tighter truncate">
      {value}
    </div>
  </div>
);

const MetricsGrid = ({ 
  latest, 
  dailyChange, 
  openInterest, 
  liquidity,
  paramLabel = "PARAMETERS",
  paramItems = [
    { label: "MATURITY", value: "1H — 1Y" },
    { label: "WITHDRAW", value: "Inst." }
  ],
  extraPanel
}) => (
  <div className={`grid ${extraPanel ? 'grid-cols-4' : 'grid-cols-3'} h-full border border-white/10 bg-[#080808] divide-x divide-white/10`}>
    <MetricCell
      label="CURRENT_SPOT"
      Icon={Terminal}
      content={
        <div className="mt-auto">
          <div className="text-2xl md:text-3xl font-light text-white mb-1 md:mb-2 tracking-tight">
            {formatNum(latest.apy)}
            <span className="text-[10px] md:text-sm text-gray-600 ml-1">%</span>
          </div>
          <div className="text-[9px] md:text-sm text-gray-500 uppercase tracking-widest">
            <div
              className={`flex items-center gap-1 md:gap-2 ${
                dailyChange >= 0 ? "text-green-500" : "text-red-500"
              }`}
            >
              {dailyChange >= 0 ? (
                <TrendingUp size={12} className="md:w-[15px] md:h-[15px]" />
              ) : (
                <TrendingDown size={12} className="md:w-[15px] md:h-[15px]" />
              )}
              <span className="font-bold whitespace-nowrap">
                <span className="hidden md:inline">24H: </span>
                {dailyChange > 0 ? "+" : ""}
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
      hideLabelOnMobile={true}
      content={
        <div className="flex flex-col gap-3 md:gap-6 mt-auto">
          <StatItem label="OPEN_INTEREST" value={formatUSD(openInterest)} />
          <StatItem label="LIQUIDITY" value={formatUSD(liquidity)} />
        </div>
      }
    />
    <MetricCell
      label={paramLabel}
      Icon={Clock}
      hideLabelOnMobile={true}
      content={
        <div className={`flex flex-col mt-auto ${paramItems.length > 2 ? 'gap-2 md:gap-3' : 'gap-3 md:gap-6'}`}>
          {paramItems.map((item, idx) => (
            <StatItem key={idx} label={item.label} value={item.value} />
          ))}
        </div>
      }
    />
    {extraPanel && extraPanel}
  </div>
);

export default MetricsGrid;
