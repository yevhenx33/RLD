import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";
import { rpcProvider } from "../utils/provider";

// ── ABI fragments ─────────────────────────────────────────────────

// New flash-loan BasisTradeFactory: accepts BasisTradeParams struct
const BASIS_TRADE_FACTORY_ABI = [
  // openBasisTradeWithUSDC(BasisTradeParams params) returns (address broker)
  // BasisTradeParams = (uint256 amount, uint256 levDebt, uint256 hedge, uint256 duration, PoolKey poolKey, bytes swapPath)
  "function openBasisTradeWithUSDC(tuple(uint256 amount, uint256 levDebt, uint256 hedge, uint256 duration, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey, bytes swapPath) params) returns (address broker)",
  "function openBasisTrade(tuple(uint256 amount, uint256 levDebt, uint256 hedge, uint256 duration, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey, bytes swapPath) params, uint256 sUsdeAmount) returns (address broker)",
  "function closeBasisTrade(address broker, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks) poolKey)",
  "event BasisTradeOpened(address indexed user, address indexed broker, uint256 amount, uint256 effectiveLeverage, uint256 duration)",
  "event BasisTradeClosed(address indexed user, address indexed broker, uint256 sUsdeReturned)",
];

const ERC20_ABI = [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function balanceOf(address owner) view returns (uint256)",
  "function transfer(address to, uint256 amount) returns (bool)",
];

const SUSDE_ABI = [
  "function convertToAssets(uint256 shares) view returns (uint256)",
  "function balanceOf(address owner) view returns (uint256)",
  "function transfer(address to, uint256 amount) returns (bool)",
];

// NOTE: sUSDe and USDC addresses are now passed via externalContracts param
const DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD";


// ── Hedge math (off-chain) ────────────────────────────────────────

/**
 * Compute levDebt and hedge from user inputs.
 *
 * @param {number} capitalUSD   User capital in USD (e.g., 5000)
 * @param {number} leverage     Target leverage multiplier (e.g., 3 => 3x)
 * @param {number} durationDays Duration in days (e.g., 90)
 * @param {number} borrowRateAPY Morpho borrow APY (e.g., 2.9 for 2.9%)
 * @returns {{ levDebt: number, hedge: number }}
 */
function computeLevDebtAndHedge(capitalUSD, leverage, durationDays, borrowRateAPY = 2.9) {
  // levDebt = capital × (leverage - 1)
  // This is the additional PYUSD borrowed via flash to achieve target leverage
  const levDebt = capitalUSD * (leverage - 1);

  // α = borrowRate × T  (T = duration in years)
  const T = durationDays / 365;
  const r = borrowRateAPY / 100;
  const alpha = r * T;

  // hedge = levDebt × α / (1 - α)
  // Self-covering: the hedge covers its own interest cost
  let hedge = 0;
  if (alpha > 0 && alpha < 1) {
    hedge = (levDebt * alpha) / (1 - alpha);
  }

  return { levDebt, hedge };
}


// ── Hook ──────────────────────────────────────────────────────────

/**
 * useBasisTradeExecution — Open and close basis trades via BasisTradeFactory (flash loan edition).
 *
 * Open:
 *   1. Ensure USDC approval for BasisTradeFactory
 *   2. Compute levDebt and hedge off-chain from (capital, leverage, duration)
 *   3. basisTradeFactory.openBasisTradeWithUSDC(BasisTradeParams)
 *
 * Close:
 *   1. basisTradeFactory.closeBasisTrade(broker, poolKey)
 *
 * @param {string} account           Connected wallet address
 * @param {object} infrastructure    { basis_trade_factory, twamm_hook, pool_fee, tick_spacing }
 * @param {string} collateralAddr    waUSDC address
 * @param {string} positionAddr      wRLP address
 */
export function useBasisTradeExecution(
  account,
  infrastructure,
  collateralAddr,
  positionAddr,
  externalContracts,
  { onRefreshComplete = [], pauseRef } = {},
) {
  const SUSDE_ADDRESS = externalContracts?.susde || "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497";
  const USDC_ADDRESS = externalContracts?.usdc || "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";
  const SUSDE_TOKEN_ADDRESS = externalContracts?.susde || "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497";
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState("");
  const [txHash, setTxHash] = useState(null);

   
  const _syncAndNotify = useCallback(async (successStep, onSuccess, result) => {
    setStep("Syncing...");
    // Small delay to let indexer process the new block
    await new Promise((r) => setTimeout(r, 500));
    await Promise.all(onRefreshComplete.map(fn => fn?.()).filter(Boolean));
    setStep(successStep);
    if (pauseRef) pauseRef.current = false;
    if (onSuccess) onSuccess(result);
  }, [onRefreshComplete, pauseRef]);

  /**
   * Open a basis trade in a single atomic transaction (flash loan).
   *
   * @param {number}   capitalUSD     Capital in USD (e.g., 5000)
   * @param {number}   leverage       Leverage multiplier (e.g., 3)
   * @param {number}   durationDays   Duration in days (e.g., 90)
   * @param {number}   borrowRateAPY  Current borrow rate (e.g., 2.9)
   * @param {Function} onSuccess      Called with { receipt, brokerAddress }
   * @param {object}   opts           { useUnderlying: bool }
   */
  const createBasisTrade = useCallback(
    async (capitalUSD, leverage, durationDays, borrowRateAPY, onSuccess, { useUnderlying = true } = {}) => {
      if (
        !account ||
        !collateralAddr ||
        !positionAddr
      ) {
        setError("Missing parameters");
        return;
      }

      const basisTradeFactoryAddress = infrastructure?.basis_trade_factory;

      if (!basisTradeFactoryAddress || !infrastructure?.twamm_hook) {
        setError("BasisTrade factory not available — waiting for config");
        return;
      }

      setExecuting(true);
      if (pauseRef) pauseRef.current = true;
      setError(null);
      setStep("Computing strategy...");

      try {
        // ── Direct RPC provider for read-only calls ─────────────
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

        // ── Compute levDebt and hedge off-chain ─────────────────
        const lev = Number(leverage) || 1;
        const days = Number(durationDays) || 90;
        const { levDebt, hedge } = computeLevDebtAndHedge(capitalUSD, lev, days, borrowRateAPY);

        const amountWei = ethers.parseUnits(capitalUSD.toString(), 6);
        const levDebtWei = ethers.parseUnits(Math.ceil(levDebt).toString(), 6);
        const hedgeWei = ethers.parseUnits(Math.ceil(hedge).toString(), 6);
        const durationSec = Math.floor(days * 86400);

        console.log("[BasisTrade] Computed:", {
          capital: capitalUSD,
          leverage: lev,
          duration: days,
          levDebt: Math.ceil(levDebt),
          hedge: Math.ceil(hedge),
          totalFlash: Math.ceil(levDebt + hedge),
          durationSec,
        });

        // The user only needs to supply the capital (USDC/sUSDe)
        const totalWei = amountWei;

        // ── Determine which token to approve ─────────────────────
        let approveTokenAddr;
        let approveLabel;

        if (useUnderlying) {
          // USDC directly
          approveTokenAddr = USDC_ADDRESS;
          approveLabel = "USDC";
        } else {
          // sUSDe
          approveTokenAddr = SUSDE_TOKEN_ADDRESS;
          approveLabel = "sUSDe";
        }

        // ── Pre-flight balance check ────────────────────────────
        setStep("Checking balance...");
        const tokenReader = new ethers.Contract(approveTokenAddr, ERC20_ABI, readProvider);
        const balance = await tokenReader.balanceOf(account);

        if (balance < totalWei) {
          const have = Number(ethers.formatUnits(balance, useUnderlying ? 6 : 18)).toFixed(2);
          const need = Number(ethers.formatUnits(totalWei, useUnderlying ? 6 : 18)).toFixed(2);
          setError(
            `Insufficient ${approveLabel} — need $${need}, have $${have}`,
          );
          setExecuting(false);
          return;
        }

        // ── Ensure approval ────────────────────────────────────
        setStep("Checking approval...");
        const allowance = await tokenReader.allowance(account, basisTradeFactoryAddress);

        let signer;
        if (allowance < totalWei) {
          setStep("Syncing chain ID...");
          signer = await getAnvilSigner();
          setStep(`Approve ${approveLabel} for BasisTradeFactory...`);
          const tokenToApprove = new ethers.Contract(approveTokenAddr, ERC20_ABI, signer);
          const approveTx = await tokenToApprove.approve(
            basisTradeFactoryAddress,
            ethers.MaxUint256,
          );
          await approveTx.wait();
          console.log(`[BasisTrade] Approved BasisTradeFactory for ${approveLabel}`);
        }

        // ── Open position (single TX with flash loan) ───────────
        if (!signer) {
          setStep("Syncing chain ID...");
          signer = await getAnvilSigner();
        }
        setStep("Opening position (flash loan)...");

        const factory = new ethers.Contract(
          basisTradeFactoryAddress,
          BASIS_TRADE_FACTORY_ABI,
          signer,
        );

        // Build BasisTradeParams struct
        const params = {
          amount: amountWei,
          levDebt: levDebtWei,
          hedge: hedgeWei,
          duration: durationSec,
          poolKey: [
            poolKey.currency0,
            poolKey.currency1,
            poolKey.fee,
            poolKey.tickSpacing,
            poolKey.hooks,
          ],
          swapPath: "0x", // empty for V1
        };

        let tx;
        if (useUnderlying) {
          tx = await factory.openBasisTradeWithUSDC(params, {
            gasLimit: 10_000_000,
          });
        } else {
          tx = await factory.openBasisTrade(params, totalWei, {
            gasLimit: 10_000_000,
          });
        }
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();
        console.log(`[BasisTrade] Gas used: ${receipt.gasUsed.toString()}`);

        if (receipt.status === 1) {
          // Parse BasisTradeOpened event for broker address
          let brokerAddress = null;
          const iface = new ethers.Interface(BASIS_TRADE_FACTORY_ABI);
          for (const log of receipt.logs) {
            try {
              const parsed = iface.parseLog({
                topics: log.topics,
                data: log.data,
              });
              if (parsed?.name === "BasisTradeOpened") {
                brokerAddress = parsed.args.broker;
                break;
              }
            } catch {
              // Not our event
            }
          }

          // Fire success immediately so toast shows before localStorage writes
          await _syncAndNotify("Position Opened ✓", onSuccess, { ...receipt, brokerAddress });

          // Save trade metadata to localStorage (non-blocking)
          if (brokerAddress) {
            try {
              const tradeMeta = {
                capitalUSD,
                leverage: lev,
                durationDays: days,
                levDebt: Math.ceil(levDebt),
                hedge: Math.ceil(hedge),
                borrowRateAPY,
                createdAt: Date.now(),
                txHash: receipt.hash,
                brokerAddress,
              };
              const key = `rld_bond_${brokerAddress.toLowerCase()}`;
              localStorage.setItem(key, JSON.stringify(tradeMeta));

              const listKey = `rld_bonds_${account.toLowerCase()}`;
              const existing = JSON.parse(localStorage.getItem(listKey) || "[]");
              if (!existing.includes(brokerAddress.toLowerCase())) {
                existing.push(brokerAddress.toLowerCase());
                localStorage.setItem(listKey, JSON.stringify(existing));
              }
            } catch { /* ignore localStorage errors */ }
          }
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[BasisTrade] createBasisTrade failed:", e);
        let msg = "Strategy entry failed";
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
    [account, infrastructure, collateralAddr, positionAddr, USDC_ADDRESS, SUSDE_TOKEN_ADDRESS, _syncAndNotify, pauseRef],
  );

  /**
   * Close a basis trade — unwind all positions, return sUSDe to user.
   *
   * @param {string}   brokerAddress  The trade's PrimeBroker clone address
   * @param {Function} onSuccess      Called with { brokerAddress } on completion
   */
  const closeBasisTrade = useCallback(
    async (brokerAddress, onSuccess) => {
      if (!account || !brokerAddress) {
        setError("Missing parameters");
        return;
      }

      const basisTradeFactoryAddress = infrastructure?.basis_trade_factory;

      if (!basisTradeFactoryAddress) {
        setError("BasisTrade factory not available — waiting for config");
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

        // ── Build pool key ─────────────────────────────────────
        const sorted = positionAddr.toLowerCase() < collateralAddr.toLowerCase();
        const poolKeyArr = [
          sorted ? positionAddr : collateralAddr,
          sorted ? collateralAddr : positionAddr,
          infrastructure.pool_fee || 500,
          infrastructure.tick_spacing || 5,
          infrastructure.twamm_hook,
        ];

        // ── Close trade (single TX) ──────────────────────────────
        setStep("Closing position...");
        const factory = new ethers.Contract(
          basisTradeFactoryAddress,
          BASIS_TRADE_FACTORY_ABI,
          signer,
        );

        const tx = await factory.closeBasisTrade(brokerAddress, poolKeyArr, {
          gasLimit: 25_000_000,
        });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();
        console.log(`[BasisTrade] Close gas used: ${receipt.gasUsed.toString()}`);

        if (receipt.status === 1) {
          // Parse BasisTradeClosed event
          const iface = new ethers.Interface(BASIS_TRADE_FACTORY_ABI);
          let sUsdeReturnedRaw = 0n;
          for (const log of receipt.logs) {
            try {
              const parsed = iface.parseLog({
                topics: log.topics,
                data: log.data,
              });
              if (parsed?.name === "BasisTradeClosed") {
                sUsdeReturnedRaw = parsed.args.sUsdeReturned;
                console.log("[BasisTrade] sUSDe returned:", ethers.formatUnits(sUsdeReturnedRaw, 18));
                break;
              }
            } catch { /* not our event */ }
          }

          // ── Burn excess sUSDe to simulate realistic flash loan repayment ──
          try {
            const bondMeta = JSON.parse(localStorage.getItem(`rld_bond_${brokerAddress.toLowerCase()}`) || "null");
            if (bondMeta?.levDebt && sUsdeReturnedRaw > 0n) {
              setStep("Settling flash loan...");
              const sUsde = new ethers.Contract(SUSDE_TOKEN_ADDRESS, SUSDE_ABI, signer);
              // Convert levDebt (PYUSD 6 dec) to sUSDe (18 dec)
              // 1 sUSDe is worth convertToAssets(1e18) USDe ≈ PYUSD
              const assetsPerShare = await sUsde.convertToAssets(ethers.parseUnits("1", 18));
              // debtSUSDe = levDebt * 1e18 / assetsPerShare  (levDebt is in 6 dec)
              const levDebtBig = BigInt(Math.ceil(bondMeta.levDebt)) * 10n ** 12n; // 6 dec → 18 dec
              const burnAmount = (levDebtBig * 10n ** 18n) / assetsPerShare;
              const userBal = await sUsde.balanceOf(account);
              const actualBurn = burnAmount > userBal ? userBal / 2n : burnAmount; // safety cap
              if (actualBurn > 0n) {
                console.log(`[BasisTrade] Burning ${ethers.formatUnits(actualBurn, 18)} sUSDe (flash loan repayment simulation)`);
                const burnTx = await sUsde.transfer(DEAD_ADDRESS, actualBurn);
                await burnTx.wait();
              }
            }
          } catch (burnErr) {
            console.warn("[BasisTrade] Burn excess failed (non-critical):", burnErr);
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

          await _syncAndNotify("Position Closed ✓", onSuccess, { brokerAddress });
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[BasisTrade] closeBasisTrade failed:", e);
        let msg = "Close position failed";
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
    [account, infrastructure, collateralAddr, positionAddr, SUSDE_TOKEN_ADDRESS, _syncAndNotify, pauseRef],
  );

  return {
    createBasisTrade,
    closeBasisTrade,
    executing,
    error,
    step,
    txHash,
    computeLevDebtAndHedge,
  };
}
