import React from "react";
import { X, Droplets, ArrowDown, Loader2, AlertTriangle } from "lucide-react";

/**
 * Add Liquidity confirmation modal — matches site modal design system.
 *
 * Shows price range, token deposit amounts, pool summary, and confirm button.
 */
export default function AddLiquidityModal({
  isOpen,
  onClose,
  onConfirm,
  minPrice,
  maxPrice,
  token0Amount,
  token1Amount,
  token0,
  token1,
  pool,
  executing,
  executionStep,
  executionError,
}) {
  if (!isOpen) return null;

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
              Add_Liquidity
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
          {/* Price Range */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-3">
              Price Range
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="text-sm text-gray-600 uppercase tracking-widest mb-1">Min</div>
                <div className="text-xl font-light text-white font-mono tracking-tight">
                  {minPrice ? Number(minPrice).toFixed(2) : "—"}
                </div>
              </div>
              <div>
                <div className="text-sm text-gray-600 uppercase tracking-widest mb-1">Max</div>
                <div className="text-xl font-light text-white font-mono tracking-tight">
                  {maxPrice ? Number(maxPrice).toFixed(2) : "—"}
                </div>
              </div>
            </div>
            <div className="text-sm text-gray-600 mt-2">
              {token0.symbol} per {token1.symbol}
            </div>
          </div>

          {/* Arrow */}
          <div className="flex justify-center -my-2 relative z-10">
            <div className="w-8 h-8 flex items-center justify-center bg-[#080808] border border-white/10 text-cyan-400">
              <ArrowDown size={14} />
            </div>
          </div>

          {/* Deposit Amounts */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-sm text-gray-500 uppercase tracking-widest mb-3">
              You Deposit
            </div>
            <div className="flex items-baseline justify-between mb-2">
              <span className="text-2xl font-light text-white tracking-tight">
                {Number(token0Amount || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </span>
              <span className="text-sm text-gray-500 uppercase tracking-widest">
                {token0.symbol}
              </span>
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-light text-white tracking-tight">
                {Number(token1Amount || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}
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
                Pool
              </span>
              <span className="text-sm font-mono text-gray-300">
                {pool.pair}
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Fee_Tier
              </span>
              <span className="text-sm font-mono text-gray-300">
                {pool.feeTier}
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Current_Price
              </span>
              <span className="text-sm font-mono text-gray-300">
                {pool.currentPrice.toFixed(4)}
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Est._APR
              </span>
              <span className="text-sm font-mono text-green-400">
                {Number(pool.apr).toFixed(2)}%
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-3">
              <span className="text-sm text-gray-500 uppercase tracking-widest font-mono">
                Est._Gas
              </span>
              <span className="text-sm font-mono text-gray-400">
                ~$1.20
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
            className={`w-full py-4 text-sm font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none flex items-center justify-center gap-2 bg-cyan-500 text-black hover:bg-cyan-400 ${
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
                Add Liquidity
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
              <span className="text-cyan-400/60">{executionStep}</span>
            ) : (
              <span className="text-gray-600">
                REVIEW · Confirm deposit before submitting
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
