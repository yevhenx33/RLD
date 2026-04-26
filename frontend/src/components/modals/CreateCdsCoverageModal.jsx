import React from "react";
import { X, Shield, Loader2 } from "lucide-react";

const formatCurrency = (value, decimals = 2) => {
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  return `$${num.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
};

function Row({ label, value, valueClassName = "text-white" }) {
  return (
    <div className="flex justify-between items-center text-sm font-mono">
      <span className="text-gray-500 uppercase tracking-widest">{label}</span>
      <span className={valueClassName}>{value}</span>
    </div>
  );
}

export default function CreateCdsCoverageModal({
  isOpen,
  onClose,
  onConfirm,
  coverage,
  durationLabel,
  borrowRatePct,
  premium,
  reclaim,
  total,
  rMaxPct,
  executing,
  executionStep,
  executionError,
}) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-md border border-white/10 bg-[#080808] shadow-2xl font-mono">
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10">
          <div className="flex items-center gap-2">
            <Shield size={15} className="text-cyan-500" />
            <h3 className="text-sm font-bold uppercase tracking-widest text-white">
              Open CDS Coverage
            </h3>
          </div>
          <button
            onClick={onClose}
            disabled={executing}
            className="text-gray-600 hover:text-white transition-colors disabled:opacity-40"
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-5 space-y-5">
          <div className="space-y-3 border border-white/5 bg-white/[0.02] p-4">
            <Row label="Coverage" value={formatCurrency(coverage, 0)} valueClassName="text-cyan-400" />
            <Row label="Duration" value={durationLabel} />
            <Row label="Borrow Rate" value={`${Number(borrowRatePct || 0).toFixed(2)}%`} />
            <Row label="r_max Snapshot" value={`${Number(rMaxPct || 0).toFixed(0)}%`} />
          </div>

          <div className="space-y-3 border border-white/5 bg-white/[0.02] p-4">
            <Row label="Premium" value={formatCurrency(premium, 2)} />
            <Row label="Reclaim after expiration" value={formatCurrency(reclaim, 2)} valueClassName="text-cyan-400" />
            <div className="h-px bg-white/10" />
            <Row label="Total" value={formatCurrency(total, 2)} valueClassName="text-white font-bold" />
          </div>

          <div className="text-[10px] leading-relaxed tracking-widest text-gray-500 uppercase">
            Reclaim funds are not collateral and cannot be liquidated. They are
            used to maintain constant coverage through expiration and are
            refundable after expiration.
          </div>

          {executionError && (
            <div className="border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
              {executionError}
            </div>
          )}

          {executing && executionStep && (
            <div className="border border-cyan-500/20 bg-cyan-500/5 p-3 text-xs text-cyan-400">
              {executionStep}
            </div>
          )}
        </div>

        <div className="p-5 pt-0">
          <button
            onClick={onConfirm}
            disabled={executing}
            className="w-full py-4 text-sm font-bold tracking-[0.2em] uppercase transition-all bg-cyan-500 text-black hover:bg-cyan-400 disabled:opacity-50 disabled:cursor-wait"
          >
            {executing ? (
              <span className="inline-flex items-center justify-center gap-2">
                <Loader2 size={14} className="animate-spin" />
                {executionStep || "Opening..."}
              </span>
            ) : (
              "Confirm Coverage"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
