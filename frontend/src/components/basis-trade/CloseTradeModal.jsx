import React from "react";
import { X, ArrowDown, Loader2, AlertTriangle, Lock } from "lucide-react";
import { formatNum } from "../../utils/helpers";

/**
 * Close Basis Trade confirmation — concise, shows locked rate and PnL.
 */
export default function CloseTradeModal({
  isOpen,
  onClose,
  onConfirm,
  bond,
  executing,
  executionStep,
  executionError,
  susdeYield,
  usdcCost,
}) {
  if (!isOpen || !bond) return null;

  const spread = (susdeYield || 0) - (usdcCost || 0);
  const lev = bond.leverage || 3;
  const lockedRate = spread * lev;
  const leveragedCapital = bond.principal * lev;
  const pnl = leveragedCapital * (spread / 100) * (bond.elapsed / 365);
  const estReturn = bond.principal + pnl;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={!executing ? onClose : undefined}
      />

      <div className="relative w-full max-w-md bg-[#080808] border border-white/10 shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-white/10 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 bg-pink-500 shadow-[0_0_8px_rgba(236,72,153,0.5)]" />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              Close_Position
            </h2>
          </div>
          {!executing && (
            <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
              <X size={18} />
            </button>
          )}
        </div>

        <div className="p-6 flex flex-col gap-0">
          {/* Position */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="flex items-center gap-2 mb-2">
              <Lock size={12} className="text-pink-400" />
              <span className="text-[10px] text-pink-400 uppercase tracking-widest">
                Fixed At {lockedRate >= 0 ? "+" : ""}{formatNum(lockedRate, 2)}%
              </span>
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-light text-white tracking-tight">
                #{String(bond.id).padStart(4, "0")}
              </span>
              <span className="text-xs text-gray-500 uppercase tracking-widest">
                {lev}× · {bond.maturityDays}D
              </span>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex justify-center -my-2 relative z-10">
            <div className="w-8 h-8 flex items-center justify-center bg-[#080808] border border-white/10 text-pink-400">
              <ArrowDown size={14} />
            </div>
          </div>

          {/* Return */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">Estimated Return</div>
            <div className="flex items-baseline justify-between">
              <span className={`text-2xl font-light tracking-tight ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                {formatNum(estReturn, 2)}
              </span>
              <span className="text-xs text-gray-500 uppercase tracking-widest">sUSDe</span>
            </div>
          </div>

          {/* Key details */}
          <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
            {[
              { label: "Capital", value: `${Number(bond.principal).toLocaleString()} sUSDe` },
              { label: "Elapsed", value: `${bond.elapsed} / ${bond.maturityDays} Days` },
              { label: "Est._PnL", value: `${pnl >= 0 ? "+" : ""}${formatNum(pnl, 2)} sUSDe`, color: pnl >= 0 ? "text-green-400" : "text-red-400" },
            ].map((row) => (
              <div key={row.label} className="flex justify-between items-center px-4 py-2.5">
                <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">{row.label}</span>
                <span className={`text-[12px] font-mono ${row.color || "text-gray-300"}`}>{row.value}</span>
              </div>
            ))}
          </div>

          {/* Early exit warning */}
          {bond.elapsed < bond.maturityDays && (
            <div className="mt-4 bg-yellow-900/10 border border-yellow-700/30 p-3 flex gap-2">
              <AlertTriangle size={14} className="text-yellow-600 shrink-0 mt-0.5" />
              <p className="text-[11px] text-gray-400 leading-relaxed font-mono">
                Early exit — TWAMM hedge will be cancelled, remaining wRLP sold at market.
              </p>
            </div>
          )}
        </div>

        {/* Button */}
        <div className="px-6 pb-5">
          <button
            onClick={onConfirm}
            disabled={executing}
            className={`w-full py-4 text-xs font-bold tracking-[0.2em] uppercase transition-all rounded-none flex items-center justify-center gap-2 bg-pink-500 text-black hover:bg-pink-400 ${executing ? "opacity-70 cursor-wait" : ""}`}
          >
            {executing ? (<><Loader2 size={14} className="animate-spin" />{executionStep || "Processing..."}</>) : "Close Position"}
          </button>
        </div>

        {/* Footer */}
        <div className="px-6 pb-5">
          <div className="flex items-center gap-2 text-[10px] font-mono pt-2 border-t border-white/5">
            {executionError ? (
              <span className="text-red-400 flex items-center gap-1"><AlertTriangle size={10} />{executionError}</span>
            ) : executing ? (
              <span className="text-pink-400/60">{executionStep}</span>
            ) : (
              <span className="text-gray-600">REVIEW · Unwinds TWAMM · repays Aave · returns sUSDe</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
