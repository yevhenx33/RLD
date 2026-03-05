import React, { useState } from "react";
import { X, ArrowDown, Loader2, AlertTriangle } from "lucide-react";

/**
 * Withdraw liquidity modal — matches SwapConfirmModal / ClaimFeesModal design system.
 *
 * Shows position details, withdrawal percentage selector, and token amounts to receive.
 */
export default function WithdrawModal({
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
  const [percent, setPercent] = useState(100);

  if (!isOpen || !position) return null;

  const amount0 = parseFloat((position.token0Amount || "0").replace(",", ""));
  const amount1 = parseFloat((position.token1Amount || "0").replace(",", ""));
  const fee0 = parseFloat((position.feesEarned0 || "0").replace(",", ""));
  const fee1 = parseFloat((position.feesEarned1 || "0").replace(",", ""));

  const withdrawAmount0 = ((amount0 * percent) / 100).toFixed(2);
  const withdrawAmount1 = ((amount1 * percent) / 100).toFixed(2);
  const totalWithdraw = (
    (amount0 * percent) / 100 +
    (amount1 * percent) / 100
  ).toFixed(2);

  const presets = [25, 50, 75, 100];

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
            <div className="w-2 h-2 bg-pink-500 shadow-[0_0_8px_rgba(236,72,153,0.5)]" />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              Withdraw_Liquidity
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

        <div className="p-6 flex flex-col gap-0">
          {/* Position Info */}
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

          {/* Percentage Selector */}
          <div className="mt-4 border border-white/10 bg-white/[0.02] p-4">
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-3">
              Withdraw Amount
            </div>
            <div className="flex items-center gap-2 mb-4">
              {presets.map((p) => (
                <button
                  key={p}
                  onClick={() => setPercent(p)}
                  className={`flex-1 py-2 text-sm font-bold uppercase tracking-widest transition-colors border ${
                    percent === p
                      ? "bg-white/10 text-white border-white/20"
                      : "text-gray-500 border-white/5 hover:text-white hover:border-white/10"
                  }`}
                >
                  {p}%
                </button>
              ))}
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-3xl font-light text-pink-400 tracking-tight">
                {percent}%
              </span>
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                ${totalWithdraw}
              </span>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex justify-center -my-2 relative z-10">
            <div className="w-8 h-8 flex items-center justify-center bg-[#080808] border border-white/10 text-pink-400">
              <ArrowDown size={14} />
            </div>
          </div>

          {/* You Receive */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-3">
              You Receive
            </div>
            <div className="flex items-baseline justify-between mb-2">
              <span className="text-2xl font-light text-white tracking-tight">
                {Number(withdrawAmount0).toLocaleString()}
              </span>
              <span className="text-sm text-gray-500 uppercase tracking-widest">
                {token0.symbol}
              </span>
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-light text-white tracking-tight">
                {Number(withdrawAmount1).toLocaleString()}
              </span>
              <span className="text-sm text-gray-500 uppercase tracking-widest">
                {token1.symbol}
              </span>
            </div>
          </div>

          {/* Detail rows */}
          <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Unclaimed_Fees
              </span>
              <span className="text-sm font-mono text-green-400">
                +${(fee0 + fee1).toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Collect_Fees
              </span>
              <span className="text-sm font-mono text-gray-300">
                Auto
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Est._Gas
              </span>
              <span className="text-sm font-mono text-gray-400">
                ~$0.68
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
            onClick={() => onConfirm?.(percent)}
            disabled={executing}
            className={`w-full py-4 text-sm font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none flex items-center justify-center gap-2 bg-pink-500 text-black hover:bg-pink-400 ${
              executing ? "opacity-70 cursor-wait" : "hover:opacity-90"
            }`}
          >
            {executing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {executionStep || "Processing..."}
              </>
            ) : (
              `Withdraw ${percent}%`
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
              <span className="text-pink-400/60">{executionStep}</span>
            ) : (
              <span className="text-gray-600">
                REVIEW · Fees will be auto-collected on withdrawal
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
