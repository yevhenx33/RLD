import React, { useState } from "react";

export function CollateralConfirmModal({ label, sub, onConfirm, onCancel }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onCancel}>
      <div
        className="bg-[#0a0b0d] border border-white/10 shadow-2xl w-full max-w-sm mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-white/5">
          <div className="text-sm font-mono text-white font-bold">{label}</div>
          {sub && <div className="text-xs font-mono text-gray-500 mt-1">{sub}</div>}
        </div>
        <div className="px-5 py-4 text-xs text-gray-500 font-mono">
          This will send a transaction to update which position is counted toward your collateral ratio. A solvency check will be performed.
        </div>
        <div className="flex border-t border-white/5">
          <button
            onClick={onCancel}
            disabled={busy}
            className="flex-1 px-4 py-3 text-sm font-mono text-gray-400 hover:bg-white/5 transition-colors border-r border-white/5"
          >
            Cancel
          </button>
          <button
            onClick={async () => {
              setBusy(true);
              try { await onConfirm(); } finally { setBusy(false); }
            }}
            disabled={busy}
            className="flex-1 px-4 py-3 text-sm font-mono text-cyan-400 hover:bg-cyan-500/5 transition-colors font-bold"
          >
            {busy ? "Sending..." : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ClaimConfirmModal({ order, executing, onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onCancel}>
      <div
        className="bg-[#0a0b0d] border border-white/10 shadow-2xl w-full max-w-sm mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-white/5">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]" />
            <span className="text-sm font-mono text-white font-bold">Claim Expired Order</span>
          </div>
        </div>

        {/* Order details */}
        <div className="px-5 py-4 space-y-2 text-xs font-mono">
          <div className="flex justify-between">
            <span className="text-gray-500">Direction</span>
            <span className="text-white">{order.direction}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Deposit</span>
            <span className="text-gray-400">{order.amountIn.toFixed(2)} {order.sellToken}</span>
          </div>
          {order.earned > 0 && (
            <div className="flex justify-between">
              <span className="text-gray-500">Earned</span>
              <span className="text-green-400">{order.earned.toFixed(4)} {order.buyToken}</span>
            </div>
          )}
          {order.sellRefund > 0 && (
            <div className="flex justify-between">
              <span className="text-gray-500">Unsold Refund</span>
              <span className="text-gray-400">{order.sellRefund.toFixed(2)} {order.sellToken}</span>
            </div>
          )}
          <div className="flex justify-between border-t border-white/5 pt-2">
            <span className="text-gray-500">Total Value</span>
            <span className="text-white">${order.valueUsd.toFixed(2)}</span>
          </div>
        </div>

        {/* Description */}
        <div className="px-5 pb-4 text-xs text-gray-500 font-mono">
          Tokens will be returned to your broker account.
        </div>

        {/* Actions */}
        <div className="flex border-t border-white/5">
          <button
            onClick={onCancel}
            disabled={executing}
            className="flex-1 px-4 py-3 text-sm font-mono text-gray-400 hover:bg-white/5 transition-colors border-r border-white/5"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={executing}
            className="flex-1 px-4 py-3 text-sm font-mono text-green-400 hover:bg-green-500/5 transition-colors font-bold"
          >
            {executing ? "Claiming..." : "Confirm Claim"}
          </button>
        </div>
      </div>
    </div>
  );
}
