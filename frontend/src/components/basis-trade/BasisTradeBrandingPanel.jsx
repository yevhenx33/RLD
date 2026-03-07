import React from "react";
import { TrendingUp, Layers } from "lucide-react";

const MECHANISM_STEPS = [
  { step: "1", label: "Supply sUSDe" },
  { step: "2", label: "Borrow USDC" },
  { step: "3", label: "Loop sUSDe/USDC" },
  { step: "4", label: "Buy USDC wRLP to fix borrowing cost" },
  { step: "5", label: "Collect boosted basis-trade yield" },
];

export default function BasisTradeBrandingPanel({ accentSteps = ["1", "2", "3", "4", "5"] }) {
  return (
    <div className="w-full min-w-[360px] mx-auto xl:mx-0 border border-white/10 bg-[#080808] p-4 md:px-6 md:pt-6 md:pb-7 flex flex-col">
      {/* Product Identity */}
      <div className="flex justify-between items-center mb-6">
        <span className="text-sm font-bold uppercase tracking-widest text-pink-400 bg-pink-400/10 px-2 py-1">
          Leveraged Carry
        </span>
        <TrendingUp size={20} className="text-pink-400" />
      </div>
      <h3 className="text-lg font-mono text-white mb-2 tracking-tight">
        BASIS_TRADE
      </h3>
      <p className="text-sm text-gray-500 font-mono mb-6 leading-relaxed">
        High-yield carry strategy using sUSDe collateral with built-in rate hedging.
      </p>

      {/* Sample Vault Card */}
      <div className="border border-white/10 bg-[#0a0a0a]">
        <div className="px-4 py-2.5 border-b border-white/10 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-pink-400" />
            <span className="text-sm font-bold uppercase tracking-widest text-white">
              Vault
            </span>
          </div>
          <span className="text-sm text-gray-700 tracking-[0.15em]">#004</span>
        </div>
        <div className="px-4 py-3 flex justify-between items-end">
          <div>
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
              Projected APY
            </div>
            <div className="text-xl font-mono font-light text-pink-400 tracking-tight">
              22.10%
            </div>
          </div>
          <div className="text-right">
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
              Strategy TVL
            </div>
            <div className="text-xl font-mono text-white">
              3.1M USDC
            </div>
          </div>
        </div>
        <div className="px-4 py-2 border-t border-white/5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-pink-400 animate-pulse shadow-[0_0_8px_rgba(244,114,182,0.6)]" />
            <span className="text-sm text-gray-500 uppercase tracking-widest">
              Active
            </span>
          </div>
          <span className="text-sm text-gray-500 font-mono tracking-widest flex items-center gap-2">
            <Layers size={14} className="text-gray-600" /> AAVE V3
          </span>
        </div>
      </div>

      {/* Mechanism Flow */}
      <div className="mt-6">
        <div className="flex items-center justify-between mb-4">
          <span className="text-sm font-bold uppercase tracking-widest text-gray-500">
            How_It_Works
          </span>
          <span className="text-sm text-gray-700 tracking-[0.15em]">::FLOW</span>
        </div>
        <div className="flex flex-col">
          {MECHANISM_STEPS.map((s, i, arr) => {
            const accent = accentSteps.includes(s.step);
            return (
            <div key={i} className="flex items-start gap-2.5">
              <div className="flex flex-col items-center shrink-0">
                <div
                  className={`w-6 h-6 border ${accent ? "border-pink-500/50 bg-pink-500/10" : "border-white/10 bg-[#0a0a0a]"} flex items-center justify-center`}
                >
                  <span
                    className={`text-sm font-bold ${accent ? "text-pink-400" : "text-gray-600"}`}
                  >
                    {s.step}
                  </span>
                </div>
                {i < arr.length - 1 && (
                  <div className="w-px h-6 bg-white/10" />
                )}
              </div>
              <span
                className={`text-[11px] pt-1 ${accent ? "text-pink-400" : "text-gray-500"} uppercase tracking-widest`}
              >
                {s.label}
              </span>
            </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
