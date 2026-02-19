import React, { useState } from "react";
import { InputGroup, SummaryRow } from "./TradingTerminal";

/* ── TWAP Form ────────────────────────────────────────────────── */
function TwapForm() {
  const [amount, setAmount] = useState("");
  const [duration, setDuration] = useState("");
  const [direction, setDirection] = useState("BUY");

  const ratePerBlock =
    amount && duration ? (Number(amount) / (Number(duration) * 7200)).toFixed(6) : "—";
  const totalBlocks = duration ? (Number(duration) * 7200).toLocaleString() : "—";

  return (
    <div className="flex flex-col gap-4">
      {/* Direction toggle */}
      <div className="flex border border-white/10 bg-[#060606]">
        {["BUY", "SELL"].map((d) => (
          <button
            key={d}
            onClick={() => setDirection(d)}
            className={`flex-1 py-2 text-sm font-bold tracking-[0.2em] uppercase transition-colors ${
              direction === d
                ? d === "BUY"
                  ? "bg-cyan-500/10 text-cyan-400 border-b-2 border-cyan-500"
                  : "bg-pink-500/10 text-pink-400 border-b-2 border-pink-500"
                : "text-gray-600 hover:text-gray-400"
            }`}
          >
            {d}
          </button>
        ))}
      </div>

      <InputGroup
        label="Amount"
        subLabel="wRLP"
        value={amount}
        onChange={setAmount}
        suffix="wRLP"
        placeholder="0.00"
      />
      <InputGroup
        label="Duration"
        subLabel="days"
        value={duration}
        onChange={setDuration}
        suffix="DAYS"
        placeholder="7"
      />

      <div className="border-t border-white/10 pt-3 space-y-2">
        <SummaryRow label="Rate / Block" value={ratePerBlock} />
        <SummaryRow label="Total Blocks" value={totalBlocks} />
        <SummaryRow label="Est. Impact" value="< 0.01%" valueColor="text-green-400" />
      </div>

      <button
        onClick={() =>
          console.log("[TWAP]", { direction, amount, duration })
        }
        disabled={!amount || !duration}
        className={`w-full py-3 text-sm font-bold tracking-[0.2em] uppercase transition-all ${
          direction === "BUY"
            ? "bg-cyan-500 text-black hover:bg-cyan-400"
            : "bg-pink-500 text-black hover:bg-pink-400"
        } ${!amount || !duration ? "opacity-50 cursor-not-allowed" : ""}`}
      >
        Place {direction} TWAP
      </button>
    </div>
  );
}

/* ── LP Form ──────────────────────────────────────────────────── */
function LpForm() {
  const [amount, setAmount] = useState("");
  const [tickLower, setTickLower] = useState("");
  const [tickUpper, setTickUpper] = useState("");

  const rangeWidth =
    tickLower && tickUpper
      ? Math.abs(Number(tickUpper) - Number(tickLower))
      : "—";

  return (
    <div className="flex flex-col gap-4">
      <InputGroup
        label="Deposit"
        subLabel="waUSDC"
        value={amount}
        onChange={setAmount}
        suffix="waUSDC"
        placeholder="0.00"
      />
      <InputGroup
        label="Min Tick"
        subLabel="lower"
        value={tickLower}
        onChange={setTickLower}
        suffix=""
        placeholder="-100"
      />
      <InputGroup
        label="Max Tick"
        subLabel="upper"
        value={tickUpper}
        onChange={setTickUpper}
        suffix=""
        placeholder="100"
      />

      <div className="border-t border-white/10 pt-3 space-y-2">
        <SummaryRow label="Range Width" value={`${rangeWidth} ticks`} />
        <SummaryRow label="Fee Tier" value="0.30%" />
        <SummaryRow
          label="Capital Eff."
          value={
            rangeWidth !== "—" && rangeWidth > 0
              ? `${(10000 / rangeWidth).toFixed(1)}x`
              : "—"
          }
          valueColor="text-cyan-400"
        />
      </div>

      <button
        onClick={() =>
          console.log("[LP]", { amount, tickLower, tickUpper })
        }
        disabled={!amount || !tickLower || !tickUpper}
        className={`w-full py-3 text-sm font-bold tracking-[0.2em] uppercase transition-all bg-cyan-500 text-black hover:bg-cyan-400 ${
          !amount || !tickLower || !tickUpper
            ? "opacity-50 cursor-not-allowed"
            : ""
        }`}
      >
        Provide Liquidity
      </button>
    </div>
  );
}

/* ── Loop Form ────────────────────────────────────────────────── */
function LoopForm() {
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
          console.log("[LOOP]", { deposit, leverage, duration })
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
function BatchForm() {
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

/* ── ActionForm Router ────────────────────────────────────────── */
export default function ActionForm({ type }) {
  const forms = {
    twap: <TwapForm />,
    lp: <LpForm />,
    loop: <LoopForm />,
    batch: <BatchForm />,
  };

  return (
    <div className="p-4">
      {forms[type] || null}
    </div>
  );
}
