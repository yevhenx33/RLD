import { useState, useEffect, useCallback } from "react";
import { ethers } from "ethers";
import { RPC_URL } from "../utils/anvil";

// ── ABI fragments ─────────────────────────────────────────────────

const BROKER_ABI = [
  "function CORE() view returns (address)",
  "function marketId() view returns (bytes32)",
  "function frozen() view returns (bool)",
  "function activeTwammOrder() view returns (tuple(address,address,uint24,int24,address) key, tuple(address,uint160,bool) orderKey, bytes32 orderId)",
  "function collateralToken() view returns (address)",
];

const CORE_ABI = [
  "function getPosition(bytes32,address) view returns (tuple(uint128 debtPrincipal))",
  "function getMarketState(bytes32) view returns (tuple(uint128 normalizationFactor, uint128 totalDebt, uint128 badDebt, uint48 lastUpdateTimestamp))",
];

const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
];

/**
 * useBondPositions — Fetch real on-chain bond data for all bond broker NFTs.
 *
 * Each bond is an independent PrimeBroker clone. Created bonds are tracked
 * in localStorage under `rld_bonds_<account>` (list of broker addresses)
 * and `rld_bond_<broker>` (metadata: notional, rate, duration).
 *
 * @param {string}  account        Connected wallet address
 * @param {number}  entryRate      Fallback rate if no localStorage data
 * @param {number}  pollInterval   Polling ms (default 15000)
 */
export function useBondPositions(account, entryRate, pollInterval = 15000) {
  const [bonds, setBonds] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchPositions = useCallback(async () => {
    if (!account) return;

    try {
      setLoading(true);

      // 1. Get list of bond broker addresses from localStorage
      const listKey = `rld_bonds_${account.toLowerCase()}`;
      const brokerAddresses = JSON.parse(localStorage.getItem(listKey) || "[]");

      if (brokerAddresses.length === 0) {
        setBonds([]);
        return;
      }

      const provider = new ethers.JsonRpcProvider(RPC_URL);
      const results = [];

      for (const brokerAddr of brokerAddresses) {
        try {
          const bond = await fetchSingleBond(provider, brokerAddr, entryRate);
          if (bond) results.push(bond);
        } catch (err) {
          console.warn(`[BondPositions] Error fetching ${brokerAddr}:`, err.message);
        }
      }

      setBonds(results);
    } catch (err) {
      console.warn("[BondPositions] fetch error:", err.message);
    } finally {
      setLoading(false);
    }
  }, [account, entryRate]);

  useEffect(() => {
    fetchPositions();
    const id = setInterval(fetchPositions, pollInterval);
    return () => clearInterval(id);
  }, [fetchPositions, pollInterval]);

  return { bonds, loading, refresh: fetchPositions };
}

// ── Fetch a single bond's on-chain + localStorage data ──────────

async function fetchSingleBond(provider, brokerAddr, fallbackRate) {
  const broker = new ethers.Contract(brokerAddr, BROKER_ABI, provider);

  // Get core address + market ID
  const [coreAddr, marketId, collateralTokenAddr, frozen] = await Promise.all([
    broker.CORE(),
    broker.marketId(),
    broker.collateralToken(),
    broker.frozen(),
  ]);

  const core = new ethers.Contract(coreAddr, CORE_ABI, provider);
  const collateralToken = new ethers.Contract(collateralTokenAddr, ERC20_ABI, provider);

  // Get position data
  const [position, marketState, brokerWaUSDC] = await Promise.all([
    core.getPosition(marketId, brokerAddr),
    core.getMarketState(marketId),
    collateralToken.balanceOf(brokerAddr),
  ]);

  const debtPrincipal = position.debtPrincipal ?? position[0];

  // Skip if no debt (empty broker)
  if (debtPrincipal === 0n) return null;

  // TWAMM order
  const twammOrder = await broker.activeTwammOrder();
  const orderExpiration = BigInt(twammOrder.orderKey[1]);
  const orderId = twammOrder.orderId;

  // Block time
  const block = await provider.getBlock("latest");
  const now = BigInt(block.timestamp);

  // Read saved metadata
  let savedMeta = null;
  try {
    const key = `rld_bond_${brokerAddr.toLowerCase()}`;
    const raw = localStorage.getItem(key);
    if (raw) savedMeta = JSON.parse(raw);
  } catch {}

  // Compute values
  const normFactor = BigInt(marketState.normalizationFactor ?? marketState[0]);
  const trueDebt = (BigInt(debtPrincipal) * normFactor) / (10n ** 18n);
  const debtUsd = Number(ethers.formatUnits(trueDebt, 6));
  const freeWaUSDC = Number(ethers.formatUnits(brokerWaUSDC, 6));

  // TWAMM timing
  const hasActiveOrder = orderId !== ethers.ZeroHash;
  const expirationSec = Number(orderExpiration);
  const nowSec = Number(now);
  const remainingSec = Math.max(0, expirationSec - nowSec);
  const remainingDays = Math.max(0, Math.ceil(remainingSec / 86400));
  const isMatured = hasActiveOrder && remainingSec <= 0;

  // Bond ID from broker address
  const tokenId = parseInt(brokerAddr.slice(-4), 16) % 10000;

  // Use saved metadata
  const notional = savedMeta?.notionalUSD || debtUsd;
  const rate = savedMeta?.ratePercent || fallbackRate || 0;
  const durationHours = savedMeta?.durationHours || 0;
  const maturityDays = durationHours
    ? Math.ceil(durationHours / 24)
    : remainingDays;
  const createdAt = savedMeta?.createdAt || 0;

  // Elapsed
  const elapsedMs = createdAt ? Date.now() - createdAt : 0;
  const elapsedDays = Math.floor(elapsedMs / 86400000);

  // Accrued = notional × rate × elapsed/365
  const accrued = notional * (rate / 100) * (elapsedDays / 365);

  return {
    id: tokenId,
    brokerAddress: brokerAddr,
    principal: notional,
    debtTokens: debtUsd,
    fixedRate: rate,
    maturityDays,
    elapsed: elapsedDays,
    remaining: remainingDays,
    maturityDate: hasActiveOrder
      ? new Date(expirationSec * 1000).toISOString().slice(0, 10)
      : "—",
    frozen,
    isMatured,
    accrued,
    freeCollateral: freeWaUSDC,
    orderId,
    hasActiveOrder,
    txHash: savedMeta?.txHash || null,
  };
}
