import { useState, useCallback, useEffect } from "react";
import { ethers } from "ethers";
import { getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";
import { rpcProvider } from "../utils/provider";

// ── PrimeBroker LP ABI ────────────────────────────────────────────
const BROKER_LP_ABI = [
  "function addPoolLiquidity(address twammHook, int24 tickLower, int24 tickUpper, uint128 liquidity, uint128 amount0Max, uint128 amount1Max) external returns (uint256 tokenId)",
  "function removePoolLiquidity(uint256 tokenId, uint128 liquidity) external returns (uint256 amount0, uint256 amount1)",
  "function collectV4Fees() external",
  "function setActiveV4Position(uint256 newTokenId) external",
  "function activeTokenId() view returns (uint256)",
  "function hookAddress() view returns (address)",
];

const POSM_ABI = [
  "function getPositionLiquidity(uint256 tokenId) view returns (uint128)",
  "function ownerOf(uint256 tokenId) view returns (address)",
  "function positionInfo(uint256 tokenId) view returns (bytes32)",
  "function nextTokenId() view returns (uint256)",
  "event Transfer(address indexed from, address indexed to, uint256 indexed tokenId)",
];

const STATE_VIEW_ABI = [
  "function getSlot0(bytes32 poolId) view returns (uint160 sqrtPriceX96, int24 tick, uint24 protocolFee, uint24 lpFee)",
  "function getPositionInfo(bytes32 poolId, address owner, int24 tickLower, int24 tickUpper, bytes32 salt) view returns (uint128 liquidity, uint256 feeGrowthInside0LastX128, uint256 feeGrowthInside1LastX128)",
  "function getFeeGrowthInside(bytes32 poolId, int24 tickLower, int24 tickUpper) view returns (uint256 feeGrowthInside0X128, uint256 feeGrowthInside1X128)",
];

// ── Tick math helpers ─────────────────────────────────────────────

/**
 * Convert price to Uniswap V4 tick, aligned to tick spacing.
 * price = 1.0001^tick → tick = log(price) / log(1.0001)
 */
function priceToTick(price, tickSpacing = 5) {
  if (price <= 0) return 0;
  const raw = Math.log(price) / Math.log(1.0001);
  return Math.floor(raw / tickSpacing) * tickSpacing;
}

/**
 * Compute V4 concentrated liquidity from token amounts and price range.
 *
 * Follows standard Uni V3/V4 math:
 *   amount0 = L × (1/√pC − 1/√pU)   (when in range)
 *   amount1 = L × (√pC − √pL)        (when in range)
 *
 * We solve for L from whichever amount the user supplied, or min(L0, L1)
 * when both are supplied.
 */
const MAX_UINT128 = (1n << 128n) - 1n;

function safeSqrtPrice(tick) {
  // Clamp tick to avoid Infinity/zero from Math.pow
  const clamped = Math.max(-887270, Math.min(887270, tick));
  return Math.sqrt(Math.pow(1.0001, clamped));
}

/**
 * Decode tickLower and tickUpper from V4 PositionInfo (packed bytes32).
 * Layout (LSB → MSB):
 *   [0..7]   hasSubscriber (8 bits)
 *   [8..31]  tickLower     (24 bits, int24)
 *   [32..55] tickUpper     (24 bits, int24)
 *   [56..255] poolId       (200 bits)
 */
function decodePositionInfo(infoBytes32) {
  const val = BigInt(infoBytes32);
  const tickLowerRaw = Number((val >> 8n) & 0xFFFFFFn);
  const tickUpperRaw = Number((val >> 32n) & 0xFFFFFFn);
  // Sign-extend int24
  const tickLower = tickLowerRaw >= 0x800000 ? tickLowerRaw - 0x1000000 : tickLowerRaw;
  const tickUpper = tickUpperRaw >= 0x800000 ? tickUpperRaw - 0x1000000 : tickUpperRaw;
  // poolId occupies bits [56..255] — the top 200 bits (25 bytes)
  const poolId = val >> 56n;
  return { tickLower, tickUpper, poolId };
}

/**
 * Compute token0/token1 amounts from liquidity and tick range (human-readable, 6 decimals).
 */
function liquidityToAmounts(liquidity, tickLower, tickUpper, currentTick) {
  const sqrtPL = safeSqrtPrice(tickLower);
  const sqrtPU = safeSqrtPrice(tickUpper);
  const sqrtPC = safeSqrtPrice(currentTick);
  const L = Number(liquidity);

  let amount0 = 0;
  let amount1 = 0;

  if (currentTick < tickLower) {
    // Below range: all token0
    amount0 = L * (1 / sqrtPL - 1 / sqrtPU);
  } else if (currentTick >= tickUpper) {
    // Above range: all token1
    amount1 = L * (sqrtPU - sqrtPL);
  } else {
    // In range: both tokens
    amount0 = L * (1 / sqrtPC - 1 / sqrtPU);
    amount1 = L * (sqrtPC - sqrtPL);
  }

  // Convert from raw (6 decimals) to human
  return {
    amount0: amount0 / 1e6,
    amount1: amount1 / 1e6,
  };
}

function computeLiquidity(amount0, amount1, tickLower, tickUpper, currentTick) {
  const sqrtPL = safeSqrtPrice(tickLower);
  const sqrtPU = safeSqrtPrice(tickUpper);
  const sqrtPC = safeSqrtPrice(currentTick);

  const candidates = [];

  if (currentTick < tickLower) {
    // Only token0 matters
    if (amount0 > 0) {
      const denom = 1 / sqrtPL - 1 / sqrtPU;
      if (denom > 0) candidates.push(amount0 / denom);
    }
  } else if (currentTick >= tickUpper) {
    // Only token1 matters
    if (amount1 > 0) {
      const denom = sqrtPU - sqrtPL;
      if (denom > 0) candidates.push(amount1 / denom);
    }
  } else {
    // Both tokens needed
    if (amount0 > 0) {
      const denom = 1 / sqrtPC - 1 / sqrtPU;
      if (denom > 0) candidates.push(amount0 / denom);
    }
    if (amount1 > 0) {
      const denom = sqrtPC - sqrtPL;
      if (denom > 0) candidates.push(amount1 / denom);
    }
  }

  if (candidates.length === 0) return 0n;
  const L = Math.min(...candidates);
  if (!isFinite(L) || L <= 0) return 0n;

  // Cap to uint128 max
  let result = BigInt(Math.floor(L));
  if (result > MAX_UINT128) result = MAX_UINT128;

  console.log("[LP] computeLiquidity:", {
    tickLower, tickUpper, currentTick: Math.round(currentTick),
    sqrtPL, sqrtPU, sqrtPC,
    amount0, amount1,
    liquidity: result.toString(),
  });

  return result;
}

// ── Hook ──────────────────────────────────────────────────────────

/**
 * usePoolLiquidity — Execute LP operations on PrimeBroker via MetaMask.
 *
 * Provides:
 *  - executeAddLiquidity() — calls broker.addPoolLiquidity()
 *  - executeRemoveLiquidity() — calls broker.removePoolLiquidity()
 *  - activePosition — { tokenId, liquidity } from on-chain
 *  - refreshPosition() — re-read active position
 */
export { liquidityToAmounts, computeLiquidity };

export function usePoolLiquidity(brokerAddress, marketInfo, { onRefreshComplete = [] } = {}) {
  const [executing, setExecuting] = useState(false);
  const [executionStep, setExecutionStep] = useState("");
  const [executionError, setExecutionError] = useState(null);
  const [activePosition, setActivePosition] = useState(null);
  const [allPositions, setAllPositions] = useState([]);
  const [positionsLoaded, setPositionsLoaded] = useState(false);

   
  const _syncAndNotify = useCallback(async (successStep, onSuccess, result) => {
    setExecutionStep("Syncing...");
    await Promise.all(onRefreshComplete.map(fn => fn?.()).filter(Boolean));
    setExecutionStep(successStep);
    if (onSuccess) onSuccess(result);
  }, [onRefreshComplete]);

  const twammHook = marketInfo?.infrastructure?.twamm_hook;
  const tickSpacing = marketInfo?.infrastructure?.tick_spacing || 5;
  const posmAddr = marketInfo?.infrastructure?.v4_position_manager;
  const stateViewAddr = marketInfo?.infrastructure?.v4_state_view;
  const positionToken = marketInfo?.position_token?.address;
  const collateralToken = marketInfo?.collateral?.address;

  // ── Read ALL positions — prefer GraphQL, fallback to RPC ────
  const refreshPosition = useCallback(async () => {
    if (!brokerAddress || !posmAddr) {
      setActivePosition(null);
      setAllPositions([]);
      setPositionsLoaded(true);
      return;
    }

    // --- Try GraphQL first (single request) ---
    try {
      const gqlRes = await fetch("/graphql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: `{
            lpPositions(brokerAddress: "${brokerAddress.toLowerCase()}") {
              tokenId liquidity tickLower tickUpper
              entryPrice entryTick mintBlock isActive
            }
          }`,
        }),
      });
      const gqlData = await gqlRes.json();
      const gqlPositions = gqlData?.data?.lpPositions;
      if (gqlPositions && gqlPositions.length > 0) {
        const mapped = gqlPositions.map((p) => ({
          tokenId: BigInt(p.tokenId),
          liquidity: BigInt(p.liquidity),
          tickLower: p.tickLower,
          tickUpper: p.tickUpper,
          entryPrice: p.entryPrice,
          isActive: p.isActive,
          feesEarned0: "0",
          feesEarned1: "0",
        }));

        // Compute unclaimed fees from V4 StateView
        if (stateViewAddr && twammHook && positionToken && collateralToken) {
          try {
        const provider = rpcProvider;
            const stateView = new ethers.Contract(stateViewAddr, STATE_VIEW_ABI, provider);
            const [c0, c1] = positionToken.toLowerCase() < collateralToken.toLowerCase()
              ? [positionToken, collateralToken]
              : [collateralToken, positionToken];
            const poolId = ethers.keccak256(
              ethers.AbiCoder.defaultAbiCoder().encode(
                ["address", "address", "uint24", "int24", "address"],
                [c0, c1, 500, tickSpacing, twammHook],
              ),
            );
            await Promise.all(
              mapped.map(async (pos) => {
                try {
                  const salt = ethers.zeroPadValue(ethers.toBeHex(pos.tokenId), 32);
                  const [posInfo, feeInside] = await Promise.all([
                    stateView.getPositionInfo(poolId, posmAddr, pos.tickLower, pos.tickUpper, salt),
                    stateView.getFeeGrowthInside(poolId, pos.tickLower, pos.tickUpper),
                  ]);
                  const Q128 = 1n << 128n;
                  const delta0 = feeInside.feeGrowthInside0X128 - posInfo.feeGrowthInside0LastX128;
                  const delta1 = feeInside.feeGrowthInside1X128 - posInfo.feeGrowthInside1LastX128;
                  const raw0 = pos.liquidity * delta0 / Q128;
                  const raw1 = pos.liquidity * delta1 / Q128;
                  pos.feesEarned0 = ethers.formatUnits(raw0, 6);
                  pos.feesEarned1 = ethers.formatUnits(raw1, 6);
                } catch (e) {
                  console.warn(`[LP] Fee calc failed for tokenId ${pos.tokenId}:`, e);
                }
              }),
            );
          } catch (e) {
            console.warn("[LP] Fee computation failed:", e);
          }
        }

        setAllPositions(mapped);
        const active = mapped.find((p) => p.isActive) || mapped[0] || null;
        setActivePosition(active);
        setPositionsLoaded(true);
        console.log("[LP] Loaded", mapped.length, "positions via GraphQL");
        return;
      }
    } catch (gqlErr) {
      console.warn("[LP] GraphQL fetch failed, falling back to RPC:", gqlErr);
    }

    // --- Fallback: RPC chain scan ---
    try {
      console.time("[LP] RPC fallback");
      const provider = rpcProvider;
      const broker = new ethers.Contract(brokerAddress, BROKER_LP_ABI, provider);
      const posm = new ethers.Contract(posmAddr, POSM_ABI, provider);

      // Build expected poolId to filter positions belonging to this market
      let expectedPoolId = null;
      let fullPoolId = null;
      if (twammHook && positionToken && collateralToken) {
        const [c0, c1] = positionToken.toLowerCase() < collateralToken.toLowerCase()
          ? [positionToken, collateralToken]
          : [collateralToken, positionToken];
        fullPoolId = ethers.keccak256(
          ethers.AbiCoder.defaultAbiCoder().encode(
            ["address", "address", "uint24", "int24", "address"],
            [c0, c1, 500, tickSpacing, twammHook],
          ),
        );
        expectedPoolId = BigInt(fullPoolId) >> 56n;
      }

      // Run initial calls in parallel
      const transferFilter = posm.filters.Transfer(null, brokerAddress);
      const [activeTokenIdOnChain, logs] = await Promise.all([
        broker.activeTokenId(),
        posm.queryFilter(transferFilter, 0, "latest"),
      ]);

      const mintBlockMap = new Map();
      for (const log of logs) {
        const tid = log.args.tokenId;
        if (!mintBlockMap.has(tid)) mintBlockMap.set(tid, log.blockNumber);
      }

      const candidateIds = [...new Set(logs.map(l => l.args.tokenId))];

      // Build a StateView instance for fee computation (if available)
      const stateView = (fullPoolId && stateViewAddr)
        ? new ethers.Contract(stateViewAddr, STATE_VIEW_ABI, provider)
        : null;

      // Fetch ALL position data + fees in one parallel batch
      const positionResults = await Promise.all(
        candidateIds.map(async (tokenId) => {
          try {
            // All 3 RPC calls per candidate in parallel
            const [owner, liquidity, info] = await Promise.all([
              posm.ownerOf(tokenId),
              posm.getPositionLiquidity(tokenId),
              posm.positionInfo(tokenId),
            ]);
            if (owner.toLowerCase() !== brokerAddress.toLowerCase()) return null;
            if (liquidity === 0n) return null;

            const decoded = decodePositionInfo(info);
            if (expectedPoolId !== null && decoded.poolId !== expectedPoolId) return null;

            const { tickLower, tickUpper } = decoded;

            // Entry price: derive from tick range midpoint (no API call needed)
            const midTick = (tickLower + tickUpper) / 2;
            const entryPrice = Math.pow(1.0001, midTick);

            // Fee computation
            let feesEarned0 = "0", feesEarned1 = "0";
            if (stateView) {
              try {
                const salt = ethers.zeroPadValue(ethers.toBeHex(tokenId), 32);
                const [posInfo, feeInside] = await Promise.all([
                  stateView.getPositionInfo(fullPoolId, posmAddr, tickLower, tickUpper, salt),
                  stateView.getFeeGrowthInside(fullPoolId, tickLower, tickUpper),
                ]);
                const Q128 = 1n << 128n;
                const delta0 = feeInside.feeGrowthInside0X128 - posInfo.feeGrowthInside0LastX128;
                const delta1 = feeInside.feeGrowthInside1X128 - posInfo.feeGrowthInside1LastX128;
                feesEarned0 = ethers.formatUnits(liquidity * delta0 / Q128, 6);
                feesEarned1 = ethers.formatUnits(liquidity * delta1 / Q128, 6);
              } catch (e) {
                console.warn(`[LP] Fee calc failed for tokenId ${tokenId}:`, e.message);
              }
            }

            return {
              tokenId, liquidity, tickLower, tickUpper, entryPrice,
              isActive: tokenId === activeTokenIdOnChain,
              feesEarned0, feesEarned1,
            };
          } catch { return null; }
        }),
      );
      const positions = positionResults.filter(Boolean);

      setAllPositions(positions);
      const active = positions.find(p => p.isActive) || positions[0] || null;
      setActivePosition(active || null);
      setPositionsLoaded(true);
      console.timeEnd("[LP] RPC fallback");
      console.log("[LP] Found", positions.length, "positions via RPC fallback");
    } catch (err) {
      console.warn("[LP] Failed to read positions:", err);
      setPositionsLoaded(true);
    }
  }, [brokerAddress, posmAddr, stateViewAddr, twammHook, positionToken, collateralToken, tickSpacing]);

  // Auto-fetch on mount / broker change
  useEffect(() => {
    refreshPosition();
  }, [refreshPosition]);

  // ── Add Liquidity ─────────────────────────────────────────────
  const executeAddLiquidity = useCallback(
    async (minPrice, maxPrice, amount0Str, amount1Str, currentPrice, onSuccess) => {
      if (!brokerAddress || !twammHook) {
        setExecutionError("Broker or hook address not available");
        return;
      }

      setExecuting(true);
      setExecutionError(null);

      try {
        // 1. Price → tick
        setExecutionStep("Computing tick range...");
        const tickLower = priceToTick(parseFloat(minPrice), tickSpacing);
        const tickUpper = priceToTick(parseFloat(maxPrice), tickSpacing);

        if (tickLower >= tickUpper) {
          throw new Error("Min price must be less than max price");
        }

        // 2. Parse amounts (6 decimals for both tokens)
        const amt0 = parseFloat(amount0Str || "0");
        const amt1 = parseFloat(amount1Str || "0");
        if (amt0 <= 0 && amt1 <= 0) {
          throw new Error("Enter at least one token amount");
        }

        const amount0Raw = ethers.parseUnits(amt0.toFixed(6), 6);
        const amount1Raw = ethers.parseUnits(amt1.toFixed(6), 6);

        // 3. Compute liquidity
        const currentTick = Math.log(parseFloat(currentPrice)) / Math.log(1.0001);
        const liquidity = computeLiquidity(
          Number(amount0Raw),
          Number(amount1Raw),
          tickLower,
          tickUpper,
          currentTick,
        );

        if (liquidity <= 0n) {
          throw new Error("Computed liquidity is zero — increase amounts");
        }

        // 4. Connect MetaMask (with Anvil chainId sync)
        setExecutionStep("Connecting wallet...");
        const signer = await getAnvilSigner();

        // 5. Send addPoolLiquidity tx
        setExecutionStep("Sending add liquidity tx...");
        const broker = new ethers.Contract(brokerAddress, BROKER_LP_ABI, signer);

        // Use generous slippage (2×) for the simulation
        const slippage = 2n;
        const a0Max = amount0Raw > 0n ? amount0Raw * slippage : ethers.MaxUint256;
        const a1Max = amount1Raw > 0n ? amount1Raw * slippage : ethers.MaxUint256;

        console.log("[LP] addPoolLiquidity params:", {
          twammHook,
          tickLower,
          tickUpper,
          liquidity: liquidity.toString(),
          amount0Max: a0Max.toString(),
          amount1Max: a1Max.toString(),
        });

        // Pre-check with staticCall to surface revert reason
        try {
          await broker.addPoolLiquidity.staticCall(
            twammHook,
            tickLower,
            tickUpper,
            liquidity,
            a0Max,
            a1Max,
          );
        } catch (simErr) {
          const reason = simErr?.revert?.args?.[0]
            || simErr?.info?.error?.data?.message
            || simErr?.info?.error?.message
            || simErr?.reason
            || simErr?.shortMessage
            || simErr?.message
            || "Simulation failed";
          console.error("[LP] staticCall revert:", reason, simErr);
          throw new Error(reason);
        }

        const tx = await broker.addPoolLiquidity(
          twammHook,
          tickLower,
          tickUpper,
          liquidity,
          a0Max,
          a1Max,
          { gasLimit: 2_000_000 },
        );

        setExecutionStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        // 6. Refresh position
        await refreshPosition();

        await _syncAndNotify("Liquidity added ✓", onSuccess, receipt);
      } catch (err) {
        console.error("[LP] addPoolLiquidity failed:", err);
        setExecutionError(err.reason || err.shortMessage || err.message || "Transaction failed");
      } finally {
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [brokerAddress, twammHook, tickSpacing, refreshPosition, _syncAndNotify],
  );

  // ── Remove Liquidity ──────────────────────────────────────────
  const executeRemoveLiquidity = useCallback(
    async (tokenId, percent, onSuccess) => {
      if (!brokerAddress) {
        setExecutionError("Broker address not available");
        return;
      }

      setExecuting(true);
      setExecutionError(null);

      try {
        // Read current liquidity
        setExecutionStep("Reading position...");
        const provider = rpcProvider;
        const posm = new ethers.Contract(posmAddr, POSM_ABI, provider);
        const currentLiquidity = await posm.getPositionLiquidity(tokenId);

        if (currentLiquidity === 0n) {
          throw new Error("Position has no liquidity");
        }

        // Compute removal amount based on percent
        const removeAmount = (currentLiquidity * BigInt(percent)) / 100n;
        if (removeAmount === 0n) {
          throw new Error("Removal amount is zero");
        }

        // Connect MetaMask (with Anvil chainId sync)
        setExecutionStep("Connecting wallet...");
        const signer = await getAnvilSigner();

        // Send removePoolLiquidity tx
        setExecutionStep("Sending remove liquidity tx...");
        const broker = new ethers.Contract(brokerAddress, BROKER_LP_ABI, signer);
        const tx = await broker.removePoolLiquidity(tokenId, removeAmount);

        setExecutionStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        // Refresh position
        await refreshPosition();

        await _syncAndNotify("Liquidity removed ✓", onSuccess, receipt);
      } catch (err) {
        console.error("[LP] removePoolLiquidity failed:", err);
        setExecutionError(err.reason || err.shortMessage || err.message || "Transaction failed");
      } finally {
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [brokerAddress, posmAddr, refreshPosition, _syncAndNotify],
  );
  /**
   * Track a V4 LP position as collateral.
   * @param {BigInt|number} tokenId NFT token ID to track
   * @param {Function} onSuccess Called with receipt on success
   */
  const trackLpPosition = useCallback(
    async (tokenId, onSuccess) => {
      if (!brokerAddress) { setExecutionError("Missing broker address"); return; }
      if (!window.ethereum) { setExecutionError("MetaMask not found"); return; }

      setExecuting(true);
      setExecutionError(null);
      setExecutionStep("Tracking LP position as collateral...");

      try {
        const signer = await getAnvilSigner();
        const broker = new ethers.Contract(brokerAddress, BROKER_LP_ABI, signer);

        setExecutionStep("Confirm in wallet...");
        const tx = await broker.setActiveV4Position(tokenId, { gasLimit: 300_000n });

        setExecutionStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await refreshPosition();
          await _syncAndNotify("Position tracked ✓", onSuccess, receipt);
        } else {
          setExecutionError("Transaction reverted");
          setExecutionStep("");
        }
      } catch (e) {
        console.error("[LP] trackPosition failed:", e);
        const reason = e.revert?.args?.[0] || e.reason || e.shortMessage || e.message;
        setExecutionError(reason || "Track position failed");
        setExecutionStep("");
      } finally {
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [brokerAddress, refreshPosition, _syncAndNotify],
  );

  /**
   * Untrack the active V4 LP position from collateral.
   * @param {Function} onSuccess Called with receipt on success
   */
  const untrackLpPosition = useCallback(
    async (onSuccess) => {
      if (!brokerAddress) { setExecutionError("Missing broker address"); return; }
      if (!window.ethereum) { setExecutionError("MetaMask not found"); return; }

      setExecuting(true);
      setExecutionError(null);
      setExecutionStep("Untracking LP position...");

      try {
        const signer = await getAnvilSigner();
        const broker = new ethers.Contract(brokerAddress, BROKER_LP_ABI, signer);

        setExecutionStep("Confirm in wallet...");
        const tx = await broker.setActiveV4Position(0, { gasLimit: 300_000n });

        setExecutionStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await refreshPosition();
          await _syncAndNotify("Position untracked ✓", onSuccess, receipt);
        } else {
          setExecutionError("Transaction reverted");
          setExecutionStep("");
        }
      } catch (e) {
        console.error("[LP] untrackPosition failed:", e);
        const reason = e.revert?.args?.[0] || e.reason || e.shortMessage || e.message;
        setExecutionError(reason || "Untrack position failed");
        setExecutionStep("");
      } finally {
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [brokerAddress, refreshPosition, _syncAndNotify],
  );

  /**
   * Collect accrued V4 LP fees for a given position.
   * The contract's collectV4Fees() operates on activeTokenId, so we:
   *   1. Track the target position (setActiveV4Position)
   *   2. Call collectV4Fees()
   *   3. Restore the previously tracked position
   */
  const executeCollectFees = useCallback(
    async (tokenId, onSuccess) => {
      if (!brokerAddress) { setExecutionError("Missing broker address"); return; }
      if (!window.ethereum) { setExecutionError("MetaMask not found"); return; }

      setExecuting(true);
      setExecutionError(null);
      setExecutionStep("Preparing fee collection...");

      try {
        const signer = await getAnvilSigner();
        const broker = new ethers.Contract(brokerAddress, BROKER_LP_ABI, signer);

        // Read current activeTokenId to restore later
        const provider = rpcProvider;
        const brokerRead = new ethers.Contract(brokerAddress, BROKER_LP_ABI, provider);
        const currentActive = await brokerRead.activeTokenId();

        // If the target position isn't already tracked, temporarily track it
        const needsSwitch = currentActive !== BigInt(tokenId);
        if (needsSwitch) {
          setExecutionStep("Tracking position for fee collection...");
          const trackTx = await broker.setActiveV4Position(tokenId, { gasLimit: 300_000n });
          await trackTx.wait();
        }

        // Collect fees
        setExecutionStep("Collecting fees...");
        const tx = await broker.collectV4Fees({ gasLimit: 500_000n });

        setExecutionStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        // Restore previously tracked position if we switched
        if (needsSwitch && currentActive !== 0n) {
          setExecutionStep("Restoring tracked position...");
          const restoreTx = await broker.setActiveV4Position(currentActive, { gasLimit: 300_000n });
          await restoreTx.wait();
        }

        if (receipt.status === 1) {
          await refreshPosition();
          await _syncAndNotify("Fees collected ✓", onSuccess, receipt);
        } else {
          setExecutionError("Transaction reverted");
          setExecutionStep("");
        }
      } catch (e) {
        console.error("[LP] collectV4Fees failed:", e);
        const reason = e.revert?.args?.[0] || e.reason || e.shortMessage || e.message;
        setExecutionError(reason || "Fee collection failed");
        setExecutionStep("");
      } finally {
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [brokerAddress, refreshPosition, _syncAndNotify],
  );

  return {
    executeAddLiquidity,
    executeRemoveLiquidity,
    executeCollectFees,
    trackLpPosition,
    untrackLpPosition,
    activePosition,
    allPositions,
    positionsLoaded,
    refreshPosition,
    executing,
    executionStep,
    executionError,
    clearError: () => setExecutionError(null),
  };
}
