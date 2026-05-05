import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { getSigner } from "../utils/connection";
import { rpcProvider } from "../utils/provider";
import {
  buildHooklessPoolKey,
  buildHooklessPoolKeyArray,
} from "../lib/peripheryIntegration";
import { debugLog } from "../utils/debugLogger";

// ── ABI fragments ─────────────────────────────────────────────────

const BOND_FACTORY_ABI = [
  "error BondMintPreview(uint256 hedgeProceeds)",
  "error BondClosePreview(uint256 debtRepayCollateralIn, uint256 collateralReturned)",
  "function mintBond(uint256 notional, uint256 hedgeAmount, uint256 duration, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey, bool useUnderlying, uint256 minHedgeProceeds) returns (address broker)",
  "function previewMintBond(uint256 notional, uint256 hedgeAmount, uint256 duration, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey, bool useUnderlying)",
  "function closeBond(address broker, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey, bool useUnderlying, uint256 maxDebtRepayCollateralIn, uint256 minCollateralReturned)",
  "function previewCloseBond(address broker, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey, bool useUnderlying)",
  "event BondMinted(address indexed user, address indexed broker, uint256 notional, uint256 hedge, uint256 duration)",
  "event BondClosed(address indexed user, address indexed broker, uint256 collateralReturned, uint256 positionReturned)",
];

const WRAPPED_ATOKEN_ABI = [
  "function aToken() view returns (address)",
];

const ATOKEN_ABI = [
  "function UNDERLYING_ASSET_ADDRESS() view returns (address)",
];

const ERC20_ABI = [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function balanceOf(address owner) view returns (uint256)",
];

const BOND_FACTORY_IFACE = new ethers.Interface(BOND_FACTORY_ABI);

function extractRevertData(error) {
  const candidates = [
    error?.data,
    error?.error?.data,
    error?.info?.error?.data,
    error?.info?.error?.error?.data,
    error?.revert?.data,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.startsWith("0x")) return value;
  }
  return null;
}

function parseKnownBondError(error) {
  const data = extractRevertData(error);
  if (!data) return null;
  try {
    return BOND_FACTORY_IFACE.parseError(data)?.name || null;
  } catch {
    return null;
  }
}

function formatRaw6(rawAmount) {
  return Number(ethers.formatUnits(BigInt(rawAmount || 0), 6)).toLocaleString(
    undefined,
    { maximumFractionDigits: 6 },
  );
}

function mintSlippageMessage(previewAmount, minAmount) {
  const preview = formatRaw6(previewAmount);
  const minimum = formatRaw6(minAmount);
  return `Bond hedge route slippage exceeded. Previewed ${preview} waUSDC with minimum ${minimum} waUSDC. Increase Max_Slippage and retry.`;
}

function parseBondPreview(error, expectedName) {
  if (error?.revert?.name === expectedName) {
    return error.revert.args;
  }
  const data = extractRevertData(error);
  if (!data) return null;
  try {
    const parsed = BOND_FACTORY_IFACE.parseError(data);
    if (parsed?.name === expectedName) return parsed.args;
  } catch {
    // Not a BondFactory preview payload.
  }
  return null;
}

async function readBondPreview(callPreview, expectedName) {
  try {
    await callPreview();
  } catch (error) {
    const parsed = parseBondPreview(error, expectedName);
    if (parsed) return parsed;
    throw error;
  }
  throw new Error("Bond preview did not return a preview payload");
}

function slippageBps(maxSlippage) {
  const parsed = Number(maxSlippage);
  if (!Number.isFinite(parsed) || parsed < 0) return 10;
  return Math.min(5000, Math.round(parsed * 100));
}

function minWithSlippage(rawAmount, maxSlippage) {
  const bps = BigInt(slippageBps(maxSlippage));
  return (BigInt(rawAmount) * (10_000n - bps)) / 10_000n;
}

function maxWithSlippage(rawAmount, maxSlippage) {
  const raw = BigInt(rawAmount);
  if (raw === 0n) return 0n;
  const bps = BigInt(slippageBps(maxSlippage));
  return (raw * (10_000n + bps) + 9_999n) / 10_000n;
}

function assertRuntimeReady(infrastructure) {
  if (
    infrastructure?.runtime_ready !== false &&
    infrastructure?.runtimeReady !== false
  ) {
    return;
  }
  const reasons =
    infrastructure?.runtimeReadiness?.reasons ||
    infrastructure?.runtime_readiness?.reasons ||
    [];
  throw new Error(
    reasons.length
      ? `Runtime not ready: ${reasons.join(", ")}`
      : "Runtime manifest is not ready",
  );
}

// ── Hook ──────────────────────────────────────────────────────────

/**
 * useBondExecution — Create and close bonds via BondFactory (single TX each).
 *
 * Bond creation:
 *   1. Ensure waUSDC approval for BondFactory
 *   2. bondFactory.mintBond() → creates broker, short, TWAMM, freezes, mints NFT
 *
 * Bond close:
 *   1. Ensure NFT approval for BondFactory
 *   2. bondFactory.closeBond() → unfreezes, unwinds TWAMM, repays debt, withdraws
 *
 * @param {string} account           Connected wallet address
 * @param {object} infrastructure    { bond_factory, broker_factory, broker_router, pool_fee, tick_spacing }
 * @param {string} collateralAddr    waUSDC address
 * @param {string} positionAddr      wRLP address
 */
export function useBondExecution(
  account,
  infrastructure,
  collateralAddr,
  positionAddr,
  { onRefreshComplete = [], pauseRef = null } = {},
) {
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState("");
  const [txHash, setTxHash] = useState(null);

   
  const _syncAndNotify = useCallback(async (successStep, onSuccess, result) => {
    setStep("Syncing...");
    await Promise.all(onRefreshComplete.map(fn => fn?.()).filter(Boolean));
    setStep(successStep);
    if (onSuccess) onSuccess(result);
  }, [onRefreshComplete]);

  /**
   * Create a bond in a single transaction.
   *
   * @param {number} notionalUSD    Bond notional in USD
   * @param {number} durationHours  Bond duration in hours (>= 1)
   * @param {number} ratePercent    Entry rate (e.g. 5.25)
   * @param {Function} onSuccess    Called with { receipt, brokerAddress }
   */
  const createBond = useCallback(
    async (
      notionalUSD,
      durationHours,
	      ratePercent,
	      onSuccess,
	      { useUnderlying = true, maxSlippage = 5 } = {},
	    ) => {
      if (
        !account ||
        !collateralAddr ||
        !positionAddr
      ) {
        setError("Missing parameters");
        return;
      }

      // Bond factory address (from indexer API — no fallback)
      const bondFactoryAddr = infrastructure?.bond_factory;

      if (!bondFactoryAddr) {
        setError("Bond factory not available — waiting for config");
        return;
      }

      setExecuting(true);
      if (pauseRef) pauseRef.current = true;
      setError(null);
      setStep("Preparing...");

	      let previewHedgeProceeds = null;
	      let minHedgeProceeds = null;

	      try {
        // ── Direct RPC provider for read-only calls ─────────────
        // Uses the Vite-proxied RPC (/rpc → Anvil) to avoid MetaMask
        // RPC issues after simulation restarts.
        assertRuntimeReady(infrastructure);
        const readProvider = rpcProvider;

        // ── Build pool key ──────────────────────────────────────
        const poolKey = buildHooklessPoolKey(
          infrastructure,
          collateralAddr,
          positionAddr,
        );

        // ── Compute debt amount (wRLP tokens to mint) ────────────
        // 1. Yield in USD = notional × rate × pro-rata duration
        // 2. wRLP tokens = yield / markPrice  (markPrice ≈ ratePercent in RLD)
        // Floor: at least 0.5 wRLP to avoid dust amounts
        const markPrice = ratePercent;  // In RLD, mark price ≈ APY%
        const yieldUSD = notionalUSD * (ratePercent / 100) * (durationHours / 8760);
        const debtWRLP = Math.max(
          yieldUSD / markPrice,
          0.5,  // minimum 0.5 wRLP
        );
        const notionalWei = ethers.parseUnits(notionalUSD.toString(), 6);
        const debtWei = ethers.parseUnits(debtWRLP.toFixed(6), 6);
        // Self-funding: user pays only notional (swap proceeds fund TWAMM)
        const totalWei = notionalWei;

        debugLog("[Bond] Notional:", notionalUSD, "Yield:", yieldUSD.toFixed(2),
                     "Debt:", debtWRLP.toFixed(6), "wRLP (mark:", markPrice, ")");

        // ── Determine which token to approve ─────────────────────
        let approveTokenAddr = collateralAddr; // default: waUSDC
        let approveLabel = "waUSDC";

        if (useUnderlying) {
          // Derive USDC address from WrappedAToken chain (read-only, use direct RPC)
          try {
            const wrapper = new ethers.Contract(collateralAddr, WRAPPED_ATOKEN_ABI, readProvider);
            const aTokenAddr = await wrapper.aToken();
            const aToken = new ethers.Contract(aTokenAddr, ATOKEN_ABI, readProvider);
            approveTokenAddr = await aToken.UNDERLYING_ASSET_ADDRESS();
            approveLabel = "USDC";
            debugLog("[Bond] Using underlying:", approveTokenAddr);
          } catch (e) {
            console.warn("[Bond] Failed to derive underlying, falling back to waUSDC", e);
          }
        }

        // ── Ensure approval (read-only check via direct RPC) ────
        setStep("Checking balance...");
        const tokenReader = new ethers.Contract(approveTokenAddr, ERC20_ABI, readProvider);

        // Pre-flight balance check
        const balance = await tokenReader.balanceOf(account);
        if (balance < totalWei) {
          const have = Number(ethers.formatUnits(balance, 6)).toFixed(2);
          const need = Number(ethers.formatUnits(totalWei, 6)).toFixed(2);
          setError(
            `Insufficient ${approveLabel} — need $${need}, have $${have}`,
          );
          setExecuting(false);
          return;
        }

        setStep("Checking approval...");
        const allowance = await tokenReader.allowance(account, bondFactoryAddr);

        // ── Get signer only when needed for transactions ────────
        let signer;
        if (allowance < totalWei) {
          signer = await getSigner();
          setStep(`Approve ${approveLabel} for BondFactory...`);
          const tokenToApprove = new ethers.Contract(approveTokenAddr, ERC20_ABI, signer);
          const approveTx = await tokenToApprove.approve(
            bondFactoryAddr,
            ethers.MaxUint256,
          );
          await approveTx.wait();
          debugLog(`[Bond] Approved BondFactory for ${approveLabel}`);
        }

        // ── Mint bond (single TX) ───────────────────────────────
        if (!signer) {
          signer = await getSigner();
        }
        setStep("Minting bond...");
        const bondFactory = new ethers.Contract(
          bondFactoryAddr,
          BOND_FACTORY_ABI,
          signer,
        );

        const durationSec = Math.floor(durationHours * 3600);
        const poolKeyArr = [
          poolKey.currency0,
          poolKey.currency1,
          poolKey.fee,
          poolKey.tickSpacing,
          poolKey.hooks,
        ];

        debugLog("[Bond] mintBond params:", {
          notionalWei: notionalWei.toString(),
          debtWei: debtWei.toString(),
          durationSec,
          poolKey,
        });

        setStep("Previewing bond route...");
        const mintPreview = await readBondPreview(
          () => bondFactory.previewMintBond.staticCall(
            notionalWei,
            debtWei,
            durationSec,
            poolKeyArr,
            useUnderlying,
          ),
          "BondMintPreview",
        );
	        previewHedgeProceeds = BigInt(mintPreview[0]);
	        minHedgeProceeds = minWithSlippage(
	          previewHedgeProceeds,
	          maxSlippage,
	        );

        const tx = await bondFactory.mintBond(
          notionalWei,
          debtWei,
          durationSec,
          poolKeyArr,
          useUnderlying,
          minHedgeProceeds,
          { gasLimit: 30_000_000 },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();
        debugLog(`[MintBond] Gas used: ${receipt.gasUsed.toString()}`);

        if (receipt.status === 1) {
          // Parse BondMinted event for broker address
          let brokerAddress = null;
          const iface = new ethers.Interface(BOND_FACTORY_ABI);
          for (const log of receipt.logs) {
            try {
              const parsed = iface.parseLog({
                topics: log.topics,
                data: log.data,
              });
              if (parsed?.name === "BondMinted") {
                brokerAddress = parsed.args.broker;
                break;
              }
            } catch {
              // Not our event
            }
          }

          // Save bond metadata to localStorage
          if (brokerAddress) {
            try {
              const bondMeta = {
                notionalUSD,
                ratePercent,
                durationHours,
                createdAt: Date.now(),
                txHash: receipt.hash,
                brokerAddress,
              };
              const key = `rld_bond_${brokerAddress.toLowerCase()}`;
              localStorage.setItem(key, JSON.stringify(bondMeta));

              // Also save to bond list for enumeration
              const listKey = `rld_bonds_${account.toLowerCase()}`;
              const existing = JSON.parse(localStorage.getItem(listKey) || "[]");
              if (!existing.includes(brokerAddress.toLowerCase())) {
                existing.push(brokerAddress.toLowerCase());
                localStorage.setItem(listKey, JSON.stringify(existing));
              }
            } catch { /* ignore localStorage errors */ }
          }

          await _syncAndNotify("Bond created ✓", onSuccess, { ...receipt, brokerAddress });
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[Bond] createBond failed:", e);
        let msg = "Bond creation failed";
	        const parsedError = parseKnownBondError(e);
	        if (parsedError === "SlippageExceeded" && previewHedgeProceeds !== null) {
	          msg = mintSlippageMessage(previewHedgeProceeds, minHedgeProceeds);
	        } else if (e.receipt?.status === 0 && previewHedgeProceeds !== null) {
	          msg = mintSlippageMessage(previewHedgeProceeds, minHedgeProceeds);
	        } else if (e.reason) msg = e.reason;
	        else if (e.message?.includes("user rejected")) msg = "User rejected";
        else if (e.data) {
          try {
            msg = ethers.toUtf8String("0x" + e.data.slice(138));
          } catch { /* ignore decode errors */ }
        }
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
        if (pauseRef) pauseRef.current = false;

      }
    },
    [account, infrastructure, collateralAddr, positionAddr, _syncAndNotify, pauseRef],
  );

  /**
   * Close a bond in a single transaction via BondFactory.closeBond().
   *
   * Flow: approve NFT → bondFactory.closeBond(broker, poolKey)
   * The contract atomically: unfreezes, handles TWAMM, repays debt, withdraws.
   *
   * @param {string}   brokerAddress  The bond's PrimeBroker clone address
   * @param {Function} onSuccess      Called with { brokerAddress } on completion
   */
  const closeBond = useCallback(
    async (
	      brokerAddress,
	      onSuccess,
	      { useUnderlying = true, maxSlippage = 5 } = {},
	    ) => {
      if (!account || !brokerAddress) {
        setError("Missing parameters");
        return;
      }

      const bondFactoryAddr = infrastructure?.bond_factory;
      const brokerFactoryAddr = infrastructure?.broker_factory;

      if (!bondFactoryAddr || !brokerFactoryAddr) {
        setError("Bond factory not available — waiting for config");
        return;
      }

      setExecuting(true);
      if (pauseRef) pauseRef.current = true;
      setError(null);
      setStep("Preparing...");

      try {
        assertRuntimeReady(infrastructure);
        const signer = await getSigner();

        // ── 1. Build pool key ─────────────────────────────────────
        const poolKeyArr = buildHooklessPoolKeyArray(
          infrastructure,
          collateralAddr,
          positionAddr,
        );

        // ── 2. Close bond (single TX, no approval needed) ──────────
        setStep("Closing bond...");
        const bondFactory = new ethers.Contract(
          bondFactoryAddr,
          BOND_FACTORY_ABI,
          signer,
        );

        setStep("Previewing close route...");
        const closePreview = await readBondPreview(
          () => bondFactory.previewCloseBond.staticCall(
            brokerAddress,
            poolKeyArr,
            useUnderlying,
          ),
          "BondClosePreview",
        );
        const maxDebtRepayCollateralIn = maxWithSlippage(
          closePreview[0],
          maxSlippage,
        );
        const minCollateralReturned = minWithSlippage(
          closePreview[1],
          maxSlippage,
        );

        const tx = await bondFactory.closeBond(
          brokerAddress,
          poolKeyArr,
          useUnderlying,
          maxDebtRepayCollateralIn,
          minCollateralReturned,
          {
            gasLimit: 25_000_000,
          },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();
        debugLog(`[CloseBond] Gas used: ${receipt.gasUsed.toString()}`);

        if (receipt.status === 1) {
          // Parse BondClosed event for return amounts
          const iface = new ethers.Interface(BOND_FACTORY_ABI);
          for (const log of receipt.logs) {
            try {
              const parsed = iface.parseLog({
                topics: log.topics,
                data: log.data,
              });
              if (parsed?.name === "BondClosed") {
                const collReturned = ethers.formatUnits(parsed.args.collateralReturned, 6);
                debugLog("[CloseBond] Returned:", collReturned, "waUSDC");
                break;
              }
            } catch { /* not our event */ }
          }

          // Clean up localStorage
          try {
            const listKey = `rld_bonds_${account.toLowerCase()}`;
            const existing = JSON.parse(localStorage.getItem(listKey) || "[]");
            const filtered = existing.filter(
              (a) => a.toLowerCase() !== brokerAddress.toLowerCase(),
            );
            localStorage.setItem(listKey, JSON.stringify(filtered));
            localStorage.removeItem(`rld_bond_${brokerAddress.toLowerCase()}`);
          } catch { /* ignore localStorage errors */ }

          await _syncAndNotify("Bond closed ✓", onSuccess, { brokerAddress });
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[CloseBond] failed:", e);
        let msg = "Close bond failed";
        if (e.reason) msg = e.reason;
        else if (e.message?.includes("user rejected")) msg = "User rejected";
        else if (e.message?.includes("revert")) {
          const match = e.message.match(/reason="([^"]+)"/);
          if (match) msg = match[1];
        }
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
        if (pauseRef) pauseRef.current = false;

      }
    },
    [account, infrastructure, collateralAddr, positionAddr, _syncAndNotify, pauseRef],
  );

  return {
    createBond,
    closeBond,
    executing,
    error,
    step,
    txHash,
  };
}
