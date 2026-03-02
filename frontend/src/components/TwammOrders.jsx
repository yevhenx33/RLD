import React, {
  useMemo,
  useState,
  useEffect,
  useRef,
  useCallback,
} from "react";
import { useSimulation } from "../hooks/useSimulation";
import { useTwammDashboard } from "../hooks/useTwammDashboard";
import {
  Activity,
  Clock,
  TrendingUp,
  TrendingDown,
  Zap,
  RefreshCw,
  ArrowRightLeft,
  Timer,
  Droplets,
  ChevronDown,
  ChevronRight,
  Shield,
  Target,
  BarChart3,
  Terminal,
} from "lucide-react";

// ── Helpers ─────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, icon: Icon, accent = "cyan" }) {
  const colors = {
    cyan: "border-cyan-500/20 text-cyan-400",
    green: "border-green-500/20 text-green-400",
    amber: "border-amber-500/20 text-amber-400",
    purple: "border-purple-500/20 text-purple-400",
    red: "border-red-500/20 text-red-400",
  };
  return (
    <div
      className={`border ${colors[accent]} bg-white/[0.02] p-4 flex flex-col gap-1`}
    >
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-gray-500">
        {Icon && <Icon size={12} />}
        {label}
      </div>
      <div className="text-lg font-bold tracking-tight font-mono">{value}</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  );
}

function ProgressBar({ percent, isDone }) {
  const color = isDone
    ? "bg-gray-600"
    : percent > 80
      ? "bg-green-500"
      : percent > 40
        ? "bg-cyan-500"
        : "bg-amber-500";
  return (
    <div className="w-full h-1.5 bg-white/5 overflow-hidden">
      <div
        className={`h-full ${color} transition-all duration-500`}
        style={{ width: `${Math.min(100, percent)}%` }}
      />
    </div>
  );
}

function DirectionBadge({ isBuy }) {
  return isBuy ? (
    <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-green-400 bg-green-500/10 border border-green-500/20 px-2 py-0.5">
      <TrendingUp size={10} /> BUY
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-red-400 bg-red-500/10 border border-red-500/20 px-2 py-0.5">
      <TrendingDown size={10} /> SELL
    </span>
  );
}

function StatusBadge({ isDone, isExpired, isPending }) {
  if (isDone || isExpired) {
    return (
      <span className="text-[10px] font-bold uppercase tracking-widest text-gray-500 bg-white/5 border border-white/10 px-2 py-0.5">
        {isExpired ? "EXPIRED" : "DONE"}
      </span>
    );
  }
  if (isPending) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5">
        <div className="w-1.5 h-1.5 bg-amber-400 animate-pulse" /> PENDING
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 px-2 py-0.5">
      <div className="w-1.5 h-1.5 bg-cyan-400 animate-pulse" /> LIVE
    </span>
  );
}

function PreservationBadge({ pct }) {
  if (pct >= 99)
    return <span className="text-green-400 font-bold">{pct.toFixed(1)}%</span>;
  if (pct >= 95)
    return <span className="text-cyan-400 font-bold">{pct.toFixed(1)}%</span>;
  if (pct >= 90)
    return <span className="text-amber-400 font-bold">{pct.toFixed(1)}%</span>;
  return <span className="text-red-400 font-bold">{pct.toFixed(1)}%</span>;
}

function fmtUsd(n) {
  if (Math.abs(n) >= 1000)
    return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  if (Math.abs(n) >= 1) return `$${n.toFixed(2)}`;
  if (Math.abs(n) >= 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(6)}`;
}

function fmtTokens(n, decimals = 2) {
  if (Math.abs(n) >= 1000)
    return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toFixed(decimals);
}

// ── Main Component ──────────────────────────────────────────────────

export default function TwammOrders() {
  const { marketInfo } = useSimulation();
  const { orders, streamState, config, loading, lastRefresh, refresh } =
    useTwammDashboard(marketInfo);

  const activeOrders = useMemo(() => orders.filter((o) => !o.isDone), [orders]);
  const doneOrders = useMemo(() => orders.filter((o) => o.isDone), [orders]);

  // Aggregate stats
  const agg = useMemo(() => {
    const a = {
      totalSpentUsd: 0,
      totalEarnedUsd: 0,
      totalGhostUsd: 0,
      totalDiscountCost: 0,
      totalIdealUsd: 0,
      totalActualUsd: 0,
      totalDepositUsd: 0,
      totalValueUsd: 0,
    };
    for (const o of orders) {
      a.totalSpentUsd += o.spentUsd || 0;
      a.totalEarnedUsd += o.earnedUsd || 0;
      a.totalGhostUsd += o.discountedGhostUsd || 0;
      a.totalDiscountCost += o.discountCostUsd || 0;
      a.totalIdealUsd += o.idealOutputUsd || 0;
      a.totalActualUsd += o.actualOutputUsd || 0;
      a.totalDepositUsd += o.depositUsd || 0;
      a.totalValueUsd += o.valueUsd || 0;
    }
    a.preservation =
      a.totalSpentUsd > 0 ? (a.totalActualUsd / a.totalSpentUsd) * 100 : 0;
    a.leakage = 100 - a.preservation;
    return a;
  }, [orders]);

  // Ghost in USD
  const ghostUsd = useMemo(() => {
    if (!streamState) return 0;
    const price = orders[0]?.markPrice || 1;
    return (streamState.accrued0 / 1e6) * price + streamState.accrued1 / 1e6;
  }, [streamState, orders]);

  return (
    <div className="min-h-screen bg-[#050505] text-white">
      <div className="max-w-[1400px] mx-auto px-6 py-8">
        {/* ── Header ──────────────────────────────────── */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <ArrowRightLeft size={16} className="text-cyan-400" />
              <h1 className="text-sm font-bold uppercase tracking-[0.2em]">
                TWAMM Orders
              </h1>
            </div>
            <span className="text-[10px] text-gray-600 uppercase tracking-widest">
              Real-time dashboard
            </span>
          </div>
          <div className="flex items-center gap-4">
            {lastRefresh && (
              <span className="text-[10px] text-gray-600 font-mono">
                {lastRefresh.toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={refresh}
              className="p-2 border border-white/10 hover:border-cyan-500/30 hover:bg-cyan-500/5 transition-all text-gray-500 hover:text-cyan-400"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            </button>
          </div>
        </div>

        {/* ── System Metrics ──────────────────────────── */}
        {streamState && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
            <MetricCard
              label="Discount"
              value={`${(streamState.discountBps / 100).toFixed(2)}%`}
              sub={
                config ? `max ${(config.maxDiscountBps / 100).toFixed(1)}%` : ""
              }
              icon={Zap}
              accent="amber"
            />
            <MetricCard
              label="Since Clear"
              value={`${streamState.timeSinceClear}s`}
              sub={streamState.timeSinceClear > 60 ? "overdue" : "recent"}
              icon={Timer}
              accent={streamState.timeSinceClear > 120 ? "amber" : "green"}
            />
            <MetricCard
              label="Ghost"
              value={fmtUsd(ghostUsd)}
              sub="uncleared balance"
              icon={Droplets}
              accent="purple"
            />
            <MetricCard
              label="Active Orders"
              value={activeOrders.length}
              sub={`${doneOrders.length} expired`}
              icon={Activity}
              accent="cyan"
            />
            <MetricCard
              label="Total Value"
              value={fmtUsd(agg.totalValueUsd)}
              sub="refund + earned"
              icon={TrendingUp}
              accent="green"
            />
            <MetricCard
              label="Total Earned"
              value={fmtUsd(agg.totalEarnedUsd)}
              sub="cleared earnings"
              icon={Zap}
              accent="cyan"
            />
          </div>
        )}

        {/* ── VALUE PRESERVATION ANALYSIS ─────────────── */}
        {orders.length > 0 && agg.totalSpentUsd > 0 && (
          <div className="border border-cyan-500/20 bg-gradient-to-b from-cyan-500/[0.03] to-transparent mb-8">
            <div className="px-5 py-3 border-b border-cyan-500/10 flex items-center gap-2">
              <Shield size={14} className="text-cyan-400" />
              <span className="text-xs font-bold uppercase tracking-[0.2em] text-cyan-400">
                Value Preservation Analysis
              </span>
              <span className="text-[10px] text-gray-600 ml-2">
                vs. ideal instant swap at pool price
              </span>
            </div>

            {/* Aggregate summary */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-px bg-white/5">
              <div className="bg-[#050505] p-4">
                <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-1">
                  Tokens Spent
                </div>
                <div className="text-base font-bold font-mono text-gray-300">
                  {fmtUsd(agg.totalSpentUsd)}
                </div>
                <div className="text-[10px] text-gray-600">
                  consumed by TWAMM
                </div>
              </div>
              <div className="bg-[#050505] p-4">
                <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-1">
                  Cleared Earnings
                </div>
                <div className="text-base font-bold font-mono text-green-400">
                  {fmtUsd(agg.totalEarnedUsd)}
                </div>
                <div className="text-[10px] text-gray-600">
                  buy tokens received
                </div>
              </div>
              <div className="bg-[#050505] p-4">
                <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-1">
                  Pending Ghost
                </div>
                <div className="text-base font-bold font-mono text-purple-400">
                  {fmtUsd(agg.totalGhostUsd)}
                </div>
                <div className="text-[10px] text-gray-600">
                  after discount ({fmtUsd(agg.totalDiscountCost)} lost)
                </div>
              </div>
              <div className="bg-[#050505] p-4">
                <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-1">
                  Ideal (Pool Swap)
                </div>
                <div className="text-base font-bold font-mono text-gray-400">
                  {fmtUsd(agg.totalIdealUsd)}
                </div>
                <div className="text-[10px] text-gray-600">
                  same qty at pool price
                </div>
              </div>
              <div className="bg-[#050505] p-4">
                <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-1">
                  Preservation
                </div>
                <div className="text-base font-bold font-mono">
                  <PreservationBadge pct={agg.preservation} />
                </div>
                <div className="text-[10px] text-gray-600">
                  {agg.leakage > 0
                    ? `${agg.leakage.toFixed(2)}% leakage`
                    : "zero leakage"}
                </div>
              </div>
            </div>

            {/* Per-order breakdown */}
            <div className="px-5 py-3 border-t border-white/5">
              <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-3 flex items-center gap-2">
                <BarChart3 size={11} />
                Per-Order Breakdown
              </div>
              <div className="space-y-2">
                {orders
                  .filter((o) => o.tokensSpent > 0)
                  .map((o) => (
                    <OrderAnalysisRow key={o.orderId} order={o} />
                  ))}
              </div>
            </div>

            {/* Explanation */}
            <div className="px-5 py-3 border-t border-white/5 text-[10px] text-gray-600 space-y-1">
              <div className="flex items-start gap-2">
                <Target
                  size={10}
                  className="mt-0.5 text-gray-700 flex-shrink-0"
                />
                <span>
                  <strong className="text-gray-500">Ideal Swap</strong> = if you
                  sold the exact same qty of tokens in a single atomic swap at
                  the current pool price (${orders[0]?.poolPrice?.toFixed(4)}
                  /wRLP). Zero slippage, zero time-cost.
                </span>
              </div>
              <div className="flex items-start gap-2">
                <Target
                  size={10}
                  className="mt-0.5 text-gray-700 flex-shrink-0"
                />
                <span>
                  <strong className="text-gray-500">Preservation</strong> =
                  (cleared earnings + discounted ghost) / ideal. Above 100%
                  means TWAMM is outperforming mark, below 100% means leakage to
                  discount + slippage.
                </span>
              </div>
              <div className="flex items-start gap-2">
                <Target
                  size={10}
                  className="mt-0.5 text-gray-700 flex-shrink-0"
                />
                <span>
                  <strong className="text-gray-500">Ghost</strong> = earned
                  tokens not yet cleared. Subject to discount (
                  {streamState
                    ? (streamState.discountBps / 100).toFixed(2)
                    : "?"}
                  % now). Clear bot converts ghost → real each cycle.
                </span>
              </div>
            </div>
          </div>
        )}

        {/* ── Sell Rate Summary ───────────────────────── */}
        {streamState && (
          <div className="flex flex-wrap gap-4 md:gap-6 mb-6 text-[11px] font-mono text-gray-500 border border-white/5 bg-white/[0.01] px-4 py-3">
            <span>
              <span className="text-gray-600 uppercase tracking-widest mr-2">
                ▶ Sell 0→1:
              </span>
              <span className="text-green-400 font-bold">
                {(streamState.sellRate0For1 / 1e18).toFixed(0)}
              </span>
              <span className="text-gray-600 ml-1">/s</span>
            </span>
            <span className="text-white/10">|</span>
            <span>
              <span className="text-gray-600 uppercase tracking-widest mr-2">
                ◀ Sell 1→0:
              </span>
              <span className="text-red-400 font-bold">
                {(streamState.sellRate1For0 / 1e18).toFixed(0)}
              </span>
              <span className="text-gray-600 ml-1">/s</span>
            </span>
            <span className="text-white/10">|</span>
            <span>
              <span className="text-gray-600 uppercase tracking-widest mr-2">
                Net:
              </span>
              <span className="text-cyan-400">
                {Math.abs(
                  streamState.sellRate0For1 / 1e18 -
                    streamState.sellRate1For0 / 1e18,
                ).toFixed(0)}
              </span>
              <span className="text-gray-600 ml-1">
                {streamState.sellRate0For1 > streamState.sellRate1For0
                  ? "(→ buy wRLP)"
                  : "(→ buy waUSDC)"}
              </span>
            </span>
            {config && (
              <>
                <span className="text-white/10">|</span>
                <span>
                  <span className="text-gray-600 uppercase tracking-widest mr-2">
                    Rate:
                  </span>
                  <span className="text-amber-400">
                    {(config.discountRateScaled / 10000).toFixed(2)}
                  </span>
                  <span className="text-gray-600 ml-1">bps/s</span>
                </span>
              </>
            )}
          </div>
        )}

        {/* ── Clear Bot Logs ────────────────────────── */}
        <ClearBotLogs />

        {/* ── Loading State ──────────────────────────── */}
        {loading && orders.length === 0 && (
          <div className="flex items-center justify-center py-20 text-gray-600 text-xs uppercase tracking-widest animate-pulse">
            <RefreshCw size={14} className="animate-spin mr-3" /> Scanning
            on-chain events…
          </div>
        )}

        {/* ── Active Orders ──────────────────────────── */}
        {activeOrders.length > 0 && (
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-4">
              <div className="w-1.5 h-1.5 bg-cyan-400 animate-pulse" />
              <h2 className="text-xs font-bold uppercase tracking-[0.2em] text-gray-400">
                Active Orders ({activeOrders.length})
              </h2>
            </div>
            <div className="border border-white/5">
              <div className="hidden md:grid grid-cols-[1fr_80px_100px_100px_100px_80px_100px_60px] gap-2 px-4 py-2 text-[10px] uppercase tracking-widest text-gray-600 bg-white/[0.02] border-b border-white/5">
                <span>Owner</span>
                <span>Direction</span>
                <span className="text-right">Deposit</span>
                <span className="text-right">Earned</span>
                <span className="text-right">Refund</span>
                <span className="text-right">Progress</span>
                <span className="text-right">Time Left</span>
                <span className="text-right">Status</span>
              </div>
              {activeOrders.map((order) => (
                <OrderRow key={order.orderId} order={order} />
              ))}
            </div>
          </div>
        )}

        {/* ── Expired / Done Orders ──────────────────── */}
        {doneOrders.length > 0 && (
          <div>
            <details>
              <summary className="flex items-center gap-2 mb-4 cursor-pointer text-xs font-bold uppercase tracking-[0.2em] text-gray-600 hover:text-gray-400 transition-colors">
                <Clock size={12} /> Expired Orders ({doneOrders.length})
              </summary>
              <div className="border border-white/5 opacity-60">
                <div className="hidden md:grid grid-cols-[1fr_80px_100px_100px_100px_80px_100px_60px] gap-2 px-4 py-2 text-[10px] uppercase tracking-widest text-gray-600 bg-white/[0.02] border-b border-white/5">
                  <span>Owner</span>
                  <span>Direction</span>
                  <span className="text-right">Deposit</span>
                  <span className="text-right">Earned</span>
                  <span className="text-right">Refund</span>
                  <span className="text-right">Progress</span>
                  <span className="text-right">Time Left</span>
                  <span className="text-right">Status</span>
                </div>
                {doneOrders.map((order) => (
                  <OrderRow key={order.orderId} order={order} />
                ))}
              </div>
            </details>
          </div>
        )}

        {/* ── Empty State ────────────────────────────── */}
        {!loading && orders.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-gray-600">
            <ArrowRightLeft size={32} className="mb-4 opacity-30" />
            <span className="text-xs uppercase tracking-widest">
              No TWAMM orders found
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Order Analysis Row ────────────────────────────────────────────

function OrderAnalysisRow({ order }) {
  const [expanded, setExpanded] = useState(false);
  const o = order;

  // Waterfall bar widths (% of ideal)
  const idealBase = o.idealOutputUsd || 1;
  const earnedPct = (o.earnedUsd / idealBase) * 100;
  const ghostPct = (o.discountedGhostUsd / idealBase) * 100;
  const discountPct = (o.discountCostUsd / idealBase) * 100;
  const leakagePct = Math.max(0, 100 - earnedPct - ghostPct - discountPct);

  return (
    <div className="border border-white/5 bg-white/[0.01]">
      {/* Summary row */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-3 py-2 hover:bg-white/[0.02] transition-colors text-left"
      >
        {expanded ? (
          <ChevronDown size={12} className="text-gray-600" />
        ) : (
          <ChevronRight size={12} className="text-gray-600" />
        )}
        <DirectionBadge isBuy={o.isBuy} />
        <span className="text-[11px] font-mono text-gray-400 w-20">
          {o.ownerShort}
        </span>
        <span className="text-[11px] font-mono text-gray-500 w-28">
          {fmtTokens(o.tokensSpent)} {o.sellToken} spent
        </span>
        <span className="text-[11px] font-mono text-gray-500 mx-1">→</span>
        <span className="text-[11px] font-mono text-green-400 w-28">
          {fmtTokens(o.earned)} {o.buyToken} earned
        </span>

        {/* Mini waterfall bar */}
        <div className="flex-1 flex h-2 bg-white/5 overflow-hidden mx-2">
          <div
            className="h-full bg-green-500/60"
            style={{ width: `${earnedPct}%` }}
            title="Cleared"
          />
          <div
            className="h-full bg-purple-500/60"
            style={{ width: `${ghostPct}%` }}
            title="Ghost"
          />
          <div
            className="h-full bg-amber-500/40"
            style={{ width: `${discountPct}%` }}
            title="Discount cost"
          />
          <div
            className="h-full bg-red-500/20"
            style={{ width: `${leakagePct}%` }}
            title="Slippage"
          />
        </div>

        <div className="w-16 text-right">
          <PreservationBadge pct={o.preservation} />
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 py-3 border-t border-white/5 bg-white/[0.01]">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Left: Token Flow */}
            <div className="space-y-2">
              <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-2">
                Token Flow
              </div>
              <Row
                label="Deposit"
                value={`${fmtTokens(o.amountIn)} ${o.sellToken}`}
                valueUsd={fmtUsd(o.depositUsd)}
              />
              <Row
                label="Refund (unsold)"
                value={`${fmtTokens(o.sellRefund)} ${o.sellToken}`}
                color="text-gray-400"
              />
              <Row
                label="Tokens Spent"
                value={`${fmtTokens(o.tokensSpent)} ${o.sellToken}`}
                valueUsd={fmtUsd(o.spentUsd)}
                color="text-white"
              />
              <div className="border-t border-white/5 my-1" />
              <Row
                label="Cleared Earnings"
                value={`${fmtTokens(o.earned)} ${o.buyToken}`}
                valueUsd={fmtUsd(o.earnedUsd)}
                color="text-green-400"
              />
              <Row
                label="Ghost (pre-discount)"
                value={`${fmtTokens(o.ghostShare, 4)} ${o.buyToken}`}
                valueUsd={fmtUsd(o.ghostShareUsd)}
                color="text-purple-400"
              />
              <Row
                label="Ghost (after discount)"
                value={`${fmtTokens(o.ghostShare * (1 - o.discountCostUsd / (o.ghostShareUsd || 1)), 4)} ${o.buyToken}`}
                valueUsd={fmtUsd(o.discountedGhostUsd)}
                color="text-purple-300"
              />
              <Row
                label="Discount Cost"
                value=""
                valueUsd={`-${fmtUsd(o.discountCostUsd)}`}
                color="text-amber-500"
              />
            </div>

            {/* Right: Comparison vs Ideal */}
            <div className="space-y-2">
              <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-2">
                vs. Ideal Instant Swap
              </div>
              <Row
                label={`Ideal: sell ${fmtTokens(o.tokensSpent)} ${o.sellToken} @ $${o.poolPrice?.toFixed(4)}`}
                value={`${fmtTokens(o.idealOutputTokens)} ${o.buyToken}`}
                valueUsd={fmtUsd(o.idealOutputUsd)}
                color="text-gray-400"
              />
              <Row
                label="TWAMM actual (earned + ghost)"
                value={`≈ ${fmtTokens(o.earned + o.discountedGhostUsd / (o.markPrice || 1), 4)} ${o.buyToken}`}
                valueUsd={fmtUsd(o.actualOutputUsd)}
                color="text-cyan-400"
              />
              <div className="border-t border-white/5 my-1" />
              <Row
                label="Preservation"
                value=""
                valueUsd={<PreservationBadge pct={o.preservation} />}
                color="text-white"
              />
              <Row
                label="Effective exec price"
                value={`${o.effectivePrice.toFixed(4)} waUSDC/wRLP`}
                color="text-gray-400"
              />
              <Row
                label="Mark price"
                value={`${o.markPrice.toFixed(4)} waUSDC/wRLP`}
                color="text-gray-500"
              />
              <Row
                label="Price impact"
                value={`${o.priceImpactBps} bps`}
                color={
                  o.priceImpactBps > 50 ? "text-amber-400" : "text-gray-500"
                }
              />

              {/* Waterfall legend */}
              <div className="mt-3 flex flex-wrap gap-3 text-[10px]">
                <span className="flex items-center gap-1">
                  <div className="w-2 h-2 bg-green-500/60" /> Cleared (
                  {earnedPct.toFixed(0)}%)
                </span>
                <span className="flex items-center gap-1">
                  <div className="w-2 h-2 bg-purple-500/60" /> Ghost (
                  {ghostPct.toFixed(1)}%)
                </span>
                <span className="flex items-center gap-1">
                  <div className="w-2 h-2 bg-amber-500/40" /> Discount (
                  {discountPct.toFixed(2)}%)
                </span>
                <span className="flex items-center gap-1">
                  <div className="w-2 h-2 bg-red-500/20" /> Slippage (
                  {leakagePct.toFixed(1)}%)
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Clear Bot Logs ────────────────────────────────────────────

function ClearBotLogs() {
  const [lines, setLines] = useState([]);
  const [total, setTotal] = useState(0);
  const [expanded, setExpanded] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollRef = useRef(null);

  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch("/_logs/clear-bot");
      if (!res.ok) return;
      const data = await res.json();
      setLines(data.lines || []);
      setTotal(data.total || 0);
    } catch {
      /* skip */
    }
  }, []);

  useEffect(() => {
    fetchLogs();
    const iv = setInterval(fetchLogs, 5000);
    return () => clearInterval(iv);
  }, [fetchLogs]);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  const colorLine = (line) => {
    if (line.includes("✅") || line.includes("succeeded"))
      return "text-green-400";
    if (
      line.includes("❌") ||
      line.includes("reverted") ||
      line.includes("Error")
    )
      return "text-red-400/70";
    if (line.includes("🎯") || line.includes("Clearing"))
      return "text-cyan-400";
    if (line.includes("💰") || line.includes("Balances"))
      return "text-amber-400";
    if (line.includes("📊")) return "text-purple-400";
    if (line.includes("🧹") || line.includes("STARTED") || line.includes("═"))
      return "text-gray-500";
    if (line.includes("🔑") || line.includes("Approved"))
      return "text-gray-500";
    return "text-gray-500";
  };

  // Stats from logs
  const stats = useMemo(() => {
    let clears = 0,
      reverts = 0,
      totalUsd = 0;
    for (const l of lines) {
      if (l.includes("succeeded")) {
        clears++;
        const m = l.match(/Bought\s+([\d.]+)\s+\w+/);
        if (m) totalUsd += parseFloat(m[1]);
      }
      if (l.includes("reverted")) reverts++;
    }
    return { clears, reverts, totalUsd };
  }, [lines]);

  if (lines.length === 0) return null;

  return (
    <div className="border border-green-500/15 bg-white/[0.01] mb-8">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-white/[0.02] transition-colors"
      >
        <div className="flex items-center gap-2">
          <Terminal size={13} className="text-green-400" />
          <span className="text-xs font-bold uppercase tracking-[0.2em] text-green-400">
            Clear Bot
          </span>
          <div className="flex items-center gap-3 ml-3 text-[10px] font-mono text-gray-500">
            <span className="text-green-400">{stats.clears} clears</span>
            <span className="text-white/10">|</span>
            <span className="text-red-400/60">{stats.reverts} reverts</span>
            <span className="text-white/10">|</span>
            <span className="text-amber-400">
              ${stats.totalUsd.toFixed(2)} bought
            </span>
            <span className="text-white/10">|</span>
            <span>{total} lines</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 bg-green-400 animate-pulse" />
          {expanded ? (
            <ChevronDown size={14} className="text-gray-600" />
          ) : (
            <ChevronRight size={14} className="text-gray-600" />
          )}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-white/5">
          <div className="flex items-center justify-end px-3 py-1 border-b border-white/5 bg-white/[0.01]">
            <label className="flex items-center gap-1.5 text-[10px] text-gray-600 cursor-pointer">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="w-3 h-3 accent-green-500"
              />
              auto-scroll
            </label>
          </div>
          <div
            ref={scrollRef}
            className="max-h-[300px] overflow-y-auto overflow-x-hidden px-3 py-2 font-mono text-[11px] leading-[1.6] scrollbar-thin"
          >
            {lines.map((line, i) => (
              <div
                key={i}
                className={`${colorLine(line)} whitespace-pre-wrap break-all`}
              >
                {line}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ label, value, valueUsd, color = "text-gray-300" }) {
  return (
    <div className="flex items-center justify-between text-[11px] font-mono">
      <span className="text-gray-500">{label}</span>
      <div className="flex items-center gap-2">
        {value && <span className={color}>{value}</span>}
        {valueUsd && (
          <span className="text-gray-600 text-[10px]">{valueUsd}</span>
        )}
      </div>
    </div>
  );
}

// ── Order Row ─────────────────────────────────────────────────────

function OrderRow({ order }) {
  return (
    <div className="border-b border-white/5 last:border-b-0 hover:bg-white/[0.02] transition-colors">
      {/* Desktop */}
      <div className="hidden md:grid grid-cols-[1fr_80px_100px_100px_100px_80px_100px_60px] gap-2 items-center px-4 py-3">
        <div className="flex flex-col">
          <span className="text-xs font-mono text-gray-300">
            {order.ownerShort}
          </span>
          <span className="text-[10px] text-gray-600 font-mono">
            {order.direction}
          </span>
        </div>
        <DirectionBadge isBuy={order.isBuy} />
        <span className="text-right text-xs font-mono text-gray-300">
          {order.amountIn.toLocaleString(undefined, {
            maximumFractionDigits: 0,
          })}
          <span className="text-gray-600 ml-1 text-[10px]">
            {order.sellToken}
          </span>
        </span>
        <span className="text-right text-xs font-mono text-green-400">
          {order.earned.toFixed(2)}
          <span className="text-gray-600 ml-1 text-[10px]">
            {order.buyToken}
          </span>
        </span>
        <span className="text-right text-xs font-mono text-amber-400/80">
          {order.sellRefund.toLocaleString(undefined, {
            maximumFractionDigits: 0,
          })}
          <span className="text-gray-600 ml-1 text-[10px]">
            {order.sellToken}
          </span>
        </span>
        <span className="text-right text-xs font-mono text-gray-400">
          {order.progress}%
        </span>
        <span className="text-right text-xs font-mono text-gray-400">
          {order.timeLeft}
        </span>
        <div className="flex justify-end">
          <StatusBadge isDone={order.isDone} isExpired={order.isExpired} isPending={order.isPending} />
        </div>
      </div>

      {/* Mobile */}
      <div className="md:hidden px-4 py-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <DirectionBadge isBuy={order.isBuy} />
            <span className="text-xs font-mono text-gray-300">
              {order.ownerShort}
            </span>
          </div>
          <StatusBadge isDone={order.isDone} isExpired={order.isExpired} isPending={order.isPending} />
        </div>
        <div className="flex justify-between text-[11px] font-mono">
          <span className="text-gray-500">
            {order.amountIn.toLocaleString(undefined, {
              maximumFractionDigits: 0,
            })}{" "}
            {order.sellToken}
          </span>
          <span className="text-green-400">
            → {order.earned.toFixed(2)} {order.buyToken}
          </span>
        </div>
        <ProgressBar percent={order.progress} isDone={order.isDone} />
        <div className="flex justify-between text-[10px] text-gray-600">
          <span>{order.progress}% complete</span>
          <span>{order.timeLeft}</span>
        </div>
      </div>

      {/* Progress bar (desktop only) */}
      <div className="hidden md:block px-4 pb-1">
        <ProgressBar percent={order.progress} isDone={order.isDone} />
      </div>
    </div>
  );
}
