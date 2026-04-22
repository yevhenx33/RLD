import React, { useState, useEffect, useMemo } from "react";
import { X, RefreshCw } from "lucide-react";
import SettingsButton from "../shared/SettingsButton";

/**
 * Self-contained PnL Calculator modal.
 *
 * All state (side, collateral, CR, entry rate, target rate) lives inside
 * the modal. The only external input is `currentRate` which seeds the
 * defaults when the modal opens.
 */
export default function PnlCalculatorModal({ isOpen, onClose, currentRate }) {
  // ── Internal state ──────────────────────────────────────────
  const [side, setSide] = useState("LONG");
  const [collateral, setCollateral] = useState("1000");
  const [cr, setCr] = useState("200");
  const [entryRate, setEntryRate] = useState("");
  const [targetRate, setTargetRate] = useState("");

  // Seed entry/target rate from currentRate on open
  useEffect(() => {
    if (isOpen && currentRate > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setEntryRate((prev) => (prev === "" ? currentRate.toFixed(4) : prev));
      setTargetRate((prev) => (prev === "" ? currentRate.toFixed(4) : prev));
    }
    if (!isOpen) {
      // Reset on close so next open re-seeds
      setEntryRate("");
      setTargetRate("");
    }
  }, [isOpen, currentRate]);

  // ── Derived values ──────────────────────────────────────────
  const isLong = side === "LONG";
  const collateralNum = parseFloat(collateral) || 0;
  const crNum = parseFloat(cr) || 200;
  const entryRateNum = parseFloat(entryRate) || 0;
  const targetRateNum = parseFloat(targetRate) || 0;

  const notional = useMemo(() => {
    if (isLong) return collateralNum;
    const crDecimal = crNum / 100;
    return crDecimal > 0 ? collateralNum / crDecimal : 0;
  }, [isLong, collateralNum, crNum]);

  const liqRate = useMemo(() => {
    if (isLong) return null;
    return entryRateNum * (crNum / 110);
  }, [isLong, entryRateNum, crNum]);

  const pnl = useMemo(() => {
    if (entryRateNum <= 0) return { value: 0, percent: 0 };
    // PnL = ((rateChange / entryRate)) × notional
    // Long profits when rate goes up, short profits when rate goes down
    const rateChangePct = (targetRateNum - entryRateNum) / entryRateNum;
    const value = isLong
      ? rateChangePct * notional
      : -rateChangePct * notional;
    const percent = collateralNum > 0 ? (value / collateralNum) * 100 : 0;
    return { value, percent };
  }, [isLong, targetRateNum, entryRateNum, notional, collateralNum]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md bg-[#080808] border border-white/10 shadow-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-white/10 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.5)]" />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              PnL_Calculator
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Side Toggle */}
        <div className="flex border-b border-white/10">
          {["LONG", "SHORT"].map((s) => (
            <button
              key={s}
              onClick={() => setSide(s)}
              className={`flex-1 py-3 text-[11px] font-bold tracking-[0.2em] uppercase transition-colors ${
                side === s
                  ? s === "LONG"
                    ? "bg-cyan-500/10 text-cyan-400 border-b-2 border-cyan-500"
                    : "bg-pink-500/10 text-pink-400 border-b-2 border-pink-500"
                  : "text-gray-600 hover:text-gray-400"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {/* Input Fields */}
        <div className="p-5 space-y-3 border-b border-white/10">
          {/* Collateral */}
          <InputRow label="Collateral" suffix="waUSDC">
            <input
              type="number"
              value={collateral}
              onChange={(e) => setCollateral(e.target.value)}
              className="bg-transparent text-right text-sm text-white font-mono focus:outline-none w-full [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
          </InputRow>

          {/* Collateral Ratio */}
          <InputRow label="Collateral_Ratio" suffix="%" disabled={isLong}>
            <input
              type="number"
              value={isLong ? "—" : cr}
              onChange={(e) => setCr(e.target.value)}
              disabled={isLong}
              className={`bg-transparent text-right text-sm font-mono focus:outline-none w-full [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none ${
                isLong ? "text-gray-600 cursor-not-allowed" : "text-white"
              }`}
            />
          </InputRow>

          {/* Notional (computed) */}
          <InputRow label="Notional" suffix="waUSDC" computed>
            <span className="text-sm font-mono text-gray-300 select-all">
              {notional.toLocaleString(undefined, {
                maximumFractionDigits: 0,
              })}
            </span>
          </InputRow>

          {/* Liq Rate (computed) */}
          <InputRow label="Liq_Rate" computed>
            <span
              className={`text-sm font-mono ${liqRate !== null ? "text-orange-500" : "text-gray-600"}`}
            >
              {liqRate !== null ? `${liqRate.toFixed(4)}%` : "None"}
            </span>
          </InputRow>

          {/* Entry Rate */}
          <InputRow label="Entry_Rate" suffix="%">
            <input
              type="number"
              value={entryRate}
              onChange={(e) => setEntryRate(e.target.value)}
              step="0.01"
              className="bg-transparent text-right text-sm text-white font-mono focus:outline-none w-full [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
          </InputRow>
        </div>

        {/* Rate Scenario Slider */}
        <div className="p-5 flex flex-col gap-3">
          <div className="flex justify-between items-center">
            <span className="text-[11px] uppercase tracking-widest text-gray-500 font-bold">
              Rate_Scenario
            </span>
            <div className="flex items-center gap-3">
              <span className="text-sm font-mono text-white">
                {targetRateNum.toFixed(2)}%
              </span>
              <RefreshCw
                size={14}
                className="text-gray-600 cursor-pointer hover:text-white transition-colors"
                onClick={() => setTargetRate(entryRate)}
                title="Reset to entry rate"
              />
            </div>
          </div>

          <input
            type="range"
            min="0"
            max="30"
            step="0.1"
            value={targetRateNum}
            onChange={(e) => setTargetRate(e.target.value)}
            className="w-full h-1 bg-white/10 rounded-none appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-none"
          />

          {/* Quick presets */}
          <div className="flex justify-between gap-1">
            {[-50, -10, 10, 50].map((pct) => (
              <SettingsButton
                key={pct}
                onClick={() =>
                  setTargetRate((entryRateNum * (1 + pct / 100)).toFixed(4))
                }
                className="flex-1"
              >
                {pct > 0 ? "+" : ""}
                {pct}%
              </SettingsButton>
            ))}
          </div>
        </div>

        {/* PnL Result */}
        <div className="px-5 pb-5">
          <div className="border border-white/10 bg-white/[0.02] p-5">
            <div className="flex justify-between items-end">
              <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                Est. PnL (1Y)
              </span>
              <div
                className={`text-right ${
                  pnl.value >= 0 ? "text-green-500" : "text-red-500"
                }`}
              >
                <div className="text-2xl font-mono leading-none">
                  {pnl.value >= 0 ? "+" : ""}
                  {pnl.value.toLocaleString(undefined, {
                    maximumFractionDigits: 0,
                  })}{" "}
                  USDC
                </div>
                <div className="text-[12px] font-mono mt-1.5">
                  {pnl.percent.toFixed(2)}% ROI
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-5 pb-4">
          <div className="flex items-center text-[10px] font-mono pt-2 border-t border-white/5">
            <span className="text-gray-600">
              SIMULATION · Based on 1‑year hold scenario
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Reusable row for the input section */
function InputRow({ label, suffix, disabled, computed, children }) {
  return (
    <div
      className={`flex items-center justify-between py-2 border-b border-white/5 ${
        disabled ? "opacity-30" : ""
      }`}
    >
      <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono whitespace-nowrap">
        {label}
      </span>
      <div className="flex items-center gap-2 flex-1 justify-end min-w-0">
        <div className={`${computed ? "" : "flex-1"} text-right`}>
          {children}
        </div>
        {suffix && (
          <span className="text-[11px] text-gray-600 font-mono uppercase whitespace-nowrap">
            {suffix}
          </span>
        )}
      </div>
    </div>
  );
}
