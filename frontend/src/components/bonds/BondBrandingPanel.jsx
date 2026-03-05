import React from "react";
import { Shield } from "lucide-react";

const MECHANISM_STEPS = [
  { step: "1", label: "Choose Asset" },
  { step: "2", label: "Select Maturity" },
  { step: "3", label: "Deposit Funds" },
  { step: "4", label: "Collect Fixed Yield" },
];

export default function BondBrandingPanel({ accentSteps = ["1", "2", "3", "4"] }) {
  return (
    <div className="border border-white/10 bg-[#080808] p-4 md:px-6 md:pt-6 md:pb-7 flex flex-col">
      {/* Product Identity */}
      <div className="flex justify-between items-center mb-6">
        <span className="text-sm font-bold uppercase tracking-widest text-cyan-400 bg-cyan-400/10 px-2 py-1">
          Synthetic Bond
        </span>
        <Shield size={20} className="text-cyan-400" />
      </div>
      <h3 className="text-lg font-mono text-white mb-2 tracking-tight">
        FIXED_YIELD
      </h3>
      <p className="text-sm text-gray-500 font-mono mb-6 leading-relaxed">
        Fix your yield for any custom duration to protect against market volatility.
      </p>

      {/* Sample Bond Card */}
      <div className="border border-white/10 bg-[#0a0a0a]">
        <div className="px-4 py-2.5 border-b border-white/10 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-cyan-400" />
            <span className="text-sm font-bold uppercase tracking-widest text-white">
              Bond
            </span>
          </div>
          <span className="text-sm text-gray-700 tracking-[0.15em]">#0042</span>
        </div>
        <div className="px-4 py-3 flex justify-between items-end">
          <div>
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
              Fixed APY
            </div>
            <div className="text-xl font-mono font-light text-cyan-400 tracking-tight">
              8.40%
            </div>
          </div>
          <div className="text-right">
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
              Principal
            </div>
            <div className="text-xl font-mono text-white">
              25,000 USDC
            </div>
          </div>
        </div>
        <div className="px-4 py-2 border-t border-white/5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-cyan-400" />
            <span className="text-sm text-gray-500 uppercase tracking-widest">
              Active
            </span>
          </div>
          <span className="text-sm text-gray-500 font-mono tracking-widest">
            453 Days
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
                  className={`w-6 h-6 border ${accent ? "border-cyan-500/50 bg-cyan-500/10" : "border-white/10 bg-[#0a0a0a]"} flex items-center justify-center`}
                >
                  <span
                    className={`text-sm font-bold ${accent ? "text-cyan-400" : "text-gray-600"}`}
                  >
                    {s.step}
                  </span>
                </div>
                {i < arr.length - 1 && (
                  <div className="w-px h-6 bg-white/10" />
                )}
              </div>
              <span
                className={`text-sm pt-0.5 ${accent ? "text-cyan-400" : "text-gray-500"} uppercase tracking-widest`}
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
