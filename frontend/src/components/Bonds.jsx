import React, { useMemo } from "react";
import { Shield, Percent, Terminal, Calendar, AlertTriangle } from "lucide-react";
import { useWallet } from "../context/WalletContext";
import Header from "./Header";
import { formatNum } from "../utils/helpers";

// Hooks
import { useMarketData } from "../hooks/useMarketData";
import { useTradeLogic } from "../hooks/useTradeLogic";
import { useWealthProjection } from "../hooks/useWealthProjection";

// Components
import MetricsGrid from "./MetricsGrid";
import TradingTerminal, { InputGroup, SummaryRow } from "./TradingTerminal";
import WealthProjectionChart from "./WealthProjectionChart";
import ProductCard from "./ProductCard";
import SettingsButton from "./SettingsButton";

export default function BondsPage() {
  const { account, connectWallet, usdcBalance } = useWallet();
  const { rates, error, isLoading, stats, dailyChange, latest, isCappedRaw } =
    useMarketData();
  const tradeLogic = useTradeLogic(latest.apy);
  const {
      activeProduct,
      activeTab,
      notional,
      maturityDays,
      maturityDate,
      slippage,
    } = tradeLogic.state;
  const {
      setActiveTab,
      setNotional,
      handleDaysChange,
      handleDateChange,
      setSlippage,
    } = tradeLogic.actions;

  const projectionData = useWealthProjection(
    tradeLogic.state.notional,
    latest.apy,
    tradeLogic.state.maturityDays
  );
  
  // Logic for Close View Mock
  const accruedYield = useMemo(() => {
    // Mock: Assume held for 30 days at current rate
    return notional * (latest.apy / 100) * (30 / 365);
  }, [notional, latest.apy]);

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
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          <div className="xl:col-span-9 flex flex-col gap-6">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
              <div className="lg:col-span-4 h-full ">
                <ProductCard
                  theme="cyan"
                  title="FIXED_YIELD"
                  badge="Synthetic Bond"
                  Icon={Shield}
                  desc="Transform volatile rates into a fixed-income product. Short RLP + TWAMM."
                  onClick={() =>
                    tradeLogic.actions.setActiveProduct("FIXED_YIELD")
                  }
                  isActive={tradeLogic.state.activeProduct === "FIXED_YIELD"}
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
                  Icon={Percent}
                  desc="Lock in your borrowing costs. Long RLP + TWAMM."
                  onClick={() =>
                    tradeLogic.actions.setActiveProduct("FIXED_BORROW")
                  }
                  isActive={tradeLogic.state.activeProduct === "FIXED_BORROW"}
                />
              </div>
              <div className="lg:col-span-8 h-[350px] md:h-[500px]">
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
            title={activeProduct}
            subTitle={activeProduct === "FIXED_YIELD" ? "SHORT RLP" : "LONG RLP"}
            Icon={Terminal}
            tabs={[
                { id: "OPEN", label: "OPEN", onClick: () => setActiveTab("OPEN"), isActive: activeTab === "OPEN" },
                { id: "CLOSE", label: "CLOSE", onClick: () => setActiveTab("CLOSE"), isActive: activeTab === "CLOSE" }
            ]}
            actionButton={{
                label: `${activeTab} POSITION`,
                onClick: () => {}, // Placeholder
                variant: activeProduct === "FIXED_BORROW" ? "pink" : "cyan",
            }}
          >
             {/* Wraps the specific content for Bonds */}
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

                <div className="border border-white/10 p-4 space-y-3 bg-white/[0.02] text-[11px] tracking-widest">
                  <SummaryRow
                    label="Entry_Rate"
                    value={`${formatNum(latest.apy)}%`}
                  />
                  <div className="flex justify-between items-center pt-2">
                    <div className="flex items-center gap-1.5 text-[12px] text-gray-500 uppercase tracking-widest">
                      MAX_Slippage
                    </div>
                    <div className="flex gap-1">
                      {[0.1, 0.5, 1.0].map((s) => (
                        <SettingsButton
                          key={s}
                          onClick={() => setSlippage(s)}
                          isActive={slippage === s}
                          className="px-3"
                        >
                          {s}%
                        </SettingsButton>
                      ))}
                    </div>
                  </div>
                </div>
              </>
            )}

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
          </TradingTerminal>
        </div>
      </div>
    </div>
  );
}
