import React, { useCallback, useMemo, useRef } from "react";
import { Terminal, Shield } from "lucide-react";
import { useParams } from "react-router-dom";
import { useWallet } from "../../context/WalletContext";
import { useSimulation } from "../../hooks/useSimulation";
import { useTradeLogic } from "../../hooks/useTradeLogic";
import { useWealthProjection } from "../../hooks/useWealthProjection";
import { useBrokerAccount } from "../../hooks/useBrokerAccount";
import { useBrokerData } from "../../hooks/useBrokerData";
import { useSwapExecution } from "../../hooks/useSwapExecution";
import { useSwapQuote } from "../../hooks/useSwapQuote";
import { useToast } from "../../hooks/useToast";
import MetricsGrid from "../pools/MetricsGrid";
import TradingTerminal, { InputGroup, SummaryRow } from "../trading/TradingTerminal";
import { ToastContainer } from "../common/Toast";
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

const formatToken = (value, decimals = 4) => {
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  return num.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
};

const shortAddress = (address) =>
  address ? `${address.slice(0, 6)}…${address.slice(-4)}` : "—";

export default function CdsMarketPage() {
  const { address } = useParams();
  const routeMarket = String(address || "").toLowerCase();
  const marketKey = routeMarket || "cds";
  const { account, connectWallet } = useWallet();
  const txPauseRef = useRef(false);
  const { toasts, addToast, removeToast } = useToast();
  const sim = useSimulation({ marketKey, account });
  const { poolTVL, protocolStats, pool, market, marketInfo, oracleChange24h, chartData } = sim;
  const isLoading = sim.loading;
  const error = !sim.connected && !sim.loading ? "disconnected" : null;

  const latest = { apy: market?.indexPrice || pool?.markPrice || 0 };
  const dailyChange = oracleChange24h?.pctChange || 0;
  const openInterest = (protocolStats?.totalCollateral || 0) + (protocolStats?.totalDebtUsd || 0);
  const collateralSymbol = marketInfo?.collateral?.symbol || "USDC";

  const tradeLogic = useTradeLogic(latest.apy);
  const { activeTab, notional, maturityHours, maturityDays } = tradeLogic.state;
  const { setActiveTab, setNotional, handleHoursChange } = tradeLogic.actions;

  const notionalAmount = Number(notional) || 0;
  const projectionData = useWealthProjection(notionalAmount, latest.apy, maturityDays);
  const collateralAddress = marketInfo?.collateral?.address;
  const positionAddress = marketInfo?.position_token?.address;
  const positionSymbol =
    marketInfo?.collateral?.symbol === "USDC"
      ? "wCDS"
      : marketInfo?.position_token?.symbol || "wCDS";

  const {
    hasBroker,
    brokerAddress,
    brokerBalance,
    creating,
    depositing,
    error: brokerError,
    step: brokerStep,
    createBroker,
    depositFunds,
    checkBroker,
    fetchBrokerBalance,
  } = useBrokerAccount(
    account,
    marketInfo?.broker_factory,
    collateralAddress,
  );

  const {
    data: brokerData,
    refresh: refreshBrokerData,
  } = useBrokerData(
    account,
    marketInfo,
    sim.blockNumber,
    market?.blockTimestamp,
    txPauseRef,
  );

  const { quote: protectionQuote, loading: quoteLoading } = useSwapQuote(
    marketInfo?.infrastructure,
    collateralAddress,
    positionAddress,
    notionalAmount,
    "BUY",
  );

  const {
    executeLong,
    executing: swapExecuting,
    error: swapError,
    step: swapStep,
  } = useSwapExecution(
    account,
    brokerAddress,
    marketInfo?.infrastructure,
    collateralAddress,
    positionAddress,
    { onRefreshComplete: [refreshBrokerData, fetchBrokerBalance] },
  );

  const currentStep = swapStep || brokerStep;
  const executionError = swapError || brokerError;
  const isExecuting = creating || depositing || swapExecuting;
  const marketReady =
    Boolean(marketInfo?.broker_factory) &&
    Boolean(marketInfo?.infrastructure?.broker_router) &&
    Boolean(collateralAddress) &&
    Boolean(positionAddress);

  const estimatedCoverage = protectionQuote?.amountOut || 0;
  const estimatedEntry = protectionQuote?.entryRate || pool?.markPrice || latest.apy || 0;
  const brokerCollateral = Number(brokerData?.brokerBalance ?? brokerBalance ?? 0) || 0;
  const brokerPositionBalance = Number(brokerData?.wrlpTokenBalance || 0) || 0;

  const userCdsPositions = useMemo(() => {
    if (!brokerAddress || brokerPositionBalance <= 0) return [];
    const mark = pool?.markPrice || latest.apy || 0;
    return [{
      id: 1,
      brokerAddress,
      coverage: brokerPositionBalance,
      premium: brokerPositionBalance * mark,
      duration: "Perpetual",
      status: "Active",
    }];
  }, [brokerAddress, brokerPositionBalance, pool?.markPrice, latest.apy]);

  const handleOpenProtection = useCallback(async () => {
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
    if (hasBroker === false) {
      const createdBroker = await createBroker();
      if (createdBroker) {
        addToast({
          type: "success",
          title: "CDS Account Created",
          message: `Broker ${shortAddress(createdBroker)} is ready.`,
        });
        await checkBroker();
      }
      return;
    }
    if (!brokerAddress) {
      addToast({
        type: "info",
        title: "Broker Syncing",
        message: "Wait for your CDS account to finish indexing.",
      });
      await checkBroker();
      return;
    }
    if (!Number.isFinite(notionalAmount) || notionalAmount <= 0) {
      addToast({
        type: "error",
        title: "Invalid Premium",
        message: "Enter a positive USDC premium amount.",
      });
      return;
    }

    txPauseRef.current = true;
    try {
      const depositReceipt = await depositFunds(notionalAmount);
      if (!depositReceipt) {
        return;
      }
      await executeLong(notionalAmount, (receipt) => {
        addToast({
          type: "success",
          title: "CDS Opened",
          message: `${formatCurrency(notionalAmount, 2)} premium swapped into ${positionSymbol} — tx ${receipt.hash.slice(0, 10)}…`,
        });
        setActiveTab("CLOSE");
      });
    } finally {
      txPauseRef.current = false;
      await refreshBrokerData();
      await fetchBrokerBalance();
    }
  }, [
    account,
    addToast,
    brokerAddress,
    checkBroker,
    connectWallet,
    createBroker,
    depositFunds,
    executeLong,
    fetchBrokerBalance,
    hasBroker,
    marketReady,
    notionalAmount,
    positionSymbol,
    refreshBrokerData,
    setActiveTab,
  ]);

  const actionLabel = !account
    ? "Connect Wallet"
    : isExecuting
      ? currentStep || "Opening..."
      : hasBroker === false
        ? "Create CDS Account"
        : hasBroker === null
          ? "Checking CDS Account..."
          : "Open CDS Position";
  const actionDisabled =
    Boolean(account) &&
    (isExecuting || !marketReady || hasBroker === null || notionalAmount <= 0);

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
              onClick: handleOpenProtection,
              disabled: actionDisabled,
              variant: "cyan",
            }}
          >
            {activeTab === "OPEN" && (
              <>
                <InputGroup
                  label="Premium_Budget"
                  subLabel={quoteLoading ? "Quoting..." : "USDC → wCDS"}
                  value={notional}
                  onChange={(v) => setNotional(Number(v))}
                  suffix={collateralSymbol}
                />

                <div className="space-y-3">
                  <div className="flex justify-between items-end">
                    <span className="text-sm text-gray-500 uppercase tracking-widest font-bold">
                      Coverage Duration
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
                    label="Est_Coverage"
                    value={`${formatToken(estimatedCoverage)} ${positionSymbol}`}
                    valueColor="text-cyan-400"
                  />
                  <SummaryRow
                    label="Entry_Mark"
                    value={formatCurrency(estimatedEntry, 4)}
                  />
                  <SummaryRow
                    label="Broker"
                    value={
                      hasBroker === false
                        ? "Create required"
                        : brokerAddress
                          ? shortAddress(brokerAddress)
                          : "Syncing"
                    }
                  />
                  <SummaryRow
                    label="Broker_USDC"
                    value={`${formatToken(brokerCollateral, 2)} ${collateralSymbol}`}
                  />
                  <div className="text-[10px] text-gray-600 leading-relaxed uppercase tracking-widest pt-1">
                    CDS positions are perpetual. The horizon above is a planning
                    input; on-chain coverage decays through the market NF.
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
                    <div key={pos.id} className="space-y-2 text-sm font-mono border border-white/5 bg-white/[0.02] p-4">
                      <SummaryRow label="Coverage" value={`${formatToken(pos.coverage)} ${positionSymbol}`} valueColor="text-cyan-400" />
                      <SummaryRow label="Market_Value" value={formatCurrency(pos.premium, 2)} />
                      <SummaryRow label="Status" value={pos.status} valueColor="text-green-400" />
                    </div>
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
                    <div key={pos.id} className="flex items-center px-6 py-4 hover:bg-white/[0.02] transition-colors border-b border-white/5 last:border-b-0 text-sm font-mono">
                      <div className="w-16 shrink-0 text-gray-500">#{String(pos.id).padStart(4, "0")}</div>
                      <div className="flex-1 text-gray-500">{shortAddress(pos.brokerAddress)}</div>
                      <div className="w-32 text-center text-cyan-400">{formatToken(pos.coverage)} {positionSymbol}</div>
                      <div className="w-24 text-center text-white">{formatCurrency(pos.premium, 0)}</div>
                      <div className="w-32 text-center text-gray-400">{pos.duration}</div>
                      <div className="w-24 text-center text-green-400 uppercase">{pos.status}</div>
                      <div className="w-24 text-center text-gray-600">—</div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

      </div>
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
}
