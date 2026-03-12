import React, { useState, useMemo, useEffect, useCallback } from "react";
import { ethers } from "ethers";
import {
  Droplets,
  Activity,
  TrendingUp,
  ArrowUpDown,
  ExternalLink,
  ChevronDown,
  Loader2,
  Layers,
  Wallet,
} from "lucide-react";

import { useSim } from "../../context/SimulationContext";
import { useSimulation } from "../../hooks/useSimulation";
import { useWallet } from "../../context/WalletContext";
import { useBrokerAccount } from "../../hooks/useBrokerAccount";
import { usePoolLiquidity, liquidityToAmounts } from "../../hooks/usePoolLiquidity";
import { useToast } from "../../hooks/useToast";
import { useChartControls } from "../../hooks/useChartControls";
import TradingTerminal, { InputGroup, SummaryRow } from "../trading/TradingTerminal";
import StatItem from "../common/StatItem";
import RLDPerformanceChart from "../charts/RLDChart";
import ChartControlBar from "../charts/ChartControlBar";
import ClaimFeesModal from "../modals/ClaimFeesModal";
import WithdrawModal from "../modals/WithdrawModal";
import AddLiquidityModal from "../modals/AddLiquidityModal";
import AccountModal from "../modals/AccountModal";
import { ToastContainer } from "../common/Toast";

const RPC_URL = `${window.location.origin}/rpc`;
const ERC20_BALANCE_ABI = ["function balanceOf(address) view returns (uint256)"];


// ── Combo: Uniswap-style Dual-Color Mountain Chart ────────────────────────
function ComboChart({ bins, currentPrice }) {
  const containerRef = React.useRef(null);
  const [dims, setDims] = React.useState({ width: 0, height: 0 });
  const [hoveredIdx, setHoveredIdx] = React.useState(null);

  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setDims({ width, height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!bins || bins.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="animate-spin text-gray-700" />
      </div>
    );
  }

  // Use combined token amounts for Y-axis height (like Uniswap)
  const getDepth = (b) => (b.amount0 ?? 0) + (b.amount1 ?? 0);
  const maxDepth = Math.max(...bins.map(getDepth), 0.01);

  // Layout
  const MARGIN = { top: 16, right: 12, bottom: 30, left: 56 };
  const plotW = dims.width - MARGIN.left - MARGIN.right;
  const plotH = dims.height - MARGIN.top - MARGIN.bottom;
  const barW = plotW / bins.length;

  const xOf = (idx) => MARGIN.left + idx * barW + barW / 2;
  const yOf = (depth) => MARGIN.top + plotH - (maxDepth > 0 ? (depth / maxDepth) * plotH * 0.8 : 0);

  // Current price X (interpolated)
  let curPriceX = null;
  let curBinIdx = -1;
  if (currentPrice && bins.length > 0) {
    const minP = bins[0].priceFrom;
    const maxP = bins[bins.length - 1].priceTo;
    if (currentPrice >= minP && currentPrice <= maxP) {
      curPriceX = MARGIN.left + ((currentPrice - minP) / (maxP - minP)) * plotW;
      curBinIdx = bins.findIndex(b => currentPrice >= b.priceFrom && currentPrice < b.priceTo);
    }
  }

  // Build path points
  const points = bins.map((b, i) => ({ x: xOf(i), y: yOf(getDepth(b)) }));

  // Linear path — liquidity charts should show clean transitions, not smooth splines
  const baseline = MARGIN.top + plotH;
  const buildPath = (pts) => {
    if (pts.length < 2) return "";
    return pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x},${p.y}`).join(" ");
  };

  const curvePath = buildPath(points);

  // Closed area path for fills
  const areaPath = `M ${points[0].x},${baseline} L ${points[0].x},${points[0].y} ` +
    curvePath.slice(curvePath.indexOf("L")) +
    ` L ${points[points.length - 1].x},${baseline} Z`;

  // Format token amounts for Y-axis / tooltips
  const fmtAmt = (v) => {
    if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
    if (v >= 1) return v.toFixed(1);
    if (v >= 0.01) return v.toFixed(2);
    return v.toFixed(4);
  };

  return (
    <div ref={containerRef} className="w-full h-full relative font-mono" onMouseLeave={() => setHoveredIdx(null)}>
      {dims.width > 0 && plotH > 0 && (
        <svg width={dims.width} height={dims.height}>
          <defs>
            {/* Left fill (token1-heavy, below current price) */}
            <linearGradient id="liq-fill-left" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#ec4899" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#ec4899" stopOpacity={0.05} />
            </linearGradient>
            {/* Right fill (token0-heavy, above current price) */}
            <linearGradient id="liq-fill-right" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#22d3ee" stopOpacity={0.05} />
            </linearGradient>
            {/* Clip to plot area (prevents below-baseline rendering) */}
            <clipPath id="clip-plot">
              <rect x={MARGIN.left} y={MARGIN.top} width={plotW} height={plotH} />
            </clipPath>
            {/* Clip paths for dual-color split at current price */}
            {curPriceX != null && (
              <>
                <clipPath id="clip-left">
                  <rect x={0} y={0} width={curPriceX} height={dims.height} />
                </clipPath>
                <clipPath id="clip-right">
                  <rect x={curPriceX} y={0} width={dims.width - curPriceX} height={dims.height} />
                </clipPath>
              </>
            )}
            <filter id="area-glow">
              <feGaussianBlur stdDeviation="2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* Horizontal grid lines */}
          {[0.25, 0.5, 0.75].map((frac) => (
            <line
              key={frac}
              x1={MARGIN.left}
              y1={MARGIN.top + plotH * (1 - frac)}
              x2={MARGIN.left + plotW}
              y2={MARGIN.top + plotH * (1 - frac)}
              stroke="#1e1e24"
              strokeDasharray="3 3"
            />
          ))}

          {/* Mountain area — dual color split (clipped to plot area) */}
          <g clipPath="url(#clip-plot)">
          {curPriceX != null ? (
            <>
              <path d={areaPath} fill="url(#liq-fill-left)" clipPath="url(#clip-left)" />
              <path d={areaPath} fill="url(#liq-fill-right)" clipPath="url(#clip-right)" />
              {/* Outline — left side pink, right side cyan */}
              <path d={curvePath} fill="none" stroke="#ec4899" strokeWidth={1.5} strokeOpacity={0.7} clipPath="url(#clip-left)" />
              <path d={curvePath} fill="none" stroke="#22d3ee" strokeWidth={1.5} strokeOpacity={0.7} clipPath="url(#clip-right)" />
            </>
          ) : (
            <>
              <path d={areaPath} fill="url(#liq-fill-right)" />
              <path d={curvePath} fill="none" stroke="#22d3ee" strokeWidth={1.5} strokeOpacity={0.7} />
            </>
          )}
          </g>

          {/* Hovered bar highlight */}
          {hoveredIdx !== null && (
            <rect
              x={MARGIN.left + hoveredIdx * barW}
              y={yOf(getDepth(bins[hoveredIdx]))}
              width={barW}
              height={baseline - yOf(getDepth(bins[hoveredIdx]))}
              fill={hoveredIdx <= curBinIdx ? "#ec4899" : "#22d3ee"}
              opacity={0.15}
            />
          )}

          {/* Invisible hover rects */}
          {bins.map((_, i) => (
            <rect
              key={`h-${i}`}
              x={MARGIN.left + i * barW}
              y={MARGIN.top}
              width={barW}
              height={plotH}
              fill="transparent"
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
            />
          ))}

          {/* Current price vertical line */}
          {curPriceX != null && (
            <>
              <line
                x1={curPriceX} y1={MARGIN.top}
                x2={curPriceX} y2={baseline}
                stroke="#ffffff" strokeWidth={1} strokeDasharray="4 3" strokeOpacity={0.5}
              />
              <text x={curPriceX} y={MARGIN.top - 3} textAnchor="middle" fill="#e4e4e7" fontSize={12} fontFamily="inherit">
                {currentPrice.toFixed(2)}
              </text>
            </>
          )}

          {/* Bottom axis line */}
          <line x1={MARGIN.left} y1={baseline} x2={MARGIN.left + plotW} y2={baseline} stroke="#3f3f46" />

          {/* X-axis labels (prices) */}
          {bins.map((bin, i) => {
            if (i % Math.max(1, Math.floor(bins.length / 6)) !== 0) return null;
            return (
              <text key={i} x={xOf(i)} y={baseline + 16} textAnchor="middle" fill="#71717a" fontSize={11} fontFamily="inherit">
                {Number(bin.price).toFixed(1)}
              </text>
            );
          })}

          {/* Y-axis labels (token amounts) */}
          {[0.25, 0.5, 0.75, 1].map((frac) => (
            <text key={frac} x={MARGIN.left - 6} y={MARGIN.top + plotH * (1 - frac) + 4} textAnchor="end" fill="#71717a" fontSize={11} fontFamily="inherit">
              {fmtAmt(maxDepth * frac)}
            </text>
          ))}
        </svg>
      )}

      {/* Tooltip */}
      {hoveredIdx !== null && dims.width > 0 && (() => {
        const bin = bins[hoveredIdx];
        const x = xOf(hoveredIdx);
        const tipY = yOf(getDepth(bin));
        const a0 = bin.amount0 ?? 0;
        const a1 = bin.amount1 ?? 0;
        return (
          <div
            className="absolute pointer-events-none bg-[#0a0a0a]/95 border border-zinc-800 px-3 py-2 text-xs font-mono shadow-xl z-10 rounded"
            style={{ left: Math.min(Math.max(x - 80, 4), dims.width - 180), top: Math.max(tipY - 70, 4) }}
          >
            <div className="text-zinc-400 mb-1.5">
              {Number(bin.priceFrom).toFixed(2)} &ndash; {Number(bin.priceTo).toFixed(2)}
            </div>
            <div className="flex items-center gap-2 mb-0.5">
              <span className="w-2 h-2 rounded-full bg-pink-500 inline-block" />
              <span className="text-zinc-400">Token 0:</span>
              <span className="text-white">{fmtAmt(a0)}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-cyan-400 inline-block" />
              <span className="text-zinc-400">Token 1:</span>
              <span className="text-white">{fmtAmt(a1)}</span>
            </div>
          </div>
        );
      })()}
    </div>
  );
}




export default function PoolLP() {
  // ── Wallet & broker ─────────────────────────────────────────
  const { account } = useWallet();
  const { toasts, addToast, removeToast } = useToast();

  // ── Chart controls (resolution + time range) ───────────────
  const chartControls = useChartControls({
    defaultRange: "1W",
    defaultDays: 7,
    defaultResolution: "1H",
  });
  const { appliedStart, appliedEnd, resolution } = chartControls;

  // ── Simulation data ─────────────────────────────────────────
  // NOTE: The simulation uses Ethereum fork timestamps (Jan 2025), not
  // wall-clock time (Mar 2026). We compute the time *range* as a delta
  // from wall-clock "now", then anchor it to the latest simulation
  // block timestamp so the filter matches the chain's timeline.
  const [simBlockTs, setSimBlockTs] = useState(null);

  const chartStartTime = useMemo(() => {
    if (!simBlockTs || !appliedStart) return null;
    const wallNow = Math.floor(Date.now() / 1000);
    const wallStart = Math.floor(new Date(appliedStart).getTime() / 1000);
    return simBlockTs - (wallNow - wallStart);
  }, [appliedStart, simBlockTs]);

  const chartEndTime = useMemo(() => {
    if (!simBlockTs || !appliedEnd) return null;
    const wallNow = Math.floor(Date.now() / 1000);
    const wallEnd = Math.floor(new Date(appliedEnd + "T23:59:59Z").getTime() / 1000);
    return simBlockTs - (wallNow - wallEnd);
  }, [appliedEnd, simBlockTs]);

  // Shared data from global context (deduplicated across all pages)
  const simShared = useSim();
  // Chart-specific data with custom resolution/time range
  const simChart = useSimulation({
    pollInterval: 2000,
    chartResolution: resolution,
    chartStartTime,
    chartEndTime,
  });

  // Update simBlockTs once when simulation data first arrives
  useEffect(() => {
    if (simBlockTs) return; // only set once
    const ts = simShared?.latest?.market?.blockTimestamp;
    if (ts) setSimBlockTs(ts);
  }, [simShared?.latest, simBlockTs]);
  const {
    connected,
    loading,
    error,
    market,
    pool,
    poolTVL,
    funding,
    fundingFromNF: _fundingFromNF,
    volumeData,
    protocolStats: _protocolStats,
    marketInfo,
  } = simShared;
  // Chart data uses local sim with custom resolution params
  const { chartData, volumeHistory } = simChart;

  const { hasBroker, brokerAddress, checkBroker, fetchBrokerBalance } = useBrokerAccount(
    account,
    marketInfo?.broker_factory,
    marketInfo?.collateral?.address,
  );

  // ── Pool liquidity hook (contract integration) ──────────────
  const {
    executeAddLiquidity,
    executeCollectFees,
    executeRemoveLiquidity,
    activePosition: _activePosition,
    allPositions,
    refreshPosition: _refreshPosition,
    executing: lpExecuting,
    executionStep: lpStep,
    executionError: lpError,
    clearError: clearLpError,
  } = usePoolLiquidity(brokerAddress, marketInfo);

  // ── Pool-wide liquidity distribution (single API call, server-cached) ──
  const [liquidityBins, setLiquidityBins] = useState([]);
  const [liqDistPrice, setLiqDistPrice] = useState(null);

  // Local fallback: build bins from allPositions when API is unavailable
  const buildLocalBins = React.useCallback((positions, price) => {
    if (!positions?.length || !price) return [];
    const NUM_BINS = 60;
    // ±100% price range: half to double the current price
    const minP = price * 0.5, maxP = price * 2.0;
    const binW = (maxP - minP) / NUM_BINS;
    return Array.from({ length: NUM_BINS }, (_, i) => {
      const priceFrom = minP + i * binW;
      const priceTo = minP + (i + 1) * binW;
      let liq = 0;
      for (const p of positions) {
        const tl = Math.min(p.tickLower ?? 0, p.tickUpper ?? 0);
        const tu = Math.max(p.tickLower ?? 0, p.tickUpper ?? 0);
        const pL = Math.pow(1.0001, tl);
        const pH = Math.pow(1.0001, tu);
        if (pH > priceFrom && pL < priceTo) liq += Number(p.liquidity || 0);
      }
      // Token amounts (Uni V3 math) → divide by 1e6 for 6-decimal tokens
      const sa = Math.sqrt(priceFrom), sb = Math.sqrt(priceTo);
      const sp = Math.max(sa, Math.min(Math.sqrt(price), sb));
      const a0 = sp < sb ? liq * (1 / sp - 1 / sb) / 1e6 : 0;
      const a1 = sp > sa ? liq * (sp - sa) / 1e6 : 0;
      return { price: ((priceFrom + priceTo) / 2).toFixed(3), priceFrom, priceTo, liquidity: liq, amount0: Math.max(0, a0), amount1: Math.max(0, a1) };
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    const GQL_URL = `/graphql`;
    const LIQ_QUERY = `query { liquidityBins(numBins: 60) { price priceFrom priceTo liquidity amount0 amount1 } }`;

    async function fetchDistribution() {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const res = await fetch(GQL_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: LIQ_QUERY }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const json = await res.json();
          const bins = json?.data?.liquidityBins;
          if (!cancelled && bins?.length) {
            setLiquidityBins(bins);
            const priceFromMid = bins[Math.floor(bins.length / 2)]?.price;
            if (priceFromMid) setLiqDistPrice(parseFloat(priceFromMid));
            return;
          }
        } catch (err) {
          if (attempt < 2) {
            await new Promise(r => setTimeout(r, 2000));
            continue;
          }
          console.warn("[LP] GQL liquidityBins unavailable after retries, using local fallback:", err.message);
        }
      }
      // Fallback: build from local positions
      if (!cancelled && allPositions?.length) {
        const price = pool?.markPrice || 1;
        setLiquidityBins(buildLocalBins(allPositions, price));
      }
    }
    fetchDistribution();
    return () => { cancelled = true; };
  }, [allPositions, pool?.markPrice, buildLocalBins]);

  // ── Local UI state ──────────────────────────────────────────
  const [activeTab, setActiveTab] = useState("ADD");
  const [token0Amount, setToken0Amount] = useState("");
  const [token1Amount, setToken1Amount] = useState("");
  const [lastEdited, setLastEdited] = useState(null); // 'token0' | 'token1'
  const [minPrice, setMinPrice] = useState("1");
  const [maxPrice, setMaxPrice] = useState("10");
  const [removePercent, setRemovePercent] = useState(100);
  const [selectedPosition, _setSelectedPosition] = useState(null);
  const [removePage, setRemovePage] = useState(0);
  const POSITIONS_PER_PAGE = 4;
  const [actionDropdown, setActionDropdown] = useState(null);
  const [claimPosition, setClaimPosition] = useState(null);
  const [withdrawPosition, setWithdrawPosition] = useState(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [showAccountModal, setShowAccountModal] = useState(false);
  const [chartView, setChartView] = useState("PRICE");
  const [chartDropdown, setChartDropdown] = useState(null); // 'resolution' | 'timeframe' | null

  // ── Account selector (wallet vs broker) ─────────────────────
  const [accountDropdownOpen, setAccountDropdownOpen] = useState(false);
  // "wallet" | "broker"
  const [selectedAccount, setSelectedAccount] = useState("broker");
  const activeAddress = selectedAccount === "broker" && brokerAddress ? brokerAddress : account;

  // ── Token balances for selected account ─────────────────────
  const [token0Balance, setToken0Balance] = useState(null); // wRLP
  const [token1Balance, setToken1Balance] = useState(null); // waUSDC

  const token0Addr = marketInfo?.position_token?.address;
  const token1Addr = marketInfo?.collateral?.address;

  const fetchBalances = useCallback(async () => {
    if (!activeAddress || !token0Addr || !token1Addr) {
      setToken0Balance(null);
      setToken1Balance(null);
      return;
    }
    try {
      const provider = new ethers.JsonRpcProvider(RPC_URL);
      const t0 = new ethers.Contract(token0Addr, ERC20_BALANCE_ABI, provider);
      const t1 = new ethers.Contract(token1Addr, ERC20_BALANCE_ABI, provider);
      const [b0, b1] = await Promise.all([
        t0.balanceOf(activeAddress),
        t1.balanceOf(activeAddress),
      ]);
      setToken0Balance(parseFloat(ethers.formatUnits(b0, 6)));
      setToken1Balance(parseFloat(ethers.formatUnits(b1, 6)));
    } catch (e) {
      console.warn("Balance fetch failed:", e);
    }
  }, [activeAddress, token0Addr, token1Addr]);

  useEffect(() => {
    fetchBalances();
    const id = setInterval(fetchBalances, 5000);
    return () => clearInterval(id);
  }, [fetchBalances]);

  // ── V4 paired amount calculation ────────────────────────────
  // When user enters one token amount, auto-compute the other
  // using standard concentrated liquidity math:
  //   amount0 = L × (1/√pC − 1/√pU)
  //   amount1 = L × (√pC − √pL)
  const currentPrice = pool?.markPrice;
  const computePairedAmount = useCallback(
    (sourceAmount, source) => {
      const pMin = parseFloat(minPrice);
      const pMax = parseFloat(maxPrice);
      if (!pMin || !pMax || !currentPrice || pMin >= pMax) return "";

      const sqrtPL = Math.sqrt(pMin);
      const sqrtPU = Math.sqrt(pMax);
      const sqrtPC = Math.sqrt(currentPrice);
      const amt = parseFloat(sourceAmount);
      if (!amt || amt <= 0) return "";

      if (currentPrice <= pMin) {
        // Below range: only token0 needed, token1 = 0
        return source === "token0" ? "0" : "";
      }
      if (currentPrice >= pMax) {
        // Above range: only token1 needed, token0 = 0
        return source === "token1" ? "0" : "";
      }

      // In range — both tokens needed
      if (source === "token0") {
        // User entered token0 (wRLP), compute token1 (waUSDC)
        const delta0 = 1 / sqrtPC - 1 / sqrtPU;
        if (delta0 <= 0) return "";
        const L = amt / delta0;
        const paired = L * (sqrtPC - sqrtPL);
        return paired > 0 ? paired.toFixed(6) : "0";
      } else {
        // User entered token1 (waUSDC), compute token0 (wRLP)
        const delta1 = sqrtPC - sqrtPL;
        if (delta1 <= 0) return "";
        const L = amt / delta1;
        const paired = L * (1 / sqrtPC - 1 / sqrtPU);
        return paired > 0 ? paired.toFixed(6) : "0";
      }
    },
    [minPrice, maxPrice, currentPrice],
  );

  // Handlers that auto-compute the paired amount
  const handleToken0Change = useCallback(
    (val) => {
      setToken0Amount(val);
      setLastEdited("token0");
      setToken1Amount(computePairedAmount(val, "token0"));
    },
    [computePairedAmount],
  );

  const handleToken1Change = useCallback(
    (val) => {
      setToken1Amount(val);
      setLastEdited("token1");
      setToken0Amount(computePairedAmount(val, "token1"));
    },
    [computePairedAmount],
  );

  // Recompute whenever price range changes
  useEffect(() => {
    if (lastEdited === "token0" && token0Amount) {
      setToken1Amount(computePairedAmount(token0Amount, "token0"));
    } else if (lastEdited === "token1" && token1Amount) {
      setToken0Amount(computePairedAmount(token1Amount, "token1"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [minPrice, maxPrice, currentPrice]);

  // ── Derived pool data from simulation ───────────────────────
  const poolData = useMemo(() => {
    if (!pool || !market || !marketInfo) return null;

    const token0Symbol = marketInfo.position_token?.symbol || "wRLP";
    const token1Symbol = marketInfo.collateral?.symbol || "waUSDC";
    const feeTier = marketInfo.infrastructure?.pool_fee || 500;
    const feePercent = feeTier / 10000; // 500 → 0.05%

    // TVL from indexed token balances in PoolManager
    const tvl = poolTVL || 0;

    const volume24h = volumeData?.volume_usd || 0;
    const swapCount = volumeData?.swap_count || 0;

    // Fees estimated from volume × fee rate
    const fees24h = volume24h * (feeTier / 1e6);
    const fees7d = fees24h * 7;

    // APR: annualized fees / TVL
    const apr = tvl > 0 ? ((fees24h * 365) / tvl) * 100 : 0;
    const aprWeekly = apr / 52;

    // Funding — use corrected annualized exponential rate (same as perps page)
    const annualizedPct = funding?.annualizedPct ?? 0;
    const fundingRate = Math.abs(annualizedPct);
    // Positive annualizedPct → mark > index → shorts pay longs
    const fundingDirection = annualizedPct >= 0 ? "shorts" : "longs";

    // Hook address
    const hookAddr = marketInfo.infrastructure?.twamm_hook || "";
    const hookShort = hookAddr
      ? `${hookAddr.slice(0, 6)}...${hookAddr.slice(-4)}`
      : "—";

    return {
      pair: `${token0Symbol} / ${token1Symbol}`,
      protocol: "Uniswap V4",
      hookAddress: hookShort,
      hookAddressFull: hookAddr,
      feeTier: `${feePercent.toFixed(2)}%`,
      tickSpacing: marketInfo.infrastructure?.tick_spacing || 5,
      tvl,
      volume24h,
      swapCount,
      fees24h,
      fees7d,
      apr,
      aprWeekly,
      aprYearly: apr,
      currentTick: pool.tick,
      currentPrice: pool.markPrice,
      indexPrice: market.indexPrice,
      markPrice: pool.markPrice,
      fundingRate,
      fundingDirection,
      activeLiquidity: pool.liquidity || 0,
      token0: {
        symbol: token0Symbol,
        name: marketInfo.position_token?.name || "Wrapped RLP",
        decimals: 6,
      },
      token1: {
        symbol: token1Symbol,
        name: marketInfo.collateral?.name || "Wrapped aUSDC",
        decimals: 6,
      },
    };
  }, [pool, market, marketInfo, funding, volumeData, poolTVL]);

  // ── Chart configuration ─────────────────────────────────────
  const CHART_VIEWS = useMemo(
    () => ({
      PRICE: {
        label: "Price",
        areas: [
          { key: "indexPrice", name: "Index Price", color: "#22d3ee" },
          { key: "markPrice", name: "Mark Price", color: "#ec4899" },
        ],
      },
      LIQUIDITY: {
        label: "Liquidity",
        areas: [
          { key: "liquidity", name: "Active Liq", color: "#a855f7" },
        ],
      },
      VOLUME: {
        label: "Volume",
        areas: [
          { key: "volume", name: "Volume", color: "#22c55e", format: "dollar" },
        ],
      },
    }),
    [],
  );

  const activeChartConfig = CHART_VIEWS[chartView];

  // ── Derive user positions from ALL on-chain positions ─────────
  const userPositions = useMemo(() => {
    if (!allPositions || allPositions.length === 0) return [];

    const mapped = allPositions.map((pos) => {
      const { tickLower, tickUpper, liquidity, tokenId, isActive, entryPrice: onChainEntryPrice } = pos;

      // Convert ticks back to prices for display
      const priceLower = Math.pow(1.0001, tickLower);
      const priceUpper = Math.pow(1.0001, tickUpper);
      // Use on-chain entry price (pool price at mint block), fall back to current price
      const entryPrice = onChainEntryPrice ?? currentPrice ?? (priceLower + priceUpper) / 2;

      // Compute token amounts from position liquidity + tick range + current price
      const currentTick = currentPrice
        ? Math.log(currentPrice) / Math.log(1.0001)
        : 0;
      const amounts = liquidityToAmounts(liquidity, tickLower, tickUpper, currentTick);

      // Is the current price within this position's range?
      const inRange = currentTick >= tickLower && currentTick < tickUpper;

      return {
        id: Number(tokenId),
        tokenId,
        priceLower,
        priceUpper,
        entryPrice,
        liquidity: Number(liquidity),
        liquidityFormatted: Number(liquidity).toLocaleString(),
        token0Amount: amounts.amount0.toLocaleString(undefined, { maximumFractionDigits: 2 }),
        token1Amount: amounts.amount1.toLocaleString(undefined, { maximumFractionDigits: 2 }),
        value: amounts.amount1 + amounts.amount0 * (currentPrice || 0),
        feesEarned0: pos.feesEarned0 || "0",
        feesEarned1: pos.feesEarned1 || "0",
        inRange,
        isActive,
        apr: poolData?.apr?.toFixed(1) || "—",
      };
    });

    // Active position always first
    return mapped.sort((a, b) => (b.isActive ? 1 : 0) - (a.isActive ? 1 : 0));
  }, [allPositions, currentPrice, poolData]);

  // ── Error / Loading states ──────────────────────────────────
  if (error && !connected) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="text-red-500 text-sm uppercase tracking-widest">
            SIM_DISCONNECTED
          </div>
          <div className="text-gray-600 text-sm max-w-xs">
            Cannot reach simulation indexer. Make sure the Docker simulation
            stack is running.
          </div>
          <div className="text-sm text-gray-700 font-mono">
            Expected at: http://localhost:8080
          </div>
        </div>
      </div>
    );
  }

  if (loading || !poolData) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
          <span className="text-sm uppercase tracking-widest text-gray-500">
            Connecting to simulation...
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          {/* === LEFT COLUMN (Span 9) === */}
          <div className="xl:col-span-9 flex flex-col gap-4">
            {/* 1. METRICS GRID */}
            <div className="border border-white/10 grid grid-cols-1 lg:grid-cols-12">
              {/* Branding */}
              <div className="lg:col-span-4 flex flex-col justify-between p-6 border-b lg:border-b-0 lg:border-r border-white/10 h-full min-h-[180px]">
                <div>
                  <div className="text-sm text-gray-700 mb-6 font-mono leading-tight tracking-tight">
                    {poolData.hookAddress}
                  </div>
                  <h2 className="text-3xl font-medium tracking-tight mb-2 leading-none">
                    {poolData.pair}
                    <br />
                    <span className="text-gray-600 uppercase">Liquidity Pool</span>
                  </h2>
                </div>
                <div className="mt-auto pt-4 border-t border-white/10 flex items-center justify-between">
                  <span className="text-sm uppercase tracking-widest text-gray-500">
                    {poolData.protocol}
                  </span>
                  <a
                    href={`https://etherscan.io/address/${poolData.hookAddressFull}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm uppercase tracking-widest text-purple-400 font-mono flex items-center gap-1 hover:text-purple-300 transition-colors"
                  >
                    <Droplets size={10} />
                    V4_HOOK
                    <ExternalLink size={9} className="opacity-60" />
                  </a>
                </div>
              </div>

              {/* Stats Cards — 3 panels */}
              <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
                {/* PRICE */}
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                  <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                    PRICE <ArrowUpDown size={15} className="opacity-90" />
                  </div>
                  <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                    <StatItem
                      label="INDEX"
                      value={poolData.indexPrice.toFixed(4)}
                    />
                    <StatItem
                      label="MARK"
                      value={poolData.markPrice.toFixed(4)}
                    />
                    <div className="col-span-2">
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">Funding</div>
                      <div className="flex items-baseline gap-2">
                        <span className={`text-sm font-light tracking-tight ${poolData.fundingDirection === "longs" ? "text-red-400" : "text-green-400"}`}>
                          {poolData.fundingRate.toFixed(2)}%
                        </span>
                        <span className="text-sm text-gray-500 uppercase tracking-widest">
                          {poolData.fundingDirection === "longs" ? "Longs pay Shorts" : "Shorts pay Longs"}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* POOL */}
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                  <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                    POOL <Droplets size={15} className="opacity-90" />
                  </div>
                  <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                    <StatItem
                      label="TVL"
                      value={poolData.tvl >= 1e6
                        ? `$${(poolData.tvl / 1e6).toFixed(2)}M`
                        : poolData.tvl >= 1e3
                          ? `$${(poolData.tvl / 1e3).toFixed(0)}K`
                          : `$${poolData.tvl.toFixed(0)}`}
                    />
                    <StatItem
                      label="VOLUME"
                      value={volumeData?.volume_formatted || "—"}
                    />
                    <StatItem
                      label="FEES_24H"
                      value={poolData.fees24h >= 1e3
                        ? `$${(poolData.fees24h / 1e3).toFixed(1)}K`
                        : `$${poolData.fees24h.toFixed(0)}`}
                    />
                    <StatItem label="FEE" value={poolData.feeTier} />
                  </div>
                </div>

                {/* YIELD APR */}
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                  <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                    YIELD APR <TrendingUp size={15} className="opacity-90" />
                  </div>
                  <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                    <StatItem
                      label="WEEKLY"
                      value={`${poolData.aprWeekly.toFixed(2)}%`}
                      valueClassName="text-green-400"
                    />
                    <StatItem
                      label="YEARLY"
                      value={`${poolData.aprYearly.toFixed(1)}%`}
                      valueClassName="text-green-400"
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* 2. CHART */}
            <div className="relative flex-1 min-h-[350px] md:min-h-[400px] border border-white/10">

              {/* ── Desktop controls (lg+): single row, unchanged ── */}
              <div className="hidden lg:flex items-stretch border-b border-white/10">
                {/* View switcher */}
                <div className="flex items-center gap-1 px-4 py-2 border-r border-white/10">
                  {Object.entries(CHART_VIEWS).map(([key, view]) => (
                    <button
                      key={key}
                      onClick={() => setChartView(key)}
                      className={`px-3 py-1 text-sm font-semibold uppercase tracking-widest transition-colors ${
                        chartView === key
                          ? "text-white bg-white/10"
                          : "text-gray-600 hover:text-gray-400"
                      }`}
                    >
                      {view.label}
                    </button>
                  ))}
                </div>

                {/* Resolution dropdown */}
                <div className="relative flex items-center px-4 py-2 border-r border-white/10">
                  <button
                    onClick={() => setChartDropdown(chartDropdown === 'resolution' ? null : 'resolution')}
                    className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-widest text-gray-600 hover:text-gray-400 transition-colors"
                  >
                    Resolution: <span className="text-white">{resolution}</span>
                    <ChevronDown size={10} className={`transition-transform ${chartDropdown === 'resolution' ? 'rotate-180' : ''}`} />
                  </button>
                  {chartDropdown === 'resolution' && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={() => setChartDropdown(null)} />
                      <div className="absolute left-0 top-full mt-0 z-50 bg-[#0a0a0a] border border-white/10">
                        {["1H", "4H", "1D", "1W"].map((res) => (
                          <button
                            key={res}
                            onClick={() => { chartControls.setResolution(res); setChartDropdown(null); }}
                            className={`block w-full text-left px-3 py-1 text-sm font-semibold uppercase tracking-widest transition-colors ${
                              resolution === res
                                ? "text-white bg-white/10"
                                : "text-gray-600 hover:text-gray-400"
                            }`}
                          >
                            {res}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </div>

                {/* Timeframe dropdown */}
                <div className="relative flex items-center px-4 py-2 border-r border-white/10">
                  <button
                    onClick={() => setChartDropdown(chartDropdown === 'timeframe' ? null : 'timeframe')}
                    className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-widest text-gray-600 hover:text-gray-400 transition-colors"
                  >
                    Timeframe: <span className="text-white">{chartControls.activeRange}</span>
                    <ChevronDown size={10} className={`transition-transform ${chartDropdown === 'timeframe' ? 'rotate-180' : ''}`} />
                  </button>
                  {chartDropdown === 'timeframe' && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={() => setChartDropdown(null)} />
                      <div className="absolute left-0 top-full mt-0 z-50 bg-[#0a0a0a] border border-white/10">
                        {[
                          { l: "1D", d: 1 },
                          { l: "1W", d: 7 },
                          { l: "1M", d: 30 },
                          { l: "3M", d: 90 },
                          { l: "ALL", d: 9999 },
                        ].map((btn) => (
                          <button
                            key={btn.l}
                            onClick={() => { chartControls.handleQuickRange(btn.d, btn.l); setChartDropdown(null); }}
                            className={`block w-full text-left px-3 py-1 text-sm font-semibold uppercase tracking-widest transition-colors ${
                              chartControls.activeRange === btn.l
                                ? "text-white bg-white/10"
                                : "text-gray-600 hover:text-gray-400"
                            }`}
                          >
                            {btn.l}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </div>

                {/* Series legend */}
                <div className="flex items-center gap-5 px-4 py-2 ml-auto">
                  {activeChartConfig.areas.map((s) => (
                    <div
                      key={s.key}
                      className="flex items-center gap-2"
                    >
                      <div
                        className="w-2 h-2"
                        style={{ backgroundColor: s.color }}
                      />
                      <span className="text-sm uppercase tracking-widest text-gray-400">
                        {s.name}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── Mobile controls (<lg): multi-row layout ── */}
              {/* Mobile Row 1: View tabs (Price, Liquidity, Volume) */}
              <div className="lg:hidden flex items-center gap-1 px-3 py-2 border-b border-white/10">
                {Object.entries(CHART_VIEWS).map(([key, view]) => (
                  <button
                    key={key}
                    onClick={() => setChartView(key)}
                    className={`flex-1 px-2 py-1.5 text-xs font-semibold uppercase tracking-widest text-center transition-colors ${
                      chartView === key
                        ? "text-white bg-white/10"
                        : "text-gray-600 hover:text-gray-400"
                    }`}
                  >
                    {view.label}
                  </button>
                ))}
              </div>

              {/* Mobile Row 2: Series legend (Index Price, Mark Price) */}
              <div className="lg:hidden flex items-center justify-center gap-5 px-3 py-2 border-b border-white/10">
                {activeChartConfig.areas.map((s) => (
                  <div
                    key={s.key}
                    className="flex items-center gap-2"
                  >
                    <div
                      className="w-2 h-2"
                      style={{ backgroundColor: s.color }}
                    />
                    <span className="text-xs uppercase tracking-widest text-gray-400">
                      {s.name}
                    </span>
                  </div>
                ))}
              </div>

              {/* Chart body */}
              <div className="h-[350px] md:h-[500px] w-full p-4">
                {chartView === "LIQUIDITY" ? (
                  <ComboChart
                    bins={liquidityBins}
                    currentPrice={poolData?.markPrice || liqDistPrice || 0}
                  />
                ) : chartView === "VOLUME" ? (
                  volumeHistory.length === 0 ? (
                    <div className="h-full flex items-center justify-center">
                      <Loader2 className="animate-spin text-gray-700" />
                    </div>
                  ) : (
                    <RLDPerformanceChart
                      data={volumeHistory}
                      areas={activeChartConfig.areas}
                      resolution="1H"
                    />
                  )
                ) : chartData.length === 0 ? (
                  <div className="h-full flex items-center justify-center">
                    <Loader2 className="animate-spin text-gray-700" />
                  </div>
                ) : (
                  <RLDPerformanceChart
                    data={chartData}
                    areas={activeChartConfig.areas}
                    resolution={resolution}
                  />
                )}
              </div>

              {/* ── Mobile Row 3 (below chart): Resolution | Timeframe ── */}
              <div className="lg:hidden flex items-stretch border-t border-white/10">
                {/* Resolution dropdown */}
                <div className="relative flex-1 flex items-center justify-center px-3 py-2 border-r border-white/10">
                  <button
                    onClick={() => setChartDropdown(chartDropdown === 'resolution' ? null : 'resolution')}
                    className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-widest text-gray-600 hover:text-gray-400 transition-colors"
                  >
                    Res: <span className="text-white">{resolution}</span>
                    <ChevronDown size={10} className={`transition-transform ${chartDropdown === 'resolution' ? 'rotate-180' : ''}`} />
                  </button>
                  {chartDropdown === 'resolution' && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={() => setChartDropdown(null)} />
                      <div className="absolute left-0 bottom-full mb-0 z-50 bg-[#0a0a0a] border border-white/10">
                        {["1H", "4H", "1D", "1W"].map((res) => (
                          <button
                            key={res}
                            onClick={() => { chartControls.setResolution(res); setChartDropdown(null); }}
                            className={`block w-full text-left px-3 py-1.5 text-xs font-semibold uppercase tracking-widest transition-colors ${
                              resolution === res
                                ? "text-white bg-white/10"
                                : "text-gray-600 hover:text-gray-400"
                            }`}
                          >
                            {res}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </div>

                {/* Timeframe dropdown */}
                <div className="relative flex-1 flex items-center justify-center px-3 py-2">
                  <button
                    onClick={() => setChartDropdown(chartDropdown === 'timeframe' ? null : 'timeframe')}
                    className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-widest text-gray-600 hover:text-gray-400 transition-colors"
                  >
                    Range: <span className="text-white">{chartControls.activeRange}</span>
                    <ChevronDown size={10} className={`transition-transform ${chartDropdown === 'timeframe' ? 'rotate-180' : ''}`} />
                  </button>
                  {chartDropdown === 'timeframe' && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={() => setChartDropdown(null)} />
                      <div className="absolute left-0 bottom-full mb-0 z-50 bg-[#0a0a0a] border border-white/10">
                        {[
                          { l: "1D", d: 1 },
                          { l: "1W", d: 7 },
                          { l: "1M", d: 30 },
                          { l: "3M", d: 90 },
                          { l: "ALL", d: 9999 },
                        ].map((btn) => (
                          <button
                            key={btn.l}
                            onClick={() => { chartControls.handleQuickRange(btn.d, btn.l); setChartDropdown(null); }}
                            className={`block w-full text-left px-3 py-1.5 text-xs font-semibold uppercase tracking-widest transition-colors ${
                              chartControls.activeRange === btn.l
                                ? "text-white bg-white/10"
                                : "text-gray-600 hover:text-gray-400"
                            }`}
                          >
                            {btn.l}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* === RIGHT COLUMN: TRADING TERMINAL (Span 3) === */}
          <TradingTerminal
            title="Pool_Liquidity"
            Icon={Droplets}
            account={account}
            subTitle={
              account ? (
                <div className="relative">
                  <button
                    onClick={() => setAccountDropdownOpen(!accountDropdownOpen)}
                    className="flex items-center gap-1.5 text-sm font-mono text-cyan-400 hover:text-cyan-300 transition-colors"
                  >
                    <Wallet size={12} />
                    {selectedAccount === "broker" && brokerAddress
                      ? `Broker #1`
                      : `0x...${account.slice(-4)}`}
                    <ChevronDown size={12} className={`transition-transform ${accountDropdownOpen ? "rotate-180" : ""}`} />
                  </button>
                  {accountDropdownOpen && (
                    <div className="absolute right-0 top-full mt-2 z-50 border border-white/10 bg-[#0a0a0a] min-w-[220px]">
                      {/* Wallet option */}
                      <button
                        onClick={() => { setSelectedAccount("wallet"); setAccountDropdownOpen(false); }}
                        className={`w-full text-left px-4 py-2.5 text-sm font-mono hover:bg-white/5 transition-colors border-b border-white/5 flex items-center justify-between ${
                          selectedAccount === "wallet" ? "text-cyan-400" : "text-gray-400"
                        }`}
                      >
                        <span>Wallet</span>
                        <span className="text-xs text-gray-600">{`0x...${account.slice(-4)}`}</span>
                      </button>
                      {/* Broker option (if exists) */}
                      {hasBroker && brokerAddress && (
                        <button
                          onClick={() => { setSelectedAccount("broker"); setAccountDropdownOpen(false); }}
                          className={`w-full text-left px-4 py-2.5 text-sm font-mono hover:bg-white/5 transition-colors border-b border-white/5 flex items-center justify-between ${
                            selectedAccount === "broker" ? "text-cyan-400" : "text-gray-400"
                          }`}
                        >
                          <span>Broker #1</span>
                          <span className="text-xs text-gray-600">{`0x...${brokerAddress.slice(-4)}`}</span>
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <span className="text-sm text-gray-600 uppercase tracking-widest">V4</span>
              )
            }
            tabs={[
              {
                id: "ADD",
                label: "Add",
                onClick: () => setActiveTab("ADD"),
                isActive: activeTab === "ADD",
                color: "cyan",
              },
              {
                id: "REMOVE",
                label: "Remove",
                onClick: () => setActiveTab("REMOVE"),
                isActive: activeTab === "REMOVE",
                color: "pink",
              },
            ]}
            actionButton={{
              label:
                !account || !hasBroker
                  ? "Create Account"
                  : lpExecuting
                    ? (lpStep || "Processing...")
                    : activeTab === "ADD" ? "Add Liquidity" : "Remove Liquidity",
              onClick: () => {
                if (!account || !hasBroker) {
                  setShowAccountModal(true);
                  return;
                }
                if (lpExecuting) return;
                if (activeTab === "ADD") {
                  setShowAddModal(true);
                } else if (activeTab === "REMOVE" && selectedPosition) {
                  setWithdrawPosition(selectedPosition);
                }
              },
              disabled:
                !account || !hasBroker
                  ? false
                  : lpExecuting || (activeTab === "REMOVE" && !selectedPosition),
              variant: activeTab === "ADD" ? "cyan" : "pink",
            }}
            footer={null}
          >
            {/* === ADD LIQUIDITY === */}
            {activeTab === "ADD" && (
              <div className="space-y-4">
                {/* Price Range */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm uppercase tracking-widest font-bold text-gray-500">
                      Price Range
                    </span>
                    <button
                      onClick={() => {
                        const ts = poolData?.tickSpacing || 5;
                        const padTicks = 10 * ts;
                        const minP = Math.pow(1.0001, -92100 + padTicks);
                        const maxP = Math.pow(1.0001, 46050 - padTicks);
                        setMinPrice(minP.toFixed(6));
                        setMaxPrice(maxP.toFixed(2));
                      }}
                      className="text-sm text-cyan-500 uppercase tracking-widest hover:text-cyan-400 transition-colors"
                    >
                      Full Range
                    </button>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="border border-white/10 bg-[#080808] p-3">
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
                        Min Price
                      </div>
                      <input
                        type="number"
                        value={minPrice}
                        onChange={(e) => setMinPrice(e.target.value)}
                        className="w-full bg-transparent text-white text-sm font-mono focus:outline-none"
                        placeholder="0.00"
                      />
                      <div className="text-sm text-gray-600 mt-1">
                        {poolData.token1.symbol} per {poolData.token0.symbol}
                      </div>
                    </div>
                    <div className="border border-white/10 bg-[#080808] p-3">
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
                        Max Price
                      </div>
                      <input
                        type="number"
                        value={maxPrice}
                        onChange={(e) => setMaxPrice(e.target.value)}
                        className="w-full bg-transparent text-white text-sm font-mono focus:outline-none"
                        placeholder="0.00"
                      />
                      <div className="text-sm text-gray-600 mt-1">
                        {poolData.token1.symbol} per {poolData.token0.symbol}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Token Amounts */}
                <InputGroup
                  label={poolData.token0.symbol}
                  subLabel={`Balance: ${token0Balance != null ? token0Balance.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}`}
                  value={token0Amount}
                  onChange={handleToken0Change}
                  suffix={poolData.token0.symbol}
                  onMax={token0Balance > 0 ? () => handleToken0Change(String(token0Balance)) : undefined}
                />
                <InputGroup
                  label={poolData.token1.symbol}
                  subLabel={`Balance: ${token1Balance != null ? token1Balance.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}`}
                  value={token1Amount}
                  onChange={handleToken1Change}
                  suffix={poolData.token1.symbol}
                  onMax={token1Balance > 0 ? () => handleToken1Change(String(token1Balance)) : undefined}
                />

                {/* Summary */}
                <div className="space-y-2">
                  <SummaryRow label="Pool" value={poolData.pair} />
                  <SummaryRow label="Fee Tier" value={poolData.feeTier} />
                  <SummaryRow
                    label="Current Price"
                    value={poolData.currentPrice.toFixed(4)}
                  />
                  <SummaryRow
                    label="Est. APR"
                    value={`${poolData.apr.toFixed(2)}%`}
                    valueColor="text-green-400"
                  />
                </div>
              </div>
            )}

            {/* === REMOVE LIQUIDITY === */}
            {activeTab === "REMOVE" && (
              <>
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm uppercase tracking-widest font-bold text-gray-500">
                      Select Position
                    </span>
                    {userPositions.length > 0 && (
                      <span className="text-sm text-gray-600 font-mono">
                        {userPositions.length} position{userPositions.length !== 1 ? "s" : ""}
                      </span>
                    )}
                  </div>

                  {/* Empty state — context aware */}
                  {userPositions.length === 0 && (
                    <div className="border border-white/10 bg-[#080808] p-6 text-center">
                      <Layers size={24} className="mx-auto text-gray-700 mb-3" />
                      {!account ? (
                        <>
                          <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
                            Wallet Not Connected
                          </div>
                          <div className="text-sm text-gray-700">
                            Connect your wallet to view and manage LP positions
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
                            No Positions Found
                          </div>
                          <div className="text-sm text-gray-700">
                            You have no active LP positions — add liquidity to get started
                          </div>
                        </>
                      )}
                    </div>
                  )}

                  {/* Position cards — show only selected when one is picked, paginated otherwise */}
                  {(selectedPosition
                    ? userPositions.filter((p) => p.id === selectedPosition.id)
                    : userPositions.slice(removePage * POSITIONS_PER_PAGE, (removePage + 1) * POSITIONS_PER_PAGE)
                  ).map((pos) => (
                    <button
                      key={pos.id}
                      onClick={() => _setSelectedPosition(
                        selectedPosition?.id === pos.id ? null : pos
                      )}
                      className={`w-full text-left border p-3 transition-all ${
                        selectedPosition?.id === pos.id
                          ? "border-pink-500/50 bg-pink-500/[0.06]"
                          : "border-white/10 bg-[#080808] hover:border-white/20"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className={`text-sm font-mono ${
                            selectedPosition?.id === pos.id ? "text-pink-400" : "text-white"
                          }`}>
                            #{pos.id}
                          </span>
                          {pos.isActive && (
                            <span className="text-[10px] px-1.5 py-0.5 bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 uppercase tracking-widest">
                              Collateral
                            </span>
                          )}
                        </div>
                        <span className={`text-[10px] px-1.5 py-0.5 uppercase tracking-widest ${
                          pos.inRange
                            ? "bg-green-500/20 text-green-400 border border-green-500/30"
                            : "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30"
                        }`}>
                          {pos.inRange ? "In Range" : "Out of Range"}
                        </span>
                      </div>
                      <div className="grid grid-cols-3 gap-2 text-sm">
                        <div>
                          <div className="text-gray-600 text-[10px] uppercase tracking-widest">Range</div>
                          <div className="font-mono text-gray-300">
                            {pos.priceLower?.toFixed(2)} – {pos.priceUpper?.toFixed(2)}
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-600 text-[10px] uppercase tracking-widest">Entry</div>
                          <div className="font-mono text-gray-300">
                            {pos.entryPrice?.toFixed(4)}
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-600 text-[10px] uppercase tracking-widest">Value</div>
                          <div className="font-mono text-gray-300">
                            ${pos.value?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                          </div>
                        </div>
                      </div>
                    </button>
                  ))}
                  {/* Pagination — only when not selecting and more than one page */}
                  {!selectedPosition && userPositions.length > POSITIONS_PER_PAGE && (
                    <div className="flex items-center justify-between pt-1">
                      <button
                        onClick={() => setRemovePage((p) => Math.max(0, p - 1))}
                        disabled={removePage === 0}
                        className="text-sm font-mono text-gray-500 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        ← Prev
                      </button>
                      <span className="text-sm text-gray-600 font-mono">
                        {removePage + 1} / {Math.ceil(userPositions.length / POSITIONS_PER_PAGE)}
                      </span>
                      <button
                        onClick={() => setRemovePage((p) => Math.min(Math.ceil(userPositions.length / POSITIONS_PER_PAGE) - 1, p + 1))}
                        disabled={removePage >= Math.ceil(userPositions.length / POSITIONS_PER_PAGE) - 1}
                        className="text-sm font-mono text-gray-500 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        Next →
                      </button>
                    </div>
                  )}
                </div>

                {/* Remove controls (only when a position is selected) */}
                {selectedPosition && (
                  <>
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm uppercase tracking-widest font-bold text-gray-500">
                          Amount to Remove
                        </span>
                        <span className="text-xl font-mono text-white">
                          {removePercent}%
                        </span>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="100"
                        value={removePercent}
                        onChange={(e) =>
                          setRemovePercent(Number(e.target.value))
                        }
                        className="w-full accent-pink-500 h-1"
                      />
                      <div className="grid grid-cols-4 gap-2">
                        {[25, 50, 75, 100].map((pct) => (
                          <button
                            key={pct}
                            onClick={() => setRemovePercent(pct)}
                            className={`py-1.5 text-sm font-bold uppercase tracking-widest border transition-colors ${
                              removePercent === pct
                                ? "border-pink-500/50 text-pink-400 bg-pink-500/10"
                                : "border-white/10 text-gray-500 hover:text-white hover:border-white/20"
                            }`}
                          >
                            {pct}%
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Removal summary */}
                    <div className="space-y-2 pt-2 border-t border-white/10">
                      <SummaryRow
                        label="Position"
                        value={`#${selectedPosition.id}`}
                      />
                      <SummaryRow
                        label="Removing"
                        value={`${removePercent}% of liquidity`}
                        valueColor="text-pink-400"
                      />
                      <SummaryRow
                        label={`Est. ${poolData.token0.symbol}`}
                        value={(() => {
                          const raw = parseFloat(selectedPosition.token0Amount?.replace(/,/g, '') || 0);
                          return (raw * removePercent / 100).toLocaleString(undefined, { maximumFractionDigits: 2 });
                        })()}
                      />
                      <SummaryRow
                        label={`Est. ${poolData.token1.symbol}`}
                        value={(() => {
                          const raw = parseFloat(selectedPosition.token1Amount?.replace(/,/g, '') || 0);
                          return (raw * removePercent / 100).toLocaleString(undefined, { maximumFractionDigits: 2 });
                        })()}
                      />
                    </div>

                    {/* Error display */}
                    {lpError && (
                      <div className="border border-pink-500/30 bg-pink-500/10 px-3 py-2 text-sm text-pink-400 font-mono">
                        {lpError}
                      </div>
                    )}
                  </>
                )}
              </>
            )}
          </TradingTerminal>
        </div>

        {/* 3. POSITIONS TABLE */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
          <div className="xl:col-span-9 border border-white/10">
              <div className="px-6 py-4 border-b border-white/10 bg-[#0a0a0a] flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="text-sm font-bold uppercase tracking-widest">
                    Your Positions
                  </h3>
                </div>
                <div className="text-sm text-gray-500 uppercase tracking-widest flex items-center gap-2">
                  <Activity size={12} />
                  {userPositions.length > 0 ? "UNISWAP V4" : "NONE"}
                </div>
              </div>

              {/* Empty state — context aware */}
              {userPositions.length === 0 && (
                <div className="px-6 py-12 text-center">
                  <Layers size={32} className="mx-auto text-gray-700 mb-4" />
                  {!account ? (
                    <>
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                        Wallet Not Connected
                      </div>
                      <div className="text-sm text-gray-700 max-w-sm mx-auto">
                        Connect your wallet to view your LP positions
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                        No LP Positions
                      </div>
                      <div className="text-sm text-gray-700 max-w-sm mx-auto">
                        You don&apos;t have any active positions yet. Use the panel above to add liquidity.
                      </div>
                    </>
                  )}
                </div>
              )}

              {/* Table Header — shown when positions exist */}
              {userPositions.length > 0 && (
                <>
                  <div className="hidden md:grid grid-cols-14 gap-4 px-6 py-3 text-sm text-gray-500 uppercase tracking-widest border-b border-white/5 text-center" style={{ gridTemplateColumns: '3fr 2fr 2fr 2fr 3fr 3fr 2fr 1fr' }}>
                    <div className="text-left">#</div>
                    <div>Range</div>
                    <div>Entry Price</div>
                    <div>Value</div>
                    <div>Token 0</div>
                    <div>Token 1</div>
                    <div>Status</div>
                    <div>Action</div>
                  </div>

                  {userPositions.map((pos) => (
                    <div key={pos.id}>
                      <div
                        className={`grid gap-4 px-6 py-4 transition-colors border-b border-white/5 last:border-b-0 items-center text-center ${
                          pos.isActive
                            ? "bg-cyan-500/[0.06] hover:bg-cyan-500/[0.1] border-l-2 border-l-cyan-500"
                            : "hover:bg-white/[0.02]"
                        }`}
                        style={{ gridTemplateColumns: '3fr 2fr 2fr 2fr 3fr 3fr 2fr 1fr' }}
                      >
                        <div className={`text-sm font-mono text-left ${pos.isActive ? "text-cyan-400" : "text-gray-500"}`}>
                          {pos.id}
                        </div>
                        <div>
                          <div className={`text-sm font-mono ${pos.isActive ? "text-cyan-300" : "text-white"}`}>
                            {pos.priceLower?.toFixed(2)} – {pos.priceUpper?.toFixed(2)}
                          </div>
                        </div>
                        <div className={`text-sm font-mono ${pos.isActive ? "text-cyan-300" : "text-white"}`}>
                          {pos.entryPrice?.toFixed(2)}
                        </div>
                        <div className={`text-sm font-mono ${pos.isActive ? "text-cyan-300" : "text-white"}`}>
                          ${pos.value?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                        </div>
                        <div className={`text-sm font-mono ${pos.isActive ? "text-cyan-300" : "text-white"}`}>
                          {pos.token0Amount}{" "}
                          <span className={pos.isActive ? "text-cyan-500/60 text-sm" : "text-gray-500 text-sm"}>
                            {poolData.token0.symbol}
                          </span>
                        </div>
                        <div className={`text-sm font-mono ${pos.isActive ? "text-cyan-300" : "text-white"}`}>
                          {pos.token1Amount}{" "}
                          <span className={pos.isActive ? "text-cyan-500/60 text-sm" : "text-gray-500 text-sm"}>
                            {poolData.token1.symbol}
                          </span>
                        </div>
                        <div className="text-sm font-mono">
                          {pos.isActive ? (
                            <span className="text-[10px] px-2 py-0.5 bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 uppercase tracking-widest">
                              Collateral
                            </span>
                          ) : (
                            <span className="text-gray-600">—</span>
                          )}
                        </div>
                        <div className="relative flex justify-center">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setActionDropdown(actionDropdown === pos.id ? null : pos.id);
                            }}
                            className={`p-1.5 hover:bg-white/5 transition-colors ${pos.isActive ? "text-cyan-500 hover:text-cyan-300" : "text-gray-600 hover:text-white"}`}
                          >
                            <ChevronDown size={16} className={`transition-transform ${actionDropdown === pos.id ? 'rotate-180' : ''}`} />
                          </button>
                          {actionDropdown === pos.id && (
                            <div className="absolute right-0 top-full mt-1 z-50 border border-white/10 bg-[#0a0a0a] backdrop-blur-sm min-w-[150px]">
                              <button
                                onClick={() => {
                                  setActionDropdown(null);
                                  setClaimPosition(pos);
                                }}
                                className="w-full text-left px-4 py-2 text-sm text-white hover:bg-white/5 transition-colors font-mono"
                              >
                                Claim Fees
                              </button>
                              <button
                                onClick={() => {
                                  setActionDropdown(null);
                                  setWithdrawPosition(pos);
                                }}
                                className="w-full text-left px-4 py-2 text-sm text-white hover:bg-white/5 transition-colors border-t border-white/5 font-mono"
                              >
                                Withdraw
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </>
              )}
          </div>{/* close col-span-9 */}
        </div>{/* close grid */}
      </div>{/* close max-w container */}

      {/* Claim Fees Modal */}
      <ClaimFeesModal
        isOpen={!!claimPosition}
        onClose={() => {
          if (!lpExecuting) {
            setClaimPosition(null);
            clearLpError();
          }
        }}
        onConfirm={() => {
          if (!claimPosition?.tokenId) return;
          executeCollectFees(
            claimPosition.tokenId,
            () => {
              setClaimPosition(null);
              addToast({
                type: "success",
                title: "Fees Collected",
                message: `Collected fees from position #${claimPosition.id}`,
              });
            },
          );
        }}
        position={claimPosition}
        token0={poolData.token0}
        token1={poolData.token1}
        executing={lpExecuting}
        executionStep={lpStep}
        executionError={lpError}
      />

      {/* Withdraw Modal */}
      <WithdrawModal
        isOpen={!!withdrawPosition}
        onClose={() => {
          if (!lpExecuting) {
            setWithdrawPosition(null);
            clearLpError();
          }
        }}
        onConfirm={(percent) => {
          if (!withdrawPosition?.tokenId) return;
          executeRemoveLiquidity(
            withdrawPosition.tokenId,
            percent,
            () => {
              const posId = withdrawPosition.id;
              setWithdrawPosition(null);
              _setSelectedPosition(null);
              fetchBalances();
              addToast({
                type: "success",
                title: "Liquidity Removed",
                message: `Removed ${percent}% from position #${posId}`,
              });
            },
          );
        }}
        position={withdrawPosition}
        token0={poolData.token0}
        token1={poolData.token1}
        executing={lpExecuting}
        executionStep={lpStep}
        executionError={lpError}
      />

      {/* Add Liquidity Modal */}
      <AddLiquidityModal
        isOpen={showAddModal}
        onClose={() => {
          if (!lpExecuting) {
            setShowAddModal(false);
            clearLpError();
          }
        }}
        onConfirm={() => {
          executeAddLiquidity(
            minPrice,
            maxPrice,
            token0Amount,
            token1Amount,
            poolData?.currentPrice || 1,
            () => {
              setShowAddModal(false);
              fetchBalances();
              addToast({ type: "success", title: "Liquidity Added", message: `Added LP in range $${minPrice} – $${maxPrice}` });
            },
          );
        }}
        minPrice={minPrice}
        maxPrice={maxPrice}
        token0Amount={token0Amount}
        token1Amount={token1Amount}
        token0={poolData.token0}
        token1={poolData.token1}
        pool={poolData}
        executing={lpExecuting}
        executionStep={lpStep}
        executionError={lpError}
      />

      <AccountModal
        isOpen={showAccountModal}
        onClose={() => setShowAccountModal(false)}
        onComplete={(addr) => {
          setShowAccountModal(false);
          checkBroker();
          if (addr) {
            fetchBrokerBalance(addr);
            addToast({
              type: "success",
              title: "Account Created",
              message: "Broker deployed & funded successfully",
              duration: 5000,
            });
          }
        }}
        brokerFactoryAddr={marketInfo?.broker_factory}
        waUsdcAddr={marketInfo?.collateral?.address}
        externalContracts={marketInfo?.external_contracts}
      />

      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
}
