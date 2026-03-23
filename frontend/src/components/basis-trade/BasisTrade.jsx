import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { ethers } from "ethers";
import { getAnvilSigner, restoreAnvilChainId } from "../../utils/anvil";
import { rpcProvider } from "../../utils/provider";
import { TrendingUp, Terminal, AlertTriangle, ChevronDown, Layers } from "lucide-react";
import { useWallet } from "../../context/WalletContext";
import { formatNum } from "../../utils/helpers";

import { useToast } from "../../hooks/useToast";
import { ToastContainer } from "../common/Toast";

// Hooks
import { useMarketData } from "../../hooks/useMarketData";
import { useTradeLogic } from "../../hooks/useTradeLogic";
import { useSim } from "../../context/SimulationContext";

import { useBasisTradeExecution } from "../../hooks/useBasisTradeExecution";
import { useBondPositions } from "../../hooks/useBondPositions";
import { useSusdeYield } from "../../hooks/useSusdeYield";

// Components
import TradingTerminal, { InputGroup, SummaryRow } from "../trading/TradingTerminal";
import OpenTradeModal from "./OpenTradeModal";
import CloseTradeModal from "./CloseTradeModal";
import BasisTradeBrandingPanel from "./BasisTradeBrandingPanel";

const fmt = (v) => {
  const abs = Math.abs(v);
  if (abs >= 1000) return `$${abs.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  return `$${abs.toFixed(0)}`;
};

const BasisTradeComparison = ({ currentYield, expectedYield, currentBorrow, leverage, capital, timeHorizon }) => {
  // Setup
  const collateral = capital * leverage;
  const debt = capital * (leverage - 1);
  const t = timeHorizon / 365; // annualization factor

  // Yield delta — when sUSDe yield rises, borrow cost also rises (market correlation)
  const yieldDelta = expectedYield - currentYield;

  // Unhedged: borrow floats up with the market
  const unhedgedBorrow = currentBorrow + yieldDelta;
  const uGross = collateral * (expectedYield / 100) * t;
  const uInterest = debt * (unhedgedBorrow / 100) * t;
  const uNet = uGross - uInterest;
  const uRoi = (uNet / capital) * 100;
  const uSpread = expectedYield - unhedgedBorrow;

  // Hedged: borrow is LOCKED at current rate via RLP
  const hGross = collateral * (expectedYield / 100) * t;
  const hInterest = debt * (currentBorrow / 100) * t;
  const hNet = hGross - hInterest;
  const hRoi = (hNet / capital) * 100;
  const hSpread = expectedYield - currentBorrow;

  return (
    <div className="bg-[#0a0a0a] border border-white/10 p-5 h-full flex flex-col">
      <div className="mb-4">
        <h3 className="text-sm font-bold uppercase tracking-widest text-white mb-1">Hedge Performance</h3>
        <p className="text-xs text-gray-500 font-mono">Scenario: Yield Growth {currentYield.toFixed(1)}% → {expectedYield.toFixed(1)}% over {timeHorizon}d</p>
      </div>

      <div className="flex-1 overflow-x-auto -mx-5 px-5 md:mx-0 md:px-0">
        <div className="min-w-[320px]">
          <table className="w-full text-sm font-mono text-left">
            <thead>
              <tr className="border-b border-white/10 text-gray-500 uppercase tracking-widest text-xs">
                <th className="py-3 font-normal w-1/3">Metric</th>
                <th className="py-3 font-normal text-right w-1/3">Unhedged<br className="md:hidden" /><span className="hidden md:inline"> </span>({leverage}x)</th>
                <th className="py-3 font-bold text-pink-400 text-right w-1/3">Hedged<br className="md:hidden" /><span className="hidden md:inline"> </span>({leverage}x+RLP)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              <tr>
                <td className="py-3 text-gray-400">Gross Yield</td>
                <td className="py-3 text-right text-white">{fmt(uGross)} <span className="text-[10px] md:text-xs text-gray-600 block">@ {expectedYield.toFixed(1)}%</span></td>
                <td className="py-3 text-right text-white">{fmt(hGross)} <span className="text-[10px] md:text-xs text-gray-600 block">@ {expectedYield.toFixed(1)}%</span></td>
              </tr>
              <tr>
                <td className="py-3 text-gray-400 leading-tight">Interest<br className="md:hidden"/> Expense</td>
                <td className="py-3 text-right text-red-500">({fmt(uInterest)}) <span className="text-[10px] md:text-xs text-gray-600 block">@ {unhedgedBorrow.toFixed(1)}% flt</span></td>
                <td className="py-3 text-right text-green-400">({fmt(hInterest)}) <span className="text-[10px] md:text-xs text-gray-600 block">@ {currentBorrow.toFixed(1)}% fix</span></td>
              </tr>
              <tr>
                <td className="py-3 text-gray-400">Spread</td>
                <td className="py-3 text-right text-gray-400">{uSpread.toFixed(2)}%</td>
                <td className="py-3 text-right text-green-400 font-bold">{hSpread.toFixed(2)}%</td>
              </tr>
              <tr>
                <td className="py-3 text-gray-400">Net Profit</td>
                <td className={`py-3 text-right ${uNet >= 0 ? 'text-white' : 'text-red-400'}`}>{uNet < 0 ? '-' : ''}{fmt(uNet)}</td>
                <td className={`py-3 text-right font-bold ${hNet >= 0 ? 'text-green-400' : 'text-red-400'}`}>{hNet < 0 ? '-' : ''}{fmt(hNet)}</td>
              </tr>
              <tr className="border-t border-white/20">
                <td className="pt-3 pb-[4px] text-white font-bold whitespace-nowrap">ROI / {timeHorizon}d</td>
                <td className={`pt-3 pb-[4px] text-right ${uRoi >= 0 ? 'text-white' : 'text-red-400'}`}>{uRoi.toFixed(2)}%</td>
                <td className="pt-3 pb-[4px] text-right text-pink-400 font-bold text-base md:text-lg">{hRoi.toFixed(2)}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default function BasisTradePage() {
  const [showBondModal, setShowBondModal] = useState(false);
  const [showCloseModal, setShowCloseModal] = useState(false);
  const [selectedBond, setSelectedBond] = useState(null);
  const [actionDropdown, setActionDropdown] = useState(null);
  const [selectedToken, setSelectedToken] = useState("USDC");
  const [tokenDropdownOpen, setTokenDropdownOpen] = useState(false);
  const [expectedYield, setExpectedYield] = useState("10");
  const [leverage, setLeverage] = useState("3");
  const [timeHorizon, setTimeHorizon] = useState("365");
  const [capital, setCapital] = useState("10000");
  const tokenDropdownRef = useRef(null);
  const bondExecutingRef = useRef(false);
  
  const { account, connectWallet } = useWallet();
  const { toasts, addToast, removeToast } = useToast();
  
  const {
    rates,
    error,
    isLoading,
    latest,
  } = useMarketData();

  const { marketInfo } = useSim();

  // Live sUSDe yield from Ethena
  const { stakingYield: susdeYield, lastUpdated: susdeUpdated } = useSusdeYield();
  const susdeRate = susdeYield ?? 0;
  const usdcCost = latest?.apy ?? 0;
  const spread = susdeRate - usdcCost;

  // Real sUSDe + USDC wallet balance — addresses from indexer
  const SUSDE_ADDRESS = marketInfo?.external_contracts?.susde || "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497";
  const USDC_ADDRESS = marketInfo?.external_contracts?.usdc || "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";
  const [susdeBalance, setSusdeBalance] = useState(null);
  const [usdcBalance, setUsdcBalance] = useState(null);
  const [susdeAllowance, setSusdeAllowance] = useState(0);
  const [usdcAllowance, setUsdcAllowance] = useState(0);

  const refreshBalances = useCallback(async (force = false) => {
    if (!account || !marketInfo?.infrastructure) return;
    const basisTradeFactory = marketInfo.infrastructure?.basis_trade_factory;
    // Fallback: if basis_trade_factory isn't available yet, use broker_factory for allowance checks
    const spender = basisTradeFactory || marketInfo.broker_factory;
    if (!spender) return;

    if (!force && bondExecutingRef.current) return; // Guard 1

    try {
      const provider = rpcProvider;
      const abi = ["function balanceOf(address) view returns (uint256)", "function allowance(address,address) view returns (uint256)"];
      
      const susde = new ethers.Contract(SUSDE_ADDRESS, abi, provider);
      const sBal = await susde.balanceOf(account);
      const sAllow = await susde.allowance(account, spender);

      if (!force && bondExecutingRef.current) return; // Guard 2

      setSusdeBalance(Number(ethers.formatUnits(sBal, 18)));
      setSusdeAllowance(Number(ethers.formatUnits(sAllow, 18)));

      const usdc = new ethers.Contract(USDC_ADDRESS, abi, provider);
      const uBal = await usdc.balanceOf(account);
      const uAllow = await usdc.allowance(account, spender);

      if (!force && bondExecutingRef.current) return; // Guard 3

      setUsdcBalance(Number(ethers.formatUnits(uBal, 6)));
      setUsdcAllowance(Number(ethers.formatUnits(uAllow, 6)));
    } catch (e) {
      console.warn("[BasisTrade] balance fetch error:", e);
    }
  }, [account, marketInfo, SUSDE_ADDRESS, USDC_ADDRESS]);

  useEffect(() => {
    refreshBalances();
    const id = setInterval(() => {
      if (!bondExecutingRef.current) refreshBalances();
    }, 10000);
    return () => clearInterval(id);
  }, [refreshBalances]);
  const walletBalance = selectedToken === "USDC" ? (usdcBalance ?? 0) : (susdeBalance ?? 0);

  useEffect(() => {
    const handler = (e) => {
      if (tokenDropdownRef.current && !tokenDropdownRef.current.contains(e.target)) {
        setTokenDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const [isApproving, setIsApproving] = useState(false);

  const handleApprove = async () => {
    try {
      if (!marketInfo?.infrastructure?.basis_trade_factory) return;
      setIsApproving(true);
      const signer = await getAnvilSigner();
      const basisTradeFactory = marketInfo.infrastructure.basis_trade_factory;
      const approveTokenAddr = selectedToken === "USDC" ? USDC_ADDRESS : SUSDE_ADDRESS;
      
      const token = new ethers.Contract(approveTokenAddr, ["function approve(address,uint256) returns (bool)"], signer);
      const tx = await token.approve(basisTradeFactory, ethers.MaxUint256);
      addToast({ type: "info", title: "Approving", message: `Approving ${selectedToken}...` });
      const receipt = await tx.wait();
      
      if (selectedToken === "USDC") {
        setUsdcAllowance(Number.MAX_SAFE_INTEGER);
      } else {
        setSusdeAllowance(Number.MAX_SAFE_INTEGER);
      }
      
      addToast({ type: "success", title: "Approved", message: `Successfully approved ${selectedToken} — tx ${receipt.hash.slice(0, 10)}…` });
    } catch (err) {
      console.error(err);
      let msg = err.message;
      if (err.reason) msg = err.reason;
      else if (err.message?.includes("user rejected")) msg = "User rejected";
      addToast({ type: "error", title: "Approval Failed", message: msg });
    } finally {
      setIsApproving(false);
      try { await restoreAnvilChainId(); } catch { /* non-critical */ }
    }
  };

  // Use a boosted APY for the basis trade simulation (Latest APY + 4% for carry)
  const basisApy = (latest?.apy || 0) + 4.2;
  const tradeLogic = useTradeLogic(basisApy);

  const { bonds: userBonds, refresh: refreshBonds, optimisticClose, optimisticCreate } = useBondPositions(
    account, 
    basisApy,
    marketInfo?.infrastructure?.basis_trade_factory,
    15000,
    bondExecutingRef.current
  );

  const {
    createBasisTrade,
    closeBasisTrade,
    executing: bondExecuting,
    error: bondError,
    step: bondStep,
    computeLevDebtAndHedge,
  } = useBasisTradeExecution(
    account,
    marketInfo?.infrastructure ? { ...marketInfo.infrastructure, broker_factory: marketInfo.broker_factory } : undefined,
    marketInfo?.collateral?.address,
    marketInfo?.position_token?.address,
    marketInfo?.external_contracts,
    { onRefreshComplete: [refreshBonds, () => refreshBalances(true)], pauseRef: bondExecutingRef },
  );

  const {
    activeTab,
  } = tradeLogic.state;
  const {
    setActiveTab,
  } = tradeLogic.actions;

  const hedgeInfo = useMemo(() => {
    const lev = Number(leverage) || 1;
    const days = Number(timeHorizon) || 90;
    const borrowRate = usdcCost || 2.9;
    const { levDebt, hedge } = computeLevDebtAndHedge
      ? computeLevDebtAndHedge(Number(capital) || 0, lev, days, borrowRate)
      : { levDebt: 0, hedge: 0 };
    return { levDebt, hedge };
  }, [capital, leverage, timeHorizon, usdcCost, computeLevDebtAndHedge]);

  const notionalAmount = Number(capital) || 0;
  const currentAllowance = selectedToken === "USDC" ? usdcAllowance : susdeAllowance;
  const needsApproval = currentAllowance < notionalAmount;

  if (error) return <div className="h-screen flex items-center justify-center text-red-600 bg-black font-mono text-xs">ERR: API_DISCONNECTED</div>;
  if (isLoading || !rates) return <div className="h-screen flex items-center justify-center text-gray-500 bg-black font-mono text-xs animate-pulse">SYSTEM_INITIALIZING...</div>;

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        {/* === 3-Column Grid: Branding | Content | Trading === */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">

          {/* COL 1: Mechanism Flow */}
          <div className="xl:col-span-3">
            <BasisTradeBrandingPanel accentSteps={userBonds.length > 0 ? ["1", "2", "3", "4", "5"] : ["1"]} />
          </div>

          {/* COL 2: Market + Settings + Comparison + Positions */}
          <div className="xl:col-span-6 flex flex-col gap-3">

            {/* Market Panel */}
            <div className="bg-[#0a0a0a] border border-white/10 p-5">
              <div className="text-sm font-bold uppercase tracking-widest text-pink-400 mb-4 flex items-center justify-between">
                <span>Market</span>
                {susdeUpdated && <span className="text-gray-600 text-xs font-mono">Updated {susdeUpdated}</span>}
              </div>
              <div className="grid grid-cols-3 gap-6 items-end">
                <div>
                  <div className="text-sm text-gray-500 font-mono mb-1">Yield sUSDe</div>
                  <div className="text-3xl font-mono tracking-tight text-white">{susdeRate.toFixed(2)}<span className="text-lg text-gray-500">%</span></div>
                </div>
                <div>
                  <div className="text-sm text-gray-500 font-mono mb-1">Cost USDC</div>
                  <div className="text-3xl font-mono tracking-tight text-white">{usdcCost.toFixed(2)}<span className="text-lg text-gray-500">%</span></div>
                </div>
                <div>
                  <div className="text-sm text-gray-500 font-mono mb-1">Spread</div>
                  <div className={`text-3xl font-mono tracking-tight ${spread >= 0 ? 'text-green-400' : 'text-red-400'}`}>{spread >= 0 ? '+' : ''}{spread.toFixed(2)}<span className={`text-lg ${spread >= 0 ? 'text-green-500/50' : 'text-red-500/50'}`}>%</span></div>
                </div>
              </div>
            </div>

            {/* Scenario Inputs */}
            <div className="bg-[#0a0a0a] border border-white/10 p-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <label className="block text-xs text-gray-500 uppercase tracking-widest font-bold mb-1.5">Capital</label>
                  <div className="relative">
                    <input type="number" step="10000" min="1000" value={capital} onChange={(e) => setCapital(e.target.value)} className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-1.5 focus:outline-none focus:border-pink-500/50 transition-colors rounded-none" />
                    <span className="absolute right-0 top-1.5 text-sm text-gray-600 font-mono">$</span>
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 uppercase tracking-widest font-bold mb-1.5">Expected Yield</label>
                  <div className="relative">
                    <input type="number" step="0.1" min="0" max="100" value={expectedYield} placeholder={susdeRate.toFixed(2)} onChange={(e) => setExpectedYield(e.target.value)} className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-1.5 focus:outline-none focus:border-pink-500/50 transition-colors placeholder:text-gray-700 rounded-none" />
                    <span className="absolute right-0 top-1.5 text-sm text-gray-600 font-mono">%</span>
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 uppercase tracking-widest font-bold mb-1.5">Leverage</label>
                  <div className="relative">
                    <input type="number" step="0.5" min="1" max="10" value={leverage} onChange={(e) => setLeverage(e.target.value)} className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-1.5 focus:outline-none focus:border-pink-500/50 transition-colors rounded-none" />
                    <span className="absolute right-0 top-1.5 text-sm text-gray-600 font-mono">x</span>
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 uppercase tracking-widest font-bold mb-1.5">Time Horizon</label>
                  <div className="relative">
                    <input type="number" step="1" min="1" max="1825" value={timeHorizon} onChange={(e) => setTimeHorizon(e.target.value)} className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-1.5 focus:outline-none focus:border-pink-500/50 transition-colors rounded-none" />
                    <span className="absolute right-0 top-1.5 text-sm text-gray-600 font-mono">days</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Hedge Performance Comparison */}
            <BasisTradeComparison
              currentYield={susdeRate}
              expectedYield={expectedYield !== "" ? Number(expectedYield) : susdeRate}
              currentBorrow={usdcCost || 4}
              leverage={Number(leverage) || 3}
              capital={Number(capital) || 100000}
              timeHorizon={Number(timeHorizon) || 365}
            />

            {/* Positions Table */}
            <div className="bg-[#0a0a0a] border border-white/10 p-5 flex flex-col">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="text-sm font-bold uppercase tracking-widest text-pink-400">Your Positions</h3>
                  <span className="text-sm text-gray-600 font-mono">{userBonds.length}</span>
                </div>
                <div className="text-sm text-gray-500 uppercase tracking-widest flex items-center gap-2">
                  <TrendingUp size={12} /> ACTIVE
                </div>
              </div>

              <div className="flex flex-col overflow-x-hidden md:overflow-x-auto -mx-5 px-5 md:mx-0 md:px-0">
                <div className="w-full md:min-w-[800px]">
                  {/* Headers */}
                  <div className="grid grid-cols-[1.4fr_1fr_1fr_1fr_3.5rem] md:flex items-center py-3 text-xs md:text-sm text-gray-500 uppercase tracking-widest border-b border-white/5">
                    <div className="hidden md:block w-14 shrink-0 text-left">#</div>
                    <div className="hidden md:block flex-1" />
                    <div className="md:w-24 text-left md:text-center">Capital</div>
                    <div className="md:w-24 text-center">Locked</div>
                    <div className="hidden md:block w-14 text-center">Lev</div>
                    <div className="md:w-32 text-center">Duration</div>
                    <div className="md:w-24 text-center">PnL</div>
                    <div className="md:w-20 shrink-0 text-right md:text-center">Action</div>
                  </div>
                  
                  {userBonds.map((bond) => {
                    const lev = bond.leverage || 3;
                    const lockedRate = bond.entryBorrowRate || basisApy;
                    const leveragedCapital = bond.principal * lev;
                    const pnl = leveragedCapital * (spread / 100) * (bond.elapsed / 365);
                    const remaining = bond.maturityDays - bond.elapsed;
                    const progress = Math.min((bond.elapsed / bond.maturityDays) * 100, 100);
                    const isMatured = remaining <= 0;
                    
                    return (
                      <div key={bond.id} className="relative group grid grid-cols-[1.4fr_1fr_1fr_1fr_3.5rem] md:flex items-center hover:bg-white/[0.02] transition-colors border-b border-white/5 last:border-b-0 py-3 md:py-4">
                        <div className="hidden md:block w-14 shrink-0 text-sm text-gray-500 font-mono text-left">{String(bond.id).padStart(4, "0")}</div>
                        <div className="hidden md:block flex-1" />
                        
                        <div className="md:w-24 text-left md:text-center text-sm font-mono text-white leading-tight">
                          {formatNum(bond.principal, 0)} <span className="text-gray-600 text-[10px] md:text-xs">sUSDe</span>
                        </div>
                        
                        <div className="md:w-24 text-center text-sm font-mono text-pink-400">
                          {lockedRate >= 0 ? "+" : ""}{formatNum(lockedRate)}<span className="md:hidden">%</span><span className="hidden md:inline">%</span>
                        </div>
                        
                        <div className="hidden md:block w-14 text-center text-sm font-mono text-white">{lev}×</div>
                        
                        <div className="md:w-32 flex items-center justify-center gap-2">
                          <span className="text-sm font-mono text-white shrink-0">{isMatured ? <span className="text-green-400">0</span> : `${remaining}`}<span className="text-gray-500 text-[10px] md:text-xs">D</span></span>
                          <div className="hidden md:block w-12 bg-white/5 h-1"><div className="h-full bg-pink-500/60" style={{ width: `${progress}%` }} /></div>
                        </div>
                        
                        <div className={`md:w-24 text-center text-sm font-mono ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {pnl >= 0 ? "+" : ""}{formatNum(pnl, 0)}
                        </div>
                        
                        <div className="md:w-20 shrink-0 relative flex justify-end md:justify-center">
                          <button onClick={(e) => { e.stopPropagation(); setActionDropdown(actionDropdown === bond.id ? null : bond.id); }} className="p-1 md:p-1.5 text-gray-600 hover:text-white hover:bg-white/5 transition-colors">
                            <ChevronDown size={14} className={`md:w-4 md:h-4 transition-transform ${actionDropdown === bond.id ? 'rotate-180' : ''}`} />
                          </button>
                          {actionDropdown === bond.id && (
                            <div className="absolute right-0 top-full mt-1 z-50 border border-white/10 bg-[#0a0a0a] backdrop-blur-sm min-w-[150px] shadow-2xl">
                              <button onClick={() => { setActionDropdown(null); setSelectedBond(bond.id); setShowCloseModal(true); }} className="w-full text-left px-4 py-2 text-sm text-white hover:bg-white/5 transition-colors font-mono">Exit Strategy</button>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

          </div>

          {/* COL 3: Trading Terminal */}
          <div className="xl:col-span-3">
          <TradingTerminal
            account={account}
            connectWallet={connectWallet}
            title="BASIS_TRADE"
            Icon={Terminal}
            tabs={[
              { id: "OPEN", label: "OPEN", onClick: () => setActiveTab("OPEN"), isActive: activeTab === "OPEN" },
              { id: "CLOSE", label: "CLOSE", onClick: () => setActiveTab("CLOSE"), isActive: activeTab === "CLOSE" },
            ]}
            actionButton={{
              label: !account 
                ? "Connect Wallet" 
                : activeTab === "OPEN"
                  ? needsApproval
                    ? isApproving ? `Approving ${selectedToken}...` : `Approve ${selectedToken}`
                    : bondExecuting ? bondStep || "Processing..." : "Enter Strategy"
                  : bondExecuting ? bondStep || "Processing..." : "Exit Strategy",
              onClick: !account 
                ? connectWallet 
                : activeTab === "OPEN" 
                  ? needsApproval 
                    ? handleApprove 
                    : () => setShowBondModal(true) 
                  : () => { if (selectedBond) setShowCloseModal(true); },
              disabled: bondExecuting || isApproving || (activeTab === "CLOSE" && !selectedBond),
              variant: "pink",
            }}
          >
            {activeTab === "OPEN" && (
              <>
                <InputGroup
                  label="Capital"
                  subLabel={
                    <span className="flex items-center gap-1">
                      Bal: {walletBalance !== null ? walletBalance.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}
                      <span className="relative" ref={tokenDropdownRef}>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); setTokenDropdownOpen(!tokenDropdownOpen); }}
                          className={`
                            h-[22px] border border-white/10 bg-[#0a0a0a] inline-flex items-center px-1.5 gap-1
                            text-xs font-mono text-white focus:outline-none uppercase tracking-widest
                            hover:border-white/30 transition-colors
                            ${tokenDropdownOpen ? "border-white/30" : ""}
                          `}
                        >
                          {selectedToken}
                          <ChevronDown size={10} className={`transition-transform duration-200 flex-shrink-0 ${tokenDropdownOpen ? "rotate-180" : ""}`} />
                        </button>
                        {tokenDropdownOpen && (
                          <div className="absolute top-full right-0 mt-1 bg-[#0a0a0a] border border-white/10 z-50 flex flex-col shadow-xl whitespace-nowrap">
                            <button onClick={() => { setSelectedToken("sUSDe"); setTokenDropdownOpen(false); }} className={`w-full flex items-center px-3 py-1.5 text-xs text-left uppercase tracking-widest transition-colors ${selectedToken === "sUSDe" ? "bg-pink-500/10 text-pink-400" : "text-gray-500 hover:bg-white/5 hover:text-gray-300"}`}>sUSDe</button>
                            <button onClick={() => { setSelectedToken("USDC"); setTokenDropdownOpen(false); }} className={`w-full flex items-center px-3 py-1.5 text-xs text-left uppercase tracking-widest transition-colors ${selectedToken === "USDC" ? "bg-pink-500/10 text-pink-400" : "text-gray-500 hover:bg-white/5 hover:text-gray-300"}`}>USDC</button>
                          </div>
                        )}
                      </span>
                    </span>
                  }
                  value={capital}
                  onChange={(v) => setCapital(v)}
                  suffix={selectedToken}
                />

                <div className="space-y-1.5">
                  <div className="flex justify-between text-sm uppercase tracking-widest font-bold text-gray-500">
                    <span>Leverage</span>
                    <span className="text-pink-500 font-mono">{leverage}x</span>
                  </div>
                  <div className="relative group">
                    <input
                      type="number"
                      step="0.5"
                      min="1"
                      max="10"
                      value={leverage}
                      onChange={(e) => setLeverage(e.target.value)}
                      className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-2 focus:outline-none focus:border-pink-500/50 transition-colors rounded-none"
                      placeholder="3"
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex justify-between text-sm uppercase tracking-widest font-bold text-gray-500">
                    <span>Duration</span>
                    <span className="text-pink-500 font-mono">
                      {(Number(timeHorizon) || 365) < 1 ? "1D" : `${Number(timeHorizon) || 365}D`}
                    </span>
                  </div>
                  <input type="range" min="1" max="1825" step="1" value={Number(timeHorizon) || 365} onChange={(e) => setTimeHorizon(e.target.value)} className="w-full h-0.5 bg-white/10 rounded-none appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-none hover:[&::-webkit-slider-thumb]:scale-125 transition-all" />
                  <div className="flex justify-between text-sm text-gray-400 font-bold font-mono"><span>1D</span><span>5Y</span></div>
                  <div className="flex items-center gap-1.5">
                    {[{ label: "1W", days: 7 }, { label: "1M", days: 30 }, { label: "3M", days: 90 }, { label: "1Y", days: 365 }, { label: "2Y", days: 730 }].map((preset) => (
                      <button key={preset.label} onClick={() => setTimeHorizon(String(preset.days))} className={`flex-1 py-1.5 text-sm font-bold font-mono transition-all border ${Number(timeHorizon) === preset.days ? "border-pink-500/50 bg-pink-500/10 text-pink-400" : "border-white/10 bg-transparent text-gray-500 hover:border-white/20 hover:text-white"}`}>
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="border border-white/10 p-4 space-y-2 bg-white/[0.02] text-sm tracking-widest">
                  <SummaryRow label="Net_APY" value={`${formatNum(basisApy)}%`} />
                  <SummaryRow label="Leverage" value={`${leverage}x`} />

                  <SummaryRow label="Hedge" value={`${Math.ceil(hedgeInfo.hedge).toLocaleString()} PYUSD`} />
                  {bondError && <div className="text-xs text-red-400 font-mono mt-2 break-all">{bondError}</div>}
                </div>
              </>
            )}

            {activeTab === "CLOSE" && (
              <>
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm uppercase tracking-widest font-bold text-gray-500">{selectedBond ? "Position" : "Select Position"}</span>
                    {selectedBond && <button onClick={() => setSelectedBond(null)} className="text-sm text-pink-500 uppercase tracking-widest hover:text-pink-400 transition-colors">Change</button>}
                  </div>

                  {selectedBond && (() => {
                    const bond = userBonds.find(b => b.id === selectedBond);
                    if (!bond) return null;
                    const accrued = bond.principal * (basisApy / 100) * (bond.elapsed / 365);
                    return (
                      <>
                        <div className="flex items-center justify-between p-3 border border-pink-500/50 bg-pink-500/5">
                          <div>
                            <div className="text-sm font-mono text-white">#{String(bond.id).padStart(4, "0")} · {formatNum(basisApy)}% APY</div>
                            <div className="text-sm text-gray-500 font-mono">{Number(bond.principal).toLocaleString()} sUSDe · {bond.maturityDays}D</div>
                          </div>
                          <span className="text-sm font-mono text-green-400">+{formatNum(accrued, 2)}</span>
                        </div>
                        <div className="border border-white/10 p-4 space-y-4 bg-white/[0.02]">
                          <div className="flex justify-between items-center">
                            <span className="text-sm text-gray-500 uppercase tracking-widest font-bold">Estimated_PnL</span>
                            <span className="text-xl font-mono tracking-tight text-green-500">+ {formatNum(accrued, 2)} <span className="text-sm">sUSDe</span></span>
                          </div>
                          <div className="flex justify-between items-center border-t border-white/5 pt-4">
                            <span className="text-sm text-gray-500 uppercase tracking-widest font-bold">Time_to_Maturity</span>
                            <span className="font-mono text-white text-sm">{bond.maturityDays - bond.elapsed > 0 ? bond.maturityDays - bond.elapsed : 0} Days</span>
                          </div>
                        </div>
                        {bond.elapsed < bond.maturityDays && (
                          <div className="bg-yellow-900/10 border border-yellow-700/30 p-4 flex gap-3">
                            <AlertTriangle size={16} className="text-yellow-600 shrink-0 mt-0.5" />
                            <div>
                              <div className="text-sm text-yellow-500 font-bold uppercase tracking-widest mb-2">Early Exit Notice</div>
                              <p className="text-sm text-gray-400 leading-relaxed font-mono">Unwinding leverage before maturity involves swap costs and potential slippage.</p>
                            </div>
                          </div>
                        )}
                      </>
                    );
                  })()}

                  {!selectedBond && (
                    <>
                      {userBonds.map((bond) => {
                        const accrued = bond.principal * (basisApy / 100) * (bond.elapsed / 365);
                        const remaining = bond.maturityDays - bond.elapsed;
                        return (
                          <button key={bond.id} onClick={() => setSelectedBond(bond.id)} className="w-full text-left border p-3 transition-all border-white/10 bg-[#060606] hover:border-white/20 flex items-center justify-between">
                            <div>
                              <div className="text-sm font-mono text-white">#{String(bond.id).padStart(4, "0")} · {formatNum(basisApy)}% APY</div>
                              <div className="text-sm text-gray-500 font-mono">{Number(bond.principal).toLocaleString()} sUSDe · {remaining > 0 ? remaining : 0}D left</div>
                            </div>
                            <div className="text-sm font-mono text-green-400">+{formatNum(accrued, 2)}</div>
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

        </div>{/* close 3-col grid */}
      </div>{/* close max-w container */}


      <OpenTradeModal
        isOpen={showBondModal}
        onClose={() => { if (!bondExecuting) setShowBondModal(false); }}
        onConfirm={() => {
          const lev = Number(leverage) || 1;
          const days = Number(timeHorizon) || 90;
          const borrowRate = usdcCost || 2.9;
          createBasisTrade(Number(capital) || 0, lev, days, borrowRate, (receipt) => {
            setShowBondModal(false);
            addToast({ type: "success", title: "Position Opened", message: `${Number(capital).toLocaleString()} ${selectedToken} position created — tx ${receipt.hash.slice(0, 10)}…` });
            if (receipt.brokerAddress) optimisticCreate(receipt.brokerAddress, Number(capital) || 0, days * 24);
            else refreshBonds();
          }, { useUnderlying: selectedToken === "USDC" });
        }}
        capital={capital}
        leverage={leverage}
        duration={timeHorizon}
        susdeYield={susdeRate}
        usdcCost={usdcCost}
        spread={spread}
        levDebt={hedgeInfo.levDebt}
        hedge={hedgeInfo.hedge}
        selectedToken={selectedToken}
        executing={bondExecuting}
        executionStep={bondStep}
        executionError={bondError}
      />

      <CloseTradeModal
        isOpen={showCloseModal}
        onClose={() => { if (!bondExecuting) setShowCloseModal(false); }}
        onConfirm={() => {
          const bond = userBonds.find(b => b.id === selectedBond);
          if (!bond?.brokerAddress) return;
          closeBasisTrade(bond.brokerAddress, () => {
            setShowCloseModal(false);
            setSelectedBond(null);
            addToast({ type: "success", title: "Position Closed", message: `Position #${String(bond.id).padStart(4, "0")} closed — sUSDe returned to wallet` });
            optimisticClose(bond.brokerAddress);
          }, { useUnderlying: selectedToken === "USDC" });
        }}
        bond={selectedBond ? userBonds.find(b => b.id === selectedBond) : null}
        executing={bondExecuting}
        executionStep={bondStep}
        executionError={bondError}
        susdeYield={susdeRate}
        usdcCost={usdcCost}
      />

      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
}
