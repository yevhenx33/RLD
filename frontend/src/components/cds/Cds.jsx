import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Info, Terminal, Shield } from "lucide-react";
import { useParams } from "react-router-dom";
import { ethers } from "ethers";
import { useWallet } from "../../context/WalletContext";
import { useSimulation } from "../../hooks/useSimulation";
import { useTradeLogic } from "../../hooks/useTradeLogic";
import { useWealthProjection } from "../../hooks/useWealthProjection";
import { useCdsCoverageExecution } from "../../hooks/useCdsCoverageExecution";
import { useCdsCoveragePositions } from "../../hooks/useCdsCoveragePositions";
import { useToast } from "../../hooks/useToast";
import { rpcProvider } from "../../utils/provider";
import MetricsGrid from "../pools/MetricsGrid";
import TradingTerminal, { InputGroup, SummaryRow } from "../trading/TradingTerminal";
import { ToastContainer } from "../common/Toast";
import CreateCdsCoverageModal from "../modals/CreateCdsCoverageModal";
import CloseCdsCoverageModal from "../modals/CloseCdsCoverageModal";
import CdsBrandingPanel from "./CdsBrandingPanel";
import CdsDataModule from "./CdsDataModule";

const formatCurrency = (value, decimals = 2) => {
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  return `$${num.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
};

const CDS_R_MAX = 0.75;

function InfoTooltip({ text }) {
  return (
    <span className="relative inline-flex items-center group">
      <button
        type="button"
        className="inline-flex items-center justify-center text-cyan-500 hover:text-cyan-300 focus:text-cyan-300 focus:outline-none"
        aria-label={text}
      >
        <Info size={12} />
      </button>
      <span className="pointer-events-none absolute left-1/2 bottom-full z-50 mb-2 w-64 -translate-x-1/2 border border-cyan-500/30 bg-[#050505] px-3 py-2 text-left text-[10px] leading-relaxed tracking-widest text-gray-300 opacity-0 shadow-2xl shadow-cyan-500/10 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        {text}
      </span>
    </span>
  );
}

export default function CdsMarketPage() {
  const { address } = useParams();
  const routeMarket = String(address || "").toLowerCase();
  const marketKey = routeMarket || "cds";
  const { account, connectWallet } = useWallet();
  const txPauseRef = useRef(false);
  const { toasts, addToast, removeToast } = useToast();
  const [optimisticPositions, setOptimisticPositions] = useState([]);
  const [selectedPosition, setSelectedPosition] = useState(null);
  const [showOpenModal, setShowOpenModal] = useState(false);
  const [showCloseModal, setShowCloseModal] = useState(false);
  const [actionDropdown, setActionDropdown] = useState(null);
  const sim = useSimulation({ marketKey, account });
  const { poolTVL, protocolStats, pool, market, marketInfo, oracleChange24h, chartData } = sim;
  const isLoading = sim.loading;
  const error = !sim.connected && !sim.loading ? "disconnected" : null;

  const latest = { apy: market?.indexPrice || pool?.markPrice || 0 };
  const dailyChange = oracleChange24h?.pctChange || 0;
  const openInterest = (protocolStats?.totalCollateral || 0) + (protocolStats?.totalDebtUsd || 0);
  const collateralSymbol = marketInfo?.collateral?.symbol || "USDC";
  const [walletCollateralBalance, setWalletCollateralBalance] = useState(null);

  const tradeLogic = useTradeLogic(latest.apy);
  const { activeTab, notional, maturityHours, maturityDays } = tradeLogic.state;
  const { setActiveTab, setNotional, handleHoursChange } = tradeLogic.actions;

  const notionalAmount = Number(notional) || 0;
  const projectionData = useWealthProjection(notionalAmount, latest.apy, maturityDays);
  const collateralAddress = marketInfo?.collateral?.address;
  const positionAddress = marketInfo?.position_token?.address;

  const refreshWalletBalance = useCallback(async (force = false) => {
    if (!account || !collateralAddress) return;
    if (!force && txPauseRef.current) return;
    try {
      const token = new ethers.Contract(
        collateralAddress,
        ["function balanceOf(address owner) view returns (uint256)"],
        rpcProvider,
      );
      const raw = await token.balanceOf(account);
      if (!force && txPauseRef.current) return;
      setWalletCollateralBalance(Number(ethers.formatUnits(raw, 6)));
    } catch (e) {
      console.warn("[CDS] failed to fetch wallet collateral balance:", e);
    }
  }, [account, collateralAddress]);

  useEffect(() => {
    if (!account || !collateralAddress) return;
    refreshWalletBalance();
    const id = setInterval(() => refreshWalletBalance(), 10000);
    return () => clearInterval(id);
  }, [account, collateralAddress, refreshWalletBalance]);

  const {
    openCoverage,
    closeCoverage,
    coverageFactory,
    executing: coverageExecuting,
    error: coverageError,
    step: coverageStep,
  } = useCdsCoverageExecution(
    account,
    marketInfo?.infrastructure,
    collateralAddress,
    positionAddress,
    { pauseRef: txPauseRef },
  );

  const currentStep = coverageStep;
  const executionError = coverageError;
  const isExecuting = coverageExecuting;
  const marketReady =
    Boolean(coverageFactory) &&
    Boolean(collateralAddress) &&
    Boolean(positionAddress);

  const currentBorrowRate = (market?.indexPrice || 0) / 100;
  const termYears = maturityHours / 8760;
  const initialBuyCost = CDS_R_MAX > 0
    ? notionalAmount * (currentBorrowRate / CDS_R_MAX)
    : 0;
  const premiumStream = notionalAmount * currentBorrowRate * termYears;
  const totalToPost = initialBuyCost + premiumStream;
  const expectedReclaim = initialBuyCost;
  const reclaimNotice =
    "Not collateral and cannot be liquidated. These funds are used to maintain constant coverage through expiration and are refundable after expiration.";

  const {
    positions: indexedPositions,
    refresh: refreshCoveragePositions,
  } = useCdsCoveragePositions(account, marketKey, isExecuting);

  const allCdsPositions = useMemo(() => {
    const mapped = indexedPositions.map((pos) => ({
      id: pos.brokerAddress || pos.openedTx,
      brokerAddress: pos.brokerAddress,
      coverage: Number(pos.coverage || 0),
      premium: Number(pos.premiumBudget || 0),
      initialCost: Number(pos.initialCost || 0),
      expectedReceive: Number(pos.collateralReturned || pos.initialCost || 0),
      collateralReturned: Number(pos.collateralReturned || 0),
      positionReturned: Number(pos.positionReturned || 0),
      duration: pos.duration
        ? `${Math.round(Number(pos.duration) / 86400)}D`
        : "—",
      status: pos.status || "active",
      openedTx: pos.openedTx,
    }));
    const indexedIds = new Set(mapped.map((pos) => pos.id));
    return [
      ...optimisticPositions.filter((pos) => !indexedIds.has(pos.id)),
      ...mapped,
    ];
  }, [indexedPositions, optimisticPositions]);
  const userCdsPositions = useMemo(
    () => allCdsPositions.filter((pos) => pos.status === "active"),
    [allCdsPositions],
  );
  const handleReviewCoverage = useCallback(() => {
    if (!account) {
      connectWallet();
      return;
    }
    if (!marketReady) {
      addToast({
        type: "error",
        title: "Market Not Ready",
        message: "CDS market configuration is still loading.",
      });
      return;
    }
    if (!Number.isFinite(notionalAmount) || notionalAmount <= 0) {
      addToast({
        type: "error",
        title: "Invalid Coverage",
        message: "Enter a positive coverage amount.",
      });
      return;
    }
    setShowOpenModal(true);
  }, [account, addToast, connectWallet, marketReady, notionalAmount]);

  const handleConfirmOpenProtection = useCallback(async () => {
    txPauseRef.current = true;
    try {
      await openCoverage(notionalAmount, maturityHours, (receipt) => {
        const position = {
          id: receipt.brokerAddress || receipt.hash,
          brokerAddress: receipt.brokerAddress,
          coverage: notionalAmount,
          premium: premiumStream,
          initialCost: expectedReclaim,
          expectedReceive: expectedReclaim,
          duration: maturityHours < 24
            ? `${maturityHours}H`
            : `${Math.round(maturityHours / 24)}D`,
          status: "Active",
        };
        setOptimisticPositions((prev) => [position, ...prev]);
        addToast({
          type: "success",
          title: "CDS Opened",
          message: `${formatCurrency(notionalAmount, 0)} fixed coverage — tx ${receipt.hash.slice(0, 10)}…`,
        });
        setShowOpenModal(false);
        setActiveTab("CLOSE");
        refreshCoveragePositions?.();
        refreshWalletBalance(true);
      });
    } finally {
      txPauseRef.current = false;
    }
  }, [
    addToast,
    expectedReclaim,
    maturityHours,
    notionalAmount,
    openCoverage,
    premiumStream,
    refreshCoveragePositions,
    refreshWalletBalance,
    setActiveTab,
  ]);

  const openCloseModal = useCallback((positionOverride = null) => {
    const targetPosition = positionOverride || selectedPosition;
    if (!account) {
      connectWallet();
      return;
    }
    if (!targetPosition?.brokerAddress) {
      addToast({
        type: "error",
        title: "No Position Selected",
        message: "Select an active CDS position to close.",
      });
      return;
    }
    setSelectedPosition(targetPosition);
    setActionDropdown(null);
    setShowCloseModal(true);
  }, [account, addToast, connectWallet, selectedPosition]);

  const handleConfirmCloseProtection = useCallback(async () => {
    if (!selectedPosition?.brokerAddress) return;
    txPauseRef.current = true;
    try {
      await closeCoverage(selectedPosition.brokerAddress, (receipt) => {
        setOptimisticPositions((prev) => prev.filter((pos) => pos.id !== selectedPosition.id));
        setSelectedPosition(null);
        setActionDropdown(null);
        setShowCloseModal(false);
        addToast({
          type: "success",
          title: "CDS Closed",
          message: `Coverage closed — tx ${receipt.hash.slice(0, 10)}…`,
        });
        refreshCoveragePositions?.();
        refreshWalletBalance(true);
      });
    } finally {
      txPauseRef.current = false;
    }
  }, [addToast, closeCoverage, refreshCoveragePositions, refreshWalletBalance, selectedPosition]);

  const actionLabel = !account
    ? "Connect Wallet"
    : isExecuting
      ? currentStep || (activeTab === "CLOSE" ? "Closing..." : "Opening...")
      : !coverageFactory
        ? "Coverage Factory Not Deployed"
        : activeTab === "CLOSE"
          ? selectedPosition
            ? "Close Selected CDS"
            : "Select CDS Position"
          : "Open Coverage";
  const actionDisabled =
    Boolean(account) &&
    (isExecuting || !marketReady || (activeTab === "CLOSE" ? !selectedPosition : notionalAmount <= 0));

  if (error)
    return (
      <div className="h-screen flex items-center justify-center text-red-600 bg-black font-mono text-xs">
        ERR: API_DISCONNECTED
      </div>
    );
  if (isLoading)
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

              <div className="lg:col-span-4 lg:row-span-2 h-full">
                <CdsBrandingPanel accentSteps={["1", "2"]} />
              </div>

              <div className="lg:col-span-8 h-full">
                <MetricsGrid
                  latest={latest}
                  dailyChange={dailyChange}
                  openInterest={openInterest}
                  liquidity={poolTVL || 0}
                  paramLabel="PAYOUT_TRIGGER"
                  paramItems={[
                    { label: "UTILIZATION", value: ">99% over 7D" },
                    {
                      label: "TRACKS",
                      value: (
                        <span className="inline-flex items-center gap-3">
                          <span>2 of 3</span>
                          <a
                            href="http://localhost:3001/risk/security-model.html#three-oracle-separation"
                            className="inline-flex items-center gap-2 border border-cyan-500/20 hover:border-cyan-500/50 hover:bg-cyan-500/5 transition-all px-3 py-2 text-xs font-bold tracking-widest text-cyan-400"
                          >
                            Learn more →
                          </a>
                        </span>
                      ),
                    }]}
                />
              </div>

              <div className="lg:col-span-8 h-[350px] md:h-[500px]">
                <CdsDataModule
                  collateral={notionalAmount}
                  durationDays={maturityDays}
                  latestApy={latest.apy}
                  projectionData={projectionData}
                  chartData={chartData}
                />
              </div>

            </div>
          </div>

          <TradingTerminal
            account={account}
            connectWallet={connectWallet}
            title="CREDIT_DEFAULT_SWAP"
            Icon={Terminal}
            tabs={[
              { id: "OPEN", label: "NEW CDS", onClick: () => setActiveTab("OPEN"), isActive: activeTab === "OPEN" },
              { id: "CLOSE", label: "ACTIVE", onClick: () => setActiveTab("CLOSE"), isActive: activeTab === "CLOSE" },
            ]}
            actionButton={{
              label: actionLabel,
              onClick: activeTab === "CLOSE" ? () => openCloseModal() : handleReviewCoverage,
              disabled: actionDisabled,
              variant: "cyan",
            }}
          >
            {activeTab === "OPEN" && (
              <>
                <InputGroup
                  label="Coverage"
                  subLabel={`BAL: ${
                    walletCollateralBalance == null
                      ? "—"
                      : walletCollateralBalance.toLocaleString(undefined, { maximumFractionDigits: 2 })
                  } ${collateralSymbol}`}
                  value={notional}
                  onChange={(v) => setNotional(Number(v))}
                  suffix={collateralSymbol}
                />

                <div className="space-y-3">
                  <div className="flex justify-between items-end">
                    <span className="text-sm text-gray-500 uppercase tracking-widest font-bold">
                      Duration
                    </span>
                    <span className="text-sm font-mono font-bold text-cyan-400">
                      {maturityHours < 24
                        ? `${maturityHours}H`
                        : maturityHours % 24 === 0
                          ? `${Math.floor(maturityHours / 24)}D`
                          : `${Math.floor(maturityHours / 24)}D ${maturityHours % 24}H`}
                    </span>
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

                  <div className="flex items-center gap-1.5 pt-1">
                    {[
                      { label: "1H", hours: 1 },
                      { label: "7D", hours: 7 * 24 },
                      { label: "1M", hours: 30 * 24 },
                      { label: "3M", hours: 90 * 24 },
                      { label: "1Y", hours: 365 * 24 },
                    ].map((preset) => {
                      const isActive = maturityHours === preset.hours;
                      return (
                        <button
                          key={preset.label}
                          onClick={() => handleHoursChange(preset.hours)}
                          className={`flex-1 py-1.5 text-sm font-bold font-mono transition-all border ${isActive
                            ? "border-cyan-500/50 bg-cyan-500/10 text-cyan-400"
                            : "border-white/10 bg-transparent text-gray-500 hover:border-white/20 hover:text-white"
                            }`}
                        >
                          {preset.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="space-y-2 pt-2 border-t border-white/5">
                  <SummaryRow
                    label="Premium"
                    value={formatCurrency(premiumStream, 2)}
                  />
                  <SummaryRow
                    label={(
                      <span className="inline-flex items-center gap-1.5">
                        Reclaim after expiration
                        <InfoTooltip text={reclaimNotice} />
                      </span>
                    )}
                    value={formatCurrency(expectedReclaim, 2)}
                    valueColor="text-cyan-400"
                  />
                  <SummaryRow
                    label="Total"
                    value={formatCurrency(totalToPost, 2)}
                    valueColor="text-white"
                  />
                  <div className="text-[10px] text-gray-600 leading-relaxed uppercase tracking-widest pt-1">
                    Premium is the insurance cost. Reclaim amount is used to
                    maintain constant coverage and returns after expiration.
                  </div>
                  {executionError && (
                    <div className="text-xs font-mono text-red-400 border border-red-500/20 bg-red-500/5 p-2">
                      {executionError}
                    </div>
                  )}
                </div>
              </>
            )}

            {activeTab === "CLOSE" && (
              <div className="space-y-4">
                {userCdsPositions.length === 0 ? (
                  <div className="text-sm font-mono text-gray-500 p-4 border border-white/5 bg-white/[0.02] text-center">
                    No active coverage contracts.
                  </div>
                ) : (
                  userCdsPositions.map((pos) => (
                    <button
                      key={pos.id}
                      type="button"
                      onClick={() => setSelectedPosition(pos)}
                      className={`w-full space-y-2 text-left text-sm font-mono border p-4 transition-colors ${
                        selectedPosition?.id === pos.id
                          ? "border-cyan-500/50 bg-cyan-500/10"
                          : "border-white/5 bg-white/[0.02] hover:border-white/20"
                      }`}
                    >
                      <SummaryRow label="Coverage" value={formatCurrency(pos.coverage, 0)} />
                      <SummaryRow label="Premium_Stream" value={formatCurrency(pos.premium, 2)} />
                      <SummaryRow label="Status" value={pos.status} valueColor="text-green-400" />
                    </button>
                  ))
                )}
              </div>
            )}
          </TradingTerminal>
        </div>

        {/* CDS POSITIONS TABLE (aligned with chart) */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
          <div className="xl:col-span-9">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-start-5 lg:col-span-8 border border-white/10 bg-[#080808]">
                <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <h3 className="text-sm font-bold uppercase tracking-widest">
                      Your Contracts
                    </h3>
                    <span className="text-sm text-gray-600 font-mono">
                      {userCdsPositions.length}
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
                  <div className="w-32 text-center">Coverage</div>
                  <div className="w-24 text-center">Premium</div>
                  <div className="w-32 text-center">Duration</div>
                  <div className="w-24 text-center">Status</div>
                  <div className="w-24 text-center">Action</div>
                </div>

                {/* Table Rows */}
                {userCdsPositions.length === 0 ? (
                  <div className="flex items-center justify-center p-8 text-sm font-mono text-gray-500 uppercase tracking-widest">
                    No active coverage contracts
                  </div>
                ) : (
                  userCdsPositions.map((pos) => (
                    <div key={pos.id} className="flex flex-col md:flex-row md:items-center gap-3 md:gap-0 px-6 py-4 transition-colors border-b border-white/5 last:border-b-0 text-sm font-mono hover:bg-white/[0.02]">
                      <div className="w-16 shrink-0 text-gray-500 truncate text-left">
                        #{String(pos.id).slice(2, 8)}
                      </div>
                      <div className="hidden md:block flex-1" />
                      <div className="md:w-32 text-white text-left md:text-center">{formatCurrency(pos.coverage, 0)}</div>
                      <div className="md:w-24 text-white text-left md:text-center">{formatCurrency(pos.premium, 0)}</div>
                      <div className="md:w-32 text-gray-400 text-left md:text-center">{pos.duration}</div>
                      <div className="md:w-24 text-green-400 uppercase text-left md:text-center">{pos.status}</div>
                      <div className="md:w-24 relative flex justify-start md:justify-center">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setActionDropdown(actionDropdown === pos.id ? null : pos.id);
                          }}
                          className="p-1.5 text-gray-600 hover:text-white hover:bg-white/5 transition-colors"
                        >
                          <ChevronDown size={16} className={`transition-transform ${actionDropdown === pos.id ? "rotate-180" : ""}`} />
                        </button>
                        {actionDropdown === pos.id && (
                          <div className="absolute right-0 top-full mt-1 z-50 border border-white/10 bg-[#0a0a0a] backdrop-blur-sm min-w-[150px]">
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                openCloseModal(pos);
                              }}
                              className="w-full text-left px-4 py-2 text-sm text-white hover:bg-white/5 transition-colors font-mono"
                            >
                              Close CDS
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

      </div>
      <CreateCdsCoverageModal
        isOpen={showOpenModal}
        onClose={() => { if (!isExecuting) setShowOpenModal(false); }}
        onConfirm={handleConfirmOpenProtection}
        coverage={notionalAmount}
        durationLabel={
          maturityHours < 24
            ? `${maturityHours}H`
            : maturityHours % 24 === 0
              ? `${Math.floor(maturityHours / 24)}D`
              : `${Math.floor(maturityHours / 24)}D ${maturityHours % 24}H`
        }
        borrowRatePct={currentBorrowRate * 100}
        premium={premiumStream}
        reclaim={expectedReclaim}
        total={totalToPost}
        rMaxPct={CDS_R_MAX * 100}
        executing={isExecuting}
        executionStep={currentStep}
        executionError={executionError}
      />
      <CloseCdsCoverageModal
        isOpen={showCloseModal}
        onClose={() => { if (!isExecuting) setShowCloseModal(false); }}
        onConfirm={handleConfirmCloseProtection}
        position={selectedPosition}
        expectedReceive={
          selectedPosition?.collateralReturned ||
          selectedPosition?.expectedReceive ||
          selectedPosition?.initialCost ||
          ((selectedPosition?.premium || 0) + (selectedPosition?.initialCost || 0))
        }
        executing={isExecuting}
        executionStep={currentStep}
        executionError={executionError}
      />
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
}
