import React, { useMemo } from "react";
import { Terminal, Calendar, AlertTriangle } from "lucide-react";
import { formatNum } from "../utils/helpers";

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

const TradingTerminal = ({
  account,
  connectWallet,
  usdcBalance,
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
        <span className="text-[12px] text-gray-600 uppercase tracking-widest">
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
              label="Notional_Amount"
              subLabel={`Bal: ${account ? parseFloat(usdcBalance).toFixed(2) : "--"} USDC`}
              value={notional}
              onChange={(v) => setNotional(Number(v))}
              suffix="USDC"
            />

            <div className="space-y-3">
              <div className="flex justify-between items-end">
                <span className="text-[12px] text-gray-500 uppercase tracking-widest font-bold">
                  Maturity_Date
                </span>
                <span
                  className={`text-[12px] font-mono font-bold ${
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
                  min="1"
                  max="365"
                  step="1"
                  value={maturityDays}
                  onChange={(e) => handleDaysChange(Number(e.target.value))}
                  className="w-full h-0.5 bg-white/10 rounded-none appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-none hover:[&::-webkit-slider-thumb]:scale-125 transition-all"
                />
                <div className="flex justify-between text-[12px] text-gray-400 font-bold font-mono mt-1">
                  <span>1D</span>
                  <span>1Y</span>
                </div>
              </div>
            </div>

            <div className="border font-bold border-white/10 p-4 space-y-3 bg-white/[0.02] text-[11px] tracking-widest">
              <SummaryRow
                label="Entry_Rate"
                value={`${formatNum(currentRate)}%`}
              />
              <div className="flex justify-between items-center pt-2">
                <div className="flex items-center gap-1.5 text-[12px] text-gray-500 uppercase tracking-widest">
                  MAX_Slippage
                </div>
                <div className="flex gap-1">
                  {[0.1, 0.5, 1.0].map((s) => (
                    <button
                      key={s}
                      onClick={() => setSlippage(s)}
                      className={`text-[12px] px-3 py-1 font-mono font-bold border transition-colors rounded-none outline-none focus:outline-none${
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
              label="Amount_to_Close"
              subLabel={`Max: ${formatNum(notional)} USDC`}
              value={notional}
              onChange={(v) => setNotional(Number(v))}
              suffix="USDC"
            />

            <div className="border border-white/10 p-4 space-y-4 bg-white/[0.02]">
              <div className="flex justify-between items-center">
                <span className="text-[11px] text-gray-500 uppercase tracking-widest font-bold">
                  {activeProduct === "FIXED_BORROW"
                    ? "Accrued_Hedge"
                    : "Accrued_Yield"}
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
                  Time_to_Maturity
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
                <div className="text-[11px] text-yellow-500 font-bold uppercase tracking-widest mb-2">
                  Early Exit Notice
                </div>
                <p className="text-[11px] text-gray-400 leading-relaxed font-mono">
                  You can exit position at any time. However, early exits are
                  subject to slippage based on liquidity availability.
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

export default TradingTerminal;
