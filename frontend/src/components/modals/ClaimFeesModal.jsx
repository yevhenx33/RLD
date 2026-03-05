import React from "react";
import { X, Droplets, Loader2, AlertTriangle } from "lucide-react";

/**
 * Claim fees modal — matches SwapConfirmModal / AccountModal design system.
 *
 * Shows unclaimed fee breakdown per token and requires confirmation.
 */
export default function ClaimFeesModal({
  isOpen,
  onClose,
  onConfirm,
  position,
  token0,
  token1,
  executing,
  executionStep,
  executionError,
}) {
  if (!isOpen || !position) return null;

  const fee0 = parseFloat((position.feesEarned0 || "0").replace(",", ""));
  const fee1 = parseFloat((position.feesEarned1 || "0").replace(",", ""));
  const totalFees = (fee0 + fee1).toFixed(2);

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
            <div className="w-2 h-2 bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]" />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              Claim_Fees
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

        {/* Position Info */}
        <div className="p-6 flex flex-col gap-0">
          {/* Position ID + Range */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
              Position #{position.id}
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-light text-white tracking-tight">
                {position.priceLower.toFixed(4)} – {position.priceUpper.toFixed(4)}
              </span>
              <span className="text-sm text-gray-500 uppercase tracking-widest">
                Range
              </span>
            </div>
          </div>

          {/* Fee Breakdown */}
          <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                {token0.symbol}_Fees
              </span>
              <span className="text-sm font-mono text-green-400">
                +{position.feesEarned0}
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                {token1.symbol}_Fees
              </span>
              <span className="text-sm font-mono text-green-400">
                +{position.feesEarned1}
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Total_USD
              </span>
              <span className="text-sm font-mono text-white font-bold">
                ${totalFees}
              </span>
            </div>
          </div>

          {/* Gas Estimate */}
          <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Est._Gas
              </span>
              <span className="text-sm font-mono text-gray-400">
                ~$0.42
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Network
              </span>
              <span className="text-sm font-mono text-gray-300">
                Ethereum
              </span>
            </div>
          </div>
        </div>

        {/* Confirm Button */}
        <div className="px-6 pb-5">
          <button
            onClick={onConfirm}
            disabled={executing}
            className={`w-full py-4 text-sm font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none flex items-center justify-center gap-2 bg-green-500 text-black hover:bg-green-400 ${
              executing ? "opacity-70 cursor-wait" : "hover:opacity-90"
            }`}
          >
            {executing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {executionStep || "Processing..."}
              </>
            ) : (
              <>
                <Droplets size={14} />
                Claim Fees
              </>
            )}
          </button>
        </div>

        {/* Footer Status */}
        <div className="px-6 pb-5">
          <div className="flex items-center gap-2 text-sm font-mono pt-2 border-t border-white/5">
            {executionError ? (
              <span className="text-red-400 flex items-center gap-1">
                <AlertTriangle size={10} />
                {executionError}
              </span>
            ) : executing ? (
              <span className="text-green-400/60">{executionStep}</span>
            ) : (
              <span className="text-gray-600">
                REVIEW · Confirm fee claim before submitting
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
