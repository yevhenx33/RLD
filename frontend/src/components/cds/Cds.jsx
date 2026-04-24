import React from "react";
import { Terminal, Shield } from "lucide-react";
import { useWallet } from "../../context/WalletContext";
import { useSim } from "../../context/SimulationContext";
import { useTradeLogic } from "../../hooks/useTradeLogic";
import { useWealthProjection } from "../../hooks/useWealthProjection";
import MetricsGrid from "../pools/MetricsGrid";
import TradingTerminal, { InputGroup, SummaryRow } from "../trading/TradingTerminal";
import CdsBrandingPanel from "./CdsBrandingPanel";
import CdsDataModule from "./CdsDataModule";

export default function CdsMarketPage() {
  const { account, connectWallet } = useWallet();
  const sim = useSim();
  const { poolTVL, protocolStats, pool, oracleChange24h } = sim;
  const isLoading = sim.loading;
  const error = !sim.connected && !sim.loading ? "disconnected" : null;

  const latest = { apy: pool?.markPrice || 0 };
  const dailyChange = oracleChange24h?.pctChange || 0;
  const openInterest = (protocolStats?.totalCollateral || 0) + (protocolStats?.totalDebtUsd || 0);

  const tradeLogic = useTradeLogic(latest.apy);
  const { activeTab, notional, maturityHours, maturityDays } = tradeLogic.state;
  const { setActiveTab, setNotional, handleHoursChange } = tradeLogic.actions;

  const notionalAmount = Number(notional) || 0;
  const projectionData = useWealthProjection(notionalAmount, latest.apy, maturityDays);

  // Mock data for CDS positions until backend is wired
  const userCdsPositions = [];

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
                    { label: "UTILIZATION", value: ">99% over 3D" },
                    { label: "COLLATERAL PRICE", value: "-75% over 3D" }]}
                />
              </div>

              <div className="lg:col-span-8 h-[350px] md:h-[500px]">
                <CdsDataModule
                  collateral={notionalAmount}
                  durationDays={maturityDays}
                  latestApy={latest.apy}
                  projectionData={projectionData}
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
              label: !account ? "Connect Wallet" : "Create Protection (Under Construction)",
              onClick: !account ? connectWallet : () => { },
              disabled: account ? true : false,
              variant: "cyan",
            }}
          >
            {activeTab === "OPEN" && (
              <>
                <InputGroup
                  label="Coverage_Amount"
                  value={notional}
                  onChange={(v) => setNotional(Number(v))}
                  suffix="USDC"
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
              </>
            )}

            {activeTab === "CLOSE" && (
              <div className="space-y-4">
                <div className="text-sm font-mono text-gray-500 p-4 border border-white/5 bg-white/[0.02] text-center">
                  No active coverage contracts.
                </div>
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
                    <div key={pos.id} className="flex items-center px-6 py-4 hover:bg-white/[0.02] transition-colors border-b border-white/5 last:border-b-0">
                      {/* Placeholder for future rows */}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
