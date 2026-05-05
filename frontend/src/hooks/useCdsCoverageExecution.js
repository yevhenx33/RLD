import { useCallback, useState } from "react";
import { ethers } from "ethers";
import { getSigner } from "../utils/connection";
import { rpcProvider } from "../utils/provider";
import { buildHooklessPoolKeyArray } from "../lib/peripheryIntegration";

const POOL_KEY_TUPLE = {
  name: "poolKey",
  type: "tuple",
  components: [
    { name: "currency0", type: "address" },
    { name: "currency1", type: "address" },
    { name: "fee", type: "uint24" },
    { name: "tickSpacing", type: "int24" },
    { name: "hooks", type: "address" },
  ],
};

const CDS_COVERAGE_FACTORY_ABI = [
  "error CoverageOpenPreview(uint256 positionReceived, uint256 totalRequired)",
  "error CoverageClosePreview(uint256 collateralReturned)",
  {
    name: "quoteOpenCoverage",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "coverage", type: "uint256" },
      { name: "duration", type: "uint256" },
      POOL_KEY_TUPLE,
    ],
    outputs: [
      { name: "initialPositionTokens", type: "uint256" },
      { name: "initialCost", type: "uint256" },
      { name: "premiumBudget", type: "uint256" },
      { name: "totalRequired", type: "uint256" },
    ],
  },
  {
    name: "openCoverage",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "coverage", type: "uint256" },
      { name: "duration", type: "uint256" },
      POOL_KEY_TUPLE,
      { name: "minInitialPositionReceived", type: "uint256" },
    ],
    outputs: [{ name: "broker", type: "address" }],
  },
  {
    name: "previewOpenCoverage",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "coverage", type: "uint256" },
      { name: "duration", type: "uint256" },
      POOL_KEY_TUPLE,
    ],
    outputs: [],
  },
  {
    name: "closeCoverage",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      POOL_KEY_TUPLE,
      { name: "minCollateralReturned", type: "uint256" },
    ],
    outputs: [],
  },
  {
    name: "previewCloseCoverage",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      POOL_KEY_TUPLE,
    ],
    outputs: [],
  },
  "event CoverageOpened(address indexed user, address indexed broker, uint256 coverage, uint256 initialCost, uint256 premiumBudget, uint256 initialPositionTokens, uint256 duration)",
];

const ERC20_ABI = [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function balanceOf(address owner) view returns (uint256)",
];

function getCoverageFactory(infrastructure) {
  return (
    infrastructure?.cds_coverage_factory ||
    infrastructure?.cdsCoverageFactory ||
    infrastructure?.coverage_factory ||
    infrastructure?.coverageFactory ||
    null
  );
}

function buildPoolKey(infrastructure, collateralAddr, positionAddr) {
  return buildHooklessPoolKeyArray(infrastructure, collateralAddr, positionAddr);
}

const CDS_COVERAGE_IFACE = new ethers.Interface(CDS_COVERAGE_FACTORY_ABI);

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

function parseCoveragePreview(error, expectedName) {
  if (error?.revert?.name === expectedName) {
    return error.revert.args;
  }
  const data = extractRevertData(error);
  if (!data) return null;
  try {
    const parsed = CDS_COVERAGE_IFACE.parseError(data);
    if (parsed?.name === expectedName) return parsed.args;
  } catch {
    // Not a CDS coverage preview payload.
  }
  return null;
}

async function readCoveragePreview(callPreview, expectedName) {
  try {
    await callPreview();
  } catch (error) {
    const parsed = parseCoveragePreview(error, expectedName);
    if (parsed) return parsed;
    throw error;
  }
  throw new Error("Coverage preview did not return a preview payload");
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

function extractBroker(receipt) {
  const iface = new ethers.Interface(CDS_COVERAGE_FACTORY_ABI);
  for (const log of receipt.logs || []) {
    try {
      const parsed = iface.parseLog({ topics: log.topics, data: log.data });
      if (parsed?.name === "CoverageOpened") {
        return parsed.args.broker;
      }
    } catch {
      // Ignore logs from other contracts.
    }
  }
  return null;
}

export function useCdsCoverageExecution(
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

  const factoryAddr = getCoverageFactory(infrastructure);

  const _syncAndNotify = useCallback(async (successStep, onSuccess, result) => {
    setStep("Syncing...");
    await Promise.all(onRefreshComplete.map((fn) => fn?.()).filter(Boolean));
    setStep(successStep);
    if (onSuccess) onSuccess(result);
  }, [onRefreshComplete]);

  const quoteCoverage = useCallback(
    async (coverageUsd, durationHours) => {
      if (!factoryAddr || !collateralAddr || !positionAddr) return null;
      assertRuntimeReady(infrastructure);
      const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
      if (!poolKey) return null;

      const coverageWei = ethers.parseUnits(String(coverageUsd), 6);
      const durationSec = BigInt(Math.max(1, Math.round(durationHours))) * 3600n;
      const factory = new ethers.Contract(
        factoryAddr,
        CDS_COVERAGE_FACTORY_ABI,
        rpcProvider,
      );
      const result = await factory.quoteOpenCoverage.staticCall(
        coverageWei,
        durationSec,
        poolKey,
      );
      return {
        initialPositionTokens: Number(ethers.formatUnits(result[0], 6)),
        initialCost: Number(ethers.formatUnits(result[1], 6)),
        premiumBudget: Number(ethers.formatUnits(result[2], 6)),
        totalRequired: Number(ethers.formatUnits(result[3], 6)),
        raw: result,
      };
    },
    [factoryAddr, infrastructure, collateralAddr, positionAddr],
  );

  const openCoverage = useCallback(
    async (coverageUsd, durationHours, onSuccess, { maxSlippage = 0.1 } = {}) => {
      if (!account || !factoryAddr || !collateralAddr || !positionAddr) {
        setError("Coverage factory not available — waiting for config");
        return;
      }

      const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
      if (!poolKey) {
        setError("Pool configuration unavailable");
        return;
      }

      if (!window.ethereum) {
        setError("MetaMask not found");
        return;
      }

      setExecuting(true);
      if (pauseRef) pauseRef.current = true;
      setError(null);
      setTxHash(null);
      setStep("Preparing coverage...");

      try {
        assertRuntimeReady(infrastructure);
        const coverageWei = ethers.parseUnits(String(coverageUsd), 6);
        const durationSec = BigInt(Math.max(1, Math.round(durationHours))) * 3600n;

        const factoryReader = new ethers.Contract(
          factoryAddr,
          CDS_COVERAGE_FACTORY_ABI,
          rpcProvider,
        );
        const quote = await factoryReader.quoteOpenCoverage.staticCall(
          coverageWei,
          durationSec,
          poolKey,
        );
        const totalRequired = quote[3];

        setStep("Checking USDC balance...");
        const tokenReader = new ethers.Contract(collateralAddr, ERC20_ABI, rpcProvider);
        const balance = await tokenReader.balanceOf(account);
        if (balance < totalRequired) {
          const need = Number(ethers.formatUnits(totalRequired, 6)).toFixed(2);
          const have = Number(ethers.formatUnits(balance, 6)).toFixed(2);
          setError(`Insufficient USDC — need $${need}, have $${have}`);
          return;
        }

        setStep("Checking approval...");
        const allowance = await tokenReader.allowance(account, factoryAddr);
        let signer;
        if (allowance < totalRequired) {
          signer = await getSigner();
          setStep("Approve USDC for CDS coverage...");
          const token = new ethers.Contract(collateralAddr, ERC20_ABI, signer);
          const approveTx = await token.approve(factoryAddr, ethers.MaxUint256);
          await approveTx.wait();
        }

        if (!signer) signer = await getSigner();
        const factory = new ethers.Contract(
          factoryAddr,
          CDS_COVERAGE_FACTORY_ABI,
          signer,
        );

        setStep("Previewing coverage route...");
        const openPreview = await readCoveragePreview(
          () => factory.previewOpenCoverage.staticCall(
            coverageWei,
            durationSec,
            poolKey,
          ),
          "CoverageOpenPreview",
        );
        const minInitialPositionReceived = minWithSlippage(
          openPreview[0],
          maxSlippage,
        );

        setStep("Open fixed coverage...");
        const tx = await factory.openCoverage(
          coverageWei,
          durationSec,
          poolKey,
          minInitialPositionReceived,
          {
            gasLimit: 5_000_000,
          },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();
        if (receipt.status === 1) {
          const brokerAddress = extractBroker(receipt);
          await _syncAndNotify("Coverage opened ✓", onSuccess, {
            ...receipt,
            brokerAddress,
            quote,
          });
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[CDS] openCoverage failed:", e);
        const msg =
          e.code === "ACTION_REJECTED"
            ? "Transaction rejected"
            : e.reason || e.shortMessage || e.message || "Coverage failed";
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
        if (pauseRef) pauseRef.current = false;
      }
    },
    [
      account,
      factoryAddr,
      collateralAddr,
      positionAddr,
      infrastructure,
      _syncAndNotify,
      pauseRef,
    ],
  );

  const closeCoverage = useCallback(
    async (brokerAddress, onSuccess, { maxSlippage = 0.1 } = {}) => {
      if (!account || !factoryAddr || !brokerAddress || !collateralAddr || !positionAddr) {
        setError("Coverage factory not available — waiting for config");
        return;
      }

      const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
      if (!poolKey) {
        setError("Pool configuration unavailable");
        return;
      }

      if (!window.ethereum) {
        setError("MetaMask not found");
        return;
      }

      setExecuting(true);
      if (pauseRef) pauseRef.current = true;
      setError(null);
      setTxHash(null);
      setStep("Preparing close...");

      try {
        assertRuntimeReady(infrastructure);
        const signer = await getSigner();
        const factory = new ethers.Contract(
          factoryAddr,
          CDS_COVERAGE_FACTORY_ABI,
          signer,
        );

        setStep("Previewing close route...");
        const closePreview = await readCoveragePreview(
          () => factory.previewCloseCoverage.staticCall(brokerAddress, poolKey),
          "CoverageClosePreview",
        );
        const minCollateralReturned = minWithSlippage(
          closePreview[0],
          maxSlippage,
        );

        setStep("Close fixed coverage...");
        const tx = await factory.closeCoverage(
          brokerAddress,
          poolKey,
          minCollateralReturned,
          {
            gasLimit: 5_000_000,
          },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();
        if (receipt.status === 1) {
          await _syncAndNotify("Coverage closed ✓", onSuccess, {
            ...receipt,
            brokerAddress,
          });
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[CDS] closeCoverage failed:", e);
        const msg =
          e.code === "ACTION_REJECTED"
            ? "Transaction rejected"
            : e.reason || e.shortMessage || e.message || "Close failed";
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
        if (pauseRef) pauseRef.current = false;
      }
    },
    [
      account,
      factoryAddr,
      collateralAddr,
      positionAddr,
      infrastructure,
      _syncAndNotify,
      pauseRef,
    ],
  );

  return {
    openCoverage,
    closeCoverage,
    quoteCoverage,
    coverageFactory: factoryAddr,
    executing,
    error,
    step,
    txHash,
  };
}
