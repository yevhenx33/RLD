import React, { useState, useEffect, useMemo } from "react";
import { ethers } from "ethers";
import { RPC_URL } from "../utils/anvil";
import { Shield, Terminal, AlertTriangle, ChevronDown, Wallet } from "lucide-react";
import { useWallet } from "../context/WalletContext";
import Header from "./Header";
import { formatNum } from "../utils/helpers";
import { calcHedgeSize, calcInitialLTV } from "../utils/hedgeCalc";
import { useToast } from "../hooks/useToast";
import { ToastContainer } from "./Toast";

// Hooks
import { useMarketData } from "../hooks/useMarketData";
import { useTradeLogic } from "../hooks/useTradeLogic";
import { useSimulation } from "../hooks/useSimulation";
import { useWealthProjection } from "../hooks/useWealthProjection";

import { useBondExecution } from "../hooks/useBondExecution";
import { useBondPositions } from "../hooks/useBondPositions";

// Components
import MetricsGrid from "./MetricsGrid";
import TradingTerminal, { InputGroup, SummaryRow } from "./TradingTerminal";
import CreateBondModal from "./CreateBondModal";
import CloseBondModal from "./CloseBondModal";
import BondBrandingPanel from "./BondBrandingPanel";
import WealthProjectionChart from "./WealthProjectionChart";
import SettingsButton from "./SettingsButton";
import AccountModal from "./AccountModal";



export default function BondsPage() {
  const [showBondModal, setShowBondModal] = useState(false);
  const [showCloseModal, setShowCloseModal] = useState(false);
  const [selectedBond, setSelectedBond] = useState(null);
  const [actionDropdown, setActionDropdown] = useState(null);
  const { account, connectWallet, usdcBalance } = useWallet();
  const { toasts, addToast, removeToast } = useToast();
  const {
    rates,
    error,
    isLoading,
    dailyChange,
    latest,
    isCappedRaw: _isCappedRaw,
  } = useMarketData();

  // Live simulation data for stats panel + infrastructure addresses
  const { poolTVL, protocolStats, marketInfo } = useSimulation({ pollInterval: 5000 });
  const openInterest = (protocolStats?.totalCollateral || 0) + (protocolStats?.totalDebtUsd || 0);

  // Wallet balance (bonds pull from wallet, not broker)
  const [walletBalance, setWalletBalance] = useState(null);
  useEffect(() => {
    if (!account || !marketInfo?.collateral?.address) return;
    const fetchBal = async () => {
      try {
        const provider = new ethers.JsonRpcProvider(RPC_URL);
        const token = new ethers.Contract(
          marketInfo.collateral.address,
          ["function balanceOf(address) view returns (uint256)"],
          provider,
        );
        const bal = await token.balanceOf(account);
        setWalletBalance(Number(ethers.formatUnits(bal, 6)));
      } catch {}
    };
    fetchBal();
    const id = setInterval(fetchBal, 10000);
    return () => clearInterval(id);
  }, [account, marketInfo?.collateral?.address]);


  // Bond execution hook (atomic via BrokerExecutor)
  const {
    createBond,
    closeBond,
    executing: bondExecuting,
    error: bondError,
    step: bondStep,
  } = useBondExecution(
    account,
    marketInfo?.infrastructure,
    marketInfo?.collateral?.address,
    marketInfo?.position_token?.address,
  );
  const tradeLogic = useTradeLogic(latest.apy);

  // Real on-chain bond positions (all bond broker NFTs owned by account)
  const { bonds: userBonds, refresh: refreshBonds } = useBondPositions(
    account,
    latest?.apy,
  );

  const {
    activeProduct,
    activeTab,
    notional,
    maturityHours,
    maturityDays,
    epochs,
  } = tradeLogic.state;
  const {
    setActiveTab,
    setNotional,
    handleHoursChange,
    handleDaysChange,
    handleEndDateChange,
  } = tradeLogic.actions;

  const projectionData = useWealthProjection(
    tradeLogic.state.notional,
    latest.apy,
    tradeLogic.state.maturityDays,
  );

  // Hedge size computation (recalculates on every input change)
  const hedgeInfo = useMemo(() => {
    const hedge = calcHedgeSize(notional, latest.apy, maturityHours);
    const ltv = calcInitialLTV(notional, hedge);
    return { hedge, ltv };
  }, [notional, latest.apy, maturityHours]);

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
                  openInterest={openInterest}
                  liquidity={poolTVL || 0}
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
              label: !account
                ? "Connect Wallet"
                : bondExecuting
                  ? bondStep || "Processing..."
                  : activeTab === "OPEN" ? "Create Bond" : "Close Bond",
              onClick: !account
                ? connectWallet
                : activeTab === "OPEN"
                  ? () => setShowBondModal(true)
                  : () => {
                      if (selectedBond) setShowCloseModal(true);
                    },
              disabled: bondExecuting || (activeTab === "CLOSE" && !selectedBond),
              variant: activeProduct === "FIXED_BORROW" ? "pink" : activeTab === "CLOSE" ? "pink" : "cyan",
            }}
          >
            {/* Wraps the specific content for Bonds */}
            {activeTab === "OPEN" && (
              <>
                <InputGroup
                  label="Notional_Amount"
                  subLabel={`Bal: ${walletBalance !== null ? walletBalance.toFixed(2) : "0.00"} waUSDC`}
                  value={notional}
                  onChange={(v) => setNotional(Number(v))}
                  suffix="USDC"
                />

                <div className="space-y-3">
                  <div className="flex justify-between items-end">
                    <span className="text-sm text-gray-500 uppercase tracking-widest font-bold">
                      Duration
                    </span>
                    <span
                      className={`text-sm font-mono font-bold ${
                        activeProduct === "FIXED_BORROW"
                          ? "text-pink-500"
                          : "text-cyan-400"
                      }`}
                    >
                      {maturityHours < 24
                        ? `${maturityHours}H`
                        : maturityHours % 24 === 0
                          ? `${Math.floor(maturityHours / 24)}D`
                          : `${Math.floor(maturityHours / 24)}D ${maturityHours % 24}H`}
                    </span>
                  </div>

                  <div
                    className="relative group cursor-pointer"
                    onClick={() => document.getElementById("bond-maturity-picker")?.showPicker?.()}
                  >
                    <div className="flex items-center gap-2 border-b border-white/20 pb-1">
                      <input
                        id="bond-maturity-picker"
                        type="datetime-local"
                        step="3600"
                        value={epochs.endDateTimeLocal}
                        onChange={(e) => handleEndDateChange(e.target.value)}
                        className="bg-transparent text-sm font-mono text-white focus:outline-none w-full uppercase pointer-events-none [&::-webkit-calendar-picker-indicator]:brightness-0 [&::-webkit-calendar-picker-indicator]:invert [&::-webkit-calendar-picker-indicator]:opacity-80"
                        style={{ colorScheme: "dark" }}
                      />
                    </div>
                  </div>

                  <div className="pt-2">
                    <input
                      type="range"
                      min="1"
                      max="8760"
                      step="1"
                      value={maturityHours}
                      onChange={(e) => handleHoursChange(Number(e.target.value))}
                      className="w-full h-0.5 bg-white/10 rounded-none appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-none hover:[&::-webkit-slider-thumb]:scale-125 transition-all"
                    />
                    <div className="flex justify-between text-sm text-gray-400 font-bold font-mono mt-1">
                      <span>1H</span>
                      <span>1Y</span>
                    </div>
                  </div>

                  {/* Duration Presets */}
                  <div className="flex items-center gap-1.5 pt-1">
                    {[
                      { label: "1H", hours: 1 },
                      { label: "1D", hours: 24 },
                      { label: "1M", hours: 30 * 24 },
                      { label: "3M", hours: 90 * 24 },
                      { label: "1Y", hours: 365 * 24 },
                    ].map((preset) => {
                      const isActive = maturityHours === preset.hours;
                      return (
                        <button
                          key={preset.label}
                          onClick={() => handleHoursChange(preset.hours)}
                          className={`flex-1 py-1.5 text-sm font-bold font-mono transition-all border ${
                            isActive
                              ? activeProduct === "FIXED_BORROW"
                                ? "border-pink-500/50 bg-pink-500/10 text-pink-400"
                                : "border-cyan-500/50 bg-cyan-500/10 text-cyan-400"
                              : "border-white/10 bg-transparent text-gray-500 hover:border-white/20 hover:text-white"
                          }`}
                        >
                          {preset.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="border border-white/10 p-4 space-y-3 bg-white/[0.02] text-sm tracking-widest">
                  <SummaryRow
                    label="Entry_Rate"
                    value={`${formatNum(latest.apy)}%`}
                  />
                  <SummaryRow
                    label="Hedge_Size"
                    value={`${hedgeInfo.hedge.toFixed(2)} wRLP`}
                  />
                  <SummaryRow
                    label="Initial_LTV"
                    value={`${hedgeInfo.ltv.toFixed(1)}%`}
                  />
                  {bondError && (
                    <div className="text-xs text-red-400 font-mono mt-2 break-all">
                      {bondError}
                    </div>
                  )}
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
                    const bond = userBonds.find(b => b.id === selectedBond);
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
                      {userBonds.map((bond) => {
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
                {userBonds.length}
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
          {userBonds.map((bond) => {
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
          createBond(notional, maturityHours, latest.apy, (receipt) => {
            console.log("[Bond] Created:", receipt.hash, "Broker:", receipt.brokerAddress);
            addToast({
              type: "success",
              title: "Bond Created",
              message: `$${notional.toLocaleString()} bond minted — tx ${receipt.hash.slice(0, 10)}…`,
            });
            refreshBonds();
          });
        }}
        notional={notional}
        maturityDays={maturityDays}
        maturityDate={epochs.endDisplay}
        entryRate={latest.apy}
        hedgeSize={hedgeInfo.hedge}
        initialLTV={hedgeInfo.ltv}
      />

      {/* Close Bond Confirmation Modal */}
      <CloseBondModal
        isOpen={showCloseModal}
        onClose={() => setShowCloseModal(false)}
        onConfirm={() => {
          setShowCloseModal(false);
          setSelectedBond(null);
        }}
        bond={selectedBond ? userBonds.find(b => b.id === selectedBond) : null}
      />


      {/* Toast notifications */}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
}
