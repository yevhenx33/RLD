import React from "react";
import { X, ArrowDown, Loader2, AlertTriangle } from "lucide-react";
import { formatNum } from "../../utils/helpers";

/**
 * Create Bond confirmation modal — consistent with SwapConfirmModal design.
 *
 * Shows bond summary and requires explicit confirmation before executing.
 */
export default function CreateBondModal({
  isOpen,
  onClose,
  onConfirm,
  notional,
  maturityDays,
  maturityDate,
  entryRate,

  initialLTV = 0,
  executing,
  executionStep,
  executionError,
}) {
  if (!isOpen) return null;

  const estimatedYield = notional * (entryRate / 100) * (maturityDays / 365);

  const rows = [
    {
      label: "Principal",
      value: `${Number(notional).toLocaleString()} USDC`,
    },
    {
      label: "Fixed_Rate",
      value: `${formatNum(entryRate)}%`,
      color: "text-cyan-400",
    },
    {
      label: "Maturity",
      value: `${maturityDays} Days`,
    },
    {
      label: "Maturity_Date",
      value: maturityDate,
    },

    {
      label: "Initial_LTV",
      value: `${formatNum(initialLTV, 1)}%`,
    },
    {
      label: "Est._Yield",
      value: `${formatNum(estimatedYield, 2)} USDC`,
      color: "text-green-400",
    },
  ];

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={!executing ? onClose : undefined}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md bg-[#080808] border border-white/10 shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-white/10 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.5)]" />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              Confirm_Bond
            </h2>
          </div>
          {!executing && (
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-white transition-colors rounded-none"
            >
              <X size={18} />
            </button>
          )}
        </div>

        {/* Bond Summary */}
        <div className="p-6 flex flex-col gap-0">
          {/* Principal → Bond */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
              You Deposit
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-light text-white tracking-tight">
                {Number(notional).toLocaleString()}
              </span>
              <span className="text-xs text-gray-500 uppercase tracking-widest">
                USDC
              </span>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex justify-center -my-2 relative z-10">
            <div className="w-8 h-8 flex items-center justify-center bg-[#080808] border border-white/10 text-cyan-400">
              <ArrowDown size={14} />
            </div>
          </div>

          {/* Receive Bond */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
              You Receive
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-light tracking-tight text-cyan-400">
                {formatNum(entryRate)}% Fixed
              </span>
              <span className="text-xs text-gray-500 uppercase tracking-widest">
                {maturityDays}D Bond
              </span>
            </div>
          </div>

          {/* Detail rows */}
          <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
            {rows.map((row) => (
              <div
                key={row.label}
                className="flex justify-between items-center px-4 py-2.5"
              >
                <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                  {row.label}
                </span>
                <span
                  className={`text-[12px] font-mono ${
                    row.color || "text-gray-300"
                  }`}
                >
                  {row.value}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Confirm Button */}
        <div className="px-6 pb-5">
          <button
            onClick={onConfirm}
            disabled={executing}
            className={`w-full py-4 text-xs font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none flex items-center justify-center gap-2 bg-cyan-500 text-black hover:bg-cyan-400 ${
              executing ? "opacity-70 cursor-wait" : "hover:opacity-90"
            }`}
          >
            {executing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {executionStep || "Processing..."}
              </>
            ) : (
              "Create Bond"
            )}
          </button>
        </div>

        {/* Footer Status */}
        <div className="px-6 pb-5">
          <div className="flex items-center gap-2 text-[10px] font-mono pt-2 border-t border-white/5">
            {executionError ? (
              <span className="text-red-400 flex items-center gap-1">
                <AlertTriangle size={10} />
                {executionError}
              </span>
            ) : executing ? (
              <span className="text-cyan-400/60">{executionStep}</span>
            ) : (
              <span className="text-gray-600">
                REVIEW · Confirm details before creating bond
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
