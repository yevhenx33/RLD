import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { RPC_URL, getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";

// ── ABI fragments ─────────────────────────────────────────────────

const BOND_FACTORY_ABI = [
  "function mintBond(uint256 notional, uint256 hedgeAmount, uint256 duration, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey) returns (address broker)",
  "function closeBond(address broker, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey)",
  "event BondMinted(address indexed user, address indexed broker, uint256 notional, uint256 hedge, uint256 duration)",
  "event BondClosed(address indexed user, address indexed broker, uint256 collateralReturned, uint256 positionReturned)",
];

const ERC20_ABI = [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
];

const ERC721_ABI = [
  "function approve(address to, uint256 tokenId)",
  "function getApproved(uint256 tokenId) view returns (address)",
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
    async (brokerAddress, onSuccess) => {
      if (!account || !brokerAddress) {
        setError("Missing parameters");
        return;
      }

      const bondFactoryAddr = infrastructure?.bond_factory
        || "0x0a5fF8eAE2104805E18a2F3646776d577Fc9Cf26";
      const brokerFactoryAddr = infrastructure?.broker_factory
        || "0x7EF7a03e9d48188c349E4F8b1d57F72C9fE27732";

      if (!infrastructure?.twamm_hook) {
        setError("Missing infrastructure");
        return;
      }

      setExecuting(true);
      setError(null);
      setStep("Preparing...");

      try {
        const signer = await getAnvilSigner();
        const tokenId = BigInt(brokerAddress); // tokenId = uint256(uint160(broker))

        // ── 1. Ensure NFT approval ────────────────────────────────
        setStep("Checking NFT approval...");
        const nftContract = new ethers.Contract(brokerFactoryAddr, ERC721_ABI, signer);
        const approved = await nftContract.getApproved(tokenId);

        if (approved.toLowerCase() !== bondFactoryAddr.toLowerCase()) {
          setStep("Approving NFT transfer...");
          const approveTx = await nftContract.approve(bondFactoryAddr, tokenId, {
            gasLimit: 200_000,
          });
          await approveTx.wait();
          console.log("[CloseBond] NFT approved for BondFactory");
        }

        // ── 2. Build pool key ─────────────────────────────────────
        const sorted = positionAddr.toLowerCase() < collateralAddr.toLowerCase();
        const poolKeyArr = [
          sorted ? positionAddr : collateralAddr,
          sorted ? collateralAddr : positionAddr,
          infrastructure.pool_fee || 500,
          infrastructure.tick_spacing || 5,
          infrastructure.twamm_hook,
        ];

        // ── 3. Close bond (single TX) ─────────────────────────────
        setStep("Closing bond...");
        const bondFactory = new ethers.Contract(
          bondFactoryAddr,
          BOND_FACTORY_ABI,
          signer,
        );

        const tx = await bondFactory.closeBond(brokerAddress, poolKeyArr, {
          gasLimit: 10_000_000,
        });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

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
            } catch {}
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
          } catch {}

          setStep("Bond closed ✓");
          if (onSuccess) onSuccess({ brokerAddress });
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
        try { await restoreAnvilChainId(); } catch {}
      }
    },
    [account, infrastructure, collateralAddr, positionAddr],
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

