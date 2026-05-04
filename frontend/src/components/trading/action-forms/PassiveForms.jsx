import React, { useState } from "react";
import { InputGroup, SummaryRow } from "../TradingTerminal";
import { debugLog } from "../../../utils/debugLogger";

/* ── Loop Form ────────────────────────────────────────────────── */
export function LoopForm() {
  const [deposit, setDeposit] = useState("");
  const [leverage, setLeverage] = useState(2);
  const [duration, setDuration] = useState("");

  const effectiveYield = deposit
    ? (10 * leverage).toFixed(1)
    : "—";
  const colRatio = leverage > 0 ? (100 / leverage).toFixed(0) : "—";
  const unwindRate =
    deposit && duration
      ? ((Number(deposit) * leverage) / (Number(duration) * 7200)).toFixed(4)
      : "—";

  return (
    <div className="flex flex-col gap-4">
      <InputGroup
        label="Deposit"
        subLabel="waUSDC"
        value={deposit}
        onChange={setDeposit}
        suffix="waUSDC"
        placeholder="0.00"
      />

      {/* Leverage slider */}
      <div className="space-y-2">
        <div className="flex justify-between text-sm uppercase tracking-widest font-bold text-gray-500">
          <span>Leverage</span>
          <span className="text-white font-mono">{leverage}x</span>
        </div>
        <input
          type="range"
          min={1}
          max={5}
          step={0.5}
          value={leverage}
          onChange={(e) => setLeverage(Number(e.target.value))}
          className="w-full accent-cyan-500 h-1 bg-white/10 appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:rounded-none [&::-webkit-slider-thumb]:bg-cyan-400"
        />
        <div className="flex justify-between text-sm text-gray-700 font-mono">
          <span>1x</span>
          <span>5x</span>
        </div>
      </div>

      <InputGroup
        label="Duration"
        subLabel="days"
        value={duration}
        onChange={setDuration}
        suffix="DAYS"
        placeholder="365"
      />

      <div className="border-t border-white/10 pt-3 space-y-2">
        <SummaryRow
          label="Eff. Yield"
          value={effectiveYield !== "—" ? `~${effectiveYield}%` : "—"}
          valueColor="text-green-400"
        />
        <SummaryRow label="Col. Ratio" value={`${colRatio}%`} />
        <SummaryRow label="Unwind Rate" value={`${unwindRate} /blk`} />
      </div>

      <button
        onClick={() =>
          debugLog("[LOOP]", { deposit, leverage, duration })
        }
        disabled={!deposit || !duration}
        className={`w-full py-3 text-sm font-bold tracking-[0.2em] uppercase transition-all bg-cyan-500 text-black hover:bg-cyan-400 ${
          !deposit || !duration ? "opacity-50 cursor-not-allowed" : ""
        }`}
      >
        Open Loop
      </button>
    </div>
  );
}

/* ── Batch Form ───────────────────────────────────────────────── */
export function BatchForm() {
  return (
    <div className="flex flex-col items-center justify-center py-6 gap-2">
      <div className="text-sm text-gray-600 uppercase tracking-widest text-center">
        Coming Soon
      </div>
      <div className="text-sm text-gray-700 font-mono text-center">
        Multi-action bundles
      </div>
    </div>
  );
}
