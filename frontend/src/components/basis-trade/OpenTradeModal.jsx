import React from "react";
import { X, ArrowRight, Loader2, AlertTriangle, Lock } from "lucide-react";
import { formatNum } from "../../utils/helpers";

/**
 * Open Basis Trade confirmation — concise, hero the locked rate.
 */
export default function OpenTradeModal({
  isOpen,
  onClose,
  onConfirm,
  capital,
  leverage,
  duration,
  susdeYield = 0,
  usdcCost = 0,
  hedge = 0,
  selectedToken = "USDC",
  executing,
  executionStep,
  executionError,
}) {
  if (!isOpen) return null;

  const lev = Number(leverage) || 1;
  const days = Number(duration) || 365;
  const cap = Number(capital) || 0;
  // The basis trade locks the borrow rate at the current wRLP market price
  const lockedBorrowRate = usdcCost || 0;
  // Net effective rate = sUSDe yield earned on leveraged capital minus locked borrow cost on debt
  const collateral = cap * lev;
  const debt = cap * (lev - 1);
  const grossYield = collateral * (susdeYield / 100) * (days / 365);
  const borrowCost = debt * (lockedBorrowRate / 100) * (days / 365);
  const estReturn = grossYield - borrowCost;

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
              Open_Position
            </h2>
          </div>
          {!executing && (
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-white transition-colors"
            >
              <X size={18} />
            </button>
          )}
        </div>

        <div className="p-6 flex flex-col gap-0">
          {/* Deposit */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
              You Deposit
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-light text-white tracking-tight">
                {cap.toLocaleString()}
              </span>
              <span className="text-xs text-gray-500 uppercase tracking-widest">
                {selectedToken}
              </span>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex justify-center -my-2 relative z-10">
            <div className="w-8 h-8 flex items-center justify-center bg-[#080808] border border-white/10 text-pink-400">
              <ArrowRight size={14} />
            </div>
          </div>

          {/* Hero: Locked Borrow Rate (wRLP market price) */}
          <div className="border border-pink-500/30 bg-pink-500/[0.04] p-5">
            <div className="flex items-center gap-2 mb-2">
              <Lock size={12} className="text-pink-400" />
              <span className="text-[10px] text-pink-400 uppercase tracking-widest">
                Borrow Cost Fixed At
              </span>
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-4xl font-light tracking-tight text-pink-400">
                {formatNum(lockedBorrowRate, 2)}%
              </span>
              <span className="text-xs text-gray-500 uppercase tracking-widest">
                {days}D · {lev}×
              </span>
            </div>
          </div>

          {/* Key details */}
          <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
            {[
              {
                label: "Yield_sUSDe",
                value: `${formatNum(susdeYield, 2)}%`,
                color: "text-green-400",
              },
              { label: "Leverage", value: `${lev}×`, color: "text-pink-400" },
              { label: "Duration", value: `${days} Days` },
              {
                label: "Rate_Hedge",
                value: `${Math.ceil(hedge).toLocaleString()} PYUSD`,
                color: "text-purple-400",
              },
              {
                label: "Est._Return",
                value: `${estReturn >= 0 ? "+" : ""}${formatNum(estReturn, 2)} ${selectedToken}`,
                color: estReturn >= 0 ? "text-green-400" : "text-red-400",
              },
            ].map((row) => (
              <div
                key={row.label}
                className="flex justify-between items-center px-4 py-2.5"
              >
                <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                  {row.label}
                </span>
                <span
                  className={`text-[12px] font-mono ${row.color || "text-gray-300"}`}
                >
                  {row.value}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Button */}
        <div className="px-6 pb-5">
          <button
            onClick={onConfirm}
            disabled={executing}
            className={`w-full py-4 text-xs font-bold tracking-[0.2em] uppercase transition-all rounded-none flex items-center justify-center gap-2 bg-pink-500 text-black hover:bg-pink-400 ${executing ? "opacity-70 cursor-wait" : ""}`}
          >
            {executing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {executionStep || "Processing..."}
              </>
            ) : (
              "Open Position"
            )}
          </button>
        </div>

        {/* Footer */}
        <div className="px-6 pb-5">
          <div className="flex items-center gap-2 text-[10px] font-mono pt-2 border-t border-white/5">
            {executionError ? (
              <span className="text-red-400 flex items-center gap-1">
                <AlertTriangle size={10} />
                {executionError}
              </span>
            ) : executing ? (
              <span className="text-pink-400/60">{executionStep}</span>
            ) : (
              <span className="text-gray-600">
                REVIEW · USDC → Morpho flash → sUSDe collateral · PYUSD hedge
                via TWAMM
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
