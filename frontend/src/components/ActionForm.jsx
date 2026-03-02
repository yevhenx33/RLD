import React, { useState } from "react";
import { ethers } from "ethers";
import { InputGroup, SummaryRow } from "./TradingTerminal";
import { getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";
import { useTwammOrder } from "../hooks/useTwammOrder";
import { ZERO_FOR_ONE_LONG } from "../config/simulationConfig";


// PrimeBroker ABI subset for mint
const PRIME_BROKER_ABI = [
  "function modifyPosition(bytes32 rawMarketId, int256 deltaCollateral, int256 deltaDebt)",
];

/* ── Mint Form ────────────────────────────────────────────────── */
function MintForm({ brokerBalance, currentRate, brokerAddress, marketId, account, addToast }) {
  const [collateral, setCollateral] = useState("");
  const [mintAmount, setMintAmount] = useState("");
  const [executing, setExecuting] = useState(false);

  const available = brokerBalance != null ? parseFloat(brokerBalance) : null;

  const newCR = collateral && available != null && available > 0
    ? ((available / Number(collateral)) * 100).toFixed(0)
    : null;

  const handleCollateralChange = (v) => {
    setCollateral(v);
    if (v && currentRate > 0) {
      setMintAmount((Number(v) / currentRate).toFixed(6));
    } else {
      setMintAmount("");
    }
  };

  const handleMintChange = (v) => {
    setMintAmount(v);
    if (v && currentRate > 0) {
      setCollateral((Number(v) * currentRate).toFixed(2));
    } else {
      setCollateral("");
    }
  };

  const executeMint = async () => {
    if (!account || !brokerAddress || !marketId || !mintAmount) return;

    try {
      setExecuting(true);

      // Get MetaMask signer (handles Anvil chainId sync)
      const signer = await getAnvilSigner();

      // 2. Connect to broker
      const broker = new ethers.Contract(brokerAddress, PRIME_BROKER_ABI, signer);

      // 3. Call modifyPosition(marketId, 0, +deltaDebt)
      // deltaDebt is in 6 decimals (wRLP), positive = mint

      const debtAmount = ethers.parseUnits(mintAmount, 6);

      // Pre-check with staticCall to get revert reason before spending gas
      try {
        await broker.modifyPosition.staticCall(marketId, 0, debtAmount);
      } catch (simErr) {
        // Extract the deepest revert reason
        const revertReason = simErr?.revert?.args?.[0]
          || simErr?.info?.error?.data?.message
          || simErr?.info?.error?.message
          || simErr?.reason
          || simErr?.shortMessage
          || simErr?.message
          || "Simulation failed";
        throw new Error(revertReason);
      }

      const tx = await broker.modifyPosition(marketId, 0, debtAmount, {
        gasLimit: 2_000_000n, // Solvency check calls TWAMM oracle which is gas-heavy
      });
      await tx.wait();

      setCollateral("");
      setMintAmount("");
      addToast({ type: "success", title: "Mint Successful", message: `Minted ${mintAmount} wRLP` });
    } catch (err) {
      console.error("[MINT] Full error:", err);
      const reason = err?.revert?.args?.[0]
        || err?.info?.error?.data?.message
        || err?.info?.error?.message
        || err?.reason
        || err?.shortMessage
        || err?.message
        || "Unknown error";
      addToast({ type: "error", title: "Mint Failed", message: reason });
    } finally {
      await restoreAnvilChainId();
      setExecuting(false);
    }
  };


  const canMint = mintAmount && Number(mintAmount) > 0 && account && brokerAddress && marketId;

  return (
    <div className="flex flex-col gap-4">
      <InputGroup
        label="Collateral"
        subLabel={`Broker: ${available != null ? `${available.toFixed(1)} waUSDC` : "—"}`}
        value={collateral}
        onChange={handleCollateralChange}
        suffix="USDC"
        onMax={available > 0 ? () => handleCollateralChange(String(available)) : undefined}
      />

      <InputGroup
        label="Mint Amount"
        value={mintAmount}
        onChange={handleMintChange}
        suffix="wRLP"
        placeholder="0.00"
      />

      {/* New CR display */}
      <div className="border border-white/10 p-4 space-y-2 bg-white/[0.02] text-sm">
        <div className="flex justify-between items-center">
          <span className="text-gray-500 uppercase">New CR</span>
          <span className={`font-mono ${
            newCR && Number(newCR) > 200 ? "text-green-400"
              : newCR && Number(newCR) > 150 ? "text-yellow-400"
              : newCR ? "text-red-400"
              : "text-white"
          }`}>
            {newCR ? `${newCR}%` : "—"}
          </span>
        </div>
      </div>



      <button
        onClick={executeMint}
        disabled={!canMint || executing}
        className={`w-full py-3 text-sm font-bold tracking-[0.2em] uppercase transition-all bg-cyan-500 text-black hover:bg-cyan-400 ${
          !canMint || executing ? "opacity-50 cursor-not-allowed" : ""
        }`}
      >
        {executing ? "Processing..." : "Mint wRLP"}
      </button>
    </div>
  );
}

/* ── TWAP Form ────────────────────────────────────────────────── */

const DURATION_PRESETS = [
  { label: "1H", hours: 1 },
  { label: "6H", hours: 6 },
  { label: "12H", hours: 12 },
  { label: "24H", hours: 24 },
  { label: "7D", hours: 168 },
];

function TwapForm({ brokerAddress, marketInfo, account, addToast }) {
  const [amount, setAmount] = useState("");
  const [durationHours, setDurationHours] = useState("");
  const [direction, setDirection] = useState("BUY");

  const infrastructure = marketInfo?.infrastructure;
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr = marketInfo?.position_token?.address;

  const {
    submitOrder,
    executing,
    error: twammError,
    step: twammStep,
  } = useTwammOrder(
    account,
    brokerAddress,
    infrastructure,
    collateralAddr,
    positionAddr,
  );

  // BUY wRLP = sell waUSDC → zeroForOne matches ZERO_FOR_ONE_LONG
  // SELL wRLP = sell wRLP → zeroForOne is opposite
  const zeroForOne = direction === "BUY" ? ZERO_FOR_ONE_LONG : !ZERO_FOR_ONE_LONG;

  const durationNum = Number(durationHours) || 0;
  const amountNum = Number(amount) || 0;
  // Option E (deferred start): duration = exactly durationNum hours
  const durationSec = durationNum * 3600;
  const sellRate =
    amountNum > 0 && durationSec > 0
      ? (amountNum / durationSec).toFixed(8)
      : "—";

  const canSubmit =
    amountNum > 0 &&
    durationNum >= 1 &&
    Number.isInteger(durationNum) &&
    account &&
    brokerAddress &&
    infrastructure?.twamm_hook;

  const handleSubmit = () => {
    submitOrder(amountNum, durationNum, zeroForOne, () => {
      setAmount("");
      setDurationHours("");
      addToast({
        type: "success",
        title: "TWAMM Order Submitted",
        message: `${direction} ${amount} over ${durationNum}h`,
        duration: 5000,
      });
    });
  };

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
        subLabel={direction === "BUY" ? "waUSDC to sell" : "wRLP to sell"}
        value={amount}
        onChange={setAmount}
        suffix={direction === "BUY" ? "waUSDC" : "wRLP"}
        placeholder="0.00"
      />

      {/* Duration presets */}
      <div className="space-y-2">
        <div className="text-sm uppercase tracking-widest font-bold text-gray-500">
          Duration
        </div>
        <div className="flex gap-1">
          {DURATION_PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => setDurationHours(String(p.hours))}
              className={`flex-1 py-1.5 text-xs font-bold tracking-widest uppercase border transition-colors ${
                Number(durationHours) === p.hours
                  ? "border-cyan-500/50 bg-cyan-500/10 text-cyan-400"
                  : "border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        <InputGroup
          label=""
          value={durationHours}
          onChange={(v) => setDurationHours(v)}
          suffix="HOURS"
          placeholder="Custom hours"
        />
      </div>

      <div className="border-t border-white/10 pt-3 space-y-2">
        <SummaryRow label="Sell Rate" value={sellRate !== "—" ? `${sellRate} /sec` : "—"} />
        <SummaryRow
          label="Total Duration"
          value={
            durationNum > 0
              ? durationNum >= 24
                ? `${(durationNum / 24).toFixed(1)} days`
                : `${durationNum}h`
              : "—"
          }
        />
        <SummaryRow
          label="Direction"
          value={direction === "BUY" ? "waUSDC → wRLP" : "wRLP → waUSDC"}
          valueColor={direction === "BUY" ? "text-cyan-400" : "text-pink-400"}
        />
      </div>

      {/* Execution feedback */}
      {twammStep && (
        <div className="text-xs text-gray-400 font-mono animate-pulse">
          {twammStep}
        </div>
      )}
      {twammError && (
        <div className="text-xs text-red-400 font-mono truncate">
          {twammError}
        </div>
      )}

      <button
        onClick={handleSubmit}
        disabled={!canSubmit || executing}
        className={`w-full py-3 text-sm font-bold tracking-[0.2em] uppercase transition-all ${
          direction === "BUY"
            ? "bg-cyan-500 text-black hover:bg-cyan-400"
            : "bg-pink-500 text-black hover:bg-pink-400"
        } ${!canSubmit || executing ? "opacity-50 cursor-not-allowed" : ""}`}
      >
        {executing ? twammStep || "Processing..." : `Place ${direction} TWAP`}
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
export default function ActionForm({ type, brokerBalance, currentRate, brokerAddress, marketId, account, addToast, marketInfo }) {
  const forms = {
    mint: <MintForm brokerBalance={brokerBalance} currentRate={currentRate} brokerAddress={brokerAddress} marketId={marketId} account={account} addToast={addToast} />,
    twap: <TwapForm brokerAddress={brokerAddress} marketInfo={marketInfo} account={account} addToast={addToast} />,
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
