import React, { useState, useEffect, useMemo } from "react";
import {
  X,
  Check,
  Wallet,
  Shield,
  CircleDollarSign,
  Droplets,
  Loader2,
} from "lucide-react";
import { useWallet } from "../context/WalletContext";
import { useBrokerAccount } from "../hooks/useBrokerAccount";
import { useFaucet } from "../hooks/useFaucet";

/**
 * 3-step waterfall modal for broker account onboarding.
 *
 * Steps:
 *   1. Connect Wallet   → MetaMask prompt
 *   2. Create Account   → Deploy PrimeBroker via factory
 *   3. Deposit Funds    → Transfer waUSDC to broker
 */
export default function AccountModal({
  isOpen,
  onClose,
  onComplete,
  brokerFactoryAddr,
  waUsdcAddr,
}) {
  const { account, connectWallet } = useWallet();
  const {
    hasBroker,
    brokerAddress,
    creating,
    depositing,
    error,
    step: statusText,
    createBroker,
    depositFunds,
  } = useBrokerAccount(account, brokerFactoryAddr, waUsdcAddr);

  const {
    requestFaucet,
    loading: faucetLoading,
    error: faucetError,
    waUsdcBalance,
  } = useFaucet(account, waUsdcAddr);

  const [depositAmount, setDepositAmount] = useState("10000");
  const [faucetDone, setFaucetDone] = useState(false);

  // ── Determine current step ───────────────────────────────────
  // 1: Connect Wallet → 2: Request Funds → 3: Create Account → 4: Deposit
  const currentStep = useMemo(() => {
    if (!account) return 1;
    if (!faucetDone && !waUsdcBalance) return 2;
    if (!hasBroker) return 3;
    return 4;
  }, [account, hasBroker, faucetDone, waUsdcBalance]);

  const [completedDeposit, setCompletedDeposit] = useState(false);

  // Reset completed state when modal closes
  useEffect(() => {
    if (!isOpen) {
      setCompletedDeposit(false);
      setFaucetDone(false);
    }
  }, [isOpen]);

  // Auto-advance past faucet step once balance arrives
  useEffect(() => {
    if (waUsdcBalance && parseFloat(waUsdcBalance) > 0) {
      setFaucetDone(true);
    }
  }, [waUsdcBalance]);

  if (!isOpen) return null;

  // ── Step definitions ─────────────────────────────────────────
  const steps = [
    {
      num: 1,
      label: "Connect_Wallet",
      icon: Wallet,
      description: account
        ? `0x${account.substring(2, 8)}...${account.substring(38)}`
        : "Link your Ethereum wallet",
    },
    {
      num: 2,
      label: "Request_Funds",
      icon: Droplets,
      description:
        faucetDone || (waUsdcBalance && parseFloat(waUsdcBalance) > 0)
          ? `${parseFloat(waUsdcBalance || 0).toLocaleString()} waUSDC received`
          : "Get testnet ETH + waUSDC",
    },
    {
      num: 3,
      label: "Create_Account",
      icon: Shield,
      description: brokerAddress
        ? `0x${brokerAddress.substring(2, 8)}...${brokerAddress.substring(38)}`
        : "Deploy your trading account",
    },
    {
      num: 4,
      label: "Deposit_Funds",
      icon: CircleDollarSign,
      description: completedDeposit
        ? `${Number(depositAmount).toLocaleString()} waUSDC deposited`
        : "Fund account with waUSDC",
    },
  ];

  const getStepState = (stepNum) => {
    if (completedDeposit && stepNum <= 4) return "completed";
    if (stepNum < currentStep) return "completed";
    if (stepNum === currentStep) return "active";
    return "pending";
  };

  // ── Action button logic ──────────────────────────────────────
  const getAction = () => {
    if (completedDeposit) {
      return {
        label: "Done",
        onClick: () => {
          onComplete?.(brokerAddress);
          onClose();
        },
        disabled: false,
        variant: "green",
      };
    }

    switch (currentStep) {
      case 1:
        return {
          label: "Connect_Wallet",
          onClick: connectWallet,
          disabled: false,
          variant: "cyan",
        };
      case 2:
        return {
          label: faucetLoading ? "Requesting..." : "Request_Funds",
          onClick: async () => {
            await requestFaucet(account);
          },
          disabled: faucetLoading,
          variant: "cyan",
        };
      case 3:
        return {
          label: "Deploy_Account",
          onClick: createBroker,
          disabled: creating || hasBroker === null,
          variant: "cyan",
        };
      case 4:
        return {
          label: "Deposit_Collateral",
          onClick: async () => {
            await depositFunds(depositAmount);
            setCompletedDeposit(true);
          },
          disabled: depositing || !depositAmount || Number(depositAmount) <= 0,
          variant: "cyan",
        };
      default:
        return {
          label: "—",
          onClick: () => {},
          disabled: true,
          variant: "cyan",
        };
    }
  };

  const action = getAction();
  const isLoading = creating || depositing || faucetLoading;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md bg-[#080808] border border-white/10 shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-white/10 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div
              className={`w-2 h-2 ${completedDeposit ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]" : "bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.5)]"}`}
            />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              Onboard_Account
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white transition-colors rounded-none"
          >
            <X size={18} />
          </button>
        </div>

        {/* Steps */}
        <div className="p-6 flex flex-col gap-1">
          {steps.map((s, idx) => {
            const state = getStepState(s.num);
            const Icon = s.icon;

            return (
              <div key={s.num} className="flex items-stretch gap-4">
                {/* Vertical line + dot */}
                <div className="flex flex-col items-center w-6 shrink-0">
                  {/* Dot */}
                  <div
                    className={`w-6 h-6 flex items-center justify-center border shrink-0 ${
                      state === "completed"
                        ? "border-green-500/50 bg-green-500/10 text-green-400"
                        : state === "active"
                          ? "border-cyan-500/50 bg-cyan-500/10 text-cyan-400"
                          : "border-white/10 bg-white/[0.02] text-gray-600"
                    }`}
                  >
                    {state === "completed" ? (
                      <Check size={12} strokeWidth={3} />
                    ) : (
                      <span className="text-[10px] font-bold">{s.num}</span>
                    )}
                  </div>
                  {/* Connector line */}
                  {idx < steps.length - 1 && (
                    <div
                      className={`w-px flex-1 min-h-[16px] ${
                        state === "completed"
                          ? "bg-green-500/30"
                          : "bg-white/10"
                      }`}
                    />
                  )}
                </div>

                {/* Content */}
                <div
                  className={`pb-5 flex-1 ${idx === steps.length - 1 ? "pb-0" : ""}`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <Icon
                        size={12}
                        className={
                          state === "completed"
                            ? "text-green-400"
                            : state === "active"
                              ? "text-cyan-400"
                              : "text-gray-600"
                        }
                      />
                      <span
                        className={`text-xs font-bold tracking-[0.15em] uppercase ${
                          state === "completed"
                            ? "text-green-400"
                            : state === "active"
                              ? "text-white"
                              : "text-gray-600"
                        }`}
                      >
                        {s.label}
                      </span>
                    </div>
                    <span
                      className={`text-[10px] uppercase tracking-widest font-bold ${
                        state === "completed"
                          ? "text-green-500/60"
                          : state === "active"
                            ? "text-cyan-500/60"
                            : "text-gray-700"
                      }`}
                    >
                      {state === "completed"
                        ? "Done"
                        : state === "active"
                          ? "Active"
                          : "Pending"}
                    </span>
                  </div>
                  <p
                    className={`text-[11px] font-mono ${
                      state === "completed"
                        ? "text-green-400/50"
                        : state === "active"
                          ? "text-gray-400"
                          : "text-gray-700"
                    }`}
                  >
                    {s.description}
                  </p>

                  {/* Deposit amount input — only show when step 4 is active */}
                  {s.num === 4 && state === "active" && !completedDeposit && (
                    <div className="mt-3 flex items-center gap-2">
                      <div className="flex-1 relative">
                        <input
                          type="number"
                          value={depositAmount}
                          onChange={(e) => setDepositAmount(e.target.value)}
                          placeholder="10000"
                          className="w-full bg-white/[0.03] border border-white/10 px-3 py-2 text-xs font-mono text-white focus:border-cyan-500/50 focus:outline-none rounded-none"
                        />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-gray-500 uppercase tracking-widest">
                          waUSDC
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Action Button */}
        <div className="px-6 pb-5">
          <button
            onClick={action.onClick}
            disabled={action.disabled}
            className={`w-full py-4 text-xs font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none flex items-center justify-center gap-2 ${
              action.variant === "green"
                ? "bg-green-500 text-black hover:bg-green-400"
                : "bg-cyan-500 text-black hover:bg-cyan-400"
            } ${action.disabled ? "opacity-50 cursor-not-allowed" : "hover:opacity-90"}`}
          >
            {isLoading && <Loader2 size={14} className="animate-spin" />}
            {action.label}
          </button>
        </div>

        {/* Footer Status */}
        <div className="px-6 pb-5">
          <div className="flex items-center gap-2 text-[10px] font-mono pt-2 border-t border-white/5">
            {error ? (
              <span className="text-red-400">ERROR: {error}</span>
            ) : statusText ? (
              <span className="text-cyan-400/60">{statusText}</span>
            ) : (
              <span className="text-gray-600">
                STEP {currentStep}/4 ·{" "}
                {completedDeposit ? "COMPLETE" : "AWAITING_INPUT"}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
