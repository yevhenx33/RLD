import React, { useEffect, useState } from "react";
import { CheckCircle, AlertTriangle, X, Info, Droplets } from "lucide-react";

/**
 * Toast notification system — top-right corner, matches terminal design system.
 *
 * Usage:
 *   const { addToast, ToastContainer } = useToast();
 *   addToast({ type: "success", title: "Funded", message: "100K waUSDC credited" });
 *   return <><ToastContainer /><YourApp /></>;
 */

// ── Individual Toast ──────────────────────────────────────────────

const ICONS = {
  success: CheckCircle,
  error: AlertTriangle,
  info: Info,
  faucet: Droplets,
};

const ACCENT = {
  success: {
    border: "border-emerald-500/40",
    glow: "shadow-emerald-500/10",
    icon: "text-emerald-400",
    bar: "bg-emerald-500",
  },
  error: {
    border: "border-red-500/40",
    glow: "shadow-red-500/10",
    icon: "text-red-400",
    bar: "bg-red-500",
  },
  info: {
    border: "border-cyan-500/40",
    glow: "shadow-cyan-500/10",
    icon: "text-cyan-400",
    bar: "bg-cyan-500",
  },
  faucet: {
    border: "border-cyan-500/40",
    glow: "shadow-cyan-500/10",
    icon: "text-cyan-400",
    bar: "bg-cyan-500",
  },
};

function Toast({
  id,
  type = "info",
  title,
  message,
  duration = 5000,
  onDismiss,
}) {
  const [visible, setVisible] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [progress, setProgress] = useState(100);

  const style = ACCENT[type] || ACCENT.info;
  const Icon = ICONS[type] || ICONS.info;

  useEffect(() => {
    // Trigger enter animation
    requestAnimationFrame(() => setVisible(true));

    // Progress bar countdown
    const startTime = Date.now();
    const interval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const remaining = Math.max(0, 100 - (elapsed / duration) * 100);
      setProgress(remaining);
      if (remaining <= 0) clearInterval(interval);
    }, 50);

    // Auto-dismiss
    const timer = setTimeout(() => {
      setExiting(true);
      setTimeout(() => onDismiss(id), 300);
    }, duration);

    return () => {
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [id, duration, onDismiss]);

  const handleDismiss = () => {
    setExiting(true);
    setTimeout(() => onDismiss(id), 300);
  };

  return (
    <div
      className={`
        relative overflow-hidden
        w-[340px] border ${style.border}
        bg-[#0a0a0a]/95 backdrop-blur-md
        shadow-lg ${style.glow}
        font-mono
        transform transition-all duration-300 ease-out
        ${
          visible && !exiting
            ? "translate-x-0 opacity-100"
            : "translate-x-[120%] opacity-0"
        }
      `}
    >
      {/* Content */}
      <div className="flex items-start gap-3 p-3.5">
        <div className={`mt-0.5 ${style.icon}`}>
          <Icon size={16} strokeWidth={2} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-bold tracking-[0.12em] uppercase text-white/90">
            {title}
          </div>
          {message && (
            <div className="text-[10px] text-gray-400 mt-1 leading-relaxed truncate">
              {message}
            </div>
          )}
        </div>
        <button
          onClick={handleDismiss}
          className="text-gray-600 hover:text-gray-400 transition-colors mt-0.5"
        >
          <X size={12} />
        </button>
      </div>

      {/* Progress bar */}
      <div className="h-[2px] w-full bg-white/5">
        <div
          className={`h-full ${style.bar} opacity-60 transition-all duration-100 ease-linear`}
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

// ── Toast Container ───────────────────────────────────────────────

export function ToastContainer({ toasts, removeToast }) {
  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-auto">
      {toasts.map((toast) => (
        <Toast key={toast.id} {...toast} onDismiss={removeToast} />
      ))}
    </div>
  );
}

export default Toast;
