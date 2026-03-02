import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { RPC_URL, getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";

// ── ABI fragments ─────────────────────────────────────────────────

const BOND_FACTORY_ABI = [
  "function mintBond(uint256 notional, uint256 hedgeAmount, uint256 duration, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey) returns (address broker)",
  "event BondMinted(address indexed user, address indexed broker, uint256 notional, uint256 hedge, uint256 duration)",
];

const ERC20_ABI = [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
];

// ── Hook ──────────────────────────────────────────────────────────

/**
 * useBondExecution — Create bonds via BondFactory (single TX).
 *
 * Bond creation flow:
 *   1. Ensure waUSDC approval for BondFactory
 *   2. Call bondFactory.mintBond(notional, hedge, duration, poolKey)
 *      → Creates broker, funds it, opens short, TWAMM, freezes, transfers NFT
 *   3. Parse BondMinted event for broker address
 *
 * @param {string} account           Connected wallet address
 * @param {object} infrastructure    { bond_factory, twamm_hook, pool_fee, tick_spacing }
 * @param {string} collateralAddr    waUSDC address
 * @param {string} positionAddr      wRLP address
 */
export function useBondExecution(
  account,
  infrastructure,
  collateralAddr,
  positionAddr,
) {
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState("");
  const [txHash, setTxHash] = useState(null);

  /**
   * Create a bond in a single transaction.
   *
   * @param {number} notionalUSD    Bond notional in USD
   * @param {number} durationHours  Bond duration in hours (>= 1)
   * @param {number} ratePercent    Entry rate (e.g. 5.25)
   * @param {Function} onSuccess    Called with { receipt, brokerAddress }
   */
  const createBond = useCallback(
    async (notionalUSD, durationHours, ratePercent, onSuccess) => {
      if (
        !account ||
        !collateralAddr ||
        !positionAddr
      ) {
        setError("Missing parameters");
        return;
      }

      // Bond factory address (from API or fallback)
      const bondFactoryAddr = infrastructure?.bond_factory
        || "0x0a5fF8eAE2104805E18a2F3646776d577Fc9Cf26";

      if (!infrastructure?.twamm_hook) {
        setError("Missing infrastructure");
        return;
      }

      setExecuting(true);
      setError(null);
      setStep("Preparing...");

      try {
        // ── Get signer ──────────────────────────────────────────
        setStep("Syncing chain ID...");
        const signer = await getAnvilSigner();

        // ── Build pool key ──────────────────────────────────────
        const sorted = positionAddr.toLowerCase() < collateralAddr.toLowerCase();
        const poolKey = {
          currency0: sorted ? positionAddr : collateralAddr,
          currency1: sorted ? collateralAddr : positionAddr,
          fee: infrastructure.pool_fee || 500,
          tickSpacing: infrastructure.tick_spacing || 5,
          hooks: infrastructure.twamm_hook,
        };

        // ── Compute amounts ─────────────────────────────────────
        // Hedge amount = notional × rate × duration / 8760
        // Minimum: max(1% of notional, $1) to avoid TWAMM sell rate underflow
        const hedgeUSD = Math.max(
          notionalUSD * (ratePercent / 100) * (durationHours / 8760),
          notionalUSD * 0.01,  // at least 1% of notional
          1.0,                 // at least $1
        );
        const notionalWei = ethers.parseUnits(notionalUSD.toString(), 6);
        const hedgeWei = ethers.parseUnits(hedgeUSD.toFixed(6), 6);
        const totalWei = notionalWei + hedgeWei;

        console.log("[Bond] Notional:", notionalUSD, "Hedge:", hedgeUSD.toFixed(6));

        // ── Ensure approval ─────────────────────────────────────
        setStep("Checking approval...");
        const collateral = new ethers.Contract(collateralAddr, ERC20_ABI, signer);
        const allowance = await collateral.allowance(account, bondFactoryAddr);

        if (allowance < totalWei) {
          setStep("Approve waUSDC for BondFactory...");
          const approveTx = await collateral.approve(
            bondFactoryAddr,
            ethers.MaxUint256,
          );
          await approveTx.wait();
          console.log("[Bond] Approved BondFactory");
        }

        // ── Mint bond (single TX) ───────────────────────────────
        setStep("Minting bond...");
        const bondFactory = new ethers.Contract(
          bondFactoryAddr,
          BOND_FACTORY_ABI,
          signer,
        );

        const durationSec = Math.floor(durationHours * 3600);

        console.log("[Bond] mintBond params:", {
          notionalWei: notionalWei.toString(),
          hedgeWei: hedgeWei.toString(),
          durationSec,
          poolKey,
        });

        const tx = await bondFactory.mintBond(
          notionalWei,
          hedgeWei,
          durationSec,
          [
            poolKey.currency0,
            poolKey.currency1,
            poolKey.fee,
            poolKey.tickSpacing,
            poolKey.hooks,
          ],
          { gasLimit: 10_000_000 },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

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
            } catch {}
          }

          setStep("Bond created ✓");
          if (onSuccess) onSuccess({ ...receipt, brokerAddress });
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
          } catch {}
        }
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
        try { await restoreAnvilChainId(); } catch {}
      }
    },
    [account, infrastructure, collateralAddr, positionAddr],
  );

  // Placeholder for close bond (future)
  const closeBond = useCallback(async () => {
    setError("Close bond not yet implemented");
  }, []);

  return {
    createBond,
    closeBond,
    executing,
    error,
    step,
    txHash,
  };
}
