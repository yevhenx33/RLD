import React, { useState, useEffect, useRef, useCallback } from "react";
import { SIM_API } from "../../config/simulationConfig";

const GQL_URL = `${SIM_API}/graphql`;
const MARKET_ID =
  "0x3de6baf71424c800a4c01e4a7b114737736e311611a7298b946609eeb0b4f0f6";

const DECIMALS = 6; // waUSDC / wRLP both 6 decimals

// ── GQL ──────────────────────────────────────────────────────────────
const ALL_BROKERS_QUERY = `
  query AllBrokers($marketId: String!) {
    brokers(marketId: $marketId) {
      address owner wausdcBalance wrlpBalance debtPrincipal
      isFrozen isLiquidated activeTokenId createdBlock
    }
    lpPositions { tokenId owner tickLower tickUpper liquidity poolId }
    twammOrders { orderId owner status zeroForOne amountIn }
    poolSnapshot(marketId: $marketId) { markPrice indexPrice tick normalizationFactor }
  }
`;

async function gqlFetch(query, variables) {
  const res = await fetch(GQL_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  if (json.errors) throw new Error(json.errors[0]?.message || "GQL error");
  return json.data;
}

// ── Helpers ──────────────────────────────────────────────────────────
function raw(val) {
  if (!val) return 0n;
  try { return BigInt(val); } catch { return 0n; }
}

function fmt(bigVal, decimals = DECIMALS) {
  const n = Number(bigVal) / 10 ** decimals;
  if (n === 0) return "0";
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  return n.toFixed(2);
}

function fmtAddr(a) {
  if (!a) return "—";
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

function statusPill(frozen, liquidated) {
  if (liquidated)
    return <span className="px-1.5 py-0.5 text-[10px] font-bold bg-red-500/20 text-red-400 border border-red-500/30 uppercase tracking-wider">LIQ</span>;
  if (frozen)
    return <span className="px-1.5 py-0.5 text-[10px] font-bold bg-amber-500/20 text-amber-400 border border-amber-500/30 uppercase tracking-wider">FROZEN</span>;
  return <span className="px-1.5 py-0.5 text-[10px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 uppercase tracking-wider">ACTIVE</span>;
}

// ── Component ────────────────────────────────────────────────────────
export default function BrokersDashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [expandedBroker, setExpandedBroker] = useState(null);
  const mountedRef = useRef(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await gqlFetch(ALL_BROKERS_QUERY, { marketId: MARKET_ID });
      if (mountedRef.current) {
        setData(res);
        setError(null);
        setLastUpdate(new Date());
      }
    } catch (e) {
      if (mountedRef.current) setError(e.message);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchData();
    const interval = setInterval(fetchData, 1000);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [fetchData]);

  const brokers = data?.brokers || [];
  const lpPositions = data?.lpPositions || [];
  const twammOrders = data?.twammOrders || [];
  const snap = data?.poolSnapshot;

  // Build lookup maps
  const lpByOwner = {};
  lpPositions.forEach((lp) => {
    const o = lp.owner?.toLowerCase();
    if (!lpByOwner[o]) lpByOwner[o] = [];
    lpByOwner[o].push(lp);
  });

  const ordersByOwner = {};
  twammOrders.forEach((t) => {
    const o = t.owner?.toLowerCase();
    if (!ordersByOwner[o]) ordersByOwner[o] = [];
    ordersByOwner[o].push(t);
  });

  // Stats
  const totalCollateral = brokers.reduce((s, b) => s + raw(b.wausdcBalance), 0n);
  const _totalWrlp = brokers.reduce((s, b) => s + raw(b.wrlpBalance), 0n);
  const totalDebt = brokers.reduce((s, b) => s + raw(b.debtPrincipal), 0n);
  const frozenCount = brokers.filter((b) => b.isFrozen).length;
  const activeCount = brokers.filter((b) => !b.isFrozen && !b.isLiquidated).length;

  return (
    <div className="max-w-[1800px] mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold tracking-widest uppercase text-white flex items-center gap-3">
            <div className="w-2 h-2 bg-cyan-400" />
            Broker Registry
          </h1>
          <p className="text-xs text-gray-500 mt-1 tracking-wider">
            Real-time state from indexer · {brokers.length} brokers tracked
          </p>
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500">
          {snap && (
            <div className="flex items-center gap-3 border border-white/5 px-3 py-2">
              <span>MARK <span className="text-white font-bold">${snap.markPrice?.toFixed(4)}</span></span>
              <span className="text-white/10">|</span>
              <span>INDEX <span className="text-white font-bold">${snap.indexPrice?.toFixed(4)}</span></span>
              <span className="text-white/10">|</span>
              <span>TICK <span className="text-white font-bold">{snap.tick}</span></span>
            </div>
          )}
          <div className="flex items-center gap-2">
            <div className={`w-1.5 h-1.5 rounded-full ${error ? "bg-red-500" : "bg-emerald-500 animate-pulse"}`} />
            <span className="tracking-wider uppercase">
              {error ? "ERR" : lastUpdate ? `${lastUpdate.toLocaleTimeString()}` : "..."}
            </span>
          </div>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <StatCard label="Total Brokers" value={brokers.length} />
        <StatCard label="Active" value={activeCount} accent="emerald" />
        <StatCard label="Frozen" value={frozenCount} accent="amber" />
        <StatCard label="Total Collateral" value={fmt(totalCollateral)} sub="waUSDC" />
        <StatCard label="Total Debt" value={fmt(totalDebt)} sub="raw" accent="red" />
      </div>

      {/* Error Banner */}
      {error && (
        <div className="mb-4 border border-red-500/30 bg-red-500/5 px-4 py-3 text-xs text-red-400 font-mono">
          ⚠ {error}
        </div>
      )}

      {/* Table */}
      <div className="border border-white/[0.06] bg-[#0a0a0a]">
        {/* Table Header */}
        <div className="grid grid-cols-[44px_1fr_1fr_120px_120px_120px_80px_60px_60px_70px] gap-0 text-[10px] font-bold uppercase tracking-widest text-gray-500 border-b border-white/[0.06]">
          <div className="px-3 py-3">#</div>
          <div className="px-3 py-3">Broker</div>
          <div className="px-3 py-3">Owner</div>
          <div className="px-3 py-3 text-right">waUSDC</div>
          <div className="px-3 py-3 text-right">wRLP</div>
          <div className="px-3 py-3 text-right">Debt</div>
          <div className="px-3 py-3 text-center">Status</div>
          <div className="px-3 py-3 text-center">LPs</div>
          <div className="px-3 py-3 text-center">Orders</div>
          <div className="px-3 py-3 text-center">Block</div>
        </div>

        {/* Rows */}
        {brokers.map((b, i) => {
          const addr = b.address?.toLowerCase();
          const lps = lpByOwner[addr] || [];
          const orders = ordersByOwner[addr] || [];
          const activeOrders = orders.filter((o) => o.status === "active");
          const isExpanded = expandedBroker === addr;
          const hasDetails = lps.length > 0 || orders.length > 0;
          const wausdc = raw(b.wausdcBalance);
          const wrlp = raw(b.wrlpBalance);
          const debt = raw(b.debtPrincipal);

          return (
            <React.Fragment key={addr}>
              <div
                className={`grid grid-cols-[44px_1fr_1fr_120px_120px_120px_80px_60px_60px_70px] gap-0 items-center border-b border-white/[0.03] text-sm transition-colors ${
                  hasDetails ? "cursor-pointer hover:bg-white/[0.02]" : ""
                } ${isExpanded ? "bg-white/[0.03]" : ""}`}
                onClick={() => hasDetails && setExpandedBroker(isExpanded ? null : addr)}
              >
                <div className="px-3 py-3 text-xs text-gray-600 font-mono">{i + 1}</div>
                <div className="px-3 py-3 font-mono text-xs">
                  <span className="text-cyan-400/80">{fmtAddr(b.address)}</span>
                </div>
                <div className="px-3 py-3 font-mono text-xs text-gray-400">{fmtAddr(b.owner)}</div>
                <div className={`px-3 py-3 text-right font-mono text-xs ${wausdc > 0n ? "text-white" : "text-gray-600"}`}>
                  {fmt(wausdc)}
                </div>
                <div className={`px-3 py-3 text-right font-mono text-xs ${wrlp > 0n ? "text-emerald-400" : "text-gray-600"}`}>
                  {fmt(wrlp)}
                </div>
                <div className={`px-3 py-3 text-right font-mono text-xs ${debt > 0n ? "text-red-400" : "text-gray-600"}`}>
                  {fmt(debt)}
                </div>
                <div className="px-3 py-3 text-center">
                  {statusPill(b.isFrozen, b.isLiquidated)}
                </div>
                <div className={`px-3 py-3 text-center text-xs font-mono ${lps.length > 0 ? "text-violet-400" : "text-gray-600"}`}>
                  {lps.length}
                </div>
                <div className={`px-3 py-3 text-center text-xs font-mono ${activeOrders.length > 0 ? "text-sky-400" : "text-gray-600"}`}>
                  {activeOrders.length}/{orders.length}
                </div>
                <div className="px-3 py-3 text-center text-xs font-mono text-gray-500">{b.createdBlock}</div>
              </div>

              {/* Expanded Detail Panel */}
              {isExpanded && (
                <div className="border-b border-white/[0.06] bg-[#070707] px-6 py-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* LP Positions */}
                    {lps.length > 0 && (
                      <div>
                        <h3 className="text-[10px] font-bold uppercase tracking-widest text-violet-400 mb-2 flex items-center gap-2">
                          <div className="w-1.5 h-1.5 bg-violet-400" />
                          LP Positions ({lps.length})
                        </h3>
                        <div className="space-y-1.5">
                          {lps.map((lp) => (
                            <div
                              key={lp.tokenId}
                              className="flex items-center justify-between py-1.5 px-3 border border-white/[0.04] bg-white/[0.01] text-xs font-mono"
                            >
                              <span className="text-gray-400">
                                ID:<span className="text-white ml-1">{lp.tokenId}</span>
                              </span>
                              <span className="text-gray-500">
                                ticks [
                                <span className="text-violet-300">{lp.tickLower ?? "?"}</span>,
                                <span className="text-violet-300">{lp.tickUpper ?? "?"}</span>
                                ]
                              </span>
                              <span className="text-gray-500">
                                liq: <span className="text-white">{fmt(raw(lp.liquidity), 0)}</span>
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* TWAMM Orders */}
                    {orders.length > 0 && (
                      <div>
                        <h3 className="text-[10px] font-bold uppercase tracking-widest text-sky-400 mb-2 flex items-center gap-2">
                          <div className="w-1.5 h-1.5 bg-sky-400" />
                          TWAMM Orders ({orders.length})
                        </h3>
                        <div className="space-y-1.5">
                          {orders.map((o) => (
                            <div
                              key={o.orderId}
                              className="flex items-center justify-between py-1.5 px-3 border border-white/[0.04] bg-white/[0.01] text-xs font-mono"
                            >
                              <span className="text-gray-400">
                                {fmtAddr(o.orderId)}
                              </span>
                              <span className={`${
                                o.status === "active" ? "text-emerald-400" :
                                o.status === "cancelled" ? "text-amber-400" :
                                "text-gray-400"
                              }`}>
                                {o.status}
                              </span>
                              <span className="text-gray-500">
                                {o.zeroForOne ? "ZFO" : "OFZ"}
                              </span>
                              <span className="text-white">
                                {fmt(raw(o.amountIn))}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Full Addresses */}
                  <div className="mt-4 pt-3 border-t border-white/[0.04] text-[10px] font-mono text-gray-500 space-y-1">
                    <div>broker: <span className="text-gray-300">{b.address}</span></div>
                    <div>owner: <span className="text-gray-300">{b.owner}</span></div>
                    {b.activeTokenId && b.activeTokenId !== "0" && (
                      <div>active LP: <span className="text-violet-300">#{b.activeTokenId}</span></div>
                    )}
                  </div>
                </div>
              )}
            </React.Fragment>
          );
        })}

        {brokers.length === 0 && !error && (
          <div className="px-6 py-12 text-center text-gray-500 text-sm">
            Loading brokers...
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="mt-3 text-[10px] text-gray-600 flex items-center justify-between">
        <span>Polling every 1s · Market {MARKET_ID.slice(0, 16)}…</span>
        <span>LP positions: {lpPositions.length} · TWAMM orders: {twammOrders.length}</span>
      </div>
    </div>
  );
}

// ── Stat Card ────────────────────────────────────────────────────────
function StatCard({ label, value, sub, accent }) {
  const colors = {
    emerald: "text-emerald-400 border-emerald-500/20",
    amber: "text-amber-400 border-amber-500/20",
    red: "text-red-400 border-red-500/20",
    default: "text-white border-white/[0.06]",
  };
  const c = colors[accent] || colors.default;

  return (
    <div className={`border ${c.split(" ")[1]} bg-[#0a0a0a] px-4 py-3`}>
      <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-1">{label}</div>
      <div className={`text-lg font-bold font-mono ${c.split(" ")[0]}`}>{value}</div>
      {sub && <div className="text-[10px] text-gray-600 mt-0.5">{sub}</div>}
    </div>
  );
}
