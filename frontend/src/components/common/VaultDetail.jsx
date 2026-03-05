import React, { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  Lock,
  Layers,
  TrendingUp,
  Users,
  Wallet,
  Terminal,
  Info,
  Clock,
  Shield,
  ExternalLink,
} from "lucide-react";
import { useWallet } from "../../context/WalletContext";
import RLDPerformanceChart from "../charts/RLDChart";
import TradingTerminal, { InputGroup, SummaryRow } from "../trading/TradingTerminal";

/**
 * VaultDetail — Individual Fixed Yield vault page
 * Route: /vaults/fixed-yield
 */

// ── VAULT METADATA ────────────────────────────────────────────
const VAULT = {
  id: "001",
  name: "Fixed Yield",
  fullName: "FIXED_YIELD",
  description:
    "Short RLP to lock a fixed borrow rate as yield. TWAMM unwinds the position over the chosen duration, converting volatile Aave supply rates into a fixed return.",
  apy: 8.4,
  tvl: 12_500_000,
  asset: "USDC",
  protocol: "AAVE V3",
  risk: "LOW",
  status: "ACTIVE",
  depositors: 847,
  inception: "2025-09-15",
  feePerformance: 10, // %
  feeManagement: 0.5, // %
  minDeposit: 100,
  maxCapacity: 50_000_000,
  mechanism: [
    "User deposits USDC into the vault",
    "Vault opens a Short RLP position via RLD Core",
    "TWAMM gradually unwinds the short over the chosen maturity",
    "Fixed rate yield accrues to depositors, net of fees",
    "At maturity, principal + yield is claimable",
  ],
};

// ── MOCK HISTORICAL APY DATA (90 days) ────────────────────────
const generateHistoricalData = () => {
  const data = [];
  const now = Math.floor(Date.now() / 1000);
  const baseApy = 8.4;
  const days = 90;

  for (let i = 0; i < days; i++) {
    const timestamp = now - (days - i) * 86400;
    // Slight random walk for realism
    const noise = Math.sin(i * 0.3) * 0.8 + Math.cos(i * 0.17) * 0.4;
    const vaultApy = baseApy + noise;
    const variableApy =
      vaultApy + (Math.sin(i * 0.5) * 2.5 + Math.random() * 1.5);
    data.push({
      timestamp,
      vaultApy: Math.max(0, parseFloat(vaultApy.toFixed(2))),
      variableApy: Math.max(0, parseFloat(variableApy.toFixed(2))),
    });
  }
  return data;
};

const CHART_DATA = generateHistoricalData();

const CHART_AREAS = [
  { key: "vaultApy", name: "Vault Fixed APY", color: "#22d3ee" },
  { key: "variableApy", name: "Aave Variable APY", color: "#ef4444" },
];

// ── MOCK RECENT ACTIVITY ──────────────────────────────────────
const ACTIVITY = [
  { type: "DEPOSIT", address: "0x1a2b...3c4d", amount: 50000, time: "2h ago" },
  { type: "DEPOSIT", address: "0x5e6f...7a8b", amount: 25000, time: "5h ago" },
  { type: "WITHDRAW", address: "0x9c0d...1e2f", amount: 10000, time: "8h ago" },
  {
    type: "DEPOSIT",
    address: "0x3a4b...5c6d",
    amount: 100000,
    time: "12h ago",
  },
  { type: "DEPOSIT", address: "0x7e8f...9a0b", amount: 15000, time: "1d ago" },
  { type: "WITHDRAW", address: "0xbc1d...2e3f", amount: 30000, time: "1d ago" },
  { type: "DEPOSIT", address: "0x4f5a...6b7c", amount: 75000, time: "2d ago" },
  { type: "DEPOSIT", address: "0x8d9e...0f1a", amount: 20000, time: "3d ago" },
];

function formatTVL(val) {
  if (val >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(0)}K`;
  return `$${val}`;
}

// ── MAIN COMPONENT ────────────────────────────────────────────
export default function VaultDetail() {
  const { account, connectWallet, usdcBalance } = useWallet();
  const [activeTab, setActiveTab] = useState("DEPOSIT");
  const [amount, setAmount] = useState(10000);

  // Estimated yield calculation
  const estimatedYield = useMemo(() => {
    const days = 365;
    return amount * (VAULT.apy / 100) * (days / 365);
  }, [amount]);

  const netYield = useMemo(() => {
    return estimatedYield * (1 - VAULT.feePerformance / 100);
  }, [estimatedYield]);

  const capacityUsed = (VAULT.tvl / VAULT.maxCapacity) * 100;

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        {/* ── BREADCRUMB ── */}
        <div className="flex items-center gap-3 py-4">
          <Link
            to="/vaults"
            className="flex items-center gap-2 text-gray-500 hover:text-white transition-colors text-[11px] uppercase tracking-widest"
          >
            <ArrowLeft size={12} />
            Vaults
          </Link>
          <span className="text-white/10">/</span>
          <span className="text-[11px] text-white uppercase tracking-widest font-bold">
            Fixed Yield
          </span>
        </div>

        {/* ── MAIN GRID ── */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          {/* LEFT PANEL — 9 cols */}
          <div className="xl:col-span-9 flex flex-col gap-6">
            {/* ── VAULT INFO CARD ── */}
            <div className="border border-white/10 bg-[#080808]">
              <div className="px-5 py-3 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
                  <div className="w-1.5 h-1.5 bg-cyan-400" />
                  {VAULT.fullName}
                </span>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1.5">
                    <div className="w-1.5 h-1.5 bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]" />
                    <span className="text-[10px] text-green-400 uppercase tracking-[0.2em]">
                      {VAULT.status}
                    </span>
                  </div>
                  <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                    ::{VAULT.id}
                  </span>
                </div>
              </div>
              <div className="px-5 py-4">
                <div className="flex flex-col md:flex-row md:items-start gap-6">
                  {/* Description */}
                  <div className="flex-1">
                    <p className="text-[12px] text-gray-400 leading-relaxed mb-4">
                      {VAULT.description}
                    </p>
                    <div className="flex flex-wrap gap-4">
                      {[
                        { label: "Asset", value: VAULT.asset },
                        { label: "Protocol", value: VAULT.protocol },
                        {
                          label: "Risk",
                          value: VAULT.risk,
                          color: "text-green-400",
                        },
                        {
                          label: "Since",
                          value: new Date(VAULT.inception).toLocaleDateString(
                            "en-US",
                            { month: "short", year: "numeric" },
                          ),
                        },
                      ].map((item) => (
                        <div key={item.label}>
                          <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-0.5">
                            {item.label}
                          </div>
                          <div
                            className={`text-[11px] uppercase tracking-widest font-bold ${item.color || "text-white"}`}
                          >
                            {item.value}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Primary APY */}
                  <div className="text-right shrink-0">
                    <div className="text-[9px] text-gray-500 uppercase tracking-widest mb-1">
                      Fixed APY
                    </div>
                    <div className="text-4xl text-cyan-400 font-mono font-light tracking-tight">
                      {VAULT.apy}%
                    </div>
                    <div className="text-[10px] text-gray-600 uppercase tracking-widest mt-1">
                      Net of fees
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* ── METRICS GRID ── */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                {
                  label: "Total TVL",
                  value: formatTVL(VAULT.tvl),
                  icon: Layers,
                  accent: "text-white",
                },
                {
                  label: "Fixed APY",
                  value: `${VAULT.apy}%`,
                  icon: TrendingUp,
                  accent: "text-cyan-400",
                },
                {
                  label: "Depositors",
                  value: VAULT.depositors.toLocaleString(),
                  icon: Users,
                  accent: "text-white",
                },
                {
                  label: "Capacity",
                  value: `${capacityUsed.toFixed(0)}%`,
                  icon: Shield,
                  accent:
                    capacityUsed > 80 ? "text-yellow-400" : "text-green-400",
                  sub: `${formatTVL(VAULT.tvl)} / ${formatTVL(VAULT.maxCapacity)}`,
                },
              ].map((m) => (
                <div
                  key={m.label}
                  className="p-5 border border-white/10 bg-[#080808] flex flex-col justify-between"
                >
                  <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-3 flex items-center justify-between">
                    {m.label}
                    <m.icon size={14} className="text-gray-600" />
                  </div>
                  <div
                    className={`text-2xl font-light tracking-tight ${m.accent}`}
                  >
                    {m.value}
                  </div>
                  {m.sub && (
                    <div className="text-[9px] text-gray-600 uppercase tracking-widest mt-1">
                      {m.sub}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* ── PERFORMANCE CHART ── */}
            <div className="border border-white/10 bg-[#080808]">
              <div className="px-5 py-3 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
                  <TrendingUp size={12} className="text-gray-500" />
                  APY_History
                </span>
                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5">
                      <div className="w-2 h-2 bg-cyan-400" />
                      <span className="text-[10px] uppercase tracking-widest text-gray-500">
                        Vault
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <div className="w-2 h-2 bg-red-500" />
                      <span className="text-[10px] uppercase tracking-widest text-gray-500">
                        Variable
                      </span>
                    </div>
                  </div>
                  <span className="text-[9px] text-gray-600 tracking-[0.15em]">
                    90D
                  </span>
                </div>
              </div>
              <div className="h-[350px] md:h-[400px] p-3">
                <RLDPerformanceChart
                  data={CHART_DATA}
                  resolution="1D"
                  areas={CHART_AREAS}
                />
              </div>
            </div>

            {/* ── HOW IT WORKS ── */}
            <div className="border border-white/10 bg-[#080808]">
              <div className="px-5 py-3 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
                  <Info size={12} className="text-gray-500" />
                  Mechanism
                </span>
                <span className="text-[9px] text-gray-700 tracking-[0.15em]">
                  ::FLOW
                </span>
              </div>
              <div className="px-5 py-4 space-y-0">
                {VAULT.mechanism.map((step, i) => (
                  <div key={i} className="flex items-start gap-3 group">
                    <div className="flex flex-col items-center shrink-0">
                      <div
                        className={`w-5 h-5 border ${i === 0 ? "border-cyan-500/50 bg-cyan-500/10" : "border-white/10 bg-[#0a0a0a]"} flex items-center justify-center`}
                      >
                        <span
                          className={`text-[9px] font-bold ${i === 0 ? "text-cyan-400" : "text-gray-600"}`}
                        >
                          {i + 1}
                        </span>
                      </div>
                      {i < VAULT.mechanism.length - 1 && (
                        <div className="w-px h-6 bg-white/10" />
                      )}
                    </div>
                    <div className="pt-0.5 pb-4">
                      <span className="text-[11px] text-gray-400 leading-relaxed">
                        {step}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* ── RECENT ACTIVITY TABLE ── */}
            <div className="border border-white/10 bg-[#080808]">
              <div className="px-5 py-3 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-white flex items-center gap-2">
                  <Clock size={12} className="text-gray-500" />
                  Recent_Activity
                </span>
                <span className="text-[9px] text-gray-600 uppercase tracking-[0.2em]">
                  {ACTIVITY.length} events
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="border-b border-white/10">
                    <tr>
                      <th className="py-3 px-5 text-[10px] font-bold uppercase tracking-widest text-gray-500 text-left">
                        Type
                      </th>
                      <th className="py-3 px-5 text-[10px] font-bold uppercase tracking-widest text-gray-500 text-left">
                        Address
                      </th>
                      <th className="py-3 px-5 text-[10px] font-bold uppercase tracking-widest text-gray-500 text-right">
                        Amount
                      </th>
                      <th className="py-3 px-5 text-[10px] font-bold uppercase tracking-widest text-gray-500 text-right">
                        Time
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {ACTIVITY.map((evt, i) => (
                      <tr
                        key={i}
                        className="hover:bg-white/[0.02] transition-colors"
                      >
                        <td className="py-3 px-5">
                          <div className="flex items-center gap-2">
                            <div
                              className={`w-1.5 h-1.5 ${evt.type === "DEPOSIT" ? "bg-green-500" : "bg-red-500"}`}
                            />
                            <span
                              className={`text-[11px] font-bold uppercase tracking-widest ${evt.type === "DEPOSIT" ? "text-green-400" : "text-red-400"}`}
                            >
                              {evt.type}
                            </span>
                          </div>
                        </td>
                        <td className="py-3 px-5">
                          <span className="text-[11px] text-gray-500 font-mono">
                            {evt.address}
                          </span>
                        </td>
                        <td className="py-3 px-5 text-right">
                          <span className="text-[12px] text-white font-mono">
                            {evt.amount.toLocaleString()} USDC
                          </span>
                        </td>
                        <td className="py-3 px-5 text-right">
                          <span className="text-[11px] text-gray-600 font-mono">
                            {evt.time}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="px-5 py-2.5 border-t border-white/10 bg-[#0a0a0a] flex items-center justify-between">
                <span className="text-[9px] text-gray-600 uppercase tracking-[0.2em]">
                  Most recent
                </span>
                <a
                  href="#"
                  className="text-[9px] text-gray-500 hover:text-white uppercase tracking-[0.2em] transition-colors flex items-center gap-1"
                >
                  View All <ExternalLink size={8} />
                </a>
              </div>
            </div>
          </div>

          {/* RIGHT PANEL — 3 cols — Trading Terminal */}
          <TradingTerminal
            account={account}
            connectWallet={connectWallet}
            title={VAULT.fullName}
            subTitle="VAULT"
            Icon={Lock}
            tabs={[
              {
                id: "DEPOSIT",
                label: "DEPOSIT",
                onClick: () => setActiveTab("DEPOSIT"),
                isActive: activeTab === "DEPOSIT",
              },
              {
                id: "WITHDRAW",
                label: "WITHDRAW",
                onClick: () => setActiveTab("WITHDRAW"),
                isActive: activeTab === "WITHDRAW",
              },
            ]}
            actionButton={{
              label: account ? `${activeTab}` : "CONNECT WALLET",
              onClick: account ? () => {} : connectWallet,
              variant: "cyan",
            }}
            footer={
              <div className="px-4 py-3 border-t border-white/10 bg-[#0a0a0a] space-y-2">
                <div className="flex justify-between text-[10px] text-gray-600 uppercase tracking-widest">
                  <span>Performance Fee</span>
                  <span className="text-gray-400">{VAULT.feePerformance}%</span>
                </div>
                <div className="flex justify-between text-[10px] text-gray-600 uppercase tracking-widest">
                  <span>Management Fee</span>
                  <span className="text-gray-400">{VAULT.feeManagement}%</span>
                </div>
                <div className="flex justify-between text-[10px] text-gray-600 uppercase tracking-widest">
                  <span>Min Deposit</span>
                  <span className="text-gray-400">{VAULT.minDeposit} USDC</span>
                </div>
              </div>
            }
          >
            {activeTab === "DEPOSIT" && (
              <>
                <InputGroup
                  label="Deposit_Amount"
                  subLabel={`Bal: ${account ? parseFloat(usdcBalance).toFixed(2) : "--"} USDC`}
                  value={amount}
                  onChange={(v) => setAmount(Number(v))}
                  suffix="USDC"
                  onMax={
                    account
                      ? () => setAmount(parseFloat(usdcBalance))
                      : undefined
                  }
                />

                {/* Yield Estimate */}
                <div className="border border-white/10 p-4 space-y-3 bg-white/[0.02]">
                  <SummaryRow
                    label="Fixed_APY"
                    value={`${VAULT.apy}%`}
                    valueColor="text-cyan-400"
                  />
                  <SummaryRow
                    label="Est._Annual_Yield"
                    value={`${netYield.toLocaleString(undefined, { maximumFractionDigits: 2 })} USDC`}
                    valueColor="text-green-400"
                  />
                  <SummaryRow label="Protocol" value={VAULT.protocol} />
                  <SummaryRow
                    label="Risk_Tier"
                    value={VAULT.risk}
                    valueColor="text-green-400"
                  />
                </div>

                {/* Capacity bar */}
                <div className="space-y-2">
                  <div className="flex justify-between text-[10px] text-gray-500 uppercase tracking-widest">
                    <span>Vault Capacity</span>
                    <span>{capacityUsed.toFixed(0)}%</span>
                  </div>
                  <div className="w-full h-1 bg-white/5">
                    <div
                      className="h-full bg-cyan-400 transition-all"
                      style={{ width: `${Math.min(100, capacityUsed)}%` }}
                    />
                  </div>
                  <div className="flex justify-between text-[9px] text-gray-700 uppercase tracking-widest">
                    <span>{formatTVL(VAULT.tvl)}</span>
                    <span>{formatTVL(VAULT.maxCapacity)}</span>
                  </div>
                </div>
              </>
            )}

            {activeTab === "WITHDRAW" && (
              <>
                <InputGroup
                  label="Withdraw_Amount"
                  subLabel="Your Position: 0.00 USDC"
                  value={amount}
                  onChange={(v) => setAmount(Number(v))}
                  suffix="USDC"
                />

                <div className="border border-white/10 p-4 space-y-3 bg-white/[0.02]">
                  <SummaryRow label="Position_Value" value="0.00 USDC" />
                  <SummaryRow
                    label="Accrued_Yield"
                    value="0.00 USDC"
                    valueColor="text-green-400"
                  />
                  <SummaryRow label="Time_in_Vault" value="--" />
                </div>

                {/* Notice */}
                <div className="bg-cyan-900/10 border border-cyan-700/30 p-4 flex gap-3">
                  <Info size={16} className="text-cyan-500 shrink-0 mt-0.5" />
                  <div>
                    <div className="text-[11px] text-cyan-400 font-bold uppercase tracking-widest mb-2">
                      Withdrawal Info
                    </div>
                    <p className="text-[11px] text-gray-400 leading-relaxed font-mono">
                      Withdrawals are processed within the next epoch (≤24h).
                      Early withdrawals during an active TWAMM position may
                      result in reduced yield.
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
