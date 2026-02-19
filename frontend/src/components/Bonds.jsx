import React, { useState } from "react";
import { Shield, Terminal, AlertTriangle, ChevronDown } from "lucide-react";
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
import CreateBondModal from "./CreateBondModal";
import CloseBondModal from "./CloseBondModal";
import BondBrandingPanel from "./BondBrandingPanel";
import WealthProjectionChart from "./WealthProjectionChart";
import SettingsButton from "./SettingsButton";

// ── Mock User Bonds ────────────────────────────────────────────
const USER_BONDS = [
  { id: 42, principal: 25000, fixedRate: 8.40, maturityDays: 180, elapsed: 127, maturityDate: "2026-08-19" },
  { id: 43, principal: 10000, fixedRate: 7.85, maturityDays: 90, elapsed: 61, maturityDate: "2026-04-15" },
  { id: 44, principal: 50000, fixedRate: 9.12, maturityDays: 365, elapsed: 45, maturityDate: "2027-01-30" },
  { id: 45, principal: 5000, fixedRate: 6.90, maturityDays: 30, elapsed: 28, maturityDate: "2026-03-15" },
  { id: 46, principal: 100000, fixedRate: 8.75, maturityDays: 270, elapsed: 12, maturityDate: "2026-11-10" },
];

export default function BondsPage() {
  const [showBondModal, setShowBondModal] = useState(false);
  const [showCloseModal, setShowCloseModal] = useState(false);
  const [selectedBond, setSelectedBond] = useState(null);
  const [actionDropdown, setActionDropdown] = useState(null);
  const { account, connectWallet, usdcBalance } = useWallet();
  const {
    rates,
    error,
    isLoading,
    dailyChange,
    latest,
    isCappedRaw: _isCappedRaw,
  } = useMarketData();
  const tradeLogic = useTradeLogic(latest.apy);
  const {
    activeProduct,
    activeTab,
    notional,
    maturityDays,
    maturityDate,
  } = tradeLogic.state;
  const {
    setActiveTab,
    setNotional,
    handleDaysChange,
    handleDateChange,
  } = tradeLogic.actions;

  const projectionData = useWealthProjection(
    tradeLogic.state.notional,
    latest.apy,
    tradeLogic.state.maturityDays,
  );

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
              {/* Merged Product + Mechanism Card */}
              <div className="lg:col-span-4 lg:row-span-2 h-full">
                <BondBrandingPanel />
              </div>

              {/* Metrics Grid */}
              <div className="lg:col-span-8 h-full">
                <MetricsGrid
                  latest={latest}
                  dailyChange={dailyChange}
                />
              </div>

              {/* Wealth Projection Chart */}
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
            subTitle={
              activeProduct === "FIXED_YIELD" ? "SHORT RLP" : "LONG RLP"
            }
            Icon={Terminal}
            tabs={[
              {
                id: "OPEN",
                label: "OPEN",
                onClick: () => setActiveTab("OPEN"),
                isActive: activeTab === "OPEN",
              },
              {
                id: "CLOSE",
                label: "CLOSE",
                onClick: () => setActiveTab("CLOSE"),
                isActive: activeTab === "CLOSE",
              },
            ]}
            actionButton={{
              label: activeTab === "OPEN" ? "Create Bond" : "Close Bond",
              onClick: activeTab === "OPEN"
                ? () => setShowBondModal(true)
                : () => {
                    if (selectedBond) setShowCloseModal(true);
                  },
              disabled: activeTab === "CLOSE" && !selectedBond,
              variant: activeProduct === "FIXED_BORROW" ? "pink" : activeTab === "CLOSE" ? "pink" : "cyan",
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
                    <span className="text-sm text-gray-500 uppercase tracking-widest font-bold">
                      Maturity_Date
                    </span>
                    <span
                      className={`text-sm font-mono font-bold ${
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
                      <input
                        type="date"
                        value={maturityDate}
                        onChange={(e) => handleDateChange(e.target.value)}
                        className="bg-transparent text-sm font-mono text-white focus:outline-none w-full uppercase [&::-webkit-calendar-picker-indicator]:brightness-0 [&::-webkit-calendar-picker-indicator]:invert [&::-webkit-calendar-picker-indicator]:opacity-80"
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
                    <div className="flex justify-between text-sm text-gray-400 font-bold font-mono mt-1">
                      <span>1D</span>
                      <span>1Y</span>
                    </div>
                  </div>
                </div>

                <div className="border border-white/10 p-4 space-y-3 bg-white/[0.02] text-sm tracking-widest">
                  <SummaryRow
                    label="Entry_Rate"
                    value={`${formatNum(latest.apy)}%`}
                  />
                </div>
              </>
            )}

            {activeTab === "CLOSE" && (
              <>
                {/* Bond Selector */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm uppercase tracking-widest font-bold text-gray-500">
                      {selectedBond ? "Bond" : "Select Bond"}
                    </span>
                    {selectedBond && (
                      <button
                        onClick={() => setSelectedBond(null)}
                        className="text-sm text-pink-500 uppercase tracking-widest hover:text-pink-400 transition-colors"
                      >
                        Change
                      </button>
                    )}
                  </div>

                  {/* Selected: collapsed view */}
                  {selectedBond && (() => {
                    const bond = USER_BONDS.find(b => b.id === selectedBond);
                    if (!bond) return null;
                    const accrued = bond.principal * (bond.fixedRate / 100) * (bond.elapsed / 365);
                    return (
                      <>
                        <div className="flex items-center justify-between p-3 border border-pink-500/50 bg-pink-500/5">
                          <div>
                            <div className="text-sm font-mono text-white">
                              #{String(bond.id).padStart(4, "0")} · {formatNum(bond.fixedRate)}% Fixed
                            </div>
                            <div className="text-sm text-gray-500 font-mono">
                              {Number(bond.principal).toLocaleString()} USDC · {bond.maturityDays}D
                            </div>
                          </div>
                          <span className="text-sm font-mono text-green-400">
                            +{formatNum(accrued, 2)}
                          </span>
                        </div>

                        {/* Bond details */}
                        <div className="border border-white/10 p-4 space-y-4 bg-white/[0.02]">
                          <div className="flex justify-between items-center">
                            <span className="text-sm text-gray-500 uppercase tracking-widest font-bold">
                              Accrued_Yield
                            </span>
                            <span className="text-xl font-mono tracking-tight text-green-500">
                              + {formatNum(accrued, 2)} <span className="text-sm">USDC</span>
                            </span>
                          </div>
                          <div className="flex justify-between items-center border-t border-white/5 pt-4">
                            <span className="text-sm text-gray-500 uppercase tracking-widest font-bold">
                              Time_to_Maturity
                            </span>
                            <span className="font-mono text-white text-sm">
                              {bond.maturityDays - bond.elapsed > 0 ? bond.maturityDays - bond.elapsed : 0} Days
                            </span>
                          </div>
                        </div>

                        {/* Early exit warning */}
                        {bond.elapsed < bond.maturityDays && (
                          <div className="bg-yellow-900/10 border border-yellow-700/30 p-4 flex gap-3">
                            <AlertTriangle
                              size={16}
                              className="text-yellow-600 shrink-0 mt-0.5"
                            />
                            <div>
                              <div className="text-sm text-yellow-500 font-bold uppercase tracking-widest mb-2">
                                Early Exit Notice
                              </div>
                              <p className="text-sm text-gray-400 leading-relaxed font-mono">
                                Closing before maturity may result in slippage based on
                                liquidity availability.
                              </p>
                            </div>
                          </div>
                        )}
                      </>
                    );
                  })()}

                  {/* Not selected: paginated list */}
                  {!selectedBond && (
                    <>
                      {USER_BONDS.map((bond) => {
                        const accrued = bond.principal * (bond.fixedRate / 100) * (bond.elapsed / 365);
                        const remaining = bond.maturityDays - bond.elapsed;
                        return (
                          <button
                            key={bond.id}
                            onClick={() => setSelectedBond(bond.id)}
                            className="w-full text-left border p-3 transition-all border-white/10 bg-[#060606] hover:border-white/20 flex items-center justify-between"
                          >
                            <div>
                              <div className="text-sm font-mono text-white">
                                #{String(bond.id).padStart(4, "0")} · {formatNum(bond.fixedRate)}% Fixed
                              </div>
                              <div className="text-sm text-gray-500 font-mono">
                                {Number(bond.principal).toLocaleString()} USDC · {remaining > 0 ? remaining : 0}D left
                              </div>
                            </div>
                            <div className="text-sm font-mono text-green-400">
                              +{formatNum(accrued, 2)}
                            </div>
                          </button>
                        );
                      })}
                    </>
                  )}
                </div>
              </>
            )}
          </TradingTerminal>
        </div>

      {/* 3. BONDS TABLE (aligned with chart) */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-9">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-start-5 lg:col-span-8 border border-white/10 bg-[#080808]">
          <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h3 className="text-sm font-bold uppercase tracking-widest">
                Your Bonds
              </h3>
              <span className="text-sm text-gray-600 font-mono">
                {USER_BONDS.length}
              </span>
            </div>
            <div className="text-sm text-gray-500 uppercase tracking-widest flex items-center gap-2">
              <Shield size={12} />
              ACTIVE
            </div>
          </div>

          {/* Table Header */}
          <div className="hidden md:flex items-center px-6 py-3 text-sm text-gray-500 uppercase tracking-widest border-b border-white/5">
            <div className="w-16 shrink-0 text-left">#</div>
            <div className="flex-1" />
            <div className="w-24 text-center">Value</div>
            <div className="w-20 text-center">Rate</div>
            <div className="w-32 text-center">Maturity</div>
            <div className="w-16 text-center">Left</div>
            <div className="w-32 text-center">Accrued</div>
            <div className="w-32 text-center">Principal</div>
            <div className="w-24 text-center">Action</div>
          </div>

          {/* Table Rows */}
          {USER_BONDS.map((bond) => {
            const accrued = bond.principal * (bond.fixedRate / 100) * (bond.elapsed / 365);
            const value = bond.principal + accrued;
            const remaining = bond.maturityDays - bond.elapsed;
            const progress = Math.min((bond.elapsed / bond.maturityDays) * 100, 100);
            const isMatured = remaining <= 0;
            return (
              <div key={bond.id}>
                <div className="flex items-center px-6 py-4 hover:bg-white/[0.02] transition-colors border-b border-white/5 last:border-b-0">
                  <div className="w-16 shrink-0 text-sm text-gray-500 font-mono text-left">
                    {String(bond.id).padStart(4, "0")}
                  </div>
                  <div className="flex-1" />
                  <div className="w-24 text-center text-sm font-mono text-white">
                    ${formatNum(value, 0)}
                  </div>
                  <div className="w-20 text-center text-sm font-mono text-cyan-400">
                    {formatNum(bond.fixedRate)}%
                  </div>
                  <div className="w-32 flex items-center justify-center gap-2">
                    <span className="text-sm font-mono text-white shrink-0">
                      {bond.maturityDays}D
                    </span>
                    <div className="w-12 bg-white/5 h-1">
                      <div
                        className="h-full bg-cyan-500/60"
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                  </div>
                  <div className="w-16 text-center text-sm font-mono text-white">
                    {isMatured ? (
                      <span className="text-green-400">Done</span>
                    ) : (
                      `${remaining}D`
                    )}
                  </div>
                  <div className="w-32 text-center text-sm font-mono">
                    <span className="text-green-400">
                      +{formatNum(accrued, 2)} USDC
                    </span>
                  </div>
                  <div className="w-32 text-center text-sm font-mono text-white">
                    {Number(bond.principal).toLocaleString()} USDC
                  </div>
                  <div className="w-24 relative flex justify-center">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setActionDropdown(actionDropdown === bond.id ? null : bond.id);
                      }}
                      className="p-1.5 text-gray-600 hover:text-white hover:bg-white/5 transition-colors"
                    >
                      <ChevronDown size={16} className={`transition-transform ${actionDropdown === bond.id ? 'rotate-180' : ''}`} />
                    </button>
                    {actionDropdown === bond.id && (
                      <div className="absolute right-0 top-full mt-1 z-50 border border-white/10 bg-[#0a0a0a] backdrop-blur-sm min-w-[150px]">
                        <button
                          onClick={() => {
                            setActionDropdown(null);
                            setSelectedBond(bond.id);
                            setShowCloseModal(true);
                          }}
                          className="w-full text-left px-4 py-2 text-sm text-white hover:bg-white/5 transition-colors font-mono"
                        >
                          Close Bond
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
            </div>{/* close lg:col-start-5 */}
          </div>{/* close inner lg:grid */}
        </div>{/* close xl:col-span-9 */}
      </div>{/* close outer xl:grid */}
      </div>{/* close max-w container */}

      {/* Create Bond Confirmation Modal */}
      <CreateBondModal
        isOpen={showBondModal}
        onClose={() => setShowBondModal(false)}
        onConfirm={() => {
          setShowBondModal(false);
        }}
        notional={notional}
        maturityDays={maturityDays}
        maturityDate={maturityDate}
        entryRate={latest.apy}
      />

      {/* Close Bond Confirmation Modal */}
      <CloseBondModal
        isOpen={showCloseModal}
        onClose={() => setShowCloseModal(false)}
        onConfirm={() => {
          setShowCloseModal(false);
          setSelectedBond(null);
        }}
        bond={selectedBond ? USER_BONDS.find(b => b.id === selectedBond) : null}
      />
    </div>
  );
}
