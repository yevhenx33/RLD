import React, { useState, useMemo, useCallback, useEffect } from "react";
import { ethers } from "ethers";
import { InputGroup, SummaryRow } from "./TradingTerminal";
import { getSigner } from "../../utils/connection";
import { useTwammOrder } from "../../hooks/useTwammOrder";
import { usePoolLiquidity, liquidityToAmounts, computeLiquidity } from "../../hooks/usePoolLiquidity";
import {
  BROKER_ROUTER_LONG_ABI,
  buildHooklessPoolKey,
  encodeExecuteLongCalldata,
} from "../../lib/peripheryIntegration";


// PrimeBroker ABI subset for mint
const PRIME_BROKER_ABI = [
  "function modifyPosition(bytes32 rawMarketId, int256 deltaCollateral, int256 deltaDebt)",
];

/* ── Mint Form ────────────────────────────────────────────────── */
function MintForm({ brokerBalance, currentRate, brokerAddress, marketId, account, addToast, onStateChange, txPauseRef }) {
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
      if (txPauseRef) txPauseRef.current = true;

      // Get MetaMask signer (handles Anvil chainId sync)
      const signer = await getSigner();

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
      onStateChange?.();
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
      setExecuting(false);
      if (txPauseRef) txPauseRef.current = false;
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

function TwapForm({ brokerAddress, marketInfo, account, addToast, onTwammRefresh, txPauseRef }) {
  const [amount, setAmount] = useState("");
  const [durationHours, setDurationHours] = useState("");
  const [direction, setDirection] = useState("BUY");

  const infrastructure = marketInfo?.infrastructure;
  const collateralSymbol = marketInfo?.collateral?.symbol || "waUSDC";
  const positionSymbol =
    marketInfo?.positionToken?.symbol ||
    marketInfo?.position_token?.symbol ||
    "wRLP";
  const twammMarketId =
    marketInfo?.twamm?.marketId ||
    infrastructure?.twammMarketId ||
    infrastructure?.twamm_market_id ||
    marketInfo?.poolId ||
    marketInfo?.pool_id ||
    marketInfo?.marketId ||
    marketInfo?.market_id ||
    "";
  const twapEngineAddr =
    marketInfo?.twamm?.engine ||
    infrastructure?.twapEngine ||
    infrastructure?.twap_engine ||
    marketInfo?.twapEngine ||
    marketInfo?.twap_engine ||
    "";
  const collateralAddr = marketInfo?.collateral?.address;
  const positionAddr =
    marketInfo?.positionToken?.address ||
    marketInfo?.position_token?.address;
  const buyPositionZeroForOne =
    typeof marketInfo?.twamm?.buyPositionZeroForOne === "boolean"
      ? marketInfo.twamm.buyPositionZeroForOne
      : typeof infrastructure?.buyPositionZeroForOne === "boolean"
        ? infrastructure.buyPositionZeroForOne
        : typeof marketInfo?.zeroForOneLong === "boolean"
          ? marketInfo.zeroForOneLong
          : false;
  const twammInfra = {
    ...infrastructure,
    twapEngine: twapEngineAddr,
    twap_engine: twapEngineAddr,
  };

  const {
    submitOrder,
    executing,
    error: twammError,
    step: twammStep,
  } = useTwammOrder(
    account,
    brokerAddress,
    twammMarketId,
    twammInfra,
    collateralAddr,
    positionAddr,
  );

  // Sync executing flag into txPauseRef to pause block-driven data updates
  useEffect(() => {
    if (txPauseRef) txPauseRef.current = !!executing;
  }, [executing, txPauseRef]);

  const zeroForOne = direction === "BUY" ? buyPositionZeroForOne : !buyPositionZeroForOne;
  const sellSymbol = direction === "BUY" ? collateralSymbol : positionSymbol;
  const buySymbol = direction === "BUY" ? positionSymbol : collateralSymbol;

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
    twammMarketId &&
    twapEngineAddr;

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
      // Refresh position panel after indexer catches up
      if (onTwammRefresh) {
        setTimeout(() => onTwammRefresh(), 3000);
        setTimeout(() => onTwammRefresh(), 6000);
      }
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
        subLabel={`${sellSymbol} to sell`}
        value={amount}
        onChange={setAmount}
        suffix={sellSymbol}
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
          value={`${sellSymbol} → ${buySymbol}`}
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
const RANGE_PRESETS = [
  { label: "±5%", factor: 0.05 },
  { label: "±10%", factor: 0.10 },
  { label: "±25%", factor: 0.25 },
  { label: "Full", factor: null },
];

// ABI fragments for BrokerExecutor atomic flow
const BROKER_EXECUTOR_ABI = [
  "function execute(address broker, bytes calldata ownerSignature, tuple(address target, bytes data)[] calldata calls) external",
  "function getEthSignedMessageHash(address broker, uint256 nonce, bytes32 callsHash) view returns (bytes32)",
];

const BROKER_NONCE_ABI = [
  "function operatorNonces(address operator) view returns (uint256)",
];

const BROKER_ADD_LP_ABI = [
  "function addPoolLiquidity(address twammHook, int24 tickLower, int24 tickUpper, uint128 liquidity, uint128 amount0Max, uint128 amount1Max) external returns (uint256 tokenId)",
];

// Keep a small buffer so LP sizing stays below quoted swap output.
const SWAP_QUOTE_BUFFER_BPS = 9950n; // 99.50%

/**
 * Compute the token split for a concentrated LP position.
 * Given a deposit D (in terms of the selected token), price range, and current price,
 * returns { waUSDC: amount for LP, wRLP: amount for LP, swapNeeded: amount to swap }.
 */
function computeTokenSplit(deposit, minP, maxP, currentP, depositMode) {
  const sqrtPL = Math.sqrt(minP);
  const sqrtPU = Math.sqrt(maxP);
  const sqrtPC = Math.sqrt(currentP);

  // Compute ratio of each token per unit of liquidity
  let ratio0 = 0; // wRLP (token0)
  let ratio1 = 0; // waUSDC (token1)

  if (currentP <= minP) {
    ratio0 = 1 / sqrtPL - 1 / sqrtPU;
    ratio1 = 0;
  } else if (currentP >= maxP) {
    ratio0 = 0;
    ratio1 = sqrtPU - sqrtPL;
  } else {
    ratio0 = 1 / sqrtPC - 1 / sqrtPU;
    ratio1 = sqrtPC - sqrtPL;
  }

  // value0 = ratio0 * price (in USDC terms), value1 = ratio1 (already USDC)
  const value0 = ratio0 * currentP;
  const value1 = ratio1;
  const totalValue = value0 + value1;

  if (totalValue <= 0) return { waUSDC: 0, wRLP: 0, swapAmount: 0 };

  if (depositMode === "USDC") {
    // Deposit D USDC total
    const waUSDC_for_LP = deposit * (value1 / totalValue);
    const waUSDC_to_swap = deposit - waUSDC_for_LP;
    const wRLP_needed = waUSDC_to_swap / currentP;
    return { waUSDC: waUSDC_for_LP, wRLP: wRLP_needed, swapAmount: waUSDC_to_swap };
  } else {
    // Deposit D wRLP total
    const _depositUSD = deposit * currentP;
    const wRLP_for_LP = deposit * (value0 / totalValue);
    const wRLP_to_swap = deposit - wRLP_for_LP;
    const waUSDC_needed = wRLP_to_swap * currentP;
    return { waUSDC: waUSDC_needed, wRLP: wRLP_for_LP, swapAmount: wRLP_to_swap };
  }
}

function LpForm({ brokerAddress, marketInfo, account, addToast, currentRate, onStateChange, txPauseRef }) {
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [depositAmount, setDepositAmount] = useState("");
  const depositMode = "USDC";
  const [_removePercent, _setRemovePercent] = useState(100);
  const [lpExecuting, setLpExecuting] = useState(false);
  const [lpStep, setLpStep] = useState("");
  const [lpError, setLpError] = useState(null);

  const price = currentRate || 0;
  const positionToken = marketInfo?.position_token;
  const collateralToken = marketInfo?.collateral;
  const infrastructure = marketInfo?.infrastructure;
  const collateralSymbol = collateralToken?.symbol || "waUSDC";
  const positionSymbol =
    marketInfo?.positionToken?.symbol ||
    positionToken?.symbol ||
    "wRLP";
  const depositTokenSymbol = collateralSymbol;

  const {
    executeAddLiquidity,
    executeRemoveLiquidity: _executeRemoveLiquidity,
    activePosition,
    refreshPosition,
    executing: removeExecuting,
  } = usePoolLiquidity(brokerAddress, marketInfo);
  const executing = lpExecuting || removeExecuting;

  // Token ordering (V4 sorts by address)
  const token0IsPosition = positionToken && collateralToken
    ? positionToken.address.toLowerCase() < collateralToken.address.toLowerCase()
    : true;
  const _token0 = token0IsPosition
    ? { symbol: positionSymbol, decimals: 6 }
    : { symbol: collateralSymbol, decimals: 6 };
  const _token1 = token0IsPosition
    ? { symbol: collateralSymbol, decimals: 6 }
    : { symbol: positionSymbol, decimals: 6 };

  const applyPreset = (factor) => {
    if (price <= 0) return;
    if (factor === null) {
      setMinPrice("0.01");
      setMaxPrice((price * 10).toFixed(4));
    } else {
      setMinPrice((price * (1 - factor)).toFixed(4));
      setMaxPrice((price * (1 + factor)).toFixed(4));
    }
  };

  // Computed token split
  const split = useMemo(() => {
    const d = parseFloat(depositAmount) || 0;
    const pL = parseFloat(minPrice) || 0;
    const pU = parseFloat(maxPrice) || 0;
    if (d <= 0 || pL <= 0 || pU <= 0 || pL >= pU || price <= 0) return null;
    return computeTokenSplit(d, pL, pU, price, depositMode);
  }, [depositAmount, minPrice, maxPrice, price, depositMode]);

  const hasExecutor = !!infrastructure?.broker_executor;
  const canAdd = account && brokerAddress && split && split.swapAmount >= 0 &&
    (split.wRLP > 0 || split.waUSDC > 0);

  // Computed active position token amounts
  const _activeAmounts = useMemo(() => {
    if (!activePosition || !price) return null;
    const currentTick = Math.log(price) / Math.log(1.0001);
    return liquidityToAmounts(
      activePosition.liquidity,
      activePosition.tickLower,
      activePosition.tickUpper,
      currentTick,
    );
  }, [activePosition, price]);

  // ── Atomic one-click execution via BrokerExecutor ─────────────
  const executeAtomicLP = useCallback(async () => {
    if (!canAdd) return;
    setLpExecuting(true);
    if (txPauseRef) txPauseRef.current = true;
    setLpError(null);
    setLpStep("Computing token split...");

    try {
      const signer = await getSigner();
      const provider = signer.provider;
      const executorAddr = infrastructure.broker_executor;
      const routerAddr = infrastructure.broker_router;
      const hookAddr = infrastructure.twamm_hook;
      const tickSpacing = infrastructure.tick_spacing || 5;

      const poolKey = buildHooklessPoolKey(
        infrastructure,
        collateralToken.address,
        positionToken.address,
      );

      // Compute tick range
      const pL = parseFloat(minPrice);
      const pU = parseFloat(maxPrice);
      const tickLower = Math.floor(Math.log(pL) / Math.log(1.0001) / tickSpacing) * tickSpacing;
      const tickUpper = Math.floor(Math.log(pU) / Math.log(1.0001) / tickSpacing) * tickSpacing;

      // Compute amounts in raw units (6 decimals)
      const wRLP_raw = ethers.parseUnits(split.wRLP.toFixed(6), 6);
      const waUSDC_raw = ethers.parseUnits(split.waUSDC.toFixed(6), 6);
      const swapAmount_raw = ethers.parseUnits(split.swapAmount.toFixed(6), 6);

      if (swapAmount_raw > 0n && depositMode !== "USDC") {
        throw new Error(`Atomic LP for ${positionSymbol} input is not implemented yet`);
      }

      // Size LP from a conservative swap quote to avoid over-asking wRLP.
      let positionForLp_raw = wRLP_raw;
      let swapMinOut_raw = 0n;
      if (swapAmount_raw > 0n) {
        setLpStep("Quoting swap output...");
        try {
          const routerForQuote = new ethers.Contract(routerAddr, BROKER_ROUTER_LONG_ABI, signer);
          const quotedOut = await routerForQuote.executeLong.staticCall(
            brokerAddress,
            swapAmount_raw,
            poolKey,
            0n,
          );
          const bufferedOut = (quotedOut * SWAP_QUOTE_BUFFER_BPS) / 10_000n;
          if (bufferedOut <= 0n) throw new Error("Swap quote returned zero output");
          swapMinOut_raw = bufferedOut;
          if (bufferedOut < positionForLp_raw) {
            positionForLp_raw = bufferedOut;
          }
        } catch (quoteErr) {
          console.warn("[LP] Swap quote failed, applying fallback haircut:", quoteErr);
          positionForLp_raw = (positionForLp_raw * SWAP_QUOTE_BUFFER_BPS) / 10_000n;
          swapMinOut_raw = positionForLp_raw;
        }
      }

      // Compute liquidity from amounts
      const currentTick = Math.log(price) / Math.log(1.0001);
      // Map to token0/token1 order
      const amt0_raw = token0IsPosition ? positionForLp_raw : waUSDC_raw;
      const amt1_raw = token0IsPosition ? waUSDC_raw : positionForLp_raw;
      const liquidity = computeLiquidity(Number(amt0_raw), Number(amt1_raw), tickLower, tickUpper, currentTick);

      if (liquidity <= 0n) throw new Error("Computed liquidity is zero — increase amount");

      // Build Call[] array for BrokerExecutor
      const calls = [];

      // Call 1: Swap (if swap needed)
      if (swapAmount_raw > 0n) {
        if (depositMode === "USDC") {
          // executeLong: swap waUSDC → wRLP
          const swapData = encodeExecuteLongCalldata(
            brokerAddress,
            swapAmount_raw,
            poolKey,
            swapMinOut_raw,
          );
          calls.push({ target: routerAddr, data: swapData });
        } else {
          throw new Error(`Atomic LP for ${positionSymbol} input is not implemented yet`);
        }
      }

      // Call 2: addPoolLiquidity
      const brokerIface = new ethers.Interface(BROKER_ADD_LP_ABI);
      const slippage = 3n; // 3× slippage for simulation
      const a0Max = amt0_raw > 0n ? amt0_raw * slippage : ethers.MaxUint256;
      const a1Max = amt1_raw > 0n ? amt1_raw * slippage : ethers.MaxUint256;
      const lpData = brokerIface.encodeFunctionData("addPoolLiquidity", [
        hookAddr, tickLower, tickUpper, liquidity, a0Max, a1Max,
      ]);
      calls.push({ target: brokerAddress, data: lpData });

      // Get nonce for executor
      setLpStep("Preparing signature...");
      const brokerContract = new ethers.Contract(brokerAddress, BROKER_NONCE_ABI, provider);
      const nonce = await brokerContract.operatorNonces(executorAddr);

      // Compute calls hash (matches BrokerExecutor.sol encoding)
      const callsTupleType = "tuple(address target, bytes data)[]";
      const callsHash = ethers.keccak256(
        ethers.AbiCoder.defaultAbiCoder().encode(
          [callsTupleType],
          [calls.map(c => [c.target, c.data])],
        ),
      );

      // Get raw message hash (without EIP-191 prefix)
      // signer.signMessage will add EIP-191 prefix, matching the contract's ecrecover
      const executorForHash = new ethers.Contract(executorAddr, [
        ...BROKER_EXECUTOR_ABI,
        "function getMessageHash(address broker, uint256 nonce, bytes32 callsHash) view returns (bytes32)",
      ], provider);
      const rawMsgHash = await executorForHash.getMessageHash(brokerAddress, nonce, callsHash);

      setLpStep("Sign authorization in wallet...");
      const ownerSignature = await signer.signMessage(ethers.getBytes(rawMsgHash));

      // Execute atomically
      setLpStep("Executing swap + LP atomically...");
      const executorSigned = new ethers.Contract(executorAddr, BROKER_EXECUTOR_ABI, signer);
      const tx = await executorSigned.execute(
        brokerAddress,
        ownerSignature,
        calls,
        { gasLimit: 3_000_000 },
      );

      setLpStep("Waiting for confirmation...");
      await tx.wait();

      // Refresh position
      await refreshPosition();

      setLpStep("Liquidity added ✓");
      setDepositAmount("");
      addToast({ type: "success", title: "Liquidity Added", message: "Swap + LP executed atomically", duration: 5000 });
      onStateChange?.();
    } catch (err) {
      console.error("[LP] Atomic execution failed:", err);
      const reason = err?.revert?.args?.[0]
        || err?.info?.error?.data?.message
        || err?.info?.error?.message
        || err?.reason
        || err?.shortMessage
        || err?.message
        || "Transaction failed";
      setLpError(reason);
    } finally {
      setLpExecuting(false);
      if (txPauseRef) txPauseRef.current = false;
    }
  }, [canAdd, infrastructure, brokerAddress, collateralToken, positionToken, minPrice, maxPrice, split, price, token0IsPosition, depositMode, addToast, refreshPosition, onStateChange, txPauseRef, positionSymbol]);

  return (
    <div className="flex flex-col gap-4">


      {/* Lower / Upper price inputs */}
      <InputGroup
        label="Lower"
        subLabel={price > 0 ? `Curr: ${price.toFixed(4)}` : ""}
        value={minPrice}
        onChange={setMinPrice}
        suffix=""
        placeholder="0.00"
      />
      <InputGroup
        label="Upper"
        value={maxPrice}
        onChange={setMaxPrice}
        suffix=""
        placeholder="0.00"
      />

      {/* Preset buttons */}
      <div className="flex gap-1">
        {RANGE_PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => applyPreset(p.factor)}
            className="flex-1 py-1.5 text-xs font-bold tracking-widest uppercase border border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20 transition-colors"
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Deposit input with market collateral token */}
      <div className="flex items-center justify-between text-sm uppercase tracking-widest font-bold text-gray-500">
        <span>Deposit_In</span>
        <div
          className="
            h-[28px] border border-white/10 bg-[#0a0a0a] flex items-center px-2
            text-sm font-mono text-white uppercase tracking-widest
          "
        >
          {depositTokenSymbol}
        </div>
      </div>
      <InputGroup
        label={depositTokenSymbol}
        value={depositAmount}
        onChange={setDepositAmount}
        suffix={depositTokenSymbol}
        placeholder="0.00"
      />

      {/* Summary */}
      <div className="border-t border-white/10 pt-3 space-y-2">
        <SummaryRow
          label="Price Range"
          value={minPrice && maxPrice ? `${Number(minPrice).toFixed(2)} — ${Number(maxPrice).toFixed(2)}` : "—"}
        />
        <SummaryRow label="Fee Tier" value="0.05%" />
        <SummaryRow
          label="Projected APY"
          value={
            minPrice && maxPrice && Number(minPrice) > 0 && Number(maxPrice) > Number(minPrice)
              ? (() => {
                  const ticks = Math.abs(
                    Math.round(Math.log(Number(maxPrice)) / Math.log(1.0001)) -
                    Math.round(Math.log(Number(minPrice)) / Math.log(1.0001))
                  );
                  return ticks > 0 ? `${((887272 * 2 / ticks) * 0.05).toFixed(1)}%` : "—";
                })()
              : "—"
          }
          valueColor="text-green-400"
        />
      </div>

      {/* Execution feedback */}
      {lpStep && (
        <div className="text-xs text-gray-400 font-mono animate-pulse">
          {lpStep}
        </div>
      )}
      {lpError && (
        <div className="text-xs text-red-400 font-mono truncate">
          {lpError}
        </div>
      )}

      <button
        onClick={() => {
          setLpError(null);
          if (hasExecutor) {
            executeAtomicLP();
          } else {
            // Direct LP fallback (no swap, just addPoolLiquidity)
            const pL = parseFloat(minPrice);
            const pU = parseFloat(maxPrice);
            executeAddLiquidity(pL, pU, String(split?.wRLP || 0), String(split?.waUSDC || 0), price, () => {
              setDepositAmount("");
              addToast({ type: "success", title: "Liquidity Added", message: "LP position created", duration: 5000 });
              onStateChange?.();
            });
          }
        }}
        disabled={!canAdd || executing}
        className={`w-full py-3 text-sm font-bold tracking-[0.2em] uppercase transition-all bg-cyan-500 text-black hover:bg-cyan-400 ${
          !canAdd || executing ? "opacity-50 cursor-not-allowed" : ""
        }`}
      >
        {executing ? lpStep || "Processing..." : "Provide Liquidity"}
      </button>
    </div>
  );
}

import { BatchForm, LoopForm } from "./action-forms/PassiveForms";

/* ── ActionForm Router ────────────────────────────────────────── */
export default function ActionForm({ type, brokerBalance, currentRate, brokerAddress, marketId, account, addToast, marketInfo, onStateChange, onTwammRefresh, txPauseRef }) {
  const forms = {
    mint: <MintForm brokerBalance={brokerBalance} currentRate={currentRate} brokerAddress={brokerAddress} marketId={marketId} account={account} addToast={addToast} onStateChange={onStateChange} txPauseRef={txPauseRef} />,
    twap: <TwapForm brokerAddress={brokerAddress} marketInfo={marketInfo} account={account} addToast={addToast} onTwammRefresh={onTwammRefresh} txPauseRef={txPauseRef} />,
    lp: <LpForm brokerAddress={brokerAddress} marketInfo={marketInfo} account={account} addToast={addToast} currentRate={currentRate} onStateChange={onStateChange} txPauseRef={txPauseRef} />,
    loop: <LoopForm />,
    batch: <BatchForm />,
  };

  return (
    <div className="p-4">
      {forms[type] || null}
    </div>
  );
}
