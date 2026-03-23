import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";
import { rpcProvider } from "../utils/provider";

// ── ABI fragments ─────────────────────────────────────────────────

const BOND_FACTORY_ABI = [
  "function mintBond(uint256 notional, uint256 hedgeAmount, uint256 duration, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey, bool useUnderlying) returns (address broker)",
  "function closeBond(address broker, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey, bool useUnderlying)",
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
 * @param {object} infrastructure    { bond_factory, broker_factory, broker_router, twamm_hook, pool_fee, tick_spacing }
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
    async (notionalUSD, durationHours, ratePercent, onSuccess, { useUnderlying = true } = {}) => {
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

      if (!bondFactoryAddr || !infrastructure?.twamm_hook) {
        setError("Bond factory not available — waiting for config");
        return;
      }

      setExecuting(true);
      if (pauseRef) pauseRef.current = true;
      setError(null);
      setStep("Preparing...");

      try {
        // ── Direct RPC provider for read-only calls ─────────────
        // Uses the Vite-proxied RPC (/rpc → Anvil) to avoid MetaMask
        // RPC issues after simulation restarts.
        const readProvider = rpcProvider;

        // ── Build pool key ──────────────────────────────────────
        const sorted = positionAddr.toLowerCase() < collateralAddr.toLowerCase();
        const poolKey = {
          currency0: sorted ? positionAddr : collateralAddr,
          currency1: sorted ? collateralAddr : positionAddr,
          fee: infrastructure.pool_fee || 500,
          tickSpacing: infrastructure.tick_spacing || 5,
          hooks: infrastructure.twamm_hook,
        };

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

        console.log("[Bond] Notional:", notionalUSD, "Yield:", yieldUSD.toFixed(2),
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
            console.log("[Bond] Using underlying:", approveTokenAddr);
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
          setStep("Syncing chain ID...");
          signer = await getAnvilSigner();
          setStep(`Approve ${approveLabel} for BondFactory...`);
          const tokenToApprove = new ethers.Contract(approveTokenAddr, ERC20_ABI, signer);
          const approveTx = await tokenToApprove.approve(
            bondFactoryAddr,
            ethers.MaxUint256,
          );
          await approveTx.wait();
          console.log(`[Bond] Approved BondFactory for ${approveLabel}`);
        }

        // ── Mint bond (single TX) ───────────────────────────────
        if (!signer) {
          setStep("Syncing chain ID...");
          signer = await getAnvilSigner();
        }
        setStep("Minting bond...");
        const bondFactory = new ethers.Contract(
          bondFactoryAddr,
          BOND_FACTORY_ABI,
          signer,
        );

        const durationSec = Math.floor(durationHours * 3600);

        console.log("[Bond] mintBond params:", {
          notionalWei: notionalWei.toString(),
          debtWei: debtWei.toString(),
          durationSec,
          poolKey,
        });

        const tx = await bondFactory.mintBond(
          notionalWei,
          debtWei,
          durationSec,
          [
            poolKey.currency0,
            poolKey.currency1,
            poolKey.fee,
            poolKey.tickSpacing,
            poolKey.hooks,
          ],
          useUnderlying,
          { gasLimit: 30_000_000 },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();
        console.log(`[MintBond] Gas used: ${receipt.gasUsed.toString()}`);

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
        if (e.reason) msg = e.reason;
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
        try { await restoreAnvilChainId(); } catch { /* ignore */ }
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
    async (brokerAddress, onSuccess, { useUnderlying = true } = {}) => {
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

      if (!infrastructure?.twamm_hook) {
        setError("Missing infrastructure");
        return;
      }

      setExecuting(true);
      if (pauseRef) pauseRef.current = true;
      setError(null);
      setStep("Preparing...");

      try {
        const signer = await getAnvilSigner();

        // ── 1. Build pool key ─────────────────────────────────────
        const sorted = positionAddr.toLowerCase() < collateralAddr.toLowerCase();
        const poolKeyArr = [
          sorted ? positionAddr : collateralAddr,
          sorted ? collateralAddr : positionAddr,
          infrastructure.pool_fee || 500,
          infrastructure.tick_spacing || 5,
          infrastructure.twamm_hook,
        ];

        // ── 2. Close bond (single TX, no approval needed) ──────────
        setStep("Closing bond...");
        const bondFactory = new ethers.Contract(
          bondFactoryAddr,
          BOND_FACTORY_ABI,
          signer,
        );

        const tx = await bondFactory.closeBond(brokerAddress, poolKeyArr, useUnderlying, {
          gasLimit: 25_000_000,
        });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();
        console.log(`[CloseBond] Gas used: ${receipt.gasUsed.toString()}`);

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
                console.log("[CloseBond] Returned:", collReturned, "waUSDC");
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
        try { await restoreAnvilChainId(); } catch { /* ignore */ }
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

