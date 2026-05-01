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
    ],
    outputs: [{ name: "broker", type: "address" }],
  },
  {
    name: "closeCoverage",
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
    async (coverageUsd, durationHours, onSuccess) => {
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

        setStep("Open fixed coverage...");
        const tx = await factory.openCoverage(coverageWei, durationSec, poolKey, {
          gasLimit: 5_000_000,
        });
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
    async (brokerAddress, onSuccess) => {
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
        const signer = await getSigner();
        const factory = new ethers.Contract(
          factoryAddr,
          CDS_COVERAGE_FACTORY_ABI,
          signer,
        );

        setStep("Close fixed coverage...");
        const tx = await factory.closeCoverage(brokerAddress, poolKey, {
          gasLimit: 5_000_000,
        });
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
